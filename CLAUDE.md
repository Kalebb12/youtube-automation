# CLAUDE.md — story pipeline

> Brief in, YouTube Short out. A free, automated pipeline that turns stories into
> narrated, illustrated videos, using Kaggle's free GPU for the heavy lifting and a
> laptop for everything else. Niche-agnostic: each channel is a profile file.

## What this is

A faceless YouTube channel engine. A **channel profile** (`channels/<name>.json`)
holds everything niche-specific; the code is generic. Each video is a **story**
(`scenes/<id>.json`): storytelling narration + one image prompt per scene. Gemini
drafts the story, a human approves it, Kaggle renders the images, the laptop
narrates, animates and captions it (upload is next).

The first channel is `bible` (Bible stories); `horror` exists as a second example.

**Guiding constraint: it costs ₦0 to run.** No paid APIs, no paid GPUs (Gemini is used
on its free tier only). If a change adds a recurring cost, flag it before making it.

## Architecture

```
LAPTOP                                          KAGGLE (free GPU, wakes per job)
──────                                          ───────────────────────────────
script.py  ── Gemini (free tier) ──▶ scenes/<id>.json  "status": "draft"
                                         │
                        HUMAN REVIEW → "status": "approved"
                                         │
run_kaggle.py ── approved stories + channels/*.json ──▶ bible-scenes dataset
              ── kaggle kernels push ─────────────────▶ bible-scene-generator (T4)
                                                          ├─ attaches bible-scenes
                                                          ├─ attaches sdxl-model-cache output
                                                          └─ writes /kaggle/working/<story>/*.png
images/<story>/ ◀── kaggle kernels output ────────────────┘
      ├─▶ voice.py     Kokoro TTS (CPU)        → images/<story>/audio/scene_XX.wav
      ├─▶ assemble.py  FFmpeg zoom + subtitles → images/<story>/<story>.mp4
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
| `script.py` | laptop | Stage 0. Gemini (JSON output) drafts `scenes/<id>.json` from a brief, optional `--passage` (KJV via bible-api.com) or `--source-file`. Validates; retries with the errors. |
| `run_kaggle.py` | laptop | Controller. Stages **approved** stories + channel profiles, uploads, pushes the GPU job, polls, downloads. No AI here. |
| `kaggle_sd/generate.py` | Kaggle GPU | Loads SDXL from the cache, renders every scene with its channel's style. |
| `kaggle_cache/cache_model.py` | Kaggle CPU | One-time job: downloads SDXL (~7 GB) and saves it as job output for reuse. |
| `voice.py` | laptop | Kokoro TTS (kokoro-onnx, CPU) per scene, voice from the profile. Cached by text+voice+speed. |
| `assemble.py` | laptop | FFmpeg: slow zoom per image, burned-in subtitles, optional music, loudness-normalised mp4. |
| `common.py` | laptop | Shared: channel loading, story schema validation, story loading, ffmpeg helpers. |
| `scenes/*.json` | — | Stories. Source of truth for narration. |
| `images/` | — | All outputs per story: images, `audio/`, final mp4. Gitignored. |
| `models/`, `music/` | — | Kokoro model (~350 MB, auto-downloaded); royalty-free music in `music/<channel>/`. Gitignored. |

Generated, never hand-edited or committed: `kaggle_sd/kernel-metadata.json`,
`kaggle_cache/kernel-metadata.json`, `.kaggle_upload/` (the staged dataset).

## Commands

```bash
source .venv/bin/activate
export GEMINI_API_KEY=...                # free key: https://aistudio.google.com/apikey
python script.py --channel bible --passage "Genesis 37:12-24" --brief "Joseph part 2: the pit"
python script.py --channel horror --brief "a lighthouse keeper hears knocking from below"
#   → read scenes/<id>.json, fix it, set "status": "approved"
python run_kaggle.py --setup-model       # ONE TIME: cache the model on Kaggle
python run_kaggle.py [story ...]         # approved stories → generate → download
python run_kaggle.py --download          # re-download the last finished run
python voice.py [story]                  # narration → images/<story>/audio/
python assemble.py [story]               # → images/<story>/<story>.mp4
python script.py --list-models           # if the default Gemini model is retired
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
| `style`, `negative` | generate.py | SDXL style suffix and negative prompt — **the channel's visual brand** |
| `voice.engine/id/speed/lang` | voice.py | TTS voice (only `kokoro` for now) |
| `format` | generate.py, assemble.py | `short` (9:16) or `long` (16:9) |
| `target_duration_s` | script.py, voice.py | word budget for drafts; warning if narration runs long |
| `music_mood` | assemble.py (hint) | what to put in `music/<channel>/` |

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
- Optional per-story overrides of the profile: `style`, `negative`, `format`, `voice`, `speed`.
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

**Kaggle / GPU**
- Target hardware is a **T4 (16 GB, fp16, no bf16)**. Keep pipelines in `float16`,
  keep VAE slicing on (`pipe.vae.enable_slicing()`; the pipeline-level
  `enable_vae_slicing()` was removed from diffusers), and test anything bigger than SDXL for memory first.
- Load the model **once** per job and batch all stories through it.
- Never download weights inside `generate.py` in the normal path. Cache them via a
  `kaggle_cache` job and attach with `kernel_sources`. `HF_HUB_OFFLINE=1` enforces this.
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
- `~/.kaggle/kaggle.json` and `GEMINI_API_KEY` are credentials. Never copy them into the
  repo, print them, or commit them.

## Gotchas

- `ModuleNotFoundError: six` after `pip install kaggle`: run `pip install six`.
- GPU or internet errors on Kaggle: the account's phone number isn't verified.
- Dataset "not ready": Kaggle needs a minute after upload. `upload_scenes()` waits for it.
- Black images: fp16 VAE overflow. That's why we use `madebyollin/sdxl-vae-fp16-fix`.
- Faces drift / characters blend: SDXL merges people in one image. Prefer one character
  per image; tighten `characters` before reaching for IP-Adapter or LoRAs.
- Gemini 404: the default model was retired. `python script.py --list-models`, then
  `--model <name>` or `export GEMINI_MODEL=<name>`. 429 = free-tier rate limit; wait.

## Roadmap

- [x] Image generation on Kaggle with cached model
- [x] `voice.py`: Kokoro TTS per scene → `images/<story>/audio/scene_XX.wav`
- [x] `assemble.py`: Ken Burns motion + music bed (FFmpeg) — crossfades not yet
- [x] Captions: burned-in subtitles, timed from TTS durations (Whisper word timings would be tighter)
- [x] Channel profiles; `script.py` drafts stories with Gemini; draft → approved gate
- [ ] Upload via YouTube Data API (private by default, human approves publish)
- [ ] Try Z-Image Turbo vs SDXL on the Joseph story
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
