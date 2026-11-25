"""
Runs ON KAGGLE (GPU). Reads every story JSON from the attached scenes dataset,
generates one image per scene with Stable Diffusion XL, and saves them to
/kaggle/working/<story>/scene_XX.png so the laptop can download them.

The model comes from the attached output of the one-time cache job
(kaggle_cache/cache_model.py). Hugging Face is switched to offline mode so nothing
is downloaded by accident; if the cache is missing it falls back to downloading.
"""
import importlib.util, subprocess, sys
# Kaggle's image already has these; only pip-install what's missing (needs internet)
_missing = [m for m in ("diffusers", "transformers", "accelerate", "safetensors")
            if importlib.util.find_spec(m) is None]
if _missing:
    print("Installing missing packages:", _missing)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--retries", "1",
                    "--timeout", "15", *_missing], check=True)

import glob, json, os, time

# ---------- Find the cached model (must happen before importing diffusers) ----------
HUB_BASE, HUB_VAE = "stabilityai/stable-diffusion-xl-base-1.0", "madebyollin/sdxl-vae-fp16-fix"
markers = glob.glob("/kaggle/input/**/models/CACHE_OK.json", recursive=True)
if markers:
    MODEL_ROOT = os.path.dirname(markers[0])
    BASE_SRC, VAE_SRC = os.path.join(MODEL_ROOT, "sdxl-base"), os.path.join(MODEL_ROOT, "sdxl-vae")
    os.environ["HF_HUB_OFFLINE"] = "1"          # never touch the network for models
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    print(f"Using cached model from {MODEL_ROOT} (Hugging Face offline mode)")
else:
    BASE_SRC, VAE_SRC = HUB_BASE, HUB_VAE
    print("=" * 70)
    print("WARNING: cached model not found under /kaggle/input.")
    print("Falling back to downloading ~7 GB from Hugging Face (slow, uses GPU time).")
    print("Fix: run  python run_kaggle.py --setup-model  on your laptop.")
    print("=" * 70)

import torch
from diffusers import StableDiffusionXLPipeline, AutoencoderKL, DPMSolverMultistepScheduler

# ---------- Channel style (keep fixed so every video looks like one series) ----------
STYLE = ("biblical illustration, painterly digital art, warm cinematic lighting, "
         "ancient Middle East setting, rich earthy colours, detailed, soft volumetric light")
NEGATIVE = ("text, watermark, logo, signature, modern clothing, modern buildings, "
            "blurry, low quality, deformed hands, extra fingers, extra limbs, distorted face")
SIZES = {"short": (768, 1344), "long": (1344, 768)}   # vertical Shorts / horizontal long-form
STEPS, GUIDANCE, BASE_SEED = 30, 6.5, 1234
OUT_ROOT = "/kaggle/working"

assert torch.cuda.is_available(), "No GPU - check enable_gpu in kernel-metadata.json"

# ---------- Find story files from the attached dataset ----------
def is_story(path):
    """Only JSONs with a 'scenes' list - skips model configs from the cache, metadata, etc."""
    if path.endswith("dataset-metadata.json") or "/models/" in path:
        return False
    try:
        return isinstance(json.load(open(path)).get("scenes"), list)
    except Exception:
        return False


story_files = [f for f in sorted(glob.glob("/kaggle/input/**/*.json", recursive=True)) if is_story(f)]
print(f"Found {len(story_files)} story file(s):", *story_files, sep="\n  ")
if not story_files:
    sys.exit("No story JSON files found under /kaggle/input")

# ---------- Load model once ----------
t0 = time.time()
if os.path.isdir(BASE_SRC):
    # /kaggle/input is fetched lazily, and loading via mmap reads it in small random
    # chunks (slow). Reading each file once, front to back, pulls it into RAM so the
    # real load below is fast.
    n = 0
    for f in glob.glob(os.path.join(os.path.dirname(BASE_SRC), "**/*.safetensors"), recursive=True):
        with open(f, "rb") as fh:
            while chunk := fh.read(64 << 20):
                n += len(chunk)
    print(f"Pre-read {n / 1e9:.1f} GB of weights in {time.time() - t0:.0f}s", flush=True)

t1 = time.time()
vae = AutoencoderKL.from_pretrained(VAE_SRC, torch_dtype=torch.float16)
pipe = StableDiffusionXLPipeline.from_pretrained(
    BASE_SRC,
    vae=vae, torch_dtype=torch.float16, variant="fp16", use_safetensors=True,
)
print(f"Pipeline built in {time.time() - t1:.0f}s", flush=True)
t1 = time.time()
pipe.to("cuda")
print(f"Moved to GPU in {time.time() - t1:.0f}s", flush=True)
pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config, use_karras_sigmas=True)
# pipe.enable_vae_slicing() was removed in newer diffusers; it only saves memory for
# batches >1 (we make one image at a time), so skip it if the VAE method is gone too
if hasattr(pipe.vae, "enable_slicing"):
    pipe.vae.enable_slicing()
pipe.set_progress_bar_config(disable=True)
print(f"Model loaded in {time.time() - t0:.0f}s", flush=True)


def fill(text, characters):
    """Replace {JOSEPH}-style placeholders with the fixed character description."""
    for name, desc in characters.items():
        text = text.replace("{" + name + "}", desc)
    return text


manifest = {}
for path in story_files:
    story = json.load(open(path))
    story_id = story.get("story") or os.path.splitext(os.path.basename(path))[0]
    chars = story.get("characters", {})
    style = story.get("style", STYLE)
    width, height = SIZES[story.get("format", "short")]
    out_dir = os.path.join(OUT_ROOT, story_id)
    os.makedirs(out_dir, exist_ok=True)

    print(f"\n=== {story_id}: {len(story['scenes'])} scenes ({width}x{height}) ===")
    done = []
    for s in story["scenes"]:
        seed = s.get("seed", BASE_SEED + s["id"])
        prompt = f"{fill(s['image_prompt'], chars)}, {style}"
        img = pipe(prompt=prompt, negative_prompt=story.get("negative", NEGATIVE),
                   width=width, height=height, num_inference_steps=STEPS,
                   guidance_scale=GUIDANCE,
                   generator=torch.Generator("cuda").manual_seed(seed)).images[0]
        name = f"scene_{s['id']:02d}.png"
        img.save(os.path.join(out_dir, name))
        done.append({"id": s["id"], "file": name, "seed": seed, "prompt": prompt})
        print(f"  saved {name} (seed {seed})", flush=True)

    # Copy the story file next to its images so the laptop has narration + images together
    json.dump(story, open(os.path.join(out_dir, "story.json"), "w"), indent=2)
    manifest[story_id] = done

json.dump(manifest, open(os.path.join(OUT_ROOT, "manifest.json"), "w"), indent=2)
print(f"\nAll done in {(time.time() - t0) / 60:.1f} min")
