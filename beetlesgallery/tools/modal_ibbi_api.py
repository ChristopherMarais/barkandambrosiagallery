"""
The AI classifier service on Modal (GPU). The site posts an image and a model key; the answer is described in
ibbi_models.py, which also lists the models (ibbi 0.3.2).

    Deploy:        pixi run modal deploy beetlesgallery/tools/modal_ibbi_api.py
                   (automatic: "Deploy App" on a release when this code changed, or by hand with target modal)
    Pre-download:  pixi run modal run beetlesgallery/tools/modal_ibbi_api.py::download_all_models

The staging site has its own copy, deployed by "Deploy Staging" with IBBI_MODAL_APP=ibbi-api-staging, so trying a
change on staging never touches the live service (its address: ...--ibbi-api-staging-fastapi-app.modal.run).
"""
import os
import sys
from pathlib import Path

import modal
from fastapi import FastAPI, File, Form, UploadFile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))   # ibbi_models sits next to this file, here and in the container (/root)
import ibbi_models  # noqa: E402

# --- Configuration ---
CACHE_DIR = "/model_cache"
APP_NAME = os.environ.get("IBBI_MODAL_APP") or "ibbi-api"   # "ibbi-api-staging" for the staging site

# 1. The container: ibbi brings torch, ultralytics, transformers and timm with it
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libgl1", "libglib2.0-0")
    # The web parts have lower bounds with their security fixes (starlette 1.3.1, python-multipart 0.0.31, Pillow 12.3);
    # changing this list makes Modal build the image afresh on the next deploy.
    .pip_install(f"ibbi=={ibbi_models.IBBI_VERSION}", "fastapi>=0.142", "starlette>=1.3.1", "python-multipart>=0.0.31",
                 "pillow>=12.3")
    .env({
        # Every model download lands on the volume below, so a cold start does not fetch the weights again
        "IBBI_CACHE_DIR": f"{CACHE_DIR}/ibbi",
        "HF_HOME": f"{CACHE_DIR}/huggingface",
        "TORCH_HOME": f"{CACHE_DIR}/torch",
        "YOLO_CONFIG_DIR": f"{CACHE_DIR}/ultralytics",
        "MPLCONFIGDIR": f"{CACHE_DIR}/matplotlib",
        "IBBI_MODAL_APP": APP_NAME,   # the container names its app the same way
    })
    .add_local_file(HERE / "ibbi_models.py", "/root/ibbi_models.py")
)

app = modal.App(APP_NAME)

# --- A volume that keeps the model weights between containers ---
cache_volume = modal.Volume.from_name("ibbi-cache", create_if_missing=True)


# 2. The model service
@app.cls(
    image=image,
    gpu="any",
    scaledown_window=300,
    timeout=600,
    volumes={CACHE_DIR: cache_volume},
)
class ModelService:
    @modal.enter()
    def load_dependencies(self):
        """Runs once when the container starts."""
        import ibbi
        self.ibbi = ibbi
        self.loaded = {}   # ibbi model name -> model, kept in memory while the container lives
        print(f"IBBI {ibbi.__version__} loaded.")

    def _model(self, name):
        if name not in self.loaded:
            print(f"Loading {name}")
            self.loaded[name] = self.ibbi.create_model(name, pretrained=True)
        return self.loaded[name]

    @modal.method()
    def process_image(self, image_bytes, architecture, box_threshold=0.25):
        import io

        from PIL import Image

        key = ibbi_models.resolve(architecture)
        if key is None:
            return {"status": "error", "message": f"Unknown model: {architecture}"}
        spec = ibbi_models.MODELS[key]
        try:
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            conf = float(box_threshold)
            if spec["kind"] == "pipeline":
                pipe = self.ibbi.IdentificationPipeline(self._model(spec["detector"]), self._model(spec["classifier"]),
                                                        det_conf=conf)
                detections = ibbi_models.from_pipeline(pipe.predict(img))
            else:
                detections = ibbi_models.from_detector(self._model(spec["detector"]).predict(img, conf=conf))
            return ibbi_models.response(key, detections)
        except Exception:
            import traceback
            traceback.print_exc()   # in the Modal log; the caller only learns that it failed
            return {"status": "error", "message": "Server error: the image could not be processed."}


# 3. Download every model's weights to the volume once
@app.function(image=image, volumes={CACHE_DIR: cache_volume}, timeout=3600)
def download_all_models():
    """RUN MANUALLY: pixi run modal run beetlesgallery/tools/modal_ibbi_api.py::download_all_models"""
    import ibbi
    for name in ibbi_models.all_ibbi_models():
        print(f"Downloading {name}...")
        try:
            ibbi.create_model(name, pretrained=True)
            print(f"{name} ready.")
        except Exception as e:
            print(f"Failed to download {name}: {e}")
    cache_volume.commit()


# 4. Web endpoint
web_app = FastAPI()


@app.function(image=image)
@modal.asgi_app()
def fastapi_app():
    return web_app


@web_app.post("/analyze")
async def analyze(
    architecture: str = Form(ibbi_models.DEFAULT),
    box_threshold: float = Form(0.25),
    image: UploadFile = File(...),
):
    content = await image.read()
    return ModelService().process_image.remote(content, architecture, box_threshold)
