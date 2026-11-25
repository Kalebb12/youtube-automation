"""Shared helpers for the laptop stages: channel profiles, story loading/validation, ffmpeg."""
import glob, json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCENES_DIR = os.path.join(HERE, "scenes")
IMAGES_DIR = os.path.join(HERE, "images")
CHANNELS_DIR = os.path.join(HERE, "channels")

STATUSES = ("draft", "approved")
FORMATS = ("short", "long")
CHAR_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
STORY_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def load_dotenv(path=os.path.join(HERE, ".env")):
    """Read KEY=VALUE lines from .env into os.environ (never overrides what's already set).
    .env is gitignored - keys live there, never in code."""
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.removeprefix("export ").split("=", 1)
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        os.environ.setdefault(key, val)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"Command failed:\n{' '.join(cmd)}\n\n{r.stderr[-3000:]}")
    return r.stdout


def duration(path):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "default=nw=1:nk=1", path]).strip())


# ---------- Channels ----------
def channel_names():
    return sorted(os.path.splitext(os.path.basename(p))[0]
                  for p in glob.glob(os.path.join(CHANNELS_DIR, "*.json")))


def load_channel(name):
    path = os.path.join(CHANNELS_DIR, f"{name}.json")
    if not os.path.exists(path):
        sys.exit(f"Unknown channel '{name}'. Available: {', '.join(channel_names()) or 'none'} "
                 f"(profiles live in channels/<name>.json)")
    return json.load(open(path))


def story_setting(story, channel, key, default=None):
    """Story-level override wins, then the channel profile, then the default."""
    return story.get(key, channel.get(key, default))


def story_voice(story, channel):
    """(engine, voice id, speed, lang) - story "voice"/"speed" override the profile."""
    v = dict(channel.get("voice", {}))
    if "voice" in story:
        v["id"] = story["voice"]
        v.pop("lang", None)          # profile lang may not fit the overriding voice
    if "speed" in story:
        v["speed"] = story["speed"]
    vid = v.get("id", "bm_george")
    lang = v.get("lang") or ("en-gb" if vid.startswith("b") else "en-us")
    return v.get("engine", "kokoro"), vid, float(v.get("speed", 1.0)), lang


# ---------- Story validation ----------
def _norm(text):
    """Lower-case words only - for comparing quotes regardless of punctuation/spacing."""
    return " ".join(re.findall(r"[a-z0-9']+", text.lower().replace("’", "'")))


def validate_story(story, source_text=None):
    """Return a list of problems (empty = valid). source_text, if given, is the passage
    the story retells: every quote must appear in it word for word."""
    errs = []
    if not isinstance(story, dict):
        return ["story must be a JSON object"]
    for k, typ in (("story", str), ("channel", str), ("title", str), ("status", str),
                   ("characters", dict), ("scenes", list)):
        if not isinstance(story.get(k), typ):
            errs.append(f"'{k}' is missing or not a {typ.__name__}")
    if errs:
        return errs

    if not STORY_ID.match(story["story"]):
        errs.append(f"'story' id '{story['story']}' must be lower-case letters, digits, _ or -")
    if story["status"] not in STATUSES:
        errs.append(f"'status' must be one of {STATUSES}")
    if story["channel"] not in channel_names():
        return errs + [f"channel '{story['channel']}' has no profile in channels/"]
    channel = json.load(open(os.path.join(CHANNELS_DIR, f"{story['channel']}.json")))
    if story_setting(story, channel, "format", "short") not in FORMATS:
        errs.append(f"'format' must be one of {FORMATS}")

    chars = story["characters"]
    for name, desc in chars.items():
        if not CHAR_NAME.match(name):
            errs.append(f"character name '{name}' must be UPPER_SNAKE_CASE")
        if not isinstance(desc, str) or not desc.strip():
            errs.append(f"character '{name}' needs a visual description")

    if not story["scenes"]:
        errs.append("'scenes' is empty")
    ids, quotes = set(), 0
    allowed_tr = [t.upper() for t in channel.get("quote_translations", [])]
    for n, s in enumerate(story["scenes"], 1):
        where = f"scene {s.get('id', f'#{n}')}"
        if not isinstance(s, dict):
            errs.append(f"{where}: must be an object")
            continue
        if not isinstance(s.get("id"), int):
            errs.append(f"{where}: 'id' must be an integer")
        elif s["id"] in ids:
            errs.append(f"{where}: duplicate id")
        else:
            ids.add(s["id"])
        for k in ("narration", "image_prompt"):
            if not isinstance(s.get(k), str) or not s[k].strip():
                errs.append(f"{where}: '{k}' is missing or empty")
        if not isinstance(s.get("refs", []), list):
            errs.append(f"{where}: 'refs' must be a list")
        if "seed" in s and not isinstance(s["seed"], int):
            errs.append(f"{where}: 'seed' must be an integer")
        if isinstance(s.get("narration"), str) and PLACEHOLDER.search(s["narration"]):
            errs.append(f"{where}: narration contains a {{PLACEHOLDER}} - those are for image prompts only")
        for ph in PLACEHOLDER.findall(s.get("image_prompt") or ""):
            if ph not in chars:
                errs.append(f"{where}: image_prompt uses {{{ph}}} but no such character is defined")

        q = s.get("quote")
        if q is None:
            continue
        quotes += 1
        if not (isinstance(q, dict) and all(isinstance(q.get(k), str) and q[k].strip()
                                            for k in ("text", "ref", "translation"))):
            errs.append(f"{where}: 'quote' needs non-empty text, ref and translation")
            continue
        if q["translation"].upper() not in allowed_tr:
            errs.append(f"{where}: quote translation {q['translation']} not allowed on this channel "
                        f"(allowed: {', '.join(allowed_tr) or 'none'})")
        narr = s.get("narration") or ""
        if _norm(q["text"]) not in _norm(narr):
            errs.append(f"{where}: quote text does not appear in the narration")
        elif not re.search(r"[\"“”]", narr):
            errs.append(f"{where}: quoted text must be inside quotation marks in the narration")
        if source_text is not None and _norm(q["text"]) not in _norm(source_text):
            errs.append(f"{where}: quote is not word-for-word from the source passage: \"{q['text']}\"")
    max_q = int(channel.get("max_quotes", 0))
    if quotes > max_q:
        errs.append(f"{quotes} quotes, but channel '{channel['name']}' allows at most {max_q}")
    return errs


# ---------- Stories ----------
def story_dirs(names):
    """images/<story>/ folders that have been generated, optionally filtered by name."""
    dirs = sorted(os.path.dirname(p) for p in glob.glob(os.path.join(IMAGES_DIR, "*", "story.json")))
    if names:
        dirs = [d for d in dirs if os.path.basename(d) in names]
    if not dirs:
        sys.exit("No stories found in images/ - run python run_kaggle.py first")
    return dirs


def load_story(story_dir):
    """(story, channel). The story as it is NOW in scenes/ (so narration edits don't need a
    GPU re-run), falling back to the copy saved next to the images. Warns if images are stale."""
    sid = os.path.basename(story_dir)
    rendered = json.load(open(os.path.join(story_dir, "story.json")))
    src = os.path.join(SCENES_DIR, f"{sid}.json")
    story = json.load(open(src)) if os.path.exists(src) else rendered
    if "channel" not in story:
        sys.exit(f"{sid}: story has no 'channel' - add e.g. \"channel\": \"bible\"")
    channel = load_channel(story["channel"])
    if story.get("status") != "approved":
        print(f"  NOTE: {sid} is still a DRAFT - review it and set \"status\": \"approved\"")
    old = {s["id"]: (filled_prompt(s, rendered), s.get("seed")) for s in rendered["scenes"]}
    stale = [s["id"] for s in story["scenes"]
             if old.get(s["id"]) != (filled_prompt(s, story), s.get("seed"))]
    if stale:
        print(f"  NOTE: image prompts for scenes {stale} in scenes/{sid}.json changed since the "
              f"images were made. Run python run_kaggle.py to refresh them.")
    return story, channel


def filled_prompt(scene, story):
    """The scene part of the prompt exactly as generate.py builds it (placeholders replaced)."""
    text = scene.get("image_prompt", "")
    for name, desc in story.get("characters", {}).items():
        text = text.replace("{" + name + "}", desc)
    return text
