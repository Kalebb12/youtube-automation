"""
Runs ON KAGGLE, ONCE (no GPU needed). Downloads only the SDXL files that
kaggle_sd/generate.py actually loads and saves them as this job's output.
The image job attaches that output, so it never has to download the model again.

What gets saved (~7 GB) under /kaggle/working/models/:
  sdxl-base/        stabilityai/stable-diffusion-xl-base-1.0, fp16 weights only
                    (UNet, both text encoders, tokenizers, scheduler, configs)
  sdxl-vae/         madebyollin/sdxl-vae-fp16-fix (the VAE generate.py uses)
  CACHE_OK.json     marker + file list; written last, only if everything downloaded

Skipped on purpose (duplicates / unused): the single-file sd_xl_base_1.0.safetensors,
the fp32 copies, .bin files, ONNX/OpenVINO/Flax exports, and SDXL's own VAE
(we replace it with the fp16-fix VAE).

Re-run it (python run_kaggle.py --setup-model) only if you change models.
"""
import json, os, socket, subprocess, sys, time

# ---------- Fail fast if this Kaggle job has no internet ----------
try:
    socket.create_connection(("huggingface.co", 443), timeout=10).close()
except OSError as e:
    sys.exit(f"NO INTERNET in this Kaggle job ({e}).\n"
             "Fix: 1) kaggle.com -> Settings -> Phone verification (required for internet).\n"
             "     2) Open kaggle.com/code/<you>/sdxl-model-cache -> Edit -> Settings panel ->\n"
             "        turn Internet ON -> Save Version. Then re-run --setup-model.")
print("Internet OK")

# huggingface_hub ships with Kaggle's image; only install if it's somehow missing
try:
    import huggingface_hub  # noqa: F401
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "huggingface_hub"], check=True)
from huggingface_hub import snapshot_download

OUT = "/kaggle/working/models"

REPOS = {
    "sdxl-base": ("stabilityai/stable-diffusion-xl-base-1.0", [
        "model_index.json",
        "scheduler/scheduler_config.json",
        "tokenizer/*", "tokenizer_2/*",
        "text_encoder/config.json",   "text_encoder/model.fp16.safetensors",
        "text_encoder_2/config.json", "text_encoder_2/model.fp16.safetensors",
        "unet/config.json",           "unet/diffusion_pytorch_model.fp16.safetensors",
    ]),
    "sdxl-vae": ("madebyollin/sdxl-vae-fp16-fix", [
        "config.json", "diffusion_pytorch_model.safetensors",
    ]),
}

t0 = time.time()
files = {}
for name, (repo, patterns) in REPOS.items():
    dest = os.path.join(OUT, name)
    print(f"Downloading {repo} -> {dest}")
    snapshot_download(repo_id=repo, local_dir=dest, allow_patterns=patterns)
    for root, _, fnames in os.walk(dest):
        if "/.cache" in root:          # huggingface_hub's bookkeeping, not model files
            continue
        for f in fnames:
            p = os.path.join(root, f)
            files[os.path.relpath(p, OUT)] = os.path.getsize(p)

# Sanity check: every weight file we rely on must be present and non-trivial
required = [
    "sdxl-base/model_index.json",
    "sdxl-base/unet/diffusion_pytorch_model.fp16.safetensors",
    "sdxl-base/text_encoder/model.fp16.safetensors",
    "sdxl-base/text_encoder_2/model.fp16.safetensors",
    "sdxl-vae/diffusion_pytorch_model.safetensors",
]
# configs just need to exist and be non-empty; weight files must be real (>10 MB)
missing = [r for r in required
           if files.get(r, 0) < (10_000_000 if r.endswith(".safetensors") else 1)]
if missing:
    sys.exit(f"Download incomplete, missing: {missing}")

# Remove huggingface_hub's .cache folders so they don't end up in the output
for name in REPOS:
    subprocess.run(["rm", "-rf", os.path.join(OUT, name, ".cache")])

total = sum(files.values())
for f, size in sorted(files.items()):
    print(f"  {size / 1e9:6.2f} GB  {f}")
print(f"Total {total / 1e9:.2f} GB in {(time.time() - t0) / 60:.1f} min")

json.dump({"created": time.strftime("%Y-%m-%d %H:%M:%S"),
           "repos": {k: v[0] for k, v in REPOS.items()},
           "total_bytes": total, "files": files},
          open(os.path.join(OUT, "CACHE_OK.json"), "w"), indent=2)
print("Cache ready: CACHE_OK.json written")
