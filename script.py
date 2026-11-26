"""
Runs ON YOUR LAPTOP. Stage 0: drafts a story (scenes/<id>.json) with an LLM:
Gemini (free tier, default) or Claude (paid, Anthropic API).

Usage:
    python script.py --channel bible  --passage "Genesis 37:1-11" --brief "Joseph's dreams, part 1"
    python script.py --channel horror --brief "a lighthouse keeper hears knocking from below"
    python script.py --channel horror --brief "retell this" --source-file stories/tale.txt
    python script.py --provider claude --channel bible --passage "Genesis 37:12-24" --brief "Joseph part 2"
    python script.py --list-models [--provider claude]

Keys are read from the environment or from .env (gitignored):
  gemini: GEMINI_API_KEY (free: https://aistudio.google.com/apikey)
  claude: ANTHROPIC_API_KEY, or an `ant auth login` profile (no key needed)
Default provider: LLM_PROVIDER from env/.env, else gemini.
The narrator voice, rules, quote policy and target length all come from channels/<channel>.json.
--passage fetches the text (KJV by default) from bible-api.com and the model must retell it
faithfully; any quoted words are checked word for word against that text.

The result is written with "status": "draft". Read it, fix anything, then set
"status": "approved" - only approved stories are sent to the GPU.
Refuses to overwrite an approved story unless you pass --force.
"""
import argparse, json, os, re, sys, time, urllib.parse, urllib.request
import clip_tokens
from common import (FLUX_MAX_WORDS, MODEL_RULES, SCENES_DIR, channel_names, image_model,
                    image_problems, load_channel, load_dotenv, story_setting, validate_story)

load_dotenv()
PROVIDERS = ("gemini", "claude")
DEFAULT_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").lower()
DEFAULT_MODELS = {"gemini": os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"),
                  "claude": os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")}
# Claude models that accept server-side refusal fallbacks (fallbacks="default")
CLAUDE_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5",
                          "claude-fable-5-2", "claude-fable-5-1"}
WORDS_PER_SECOND = 2.6        # measured on Kokoro at speed ~0.95
SECONDS_PER_SCENE = 9         # ~one image every 9 s keeps a Short moving
MAX_ATTEMPTS = 3

# JSON shape Gemini must return (characters as a list: Gemini schemas can't do free-form keys)
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "characters": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "UPPER_SNAKE_CASE"},
                           "description": {"type": "string"}},
            "required": ["name", "description"]}},
        "scenes": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "narration": {"type": "string"},
                "shot": {"type": "object",
                         "properties": {
                             "subject": {"type": "string"},
                             "action": {"type": "string"},
                             "setting": {"type": "string"},
                             "camera": {"type": "string", "enum": None},   # filled from CAMERAS
                             "lighting": {"type": "string"},
                             "avoid": {"type": "array", "items": {"type": "string"}}},
                         "required": ["subject", "action", "setting", "camera", "lighting", "avoid"]},
                "refs": {"type": "array", "items": {"type": "string"}},
                "quote": {"type": "object",
                          "properties": {"text": {"type": "string"}, "ref": {"type": "string"},
                                         "translation": {"type": "string"}},
                          "required": ["text", "ref", "translation"]}},
            "required": ["narration", "shot", "refs"]}},
    },
    "required": ["title", "characters", "scenes"],
}


CAMERAS = ["extreme wide shot", "wide shot", "medium shot", "medium close-up", "close-up",
           "extreme close-up", "low-angle shot", "high-angle shot", "top-down shot",
           "over-the-shoulder shot", "silhouette shot"]
SCHEMA["properties"]["scenes"]["items"]["properties"]["shot"]["properties"]["camera"]["enum"] = CAMERAS


def compose_prompt(shot, model="sdxl"):
    """Build the image prompt in priority order: who, doing what, where, framing, light.
    SDXL gets comma-separated phrases; FLUX.2 (an LLM text encoder) gets sentences.
    The channel style is appended later by generate.py."""
    clean = lambda p: " ".join(str(p or "").split()).strip(" ,.")
    who = f"{clean(shot.get('subject'))} {clean(shot.get('action'))}".strip()
    if model == "sdxl":
        parts = [who, shot.get("setting"), shot.get("camera"), shot.get("lighting")]
        return ", ".join(clean(p) for p in parts if clean(p))
    first = ", ".join(p for p in (who, clean(shot.get("setting"))) if p)
    second = ", ".join(p for p in (clean(shot.get("camera")), clean(shot.get("lighting"))) if p)
    return ". ".join(p[:1].upper() + p[1:] for p in (first, second) if p)


def _strict(schema):
    """Copy of SCHEMA for Claude structured outputs: every object closed (additionalProperties false)."""
    if isinstance(schema, dict):
        out = {k: _strict(v) for k, v in schema.items()}
        if out.get("type") == "object":
            out["additionalProperties"] = False
        return out
    if isinstance(schema, list):
        return [_strict(v) for v in schema]
    return schema


CLAUDE_SCHEMA = _strict(SCHEMA)


def fetch_passage(passage, translation):
    url = (f"https://bible-api.com/{urllib.parse.quote(passage)}"
           f"?translation={urllib.parse.quote(translation)}")
    try:
        data = json.load(urllib.request.urlopen(url, timeout=30))
    except Exception as e:
        sys.exit(f"Couldn't fetch {passage} from bible-api.com: {e}")
    if not data.get("verses"):
        sys.exit(f"bible-api.com returned no verses for '{passage}'")
    lines = [f"{v['book_name']} {v['chapter']}:{v['verse']} {' '.join(v['text'].split())}"
             for v in data["verses"]]
    return data.get("reference", passage), "\n".join(lines)


def build_prompt(channel, brief, source_label, source_text, n_scenes, word_budget, translation,
                 previous=None):
    n = channel["narrator"]
    model = image_model({}, channel)
    rules = MODEL_RULES[model]
    style_tokens = clip_tokens.count(channel["style"])
    limit = clip_tokens.MAX_TOKENS
    scene_budget = limit - style_tokens - 1
    surface_rule = ("" if model == "sdxl" else
                    "\n  - plain surfaces: walls, stones and cloth carry no writing, inscriptions, signs or "
                    "symbols (this model can't be told \"no text\" any other way).")
    if model == "sdxl":
        char_rule = (f"compact comma-separated VISUAL tags, at most {rules['char_max_tokens']} CLIP tokens "
                     f"(about 12 words)")
        subject_rule = ("exactly ONE {NAME} placeholder (a group like {BROTHERS} counts as one), or a "
                        "short generic description (\"a lone traveller\") for minor figures. Never two named "
                        "characters in one image: SDXL blends their faces.")
        avoid_rule = ("up to 3 short things SDXL is likely to get wrong in THIS shot (e.g. \"beard\" for a "
                      "beardless boy, \"crowd\" for a lone figure); [] if none.")
        budget_rule = (f"BUDGET (hard limit - SDXL silently drops everything past it): each finished prompt = "
                       f"subject (with the placeholder replaced by its description) + action + setting + "
                       f"camera + lighting + the channel style ({style_tokens} tokens) must fit {limit} CLIP "
                       f"tokens. That leaves {scene_budget} tokens for everything in the shot INCLUDING the "
                       f"character description - so keep action, setting and lighting terse (a few words "
                       f"each). One CLIP token is roughly 0.75 of a word.")
    else:   # flux2-klein: LLM text encoder, 512-token window, no negative prompt
        char_rule = (f"a short natural-language visual description, at most {rules['char_max_tokens']} CLIP "
                     f"tokens (about 25 words)")
        subject_rule = ("one {NAME} placeholder, or at most two when the moment needs both people "
                        "(e.g. \"{REUBEN} gripping {JOSEPH}'s arm\") - give them clearly different "
                        "positions in the frame. Minor figures get a short generic description.")
        avoid_rule = ("always []. This model has NO negative prompt, so state the look positively in the "
                      "subject/action instead (\"clean-shaven\", \"an empty desert\", \"no one else in sight\").")
        budget_rule = (f"Write each shot as vivid, specific natural description - this model reads full "
                       f"sentences and follows detail well (about 40-80 words per shot is right). The whole "
                       f"prompt, with character descriptions and the channel style, must stay under "
                       f"{FLUX_MAX_WORDS} words.")
    char_costs = ""
    if previous and model == "sdxl":
        char_costs = ("\n  Series characters already cost: " + ", ".join(
            f"{{{k}}}={clip_tokens.count(v)}" for k, v in previous["characters"].items()) + " tokens.")
    guidance = channel.get("image_guidance") or []
    image_guidance = ("CHANNEL IMAGE RULES:\n" + "\n".join(f"- {g}" for g in guidance)) if guidance else ""
    rules = "\n".join(f"- {r}" for r in channel["story_rules"])
    max_q = int(channel.get("max_quotes", 0))
    if max_q and translation:
        quote_rule = (f"You may quote at most {max_q} short passage(s) word for word from the SOURCE "
                      f"({translation.upper()}). Put the quoted words inside “curly quotes” in the "
                      f"narration AND fill that scene's quote field (text = exactly the quoted words, "
                      f"ref, translation = \"{translation.upper()}\"). Leave quote out of every other scene.")
    else:
        quote_rule = "Do not quote any source text directly. Never include a quote field."
    system = f"""You write short narrated video scripts for the channel "{channel['display_name']}".

NARRATOR: {n['persona']}.
TONE: {n['tone']}.

CHANNEL RULES:
{rules}

QUOTES: {quote_rule}

FORMAT RULES:
- Exactly {n_scenes} scenes. Total narration about {word_budget} words (it is read aloud; the video must
  fit about {channel['target_duration_s']} seconds). Spread words roughly evenly; no scene over 45 words.
- Scene 1 narration is the hook: its first sentence must grab attention immediately.
- Narration is spoken storytelling, not a summary, not verse-by-verse paraphrase. No headings, no emojis,
  no stage directions, no "In this video". Spell out numbers.
- refs: the source references (e.g. "Genesis 37:3") each scene draws on; [] when there is no source.
- title: short, specific, curiosity-driven, no clickbait lies.

IMAGES - treat these as seriously as the narration: each scene is ONE still image made by Stable
Diffusion XL, and the images are most of what the viewer sees.
- characters: every recurring person, name in UPPER_SNAKE_CASE, description = {char_rule}, most
  distinctive first (age + gender, hair, face, clothing colours). Make characters easy to tell apart.
  If someone's look changes mid-story, add a second entry (e.g. JOSEPH_STRIPPED).
- shot (one per scene) - the picture of THIS scene's key moment:
  - subject: {subject_rule} Never write a character's description out - only the placeholder.
  - action: a concrete, visible pose or action ("kneeling at the rim of a dry well, reaching down").
    Only what a camera can see: no thoughts, plans, feelings-as-words, sounds, or time passing.
  - setting: the place plus one or two concrete details.
  - camera: vary it - never the same framing in two consecutive scenes. Scene 1 is the hook and the
    thumbnail: make it the most striking image.
  - lighting: time of day and quality of light, matching the mood.
  - composition (write it into subject/action/setting): named characters in the FOREGROUND, sharp and
    large in the frame, clearly the focus. Any crowd, guards or onlookers stay in the BACKGROUND -
    smaller, softly out of focus, in plain muted clothing unlike any named character's colours - and
    only as many as the moment needs. Never let extras look like a named character.{surface_rule}
  - avoid: {avoid_rule}
  - No art-style words (the channel appends its own style) and no text, letters or captions.
- {budget_rule}{char_costs}
{image_guidance}"""
    user = f"BRIEF: {brief}\n"
    if previous:
        chars = "\n".join(f"- {k}: {v}" for k, v in previous["characters"].items())
        recap = " ".join(s["narration"] for s in previous["scenes"])
        user += (f"\nTHIS IS THE NEXT EPISODE of a series. Previous episode: \"{previous['title']}\".\n"
                 f"Its narration (for continuity only - do not repeat it, and do not retell events it "
                 f"already covered unless the SOURCE requires it):\n<<<\n{recap}\n>>>\n"
                 f"\nSERIES CHARACTERS - reuse these for anyone who appears again: same NAME, description "
                 f"copied exactly, word for word. Only add new entries for people not listed here.\n"
                 f"{chars}\n")
    if source_text:
        user += (f"\nSOURCE ({source_label}) - retell this faithfully; it is the only source of facts:\n"
                 f"<<<\n{source_text}\n>>>\n")
    return system, user


def call_gemini(model, system, user):
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        sys.exit("google-genai isn't installed: pip install -r requirements.txt")
    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("GEMINI_API_KEY is not set. Get a free key at https://aistudio.google.com/apikey, then:\n"
                 "  export GEMINI_API_KEY=...")
    from google.genai import errors
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    for wait in (10, 30, 60, None):      # free tier: busy (503) / rate-limited (429) is common
        try:
            r = client.models.generate_content(
                model=model, contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system, temperature=0.7,
                    response_mime_type="application/json", response_json_schema=SCHEMA))
            break
        except errors.APIError as e:
            if e.code in (429, 500, 503) and wait:
                print(f"  Gemini busy ({e.code}) - retrying in {wait}s...")
                time.sleep(wait)
                continue
            hint = ""
            if e.code == 404:
                hint = "\nThat model may be retired - run python script.py --list-models and pass --model."
            elif e.code == 429:
                hint = "\nFree-tier rate limit hit - wait a few minutes, or try --provider claude."
            elif e.code in (500, 503):
                hint = "\nGemini is overloaded - try later, another --model, or --provider claude."
            sys.exit(f"Gemini API error: {str(e)[:800]}{hint}")
    try:
        return json.loads(r.text)
    except (TypeError, ValueError):
        return {"_raw": r.text}


def call_claude(model, system, user):
    try:
        import anthropic
    except ImportError:
        sys.exit("anthropic isn't installed: pip install -r requirements.txt")
    # Zero-arg client: ANTHROPIC_API_KEY (env or .env), else the `ant auth login` profile
    client = anthropic.Anthropic()
    kwargs = dict(
        model=model, max_tokens=16000, system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "high",
                       "format": {"type": "json_schema", "schema": CLAUDE_SCHEMA}},
    )
    if model in CLAUDE_FALLBACK_MODELS:
        # Server-side fallback: if a safety classifier declines, the API retries on a fallback model
        kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    try:
        r = client.beta.messages.create(**kwargs)
    except anthropic.AuthenticationError:
        sys.exit("Claude: no valid credentials. Run `ant auth login`, or put ANTHROPIC_API_KEY=... in .env")
    except anthropic.PermissionDeniedError as e:
        sys.exit(f"Claude: this key/profile can't use {model}: {e.message}")
    except anthropic.NotFoundError:
        sys.exit(f"Claude: model '{model}' not found - python script.py --provider claude --list-models")
    except anthropic.RateLimitError:
        sys.exit("Claude: rate limited - wait a minute and try again.")
    except anthropic.BadRequestError as e:
        sys.exit(f"Claude: request rejected: {e.message}")
    except anthropic.APIStatusError as e:
        sys.exit(f"Claude API error {e.status_code}: {e.message} (request id {e.request_id})")
    except anthropic.APIConnectionError:
        sys.exit("Claude: network error - check your internet connection.")
    if r.stop_reason == "refusal":
        why = getattr(r.stop_details, "category", None) if r.stop_details else None
        sys.exit(f"Claude declined this request (category: {why}). Rephrase the brief.")
    if r.stop_reason == "max_tokens":
        return {"_raw": "response cut off at max_tokens"}
    text = next((b.text for b in r.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except ValueError:
        return {"_raw": text}


def call_llm(provider, model, system, user):
    return (call_claude if provider == "claude" else call_gemini)(model, system, user)


def list_models(provider):
    if provider == "claude":
        import anthropic
        client = anthropic.Anthropic()
        for m in client.models.list():
            print(m.id)
    else:
        from google import genai
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))  # keep a reference: the
        for m in client.models.list():                                   # pager fetches lazily
            if "generateContent" in (getattr(m, "supported_actions", None) or []):
                print(m.name.removeprefix("models/"))


def to_story(raw, story_id, channel, source, previous=None):
    story = {"story": story_id, "channel": channel["name"], "title": raw.get("title", "").strip(),
             "status": "draft", "source": source, "characters": {}, "scenes": []}
    for c in raw.get("characters") or []:
        name = re.sub(r"[^A-Z0-9_]", "_", str(c.get("name", "")).upper()).strip("_")
        if name:
            story["characters"][name] = str(c.get("description", "")).strip()
    if previous:
        # Series consistency: recurring characters keep the previous episode's exact description
        for name, desc in previous["characters"].items():
            if name in story["characters"]:
                story["characters"][name] = desc
        story["previous"] = previous["story"]
    used = {ph for sc in raw.get("scenes") or [] for ph in re.findall(r"\{([^{}]*)\}", str((sc.get("shot") or {}).get("subject", "")))}
    if previous:
        # A series character used in a shot but not re-listed: bring their description along
        for name in sorted(used - set(story["characters"])):
            if name in previous["characters"]:
                story["characters"][name] = previous["characters"][name]
    for i, s in enumerate(raw.get("scenes") or [], 1):
        scene = {"id": i, "refs": s.get("refs") or [],
                 "narration": " ".join(str(s.get("narration", "")).split()),
                 "image_prompt": compose_prompt(s.get("shot") or {}, image_model(story, channel))}
        avoid = [str(a).strip() for a in (s.get("shot") or {}).get("avoid") or [] if str(a).strip()]
        if avoid and MODEL_RULES[image_model(story, channel)]["negatives"]:
            scene["negative"] = ", ".join(avoid[:3])
        if s.get("quote"):
            scene["quote"] = s["quote"]
        story["scenes"].append(scene)
    return story


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:50] or "story"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--channel", choices=channel_names())
    ap.add_argument("--brief", help="what the video is about / angle / part number")
    ap.add_argument("--source-file", help="text file the story must be based on")
    ap.add_argument("--passage", help='Bible passage to retell, e.g. "Genesis 37:1-11"')
    ap.add_argument("--id", help="story id / file name (default: from the title)")
    ap.add_argument("--continue-from", metavar="STORY",
                    help="previous episode's id: reuse its characters exactly and continue its story")
    ap.add_argument("--scenes", type=int, help="number of scenes (default: from target duration)")
    ap.add_argument("--provider", choices=PROVIDERS, default=DEFAULT_PROVIDER,
                    help=f"LLM to draft with (default {DEFAULT_PROVIDER}; set LLM_PROVIDER in .env to change)")
    ap.add_argument("--model", help="model id (default: gemini %s, claude %s)"
                    % (DEFAULT_MODELS["gemini"], DEFAULT_MODELS["claude"]))
    ap.add_argument("--force", action="store_true", help="overwrite even an approved story")
    ap.add_argument("--list-models", action="store_true", help="list models available for --provider")
    args = ap.parse_args(argv)

    args.model = args.model or DEFAULT_MODELS[args.provider]
    if args.list_models:
        list_models(args.provider)
        sys.exit(0)
    if not args.channel or not args.brief:
        ap.error("--channel and --brief are required")

    channel = load_channel(args.channel)
    previous = None
    if args.continue_from:
        prev_path = os.path.join(SCENES_DIR, f"{args.continue_from}.json")
        if not os.path.exists(prev_path):
            sys.exit(f"--continue-from: no such story scenes/{args.continue_from}.json")
        previous = json.load(open(prev_path))
        if previous.get("channel") != channel["name"]:
            sys.exit(f"--continue-from: {args.continue_from} is on channel "
                     f"'{previous.get('channel')}', not '{channel['name']}'")
        print(f"Continuing from {args.continue_from} "
              f"(reusing characters: {', '.join(previous['characters'])})")
    source, source_label, source_text, translation = {"brief": args.brief}, None, None, None
    if args.passage:
        translation = channel.get("passage_translation", "kjv")
        source_label, source_text = fetch_passage(args.passage, translation)
        source.update(passage=source_label, translation=translation.upper())
        print(f"Fetched {source_label} ({translation.upper()}, {len(source_text.splitlines())} verses)")
    if args.source_file:
        extra = open(args.source_file, encoding="utf-8").read().strip()
        source_label = f"{source_label} + {os.path.basename(args.source_file)}" if source_label \
            else os.path.basename(args.source_file)
        source_text = f"{source_text}\n\n{extra}" if source_text else extra
        source["file"] = os.path.basename(args.source_file)

    target = int(channel.get("target_duration_s", 55))
    n_scenes = args.scenes or max(3, round(target / SECONDS_PER_SCENE))
    word_budget = int(target * WORDS_PER_SECOND * 0.9)   # leave room for pauses between scenes
    system, user = build_prompt(channel, args.brief, source_label, source_text,
                                n_scenes, word_budget, translation, previous)

    if args.id and os.path.exists(os.path.join(SCENES_DIR, f"{args.id}.json")):
        existing = json.load(open(os.path.join(SCENES_DIR, f"{args.id}.json")))
        if existing.get("status") == "approved" and not args.force:
            sys.exit(f"scenes/{args.id}.json is APPROVED - not overwriting it. Use --force, or a new --id.")

    print(f"Asking {args.provider}/{args.model} for {n_scenes} scenes, ~{word_budget} words ({channel['display_name']})...")
    story, errs = None, []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        msg = user if not errs else (user + "\nYOUR PREVIOUS ANSWER HAD THESE PROBLEMS - fix all of them:\n"
                                     + "\n".join(f"- {e}" for e in errs))
        raw = call_llm(args.provider, args.model, system, msg)
        if "_raw" in raw:
            errs = ["response was not valid JSON"]
        else:
            sid = args.id or slugify(raw.get("title", "")) or "story"
            story = to_story(raw, sid, channel, source, previous)
            errs = validate_story(story, source_text=source_text if args.passage else None)
            new_chars = [k for k in story["characters"] if not previous or k not in previous["characters"]]
            errs += image_problems(story, channel, new_characters=new_chars)
            cams = [(sc.get("shot") or {}).get("camera") for sc in raw.get("scenes") or []]
            errs += [f"scenes {i} and {i + 1} both use '{c}' - vary the camera"
                     for i, (c, d) in enumerate(zip(cams, cams[1:]), 1) if c and c == d]
            words = sum(len(s["narration"].split()) for s in story["scenes"])
            if words > word_budget * 1.15:
                errs.append(f"narration is {words} words; it must be at most {word_budget} words "
                            f"to fit {target} seconds - shorten it")
            if len(story["scenes"]) != n_scenes:
                errs.append(f"expected {n_scenes} scenes, got {len(story['scenes'])}")
        if not errs:
            break
        print(f"  attempt {attempt}: {len(errs)} problem(s): " + "; ".join(errs[:4]))
    if errs:
        sys.exit(f"{args.provider} didn't produce a valid story after "
                 f"{MAX_ATTEMPTS} tries:\n  " + "\n  ".join(errs))

    out = os.path.join(SCENES_DIR, f"{story['story']}.json")
    if os.path.exists(out) and not args.id:
        existing = json.load(open(out))
        if existing.get("status") == "approved" and not args.force:
            sys.exit(f"scenes/{story['story']}.json is APPROVED - not overwriting it. "
                     f"Use --force, or pass a new --id.")
    os.makedirs(SCENES_DIR, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(story, f, indent=2, ensure_ascii=False)
        f.write("\n")

    words = sum(len(s["narration"].split()) for s in story["scenes"])
    print(f"\nWrote {out}  (DRAFT)")
    print(f"  \"{story['title']}\" - {len(story['scenes'])} scenes, {words} words "
          f"(~{words / WORDS_PER_SECOND:.0f}s spoken)")
    print(f"  characters: {', '.join(story['characters'])}")
    for s in story["scenes"]:
        print(f"\n  [{s['id']}] {s['narration']}")
    print("\nNext: read it (check every fact against the source), edit if needed, then set "
          "\"status\": \"approved\" and run python run_kaggle.py")


if __name__ == "__main__":
    main()
