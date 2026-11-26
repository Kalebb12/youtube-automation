"""Shared helpers for the laptop stages: channel profiles, story loading/validation, ffmpeg."""
import glob, json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCENES_DIR = os.path.join(HERE, "scenes")
IMAGES_DIR = os.path.join(HERE, "images")
CHANNELS_DIR = os.path.join(HERE, "channels")

SCENE_LEAD_IN, SCENE_TAIL = 0.35, 0.65   # silence before/after each scene's narration (s)
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
        if "negative" in s and not isinstance(s["negative"], str):
            errs.append(f"{where}: 'negative' must be a string")
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
    if "image_model" in story and story["image_model"] not in IMAGE_MODELS:
        errs.append(f"'image_model' must be one of {IMAGE_MODELS}")
    if channel.get("image_model", "sdxl") not in IMAGE_MODELS:
        errs.append(f"channel '{channel['name']}' has unknown image_model '{channel.get('image_model')}'")
    mo = story.get("motion")
    if mo is not None:
        known = {"parallax": bool, "strength": (int, float), "zoom": (int, float), "transition": str,
                 "transition_s": (int, float), "fade_in": (int, float), "fade_out": (int, float)}
        if not isinstance(mo, dict):
            errs.append("'motion' must be an object")
        else:
            for k, v in mo.items():
                if k not in known:
                    errs.append(f"motion: unknown setting '{k}' (known: {', '.join(known)})")
                elif not isinstance(v, known[k]) or (known[k] is not bool and isinstance(v, bool)):
                    errs.append(f"motion: '{k}' has the wrong type")
    for sc in story["scenes"]:
        if isinstance(sc, dict) and sc.get("move") not in (None, "zoom_in", "zoom_out", "pan_left", "pan_right"):
            errs.append(f"scene {sc.get('id')}: 'move' must be zoom_in, zoom_out, pan_left or pan_right")
    m = story.get("music")
    if m is not None:
        if not isinstance(m, dict):
            errs.append("'music' must be an object")
        else:
            known = {"track": str, "mood": str, "pick": str, "volume_db": (int, float), "start": (int, float),
                     "fade_in": (int, float), "fade_out": (int, float), "duck": bool}
            for k, v in m.items():
                if k not in known:
                    errs.append(f"music: unknown setting '{k}' (known: {', '.join(known)})")
                elif not isinstance(v, known[k]) or (known[k] is not bool and isinstance(v, bool)):
                    errs.append(f"music: '{k}' has the wrong type")
            if m.get("pick") not in (None, "rotate", "first"):
                errs.append("music: 'pick' must be 'rotate' or 'first'")
    max_q = int(channel.get("max_quotes", 0))
    if quotes > max_q:
        errs.append(f"{quotes} quotes, but channel '{channel['name']}' allows at most {max_q}")
    return errs


# ---------- Image models and prompt checks ----------
IMAGE_MODELS = ("sdxl", "flux2-klein")
# Per model: max characters named in one image, max character-description length (CLIP tokens),
# whether the 75-CLIP-token window applies, whether negative prompts work.
MODEL_RULES = {
    "sdxl":        {"max_chars": 1, "char_max_tokens": 20, "clip_window": True,  "negatives": True},
    "flux2-klein": {"max_chars": 2, "char_max_tokens": 40, "clip_window": False, "negatives": False},
}
CHAR_MAX_TOKENS = MODEL_RULES["sdxl"]["char_max_tokens"]
FLUX_MAX_WORDS = 300         # FLUX.2 reads 512 tokens; keep well inside it
NOT_DRAWABLE = ("thinking", "thinks", "remembers", "remembering", "realizes", "realising", "realizing",
                "decides", "plans", "planning", "intends", "hopes", "wonders", "feels", "imagines",
                "symbolizing", "symbolising", "representing", "meanwhile", "later", "years pass")


def full_prompt(scene, story, channel):
    """The positive prompt exactly as generate.py sends it."""
    return f"{filled_prompt(scene, story)}, {story.get('style', channel['style'])}"


def full_negative(scene, story, channel):
    neg = story.get("negative", channel["negative"])
    return f"{neg}, {scene['negative']}" if scene.get("negative") else neg


def image_model(story, channel):
    """The image model for a story: story override, else the channel profile, else sdxl."""
    return story.get("image_model") or channel.get("image_model", "sdxl")


def image_problems(story, channel, new_characters=None):
    """Problems that make images worse, for the story's image model. On SDXL, prompts past 75
    CLIP tokens are silently cut - and the channel style, which goes last, is what gets lost.
    `new_characters`: names whose description length should be enforced (default: all)."""
    import clip_tokens as ct
    model = image_model(story, channel)
    rules = MODEL_RULES.get(model, MODEL_RULES["sdxl"])
    errs = []
    chars = story.get("characters", {})
    for name in (chars if new_characters is None else new_characters):
        if name in chars and ct.count(chars[name]) > rules["char_max_tokens"]:
            errs.append(f"character {name}: description is {ct.count(chars[name])} CLIP tokens, max "
                        f"{rules['char_max_tokens']} - keep only the most distinctive visual tags")
    for s in story.get("scenes", []):
        where = f"scene {s.get('id')}"
        prompt = s.get("image_prompt") or ""
        used = PLACEHOLDER.findall(prompt)
        if len(used) > rules["max_chars"]:
            errs.append(f"{where}: image shows {len(used)} named characters ({', '.join(used)}) - {model} "
                        f"blends faces past {rules['max_chars']}; describe the others generically")
        bad = [w for w in NOT_DRAWABLE if re.search(rf"\b{w}\b", prompt.lower())]
        if bad:
            errs.append(f"{where}: image prompt uses {', '.join(bad)} - a camera can't see that; "
                        f"describe the visible pose/action instead")
        if not rules["clip_window"]:
            words = len(full_prompt(s, story, channel).split())
            if words > FLUX_MAX_WORDS:
                errs.append(f"{where}: prompt + style is {words} words, keep it under {FLUX_MAX_WORDS}")
            continue
        n = ct.count(full_prompt(s, story, channel))
        if n > ct.MAX_TOKENS:
            costs = ", ".join(f"{{{u}}}={ct.count(chars.get(u, ''))}" for u in used)
            errs.append(f"{where}: prompt + style is {n} CLIP tokens, limit {ct.MAX_TOKENS} - cut "
                        f"{n - ct.MAX_TOKENS} tokens from the shot (characters cost {costs or 'nothing'})")
        nn = ct.count(full_negative(s, story, channel))
        if nn > ct.MAX_TOKENS:
            errs.append(f"{where}: negative prompt is {nn} CLIP tokens, limit {ct.MAX_TOKENS} - shorten 'avoid'")
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
