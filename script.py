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
from common import SCENES_DIR, channel_names, load_channel, load_dotenv, story_setting, validate_story

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
                "image_prompt": {"type": "string"},
                "refs": {"type": "array", "items": {"type": "string"}},
                "quote": {"type": "object",
                          "properties": {"text": {"type": "string"}, "ref": {"type": "string"},
                                         "translation": {"type": "string"}},
                          "required": ["text", "ref", "translation"]}},
            "required": ["narration", "image_prompt", "refs"]}},
    },
    "required": ["title", "characters", "scenes"],
}


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
- characters: every recurring person, with name in UPPER_SNAKE_CASE and a purely VISUAL description
  (age, build, hair, face, clothing, colours) that stays the same in every scene. If someone's look
  changes mid-story, add a second character entry (e.g. JOSEPH_STRIPPED).
- image_prompt: one still image for that scene, in English, for Stable Diffusion XL. Refer to characters
  ONLY as {{NAME}} placeholders - never repeat their description. One clear main subject; prefer one
  character per image (SDXL blends people together); describe action, setting, camera angle and light.
  Do NOT include art-style words (the channel adds its own style), and no text or captions in the image.
- title: short, specific, curiosity-driven, no clickbait lies."""
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
    for i, s in enumerate(raw.get("scenes") or [], 1):
        scene = {"id": i, "refs": s.get("refs") or [],
                 "narration": " ".join(str(s.get("narration", "")).split()),
                 "image_prompt": " ".join(str(s.get("image_prompt", "")).split())}
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
