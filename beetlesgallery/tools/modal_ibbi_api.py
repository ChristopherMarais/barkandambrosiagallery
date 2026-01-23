import modal
import sys
import io
import base64
import os
from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import JSONResponse

# --- Configuration ---
# CHANGED: Use a new path to avoid conflicts with system/build files
CACHE_DIR = "/model_cache"

# 1. Define the Container Environment
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libgl1-mesa-glx", "libglib2.0-0")
    .pip_install(
        "ibbi",
        "fastapi",
        "python-multipart",
        "opencv-python-headless",
        "numpy",
        "pillow",
        "matplotlib",
        "torch",
        "torchvision",
        "transformers",
        "huggingface_hub",
        "supervision",
        "scipy",
        "timm",
        "einops"
    )
    .env({
        # Directing AI model caches to our volume
        "HF_HOME": CACHE_DIR,
        "TORCH_HOME": CACHE_DIR,
        # REMOVED XDG_CACHE_HOME to prevent pip from filling this dir during build
        "MPLCONFIGDIR": f"{CACHE_DIR}/matplotlib"
    })
)

app = modal.App("ibbi-api")

# --- Define a Volume to persist model weights ---
cache_volume = modal.Volume.from_name("ibbi-cache", create_if_missing=True)

# 2. Define the Model Service
@app.cls(
    image=image,
    gpu="any",
    scaledown_window=300,  # Keep warm for 5 mins
    timeout=600,
    # Mount the volume to the clean custom path
    volumes={CACHE_DIR: cache_volume} 
)
class ModelService:
    @modal.enter()
    def load_dependencies(self):
        """Runs once when the container starts."""
        import ibbi
        self.ibbi = ibbi
        # In-Memory Cache: Keeps loaded models in GPU RAM
        self.loaded_models = {} 
        print("✅ IBBI Package loaded. Cache initialized.")

    def _get_model_name(self, task, architecture):
        """Maps UI selection to internal IBBI model names"""
        REGISTRY = {
            "Single-Class Detection": {
                "yolov10": "yolov10x_bb_detect_model",
                "yolov11": "yolov11x_bb_detect_model",
                "yolov9": "yolov9e_bb_detect_model",
                "yolov8": "yolov8x_bb_detect_model",
                "rtdetr": "rtdetrx_bb_detect_model",
            },
            "Multi-Class Detection": {
                "yolov10": "yolov10x_bb_multi_class_detect_model",
                "yolov11": "yolov11x_bb_multi_class_detect_model",
                "yolov9": "yolov9e_bb_multi_class_detect_model",
                "yolov8": "yolov8x_bb_multi_class_detect_model",
                "rtdetr": "rtdetrx_bb_multi_class_detect_model",
            },
             "Zero-Shot Detection": {
                "grounding_dino": "grounding_dino_detect_model"
            }
        }
        if task == "Zero-Shot Detection":
            return "grounding_dino_detect_model"
        return REGISTRY.get(task, {}).get(architecture)

    @modal.method()
    def process_image(self, image_bytes, task, architecture, text_prompt=None, box_threshold=0.25, text_threshold=0.25):
        from PIL import Image
        import io

        print(f"Processing Task: {task} | Arch: {architecture}")
        
        try:
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            
            model_name = self._get_model_name(task, architecture)
            if not model_name:
                raise ValueError(f"Invalid model configuration: {task}/{architecture}")

            # --- OPTIMIZATION: RAM Cache Check ---
            if model_name in self.loaded_models:
                print(f"⚡ Using cached model from RAM: {model_name}")
                model = self.loaded_models[model_name]
            else:
                print(f"💾 Loading model from Disk/Vol: {model_name}")
                # This loads from /model_cache (fast volume) or downloads if missing
                model = self.ibbi.create_model(model_name, pretrained=True)
                self.loaded_models[model_name] = model # Save to RAM
            
            # Inference
            if task in ["Single-Class Detection", "Multi-Class Detection"]:
                results = model.predict(img)
                annotated_img = self._draw_yolo(img.copy(), results)
            else:
                if not text_prompt:
                    return {"status": "error", "message": "Text prompt required for Zero-Shot"}
                results = model.predict(img, text_prompt=text_prompt, box_threshold=float(box_threshold), text_threshold=float(text_threshold))
                annotated_img = self._draw_dino(img.copy(), results)

            buffered = io.BytesIO()
            annotated_img.save(buffered, format="JPEG")
            img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
            
            return {
                "status": "success",
                "image_base64": img_str,
                "model_used": model_name
            }

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"❌ Error: {str(e)}")
            return {"status": "error", "message": f"Server Error: {str(e)}"}

    # --- Drawing Helpers ---
    def _draw_yolo(self, image, results):
        from PIL import ImageDraw
        draw = ImageDraw.Draw(image)
        
        # Check for dictionary keys (IBBI format)
        if not results or "boxes" not in results or not results["boxes"]: 
            return image
        
        boxes = results.get("boxes", [])
        scores = results.get("scores", [])
        labels = results.get("labels", [])
        
        for box, score, label in zip(boxes, scores, labels):
            coords = box 
            label_text = f"{label} {score:.2f}"
            
            draw.rectangle(coords, outline="red", width=3)
            
            text_pos = (coords[0], coords[1]-12 if coords[1]-12 > 0 else coords[1])
            text_bbox = draw.textbbox(text_pos, label_text)
            draw.rectangle(text_bbox, fill="red")
            draw.text(text_pos, label_text, fill="white")
            
        return image

    def _draw_dino(self, image, results):
        from PIL import ImageDraw
        draw = ImageDraw.Draw(image)
        if not results: return image
        
        boxes = results.get("boxes", [])
        scores = results.get("scores", [])
        labels = results.get("text_labels", [])
        
        for box, score, label in zip(boxes, scores, labels):
            coords = box.tolist()
            text = f"{label} {score:.2f}"
            draw.rectangle(coords, outline="green", width=3)
            draw.text((coords[0], coords[1]-10), text, fill="green")
        return image

# 3. Batch Download Function (Run Manually Once)
@app.function(
    image=image,
    volumes={CACHE_DIR: cache_volume}, 
    timeout=3600
)
def download_all_models():
    """
    RUN THIS MANUALLY to pre-populate the persistent volume cache.
    Command: pixi run modal run beetlesgallery/tools/modal_ibbi_api.py::download_all_models
    """
    import ibbi
    print(f"⬇️ Starting bulk download of all models to {CACHE_DIR}...")
    
    all_models = [
        "yolov10x_bb_detect_model",
        "yolov11x_bb_detect_model",
        "yolov9e_bb_detect_model",
        "yolov8x_bb_detect_model",
        "rtdetrx_bb_detect_model",
        "yolov10x_bb_multi_class_detect_model",
        "yolov11x_bb_multi_class_detect_model",
        "yolov9e_bb_multi_class_detect_model",
        "yolov8x_bb_multi_class_detect_model",
        "rtdetrx_bb_multi_class_detect_model",
        "grounding_dino_detect_model"
    ]

    for model_name in all_models:
        print(f"📦 Downloading/Checking {model_name}...")
        try:
            ibbi.create_model(model_name, pretrained=True)
            print(f"✅ {model_name} ready.")
        except Exception as e:
            print(f"❌ Failed to download {model_name}: {e}")
    
    print("🎉 All models downloaded to 'ibbi-cache' volume!")

# 4. Web Endpoint
web_app = FastAPI()

@app.function(image=image)
@modal.asgi_app()
def fastapi_app():
    return web_app

@web_app.post("/analyze")
async def analyze(
    task: str = Form(...),
    architecture: str = Form(...),
    text_prompt: str = Form(None),
    box_threshold: float = Form(0.25),
    text_threshold: float = Form(0.25),
    image: UploadFile = File(...)
):
    content = await image.read()
    service = ModelService()
    result = service.process_image.remote(
        content, task, architecture, text_prompt, box_threshold, text_threshold
    )
    return result