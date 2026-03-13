import modal
import sys
import io
import base64
import os
from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import JSONResponse

# --- Configuration ---
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
        "HF_image_browser": CACHE_DIR,
        "TORCH_image_browser": CACHE_DIR,
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
    scaledown_window=300,
    timeout=600,
    volumes={CACHE_DIR: cache_volume} 
)
class ModelService:
    @modal.enter()
    def load_dependencies(self):
        """Runs once when the container starts."""
        import ibbi
        self.ibbi = ibbi
        self.loaded_models = {} 
        print("✅ IBBI Package loaded. Cache initialized.")

    def _get_model_name(self, architecture):
        """Maps UI selection to internal IBBI model names (Multi-Class Only)"""
        REGISTRY = {
            "rtdetr": "rtdetrx_bb_multi_class_detect_model",
            "yolov12": "yolov12x_bb_multi_class_detect_model",
            "yolov11": "yolov11x_bb_multi_class_detect_model",
            "yolov10": "yolov10x_bb_multi_class_detect_model",
            "yolov9": "yolov9e_bb_multi_class_detect_model",
            "yolov8": "yolov8x_bb_multi_class_detect_model",
        }
        return REGISTRY.get(architecture)

    @modal.method()
    def process_image(self, image_bytes, architecture, box_threshold=0.25):
        from PIL import Image
        import io

        print(f"Processing Arch: {architecture}")
        
        try:
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            
            model_name = self._get_model_name(architecture)
            if not model_name:
                raise ValueError(f"Invalid model architecture: {architecture}")

            # --- RAM Cache Check ---
            if model_name in self.loaded_models:
                print(f"⚡ Using cached model from RAM: {model_name}")
                model = self.loaded_models[model_name]
            else:
                print(f"💾 Loading model from Disk/Vol: {model_name}")
                model = self.ibbi.create_model(model_name, pretrained=True)
                self.loaded_models[model_name] = model
            
            # Inference with Full Probabilities
            # This is critical for the "Distribution Plot" feature
            results = model.predict(img, include_full_probabilities=True)
            
            detections = []
            class_names = results.get("class_names", [])

            # Extract data from the IBBI 'full_results' list
            # Format: [{'bbox': [], 'confidence': 0.9, 'class_probabilities': [...]}, ...]
            if results and "full_results" in results:
                full_res = results.get("full_results", [])
                
                for item in full_res:
                    score = item.get("confidence", 0.0)
                    if score < float(box_threshold): 
                        continue
                        
                    detections.append({
                        "box": item.get("bbox"),
                        "score": float(score),
                        "label": item.get("predicted_class"),
                        "probs": item.get("class_probabilities", [])
                    })

            return {
                "status": "success",
                "detections": detections,
                "class_names": class_names,
                "model_used": model_name
            }

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"❌ Error: {str(e)}")
            return {"status": "error", "message": f"Server Error: {str(e)}"}

# 3. Batch Download Function (All Multi-Class Models)
@app.function(
    image=image,
    volumes={CACHE_DIR: cache_volume}, 
    timeout=3600
)
def download_all_models():
    """
    RUN MANUALLY: pixi run modal run beetlesgallery/tools/modal_ibbi_api.py::download_all_models
    """
    import ibbi
    print(f"⬇️ Starting bulk download of all CLASSIFIER models to {CACHE_DIR}...")
    
    all_models = [
        "rtdetrx_bb_multi_class_detect_model",
        "yolov12x_bb_multi_class_detect_model",
        "yolov11x_bb_multi_class_detect_model",
        "yolov10x_bb_multi_class_detect_model",
        "yolov9e_bb_multi_class_detect_model",
        "yolov8x_bb_multi_class_detect_model",
    ]

    for model_name in all_models:
        print(f"📦 Downloading/Checking {model_name}...")
        try:
            ibbi.create_model(model_name, pretrained=True)
            print(f"✅ {model_name} ready.")
        except Exception as e:
            print(f"❌ Failed to download {model_name}: {e}")
    
    print("🎉 All classifier models downloaded to 'ibbi-cache' volume!")

# 4. Web Endpoint
web_app = FastAPI()

@app.function(image=image)
@modal.asgi_app()
def fastapi_app():
    return web_app

@web_app.post("/analyze")
async def analyze(
    architecture: str = Form(...),
    box_threshold: float = Form(0.25),
    image: UploadFile = File(...)
):
    content = await image.read()
    service = ModelService()
    result = service.process_image.remote(
        content, architecture, box_threshold
    )
    return result