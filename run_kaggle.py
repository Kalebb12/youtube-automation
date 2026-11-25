"""
Runs ON YOUR LAPTOP. Uploads stories in ./scenes to Kaggle, starts the GPU job,
waits for it to finish, and downloads the images into ./images.

Usage:
    python run_kaggle.py --setup-model   # ONE TIME: cache the SDXL model on Kaggle (~7 GB)
    python run_kaggle.py                 # upload scenes, run, download
    python run_kaggle.py --download      # just download the latest finished run
"""
import argparse, json, os, re, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
SCENES_DIR = os.path.join(HERE, "scenes")
KERNEL_DIR = os.path.join(HERE, "kaggle_sd")
IMAGES_DIR = os.path.join(HERE, "images")
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


def write_metadata(user):
    json.dump({"title": DATASET_SLUG, "id": f"{user}/{DATASET_SLUG}",
               "licenses": [{"name": "CC0-1.0"}]},
              open(os.path.join(SCENES_DIR, "dataset-metadata.json"), "w"), indent=2)
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
        sh(KAGGLE, "datasets", "version", "-p", SCENES_DIR,
           "-m", time.strftime("scenes %Y-%m-%d %H:%M"), "--dir-mode", "zip")
    else:
        sh(KAGGLE, "datasets", "create", "-p", SCENES_DIR, "--dir-mode", "zip")
    print("Waiting for Kaggle to process the dataset...")
    for _ in range(40):
        out = sh(KAGGLE, "datasets", "status", ref, check=False).lower()
        if "ready" in out:
            return
        time.sleep(15)
    sys.exit("Dataset never became ready - check kaggle.com/datasets")


def wait_for_kernel(ref):
    start = time.time()
    while True:
        out = subprocess.run([KAGGLE, "kernels", "status", ref],
                             capture_output=True, text=True).stdout.lower()
        m = re.search(r'status "?([a-z_.]+)', out)
        status = m.group(1) if m else out.strip()
        print(f"  [{(time.time() - start) / 60:4.1f} min] {status}")
        if "complete" in status:
            return
        if any(w in status for w in ("error", "cancel")):
            sys.exit(f"Run failed - open kaggle.com/code/{ref} and check the log")
        time.sleep(30)


def download(user):
    os.makedirs(IMAGES_DIR, exist_ok=True)
    sh(KAGGLE, "kernels", "output", f"{user}/{KERNEL_SLUG}", "-p", IMAGES_DIR, "--force")
    print(f"\nImages saved in {IMAGES_DIR}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true", help="only download the last run")
    ap.add_argument("--setup-model", action="store_true",
                    help="one-time: download the SDXL model on Kaggle and save it as a cache")
    args = ap.parse_args()

    user = username()
    write_metadata(user)
    if args.setup_model:
        setup_model(user)
        sys.exit(0)
    if not args.download:
        require_cache(user)
        stories = [f for f in os.listdir(SCENES_DIR)
                   if f.endswith(".json") and f != "dataset-metadata.json"]
        print(f"Stories to generate: {stories}")
        upload_scenes(user)
        sh(KAGGLE, "kernels", "push", "-p", KERNEL_DIR)
        print("GPU job started (model loads from the cache, ~1-2 min).")
        time.sleep(20)
        wait_for_kernel(f"{user}/{KERNEL_SLUG}")
    download(user)
