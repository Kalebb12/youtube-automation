"""
Runs ON YOUR LAPTOP. Uploads APPROVED stories in ./scenes (plus the channel profiles
in ./channels) to Kaggle, starts the GPU job, waits for it, and downloads the images.

Usage:
    python run_kaggle.py --setup-model   # ONE TIME: cache the SDXL model on Kaggle (~7 GB)
    python run_kaggle.py                 # all approved stories: upload, run, download
    python run_kaggle.py joseph_part1    # only these (must be approved)
    python run_kaggle.py --download      # just download the latest finished run

Stories with "status": "draft" are skipped - review them and set "status": "approved".
"""
import argparse, glob, json, os, re, shutil, subprocess, sys, time
from common import CHANNELS_DIR, validate_story

HERE = os.path.dirname(os.path.abspath(__file__))
SCENES_DIR = os.path.join(HERE, "scenes")
KERNEL_DIR = os.path.join(HERE, "kaggle_sd")
IMAGES_DIR = os.path.join(HERE, "images")
UPLOAD_DIR = os.path.join(HERE, ".kaggle_upload")   # rebuilt each run; what the dataset contains
DATASET_SLUG = "bible-scenes"
KERNEL_SLUG = "bible-scene-generator"
CACHE_DIR = os.path.join(HERE, "kaggle_cache")
CACHE_SLUG = "sdxl-model-cache"
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


def write_metadata(user):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    json.dump({"title": DATASET_SLUG, "id": f"{user}/{DATASET_SLUG}",
               "licenses": [{"name": "CC0-1.0"}]},
              open(os.path.join(UPLOAD_DIR, "dataset-metadata.json"), "w"), indent=2)
    json.dump({"id": f"{user}/{KERNEL_SLUG}", "title": KERNEL_SLUG,
               "code_file": "generate.py", "language": "python", "kernel_type": "script",
               "is_private": True, "enable_gpu": True, "enable_internet": True,
               "dataset_sources": [f"{user}/{DATASET_SLUG}"],
               "competition_sources": [], "kernel_sources": [f"{user}/{CACHE_SLUG}"]},
              open(os.path.join(KERNEL_DIR, "kernel-metadata.json"), "w"), indent=2)
    # The cache job only downloads files: internet on, GPU off (saves GPU quota)
    json.dump({"id": f"{user}/{CACHE_SLUG}", "title": CACHE_SLUG,
               "code_file": "cache_model.py", "language": "python", "kernel_type": "script",
               "is_private": True, "enable_gpu": False, "enable_internet": True,
               "dataset_sources": [], "competition_sources": [], "kernel_sources": []},
              open(os.path.join(CACHE_DIR, "kernel-metadata.json"), "w"), indent=2)


def cache_ready(user):
    """True if the cache job finished and its output contains the CACHE_OK.json marker."""
    r = subprocess.run([KAGGLE, "kernels", "files", f"{user}/{CACHE_SLUG}",
                        "--page-size", "200", "-v"], capture_output=True, text=True)
    return r.returncode == 0 and CACHE_MARKER in r.stdout


def require_cache(user):
    if cache_ready(user):
        print("Model cache found.")
        return
    ref = f"{user}/{CACHE_SLUG}"
    status = subprocess.run([KAGGLE, "kernels", "status", ref],
                            capture_output=True, text=True).stdout.strip()
    sys.exit("Model cache not found, so the GPU job was NOT started.\n"
             f"  Cache job status: {status or 'not created yet'}\n"
             "  Run once:  python run_kaggle.py --setup-model\n"
             "  (If it's still running, wait for it to finish and try again.)")


def setup_model(user):
    if cache_ready(user):
        print("Model cache already exists - nothing to do. (Re-running would just rebuild it.)")
        return
    sh(KAGGLE, "kernels", "push", "-p", CACHE_DIR)
    print("Cache job started (CPU only, ~10-20 min to download ~7 GB).")
    time.sleep(20)
    wait_for_kernel(f"{user}/{CACHE_SLUG}")
    print("Waiting for Kaggle to publish the output...")
    for _ in range(20):
        if cache_ready(user):
            print("Model cache ready. Now run:  python run_kaggle.py")
            return
        time.sleep(15)
    sys.exit(f"Job finished but {CACHE_MARKER} isn't in its output - "
             f"check the log at kaggle.com/code/{user}/{CACHE_SLUG}")


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
    ap.add_argument("--setup-model", action="store_true",
                    help="one-time: download the SDXL model on Kaggle and save it as a cache")
    args = ap.parse_args()

    user = username()
    if args.setup_model:
        write_metadata(user)
        setup_model(user)
        sys.exit(0)
    if not args.download:
        stories = stage_upload(args.stories)      # refuses before touching Kaggle if none approved
        write_metadata(user)
        print(f"Stories to generate: {', '.join(stories)}")
        require_cache(user)
        upload_scenes(user)
        sh(KAGGLE, "kernels", "push", "-p", KERNEL_DIR)
        print("GPU job started (model loads from the cache, ~1-2 min).")
        time.sleep(20)
        wait_for_kernel(f"{user}/{KERNEL_SLUG}")
    download(user)
