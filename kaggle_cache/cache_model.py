"""
Runs ON KAGGLE, ONCE PER MODEL (no GPU needed). Downloads only the files that
kaggle_sd/generate.py loads for one image model and saves them as this job's output.
Image jobs attach that output, so they never download the model again.

run_kaggle.py --setup-model <model> pushes a copy of this script with the MODEL line
below set, as its own Kaggle notebook (one cache notebook per model).

  sdxl         ~7 GB  -> models/sdxl-base, models/sdxl-vae, models/CACHE_OK.json
                       (SDXL base fp16 weights + madebyollin/sdxl-vae-fp16-fix; skips the
                        single-file checkpoint, fp32 copies, .bin/ONNX/OpenVINO/Flax, SDXL's VAE)
  flux2-klein  ~16 GB -> models/flux2-klein/..., models/flux2-klein/CACHE_OK.json
                       (black-forest-labs/FLUX.2-klein-4B diffusers folders; skips the duplicate
                        single-file flux-2-klein-4b.safetensors and sample images)

CACHE_OK.json is written last, only if every required file downloaded.
"""
import json, os, socket, subprocess, sys, time

MODEL = "sdxl"   # run_kaggle.py rewrites this line for each cache notebook

MODELS = {
    "sdxl": {
        "out": "",          # legacy layout: models/sdxl-base, models/sdxl-vae, models/CACHE_OK.json
        "repos": {
            "sdxl-base": ("stabilityai/stable-diffusion-xl-base-1.0", [
                "model_index.json", "scheduler/scheduler_config.json", "tokenizer/*", "tokenizer_2/*",
                "text_encoder/config.json", "text_encoder/model.fp16.safetensors",
                "text_encoder_2/config.json", "text_encoder_2/model.fp16.safetensors",
                "unet/config.json", "unet/diffusion_pytorch_model.fp16.safetensors"]),
            "sdxl-vae": ("madebyollin/sdxl-vae-fp16-fix", [
                "config.json", "diffusion_pytorch_model.safetensors"]),
        },
        "required": ["sdxl-base/model_index.json",
                     "sdxl-base/unet/diffusion_pytorch_model.fp16.safetensors",
                     "sdxl-base/text_encoder/model.fp16.safetensors",
                     "sdxl-base/text_encoder_2/model.fp16.safetensors",
                     "sdxl-vae/diffusion_pytorch_model.safetensors"],
    },
    "flux2-klein": {
        "out": "flux2-klein",
        "repos": {
            "": ("black-forest-labs/FLUX.2-klein-4B", [
                "model_index.json", "scheduler/*", "tokenizer/*", "text_encoder/*",
                "transformer/*", "vae/*"]),
        },
        "required": ["model_index.json",
                     "transformer/diffusion_pytorch_model.safetensors",
                     "text_encoder/model-00001-of-00002.safetensors",
                     "text_encoder/model-00002-of-00002.safetensors",
                     "vae/diffusion_pytorch_model.safetensors"],
    },
}
if MODEL not in MODELS:
    sys.exit(f"Unknown MODEL '{MODEL}' (known: {', '.join(MODELS)})")
spec = MODELS[MODEL]

# ---------- Fail fast if this Kaggle job has no internet ----------
try:
    socket.create_connection(("huggingface.co", 443), timeout=10).close()
except OSError as e:
    sys.exit(f"NO INTERNET in this Kaggle job ({e}).\n"
             "Fix: 1) kaggle.com -> Settings -> Phone verification (required for internet).\n"
             "     2) Open this notebook on kaggle.com -> Edit -> Settings panel ->\n"
             "        turn Internet ON -> Save Version. Then re-run --setup-model.")
print(f"Internet OK - caching model '{MODEL}'")

# huggingface_hub ships with Kaggle's image; only install if it's somehow missing
try:
    import huggingface_hub  # noqa: F401
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "huggingface_hub"], check=True)
from huggingface_hub import snapshot_download

ROOT = os.path.join("/kaggle/working/models", spec["out"])

t0 = time.time()
files = {}
for sub, (repo, patterns) in spec["repos"].items():
    dest = os.path.join(ROOT, sub)
    print(f"Downloading {repo} -> {dest}", flush=True)
    snapshot_download(repo_id=repo, local_dir=dest, allow_patterns=patterns)
    for root, _, fnames in os.walk(dest):
        if "/.cache" in root:          # huggingface_hub's bookkeeping, not model files
            continue
        for f in fnames:
            p = os.path.join(root, f)
            files[os.path.relpath(p, ROOT)] = os.path.getsize(p)

# Sanity check: configs must exist; weight files must be real (>10 MB)
missing = [r for r in spec["required"]
           if files.get(r, 0) < (10_000_000 if r.endswith(".safetensors") else 1)]
if missing:
    sys.exit(f"Download incomplete, missing: {missing}")

# Remove huggingface_hub's .cache folders so they don't end up in the output
subprocess.run(["bash", "-c", f"find '{ROOT}' -type d -name .cache -prune -exec rm -rf {{}} +"])

total = sum(files.values())
for f, size in sorted(files.items()):
    print(f"  {size / 1e9:6.2f} GB  {f}")
print(f"Total {total / 1e9:.2f} GB in {(time.time() - t0) / 60:.1f} min")

json.dump({"model": MODEL, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
           "repos": {k or ".": v[0] for k, v in spec["repos"].items()},
           "total_bytes": total, "files": files},
          open(os.path.join(ROOT, "CACHE_OK.json"), "w"), indent=2)
print("Cache ready: CACHE_OK.json written")
