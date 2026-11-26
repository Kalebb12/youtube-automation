"""
Runs ON KAGGLE (GPU). Reads every story JSON from the attached scenes dataset, generates one
image per scene, and saves them to /kaggle/working/<story>/scene_XX.png for the laptop.

Image model per channel ("image_model" in the profile; a story may override):
  sdxl         Stable Diffusion XL, fp16, 30 steps. Prompt window 77 CLIP tokens. Uses negatives.
  flux2-klein  FLUX.2 klein 4B (Apache 2.0), 4 distilled steps, bf16, CPU offload. Prompt window
               512 tokens (Qwen3 text encoder). No negative prompt (distilled, no CFG).
Stories are grouped by model; each model is loaded once.

The look (style / negative / format) comes from the story's channel profile, uploaded alongside
as channel__<name>.json. A story's own "style" / "negative" / "format" win.

Models come from the attached outputs of the one-time cache jobs (kaggle_cache/cache_model.py),
with Hugging Face in offline mode. If a cache is missing it falls back to downloading (slow).
"""
import importlib.util, subprocess, sys
# Kaggle's image already has these; only pip-install what's missing (needs internet)
_missing = [m for m in ("diffusers", "transformers", "accelerate", "safetensors")
            if importlib.util.find_spec(m) is None]
if _missing:
    print("Installing missing packages:", _missing)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--retries", "1",
                    "--timeout", "15", *_missing], check=True)

import gc, glob, json, os, time

OUT_ROOT = "/kaggle/working"
BASE_SEED = 1234
HUB = {"sdxl": ("stabilityai/stable-diffusion-xl-base-1.0", "madebyollin/sdxl-vae-fp16-fix"),
       "flux2-klein": ("black-forest-labs/FLUX.2-klein-4B", None)}
SIZES = {  # (width, height) per format, per model
    "sdxl": {"short": (768, 1344), "long": (1344, 768)},
    "flux2-klein": {"short": (768, 1344), "long": (1344, 768)},
}

# ---------- Stories and channel profiles ----------
def is_story(path):
    """Only JSONs with a 'scenes' list - skips model configs from the caches, metadata, etc."""
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

channels = {}
for f in glob.glob("/kaggle/input/**/channel__*.json", recursive=True):
    c = json.load(open(f))
    channels[c["name"]] = c
print("Channel profiles:", ", ".join(sorted(channels)) or "none")

stories_by_model = {}
for f in story_files:   # check everything BEFORE loading a model, so a typo doesn't waste GPU time
    story = json.load(open(f))
    if story.get("channel") not in channels:
        sys.exit(f"{f}: channel '{story.get('channel')}' has no profile (expected channel__<name>.json)")
    model = story.get("image_model") or channels[story["channel"]].get("image_model", "sdxl")
    if model not in HUB:
        sys.exit(f"{f}: unknown image_model '{model}' (known: {', '.join(HUB)})")
    stories_by_model.setdefault(model, []).append(story)
print("Models needed:", {m: [s["story"] for s in v] for m, v in stories_by_model.items()})

# ---------- Find cached models (before importing diffusers: offline mode is read at import) ----------
caches = {}
for marker in glob.glob("/kaggle/input/**/CACHE_OK.json", recursive=True):
    caches[json.load(open(marker)).get("model", "sdxl")] = os.path.dirname(marker)
missing = [m for m in stories_by_model if m not in caches]
if missing:
    print("=" * 70)
    print(f"WARNING: no cached copy of {missing} under /kaggle/input - downloading from Hugging Face")
    print("(slow, uses GPU time). Fix: python run_kaggle.py --setup-model <model> on your laptop.")
    print("=" * 70)
else:
    os.environ["HF_HUB_OFFLINE"] = "1"          # never touch the network for models
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
for m, root in caches.items():
    print(f"Cached model {m}: {root}")

# FLUX.2 klein needs a recent diffusers; upgrade before importing it if the class is missing
if "flux2-klein" in stories_by_model:
    probe = subprocess.run([sys.executable, "-c", "from diffusers import Flux2KleinPipeline"],
                           capture_output=True)
    if probe.returncode != 0:
        print("Upgrading diffusers/transformers for Flux2KleinPipeline...", flush=True)
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U", "diffusers", "transformers",
                        "accelerate"], check=True)

import torch
assert torch.cuda.is_available(), "No GPU - check enable_gpu in kernel-metadata.json"


def pre_read(root):
    """/kaggle/input is fetched lazily and mmap loading reads it in small random chunks (~8 min
    for SDXL). Reading each weight file once, front to back, pulls it into RAM (~30 s)."""
    t, n = time.time(), 0
    for f in glob.glob(os.path.join(root, "**/*.safetensors"), recursive=True):
        with open(f, "rb") as fh:
            while chunk := fh.read(64 << 20):
                n += len(chunk)
    print(f"Pre-read {n / 1e9:.1f} GB of weights in {time.time() - t:.0f}s", flush=True)


# ---------- Model loaders: each returns render(prompt, negative, width, height, seed) -> PIL image ----------
def load_sdxl(root):
    from diffusers import StableDiffusionXLPipeline, AutoencoderKL, DPMSolverMultistepScheduler
    base, vae_src = (os.path.join(root, "sdxl-base"), os.path.join(root, "sdxl-vae")) if root else HUB["sdxl"]
    vae = AutoencoderKL.from_pretrained(vae_src, torch_dtype=torch.float16)
    pipe = StableDiffusionXLPipeline.from_pretrained(
        base, vae=vae, torch_dtype=torch.float16, variant="fp16", use_safetensors=True).to("cuda")
    pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config, use_karras_sigmas=True)
    if hasattr(pipe.vae, "enable_slicing"):      # pipe.enable_vae_slicing() was removed from diffusers
        pipe.vae.enable_slicing()
    pipe.set_progress_bar_config(disable=True)

    def render(prompt, negative, width, height, seed):
        return pipe(prompt=prompt, negative_prompt=negative, width=width, height=height,
                    num_inference_steps=30, guidance_scale=6.5,
                    generator=torch.Generator("cuda").manual_seed(seed)).images[0]
    return pipe, render


def load_flux2_klein(root):
    from diffusers import Flux2KleinPipeline
    # The T4 has no native bf16 (emulated, slower) but the model is trained in bf16 and fp16 risks
    # overflow; with only 4 steps bf16 is affordable. Falls back to fp16 if bf16 isn't available.
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    print(f"FLUX.2 klein dtype: {dtype}", flush=True)
    pipe = Flux2KleinPipeline.from_pretrained(root or HUB["flux2-klein"][0], torch_dtype=dtype)
    pipe.enable_model_cpu_offload()      # transformer (7.8 GB) + Qwen3 text encoder (8 GB) > one T4
    pipe.set_progress_bar_config(disable=True)

    def render(prompt, negative, width, height, seed):
        # distilled 4-step model: no CFG, so no negative prompt
        return pipe(prompt=prompt, width=width, height=height, guidance_scale=1.0,
                    num_inference_steps=4, generator=torch.Generator("cuda").manual_seed(seed)).images[0]
    return pipe, render


LOADERS = {"sdxl": load_sdxl, "flux2-klein": load_flux2_klein}


def fill(text, characters):
    """Replace {JOSEPH}-style placeholders with the fixed character description."""
    for name, desc in characters.items():
        text = text.replace("{" + name + "}", desc)
    return text


# ---------- Render ----------
t0 = time.time()
manifest = {}
for model, stories in stories_by_model.items():
    t1 = time.time()
    root = caches.get(model)
    if root:
        pre_read(root)
    pipe, render = LOADERS[model](root)
    print(f"Model {model} loaded in {time.time() - t1:.0f}s", flush=True)

    for story in stories:
        story_id = story["story"]
        chars = story.get("characters", {})
        channel = channels[story["channel"]]
        style = story.get("style", channel["style"])          # story overrides win
        negative = story.get("negative", channel.get("negative", ""))
        width, height = SIZES[model][story.get("format", channel.get("format", "short"))]
        out_dir = os.path.join(OUT_ROOT, story_id)
        os.makedirs(out_dir, exist_ok=True)

        print(f"\n=== {story_id} [{story['channel']}, {model}]: {len(story['scenes'])} scenes "
              f"({width}x{height}) ===", flush=True)
        done = []
        for s in story["scenes"]:
            seed = s.get("seed", BASE_SEED + s["id"])
            scene = fill(s["image_prompt"], chars)
            prompt = f"{scene}, {style}" if model == "sdxl" else f"{scene}. {style}."
            scene_negative = f"{negative}, {s['negative']}" if s.get("negative") else negative
            t2 = time.time()
            img = render(prompt, scene_negative, width, height, seed)
            name = f"scene_{s['id']:02d}.png"
            img.save(os.path.join(out_dir, name))
            if max(hi for _, hi in img.getextrema()) < 8:
                print(f"  WARNING {name} is black - numeric overflow? (dtype issue)", flush=True)
            done.append({"id": s["id"], "file": name, "seed": seed, "model": model, "prompt": prompt})
            print(f"  saved {name} (seed {seed}, {time.time() - t2:.0f}s)", flush=True)

        # Copy the story next to its images so the laptop has narration + images together
        json.dump(story, open(os.path.join(out_dir, "story.json"), "w"), indent=2)
        manifest[story_id] = done

    del pipe, render
    gc.collect()
    torch.cuda.empty_cache()

json.dump(manifest, open(os.path.join(OUT_ROOT, "manifest.json"), "w"), indent=2)
print(f"\nAll done in {(time.time() - t0) / 60:.1f} min")
