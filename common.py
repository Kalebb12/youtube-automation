"""Shared helpers for the laptop stages (voice.py, assemble.py)."""
import glob, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCENES_DIR = os.path.join(HERE, "scenes")
IMAGES_DIR = os.path.join(HERE, "images")


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"Command failed:\n{' '.join(cmd)}\n\n{r.stderr[-3000:]}")
    return r.stdout


def duration(path):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "default=nw=1:nk=1", path]).strip())


def story_dirs(names):
    """images/<story>/ folders that have been generated, optionally filtered by name."""
    dirs = sorted(os.path.dirname(p) for p in glob.glob(os.path.join(IMAGES_DIR, "*", "story.json")))
    if names:
        dirs = [d for d in dirs if os.path.basename(d) in names]
    if not dirs:
        sys.exit("No stories found in images/ - run python run_kaggle.py first")
    return dirs


def load_story(story_dir):
    """The story as it is NOW in scenes/ (so narration edits don't need a GPU re-run),
    falling back to the copy saved next to the images. Warns if the images are stale."""
    sid = os.path.basename(story_dir)
    rendered = json.load(open(os.path.join(story_dir, "story.json")))
    src = os.path.join(SCENES_DIR, f"{sid}.json")
    if not os.path.exists(src):
        return rendered
    story = json.load(open(src))
    old = {s["id"]: (filled_prompt(s, rendered), s.get("seed")) for s in rendered["scenes"]}
    stale = [s["id"] for s in story["scenes"]
             if old.get(s["id"]) != (filled_prompt(s, story), s.get("seed"))]
    if stale:
        print(f"  NOTE: image prompts for scenes {stale} in scenes/{sid}.json changed since the "
              f"images were made. Run python run_kaggle.py to refresh them.")
    return story


def filled_prompt(scene, story):
    """The prompt exactly as generate.py sends it (placeholders replaced)."""
    text = scene.get("image_prompt", "")
    for name, desc in story.get("characters", {}).items():
        text = text.replace("{" + name + "}", desc)
    return text
