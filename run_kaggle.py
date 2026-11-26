"""
Runs ON YOUR LAPTOP. Uploads APPROVED stories in ./scenes (plus the channel profiles
in ./channels) to Kaggle, starts the GPU job, waits for it, and downloads the images.

Usage:
    python run_kaggle.py --setup-model   # ONE TIME per model: cache the image models the channels use
    python run_kaggle.py --setup-model flux2-klein    # (or a specific one: sdxl | flux2-klein)
    python run_kaggle.py                 # all approved stories: upload, run, download
    python run_kaggle.py joseph_part1    # only these (must be approved)
    python run_kaggle.py --download      # just download the latest finished run

Stories with "status": "draft" are skipped - review them and set "status": "approved".
"""
import argparse, glob, json, os, re, shutil, subprocess, sys, time
from common import CHANNELS_DIR, IMAGE_MODELS, image_model, image_problems, load_channel, validate_story

HERE = os.path.dirname(os.path.abspath(__file__))
SCENES_DIR = os.path.join(HERE, "scenes")
KERNEL_DIR = os.path.join(HERE, "kaggle_sd")
IMAGES_DIR = os.path.join(HERE, "images")
UPLOAD_DIR = os.path.join(HERE, ".kaggle_upload")   # rebuilt each run; what the dataset contains
DATASET_SLUG = "bible-scenes"
KERNEL_SLUG = "bible-scene-generator"
CACHE_SRC = os.path.join(HERE, "kaggle_cache", "cache_model.py")
CACHE_BUILD = os.path.join(HERE, ".kaggle_cache")   # one generated notebook folder per model
CACHE_SLUGS = {"sdxl": "sdxl-model-cache", "flux2-klein": "flux2-klein-cache"}
CACHE_SIZES = {"sdxl": "~7 GB, ~10 min", "flux2-klein": "~16 GB, ~15-25 min"}
CACHE_MARKER = "CACHE_OK.json"


def find_kaggle():
    """Locate the Kaggle CLI even when the project's .venv isn't activated."""
    exe = "kaggle.exe" if os.name == "nt" else "kaggle"
    for c in (os.path.join(os.path.dirname(sys.executable), exe),          # same env as this Python
              os.path.join(HERE, ".venv", "Scripts" if os.name == "nt" else "bin", exe),
              shutil.which("kaggle")):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    sys.exit("Kaggle CLI not found. Either activate the venv (source .venv/bin/activate) "
             "or install it: pip install kaggle")


KAGGLE = find_kaggle()


def sh(*args, check=True):
    print("$", " ".join(args))
    r = subprocess.run(args, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    if out:
        print(out)
    if check and r.returncode != 0:
        sys.exit(f"Command failed: {' '.join(args)}")
    return out


def username():
    if os.environ.get("KAGGLE_USERNAME"):
        return os.environ["KAGGLE_USERNAME"]
    cfg = os.path.expanduser("~/.kaggle/kaggle.json")
    if os.path.exists(cfg):
        return json.load(open(cfg))["username"]
    sys.exit("Kaggle username not found: put kaggle.json in ~/.kaggle/ (see README)")


def stage_upload(names):
    """Copy approved (and valid) stories + all channel profiles into UPLOAD_DIR.
    Profiles are flattened to channel__<name>.json because the dataset upload can't
    keep sub-folders as folders. Returns the story ids staged."""
    approved, skipped, bad = [], [], []
    for path in sorted(glob.glob(os.path.join(SCENES_DIR, "*.json"))):
        sid = os.path.splitext(os.path.basename(path))[0]
        if sid == "dataset-metadata" or (names and sid not in names):
            continue
        story = json.load(open(path))
        if story.get("status") != "approved":
            skipped.append(sid)
            continue
        errs = validate_story(story)
        if errs:
            bad.append((sid, errs))
        else:
            approved.append((sid, path))
            # Not blocking (it's approved), but worth knowing before spending GPU time
            for w in image_problems(story, load_channel(story["channel"])):
                print(f"  WARNING {sid}: {w}")
    if skipped:
        print(f"Skipping drafts (not approved): {', '.join(skipped)}")
    if bad:
        sys.exit("These approved stories are invalid - fix them first:\n" + "\n".join(
            f"  {sid}: " + "; ".join(e) for sid, e in bad))
    missing = sorted(set(names or []) - {sid for sid, _ in approved} - set(skipped))
    if missing:
        sys.exit(f"No such story in scenes/: {', '.join(missing)}")
    if not approved:
        sys.exit("No approved stories to generate - nothing sent to the GPU.\n"
                 "Review a draft in scenes/, set \"status\": \"approved\", and run again.")
    shutil.rmtree(UPLOAD_DIR, ignore_errors=True)
    os.makedirs(UPLOAD_DIR)
    for sid, path in approved:
        shutil.copy(path, os.path.join(UPLOAD_DIR, f"{sid}.json"))
    for path in glob.glob(os.path.join(CHANNELS_DIR, "*.json")):
        shutil.copy(path, os.path.join(UPLOAD_DIR, "channel__" + os.path.basename(path)))
    return [sid for sid, _ in approved]


def models_for(story_ids):
    """Image models needed by these stories (story override, else channel profile)."""
    out = []
    for sid in story_ids:
        story = json.load(open(os.path.join(SCENES_DIR, f"{sid}.json")))
        m = image_model(story, load_channel(story["channel"]))
        if m not in out:
            out.append(m)
    return out


def write_metadata(user, models=()):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    json.dump({"title": DATASET_SLUG, "id": f"{user}/{DATASET_SLUG}",
               "licenses": [{"name": "CC0-1.0"}]},
              open(os.path.join(UPLOAD_DIR, "dataset-metadata.json"), "w"), indent=2)
    json.dump({"id": f"{user}/{KERNEL_SLUG}", "title": KERNEL_SLUG,
               "code_file": "generate.py", "language": "python", "kernel_type": "script",
               "is_private": True, "enable_gpu": True, "enable_internet": True,
               "dataset_sources": [f"{user}/{DATASET_SLUG}"], "competition_sources": [],
               "kernel_sources": [f"{user}/{CACHE_SLUGS[m]}" for m in models]},
              open(os.path.join(KERNEL_DIR, "kernel-metadata.json"), "w"), indent=2)


def write_cache_notebook(user, model):
    """A folder with cache_model.py (MODEL line set) + metadata. CPU only: it just downloads."""
    d = os.path.join(CACHE_BUILD, model)
    os.makedirs(d, exist_ok=True)
    src = open(CACHE_SRC).read()
    src, n = re.subn(r'^MODEL = ".*?"', f'MODEL = "{model}"', src, count=1, flags=re.M)
    assert n == 1, "cache_model.py lost its MODEL line"
    open(os.path.join(d, "cache_model.py"), "w").write(src)
    json.dump({"id": f"{user}/{CACHE_SLUGS[model]}", "title": CACHE_SLUGS[model],
               "code_file": "cache_model.py", "language": "python", "kernel_type": "script",
               "is_private": True, "enable_gpu": False, "enable_internet": True,
               "dataset_sources": [], "competition_sources": [], "kernel_sources": []},
              open(os.path.join(d, "kernel-metadata.json"), "w"), indent=2)
    return d


def cache_ready(user, model):
    """True if that model's cache job finished and its output contains CACHE_OK.json."""
    r = subprocess.run([KAGGLE, "kernels", "files", f"{user}/{CACHE_SLUGS[model]}",
                        "--page-size", "200", "-v"], capture_output=True, text=True)
    return r.returncode == 0 and CACHE_MARKER in r.stdout


def require_cache(user, models):
    missing = [m for m in models if not cache_ready(user, m)]
    if not missing:
        print(f"Model cache found: {', '.join(models)}")
        return
    lines = []
    for m in missing:
        status = subprocess.run([KAGGLE, "kernels", "status", f"{user}/{CACHE_SLUGS[m]}"],
                                capture_output=True, text=True).stdout.strip()
        lines.append(f"  {m}: {status or 'not created yet'}  ->  python run_kaggle.py --setup-model {m}")
    sys.exit("Model cache not found, so the GPU job was NOT started.\n" + "\n".join(lines) +
             "\n  (If a cache job is still running, wait for it to finish and try again.)")


def setup_model(user, model):
    if cache_ready(user, model):
        print(f"{model}: cache already exists - nothing to do.")
        return
    d = write_cache_notebook(user, model)
    sh(KAGGLE, "kernels", "push", "-p", d)
    print(f"{model}: cache job started (CPU only, {CACHE_SIZES[model]}).")
    time.sleep(20)
    wait_for_kernel(f"{user}/{CACHE_SLUGS[model]}")
    print("Waiting for Kaggle to publish the output...")
    for _ in range(20):
        if cache_ready(user, model):
            print(f"{model}: cache ready.")
            return
        time.sleep(15)
    sys.exit(f"Job finished but {CACHE_MARKER} isn't in its output - "
             f"check the log at kaggle.com/code/{user}/{CACHE_SLUGS[model]}")


def upload_scenes(user):
    ref = f"{user}/{DATASET_SLUG}"
    exists = subprocess.run([KAGGLE, "datasets", "status", ref],
                            capture_output=True, text=True).returncode == 0
    if exists:
        sh(KAGGLE, "datasets", "version", "-p", UPLOAD_DIR,
           "-m", time.strftime("scenes %Y-%m-%d %H:%M"))
    else:
        sh(KAGGLE, "datasets", "create", "-p", UPLOAD_DIR)
    print("Waiting for Kaggle to process the dataset...")
    for _ in range(40):
        out = sh(KAGGLE, "datasets", "status", ref, check=False).lower()
        if "ready" in out:
            return
        time.sleep(15)
    sys.exit("Dataset never became ready - check kaggle.com/datasets")


def wait_for_kernel(ref):
    start, warned = time.time(), False
    while True:
        out = subprocess.run([KAGGLE, "kernels", "status", ref],
                             capture_output=True, text=True).stdout.lower()
        m = re.search(r'status "?([a-z_.]+)', out)
        status = m.group(1) if m else out.strip()
        mins = (time.time() - start) / 60
        print(f"  [{mins:4.1f} min] {status}", flush=True)
        if "complete" in status:
            return
        if "queued" in status and mins > 10 and not warned:
            warned = True
            print("  NOTE: still waiting for a free GPU. Either Kaggle's T4s are busy (try off-peak)\n"
                  "  or this week's GPU quota is used up (kaggle.com -> Settings -> Accelerator usage).\n"
                  "  Ctrl+C is safe; the job keeps its place - run --download later.", flush=True)
        if "cancel" in status:
            sys.exit(f"Job was cancelled (on kaggle.com, or Kaggle dropped it from the queue). "
                     f"Nothing was generated - just run again.")
        if "error" in status:
            sys.exit(f"Run failed - open kaggle.com/code/{ref} and check the log")
        time.sleep(30)


def download(user):
    os.makedirs(IMAGES_DIR, exist_ok=True)
    sh(KAGGLE, "kernels", "output", f"{user}/{KERNEL_SLUG}", "-p", IMAGES_DIR, "--force")
    print(f"\nImages saved in {IMAGES_DIR}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stories", nargs="*", help="only these approved stories (default: all approved)")
    ap.add_argument("--download", action="store_true", help="only download the last run")
    ap.add_argument("--setup-model", nargs="?", const="__channels__", choices=list(IMAGE_MODELS) + ["__channels__"],
                    metavar="MODEL", help="one-time: cache an image model on Kaggle "
                    f"({' | '.join(IMAGE_MODELS)}; default: every model the channels use)")
    args = ap.parse_args()

    user = username()
    if args.setup_model:
        wanted = [args.setup_model] if args.setup_model != "__channels__" else sorted(
            {load_channel(os.path.splitext(os.path.basename(p))[0]).get("image_model", "sdxl")
             for p in glob.glob(os.path.join(CHANNELS_DIR, "*.json"))})
        for m in wanted:
            setup_model(user, m)
        print("Now run:  python run_kaggle.py")
        sys.exit(0)
    if not args.download:
        stories = stage_upload(args.stories)      # refuses before touching Kaggle if none approved
        models = models_for(stories)
        write_metadata(user, models)
        print(f"Stories to generate: {', '.join(stories)}  (models: {', '.join(models)})")
        require_cache(user, models)
        upload_scenes(user)
        sh(KAGGLE, "kernels", "push", "-p", KERNEL_DIR)
        print("GPU job started (model loads from the cache, ~1-2 min).")
        time.sleep(20)
        wait_for_kernel(f"{user}/{KERNEL_SLUG}")
    download(user)
