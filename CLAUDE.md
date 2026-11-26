# CLAUDE.md — story pipeline

> Brief in, YouTube Short out. A free, automated pipeline that turns stories into
> narrated, illustrated videos, using Kaggle's free GPU for the heavy lifting and a
> laptop for everything else. Niche-agnostic: each channel is a profile file.

## What this is

A faceless YouTube channel engine. A **channel profile** (`channels/<name>.json`)
holds everything niche-specific; the code is generic. Each video is a **story**
(`scenes/<id>.json`): storytelling narration + one image prompt per scene. An LLM
(Gemini free tier, or Claude) drafts the story, a human approves it, Kaggle renders the images, the laptop
narrates, animates and captions it (upload is next).

The first channel is `bible` (Bible stories); `horror` exists as a second example.

**Guiding constraint: it costs ₦0 to run.** No paid APIs, no paid GPUs. If a change adds a
recurring cost, flag it before making it. **Exception, chosen by the user:** `script.py
--provider claude` is paid (~$0.05-0.10 per draft, billed to the user's Anthropic org via the
`ant auth login` profile); Gemini's free tier stays available and is the default unless
`LLM_PROVIDER=claude` is set in `.env`.

## Architecture

```
LAPTOP                                          KAGGLE (free GPU, wakes per job)
──────                                          ───────────────────────────────
script.py  ── Gemini / Claude ─────▶ scenes/<id>.json  "status": "draft"
                                         │
                        HUMAN REVIEW → "status": "approved"
                                         │
run_kaggle.py ── approved stories + channels/*.json ──▶ bible-scenes dataset
              ── kaggle kernels push ─────────────────▶ bible-scene-generator (T4)
                                                          ├─ attaches bible-scenes
                                                          ├─ attaches <model>-cache output(s)
                                                          └─ writes /kaggle/working/<story>/*.png
images/<story>/ ◀── kaggle kernels output ────────────────┘
      ├─▶ voice.py     Kokoro TTS (CPU)        → images/<story>/audio/scene_XX.wav
      ├─▶ assemble.py  parallax moves + crossfades + subtitles + music → images/<story>/<story>.mp4
      └─▶ [next] upload
```

(The Kaggle dataset/kernel slugs still say "bible" for historical reasons; they carry
every channel's stories.)

Kaggle jobs are **batch jobs, not servers**: each push boots a fresh machine, runs,
saves outputs, shuts down. GPU quota (~30 h/week) is only spent while running.

## Layout

| Path | Runs on | Role |
|---|---|---|
| `channels/<name>.json` | — | Channel profile: narrator persona/tone, story rules, quote policy, image `style`/`negative`, voice, format, target duration, music mood. |
| `script.py` | laptop | Stage 0. Gemini or Claude (`--provider`, schema-constrained JSON) drafts `scenes/<id>.json` from a brief, optional `--passage` (KJV via bible-api.com), `--source-file`, `--continue-from <prev episode>`. Validates (incl. word budget); retries with the errors. |
| `run_kaggle.py` | laptop | Controller. Stages **approved** stories + channel profiles, uploads, pushes the GPU job, polls, downloads. No AI here. |
| `kaggle_sd/generate.py` | Kaggle GPU | Groups stories by image model (`flux2-klein` / `sdxl`), loads each once from its cache, renders every scene with its channel's style. |
| `kaggle_cache/cache_model.py` | Kaggle CPU | One-time job **per model**: downloads only the files that model needs (FLUX.2 klein ~16 GB, SDXL ~7 GB) and saves them as job output. `run_kaggle.py` pushes a copy with the `MODEL` line set as `<model>-cache`. |
| `clip_tokens.py` | laptop | Exact CLIP token counter (stdlib BPE + SDXL's merges.txt) for SDXL's 77-token window. |
| `voice.py` | laptop | Kokoro TTS (kokoro-onnx, CPU) per scene, voice from the profile. Cached by text+voice+speed. |
| `assemble.py` | laptop | Per scene a silent moving clip (`motion.py`), xfade crossfades, fade in/out, burned-in subtitles, narration + music (`music.py`), loudness-normalised mp4. |
| `motion.py` | laptop | Depth parallax (Depth Anything V2 Small, ONNX, CPU; cached per image) + eased camera moves, frames piped into ffmpeg. |
| `music.py` | laptop | Music: track pool/moods/rotation, layered mix settings, ducking. CLI: `list`, `preview` (audio-only, ~20 s), `set`. |
| `common.py` | laptop | Shared: `.env` loading, channel loading, story schema validation, story loading, scene timing constants, ffmpeg helpers. |
| `scenes/*.json` | — | Stories. Source of truth for narration. |
| `images/` | — | All outputs per story: images, `audio/`, final mp4. Gitignored. |
| `models/`, `music/` | — | Kokoro model (~350 MB, auto-downloaded); royalty-free music in `music/<channel>/`. Gitignored. |

**Docs are HTML:** `docs/*.html` + `docs/style.css` (open `docs/index.html`). Pages: index (overview &
setup), making-an-episode, images, motion, music, channels, troubleshooting, how-it-works. When
behaviour, commands, defaults or timings change, update the matching page in the same change -
edit the HTML directly, reuse the existing structure (`<h2 id=...>` sections, `.table-wrap`
tables), keep the sidebar list identical on every page, and check internal links still resolve.
`README.md` is only a short pointer to the docs.

Generated, never hand-edited or committed: `kaggle_sd/kernel-metadata.json`,
`kaggle_cache/kernel-metadata.json`, `.kaggle_upload/` (the staged dataset).

## Commands

```bash
source .venv/bin/activate
# keys come from .env (gitignored): GEMINI_API_KEY; Claude uses ANTHROPIC_API_KEY or `ant auth login`
python script.py --channel bible --passage "Genesis 37:12-24" --brief "Joseph part 2: the pit"
python script.py --provider claude --channel bible --passage "Genesis 37:25-36" \
    --continue-from joseph_part2 --brief "Joseph part 3: sold to the Ishmeelites"
python script.py --channel horror --brief "a lighthouse keeper hears knocking from below"
#   → read scenes/<id>.json, fix it, set "status": "approved"
python run_kaggle.py --setup-model [flux2-klein|sdxl]   # ONE TIME per model: cache it on Kaggle
python run_kaggle.py [story ...]         # approved stories → generate → download
python run_kaggle.py --download          # re-download the last finished run
python voice.py [story]                  # narration → images/<story>/audio/
python assemble.py [story]               # → images/<story>/<story>.mp4
python music.py list | preview <story> | set <story> [--track f.mp3 --volume-db -16 --start 10 --no-duck]
python script.py --list-models [--provider claude]   # if a default model is retired
kaggle kernels status <user>/bible-scene-generator   # check a job manually
```

Logs for a failed run: kaggle.com → Your Work → Code → `bible-scene-generator`.

## Channel profiles

`channels/<name>.json` — everything that makes a channel *that* channel:

| Key | Used by | Meaning |
|---|---|---|
| `name`, `display_name` | all | id (= file name) and human name |
| `narrator.persona`, `narrator.tone` | script.py | who is telling the story and how |
| `story_rules` | script.py | content rules the draft must follow (faithfulness, rating, hooks, endings) |
| `max_quotes`, `quote_translations` | script.py, validation | how many direct quotes a story may contain, from which public-domain texts |
| `passage_translation` | script.py | translation fetched for `--passage` (bible-api.com id, e.g. `kjv`) |
| `image_model` | generate.py, script.py, checks | `flux2-klein` (default for both channels) or `sdxl` |
| `style`, `negative` | generate.py | style appended to every prompt (and SDXL-only negative prompt) — **the channel's visual brand** |
| `image_guidance` | script.py | niche rules for the pictures (period accuracy, what stays unseen, ...) |
| `voice.engine/id/speed/lang` | voice.py | TTS voice (only `kokoro` for now) |
| `format` | generate.py, assemble.py | `short` (9:16) or `long` (16:9) |
| `target_duration_s` | script.py, voice.py | word budget for drafts; warning if narration runs long |
| `motion` | motion.py, assemble.py | `parallax`, `strength`, `zoom`, `transition` (xfade name), `transition_s`, `fade_in`, `fade_out` |
| `music_mood` | assemble.py (hint) | what to put in `music/<channel>/` |
| `music` | music.py, assemble.py | house mix: `pick` (rotate/first), `volume_db`, `fade_in`/`fade_out`, `duck`, `start`, `mood`, `track` |

Adding a niche = adding a profile. Code changes should never hard-code a niche.

## Story file format

```json
{
  "story": "joseph_part1",                 // id → file and folder name; unique
  "channel": "bible",                      // → channels/bible.json
  "title": "Joseph and His Brothers (Part 1)",
  "status": "draft",                       // "draft" | "approved" - only approved reach the GPU
  "source": { "passage": "Genesis 37", "translation": "KJV", "brief": "..." },
  "characters": { "JOSEPH": "a beardless 17-year-old ... robe of many colours" },
  "scenes": [
    { "id": 1, "refs": ["Genesis 37:3"],
      "narration": "One gift tore a family apart. ... “a coat of many colours.”",
      "quote": { "text": "a coat of many colours", "ref": "Genesis 37:3", "translation": "KJV" },
      "image_prompt": "{JOSEPH} holding up a beautiful new coat ...", "seed": 555 }
  ]
}
```

- `{NAME}` placeholders are replaced with the exact `characters` text. **This is how
  character consistency works**, so never inline a character description in a prompt.
  When a character's look changes mid-story, add a second entry (e.g. `JOSEPH_STRIPPED`).
- `seed` is optional; default is `1234 + id`. Same seed + prompt + style = same image.
- `quote` is optional and only for words quoted directly from the source (see Content rules).
- `previous` (set by `--continue-from`) names the prior episode. Recurring characters keep the
  prior episode's exact description - that's what makes a series look like one series. Use
  `--continue-from` for every episode after the first.
- Scenes are drafted as a structured `shot` (subject, action, setting, camera, lighting, avoid) and
  composed into `image_prompt` in priority order; on SDXL `avoid` becomes the scene's `negative`.
- A scene may pin its camera move: `"move": "zoom_in" | "zoom_out" | "pan_left" | "pan_right"`
  (default: that cycle, so neighbours never match).
- Optional per-story overrides of the profile: `image_model`, `motion`, `style`, `negative`, `format`, `voice`, `speed`,
  `music` (same keys as the profile's `music`; `"track": "none"` = silent). Precedence:
  defaults < channel `music` < story `music` < `assemble.py` flags.
- `common.validate_story()` is the schema. `script.py` and `run_kaggle.py` both enforce it.
- `voice.py`/`assemble.py` read narration from `scenes/` (not the copy in `images/`), so
  narration edits don't need a GPU re-run. They warn if image prompts changed since render.

## The draft → approved gate

1. `script.py` always writes `"status": "draft"` and refuses to overwrite an `approved`
   story unless `--force`.
2. A human reads the draft **against the source**, edits it, and sets `"status": "approved"`.
3. `run_kaggle.py` uploads only approved, valid stories; drafts are listed as skipped.
   No approved stories → nothing is sent to Kaggle.
4. `voice.py`/`assemble.py` work on drafts too (for previewing) but print a DRAFT note.

Never flip a story to `approved` on the user's behalf.

## Rules for working in this repo

**Content**
- Narration is **storytelling**, not verse-by-verse reading. Retellings must be **faithful
  to the source**: no invented events, dialogue, motives, names, numbers or doctrine.
- **Direct quotes** must come from **public-domain translations/texts only** (Bible: KJV,
  ASV or World English Bible — never NIV/ESV/NLT) and must be **marked as quotes**: inside
  quotation marks in the narration *and* recorded in the scene's `quote` field.
  With `--passage`, quotes are checked word for word against the fetched text.
- Per-channel rules (quote limits, rating, tone) live in the profile's `story_rules`.
- Narrator voice only. No AI avatar posing as a pastor or giving spiritual counsel
  (YouTube won't monetize AI personas on sensitive topics).
- No copyrighted characters or film/TV likenesses in prompts; no real people or real
  crimes presented as true in fiction channels.

**Look and feel**
- A profile's `style` and `negative` are that channel's brand. Don't change them
  casually: it breaks visual consistency across episodes. Ask first.

**Image models**
- **FLUX.2 klein 4B** (`flux2-klein`, default): 4 distilled steps, `guidance_scale=1.0`, bf16 +
  `enable_model_cpu_offload()` (transformer 7.8 GB + Qwen3 text encoder 8 GB don't fit one T4 together).
  512-token prompts, natural language. **No negative prompt** - write avoidances positively.
  Up to 2 named characters per image. Supports reference images (`image=`) - not used yet.
- **SDXL** (`sdxl`): fp16, 30 steps, VAE slicing on (`pipe.vae.enable_slicing()`; the pipeline-level
  `enable_vae_slicing()` was removed from diffusers). **77-token CLIP window**: everything past it is
  silently dropped (the style goes last, so it's what gets lost). `script.py` budgets prompts exactly
  with `clip_tokens.py`; 1 named character per image; character descriptions ≤ 20 tokens.
- `common.image_problems()` holds the per-model rules (`MODEL_RULES`); `script.py` treats them as
  errors (retry), `run_kaggle.py` as warnings.

**Kaggle / GPU**
- Target hardware is a **T4 (16 GB, no native bf16)**. Test anything bigger than FLUX.2 klein 4B for
  memory first.
- Load each model **once** per job and batch all its stories through it (`generate.py` groups by model).
- Never download weights inside `generate.py` in the normal path. Cache them via the
  `<model>-cache` job and attach with `kernel_sources` (only the models the approved stories need).
  `HF_HUB_OFFLINE=1` enforces this. Kaggle job output is capped at ~20 GB per notebook, so one
  cache notebook per model.
- Only files in `/kaggle/working/` come back to the laptop.
- `/kaggle/input` is fetched lazily; `generate.py` pre-reads the weights sequentially before
  loading (8 min → ~30 s). Don't remove that.
- Channel profiles are uploaded flattened as `channel__<name>.json` (dataset uploads don't
  keep sub-folders). `generate.py` checks every story's channel before loading the model.

**Models and licences** (channels are monetized)
- OK: SDXL 1.0, Z-Image Turbo (Apache 2.0), FLUX.2 klein 4B (Apache 2.0).
- Not OK: FLUX.1 dev, FLUX.2 dev, or anything else with a non-commercial licence.
- Swapping models should only touch `generate.py` + a matching cache job.

**Secrets**
- `~/.kaggle/kaggle.json`, `.env` (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`) and the `ant` profile are
  credentials. Never copy them into the repo, print them, or commit them. `.env` is gitignored.

## Gotchas

- `ModuleNotFoundError: six` after `pip install kaggle`: run `pip install six`.
- GPU or internet errors on Kaggle: the account's phone number isn't verified.
- Dataset "not ready": Kaggle needs a minute after upload. `upload_scenes()` waits for it.
- Black images: fp16 VAE overflow. That's why we use `madebyollin/sdxl-vae-fp16-fix`.
- Faces drift / characters blend: SDXL merges people in one image (FLUX.2 copes with two). Prefer
  one character per image; tighten `characters` before reaching for reference images or LoRAs.
- SDXL log line `truncated because CLIP can only handle sequences up to 77 tokens`: the prompt
  overflowed - the style was lost. `common.image_problems()` catches this before rendering.
- Gemini 404: the default model was retired. `python script.py --list-models`, then
  `--model <name>` or `GEMINI_MODEL=<name>` in `.env`. 429/503 = free tier busy; `script.py`
  retries for ~2 min, then use `--provider claude`.
- Music: tracks are loudness-levelled before `volume_db`, so never "fix" a quiet track by
  editing the file. Keep `music/` gitignored; only royalty-free tracks (channels are monetized).
- Claude: default `claude-opus-5-5` (`CLAUDE_MODEL` to change), effort `high`, server-side
  refusal fallback on. Don't send `temperature` (rejected on current Claude models).

## Roadmap

- [x] Image generation on Kaggle with cached model
- [x] `voice.py`: Kokoro TTS per scene → `images/<story>/audio/scene_XX.wav`
- [x] `assemble.py`: depth-parallax camera moves, crossfades, fade in/out, music bed
- [x] Music tools: pools/moods/rotation, levelling, ducking, fades, `music.py preview`/`set`
- [x] Captions: burned-in subtitles, timed from TTS durations (Whisper word timings would be tighter)
- [x] Channel profiles; `script.py` drafts stories (Gemini or Claude); draft → approved gate
- [x] Series continuity: `--continue-from` reuses characters across episodes
- [ ] Upload via YouTube Data API (private by default, human approves publish)
- [x] Image model switch: FLUX.2 klein 4B (per-channel `image_model`, per-model caches); SDXL kept
- [x] Image-focused drafting: structured shots, per-model budgets/rules, channel `image_guidance`
- [ ] Character reference images with FLUX.2 klein (`image=`) for cross-episode faces
- [ ] Wan2.2 image-to-video for the hook scene only (TI2V-5B needs ~24 GB even offloaded; T4 has 16 GB - test first)
- [ ] Try Z-Image Turbo (needs fp32→fp16 conversion: 33 GB repo exceeds the 20 GB cache limit)
- [ ] Narration in Nigerian Pidgin / Yoruba / Igbo

**The human review step is not optional.** Every script gets read before it renders
and every video gets watched before it goes public. Accuracy is the channel's moat.

## Style for code changes

- Python 3.12, standard library first, few dependencies.
- Laptop scripts shell out to the `kaggle` CLI rather than the Python API.
- Print progress plainly; the user watches the terminal.
- Keep each stage a separate script that reads and writes files in `images/<story>/`,
  so any stage can be rerun alone.
- Nothing niche-specific in code: it belongs in a channel profile.
