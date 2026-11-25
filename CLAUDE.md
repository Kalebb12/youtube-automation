# CLAUDE.md — bible-pipeline

> Scripture in, YouTube Short out. A free, automated pipeline that turns Bible
> stories into narrated, illustrated videos, using Kaggle's free GPU for the
> heavy lifting and a laptop for everything else.

## What this is

A faceless YouTube channel engine for Bible stories. Each story is a JSON file of
scenes (narration + image prompt). The pipeline generates one image per scene on
Kaggle, then (coming next) narrates, animates, captions and uploads on the laptop.

**Guiding constraint: it costs ₦0 to run.** No paid APIs, no paid GPUs. If a change
adds a recurring cost, flag it before making it.

## Architecture

```
LAPTOP                                    KAGGLE (free GPU, wakes per job)
──────                                    ───────────────────────────────
scenes/*.json ──upload as dataset──────▶  bible-scenes (private dataset)
                                                    │
run_kaggle.py ──kaggle kernels push────▶  bible-scene-generator  (T4 GPU)
                                            ├─ attaches bible-scenes
                                            ├─ attaches sdxl-model-cache output
                                            └─ writes /kaggle/working/<story>/*.png
images/<story>/ ◀──kaggle kernels output───┘
      │
      └─▶ [next] voice (Kokoro) → assembly (MoviePy/FFmpeg) → captions (Whisper) → upload
```

Kaggle jobs are **batch jobs, not servers**: each push boots a fresh machine, runs,
saves outputs, shuts down. GPU quota (~30 h/week) is only spent while running.

## Layout

| Path | Runs on | Role |
|---|---|---|
| `run_kaggle.py` | laptop | Controller. Writes Kaggle metadata, uploads scenes, pushes jobs, polls, downloads. No AI here. |
| `kaggle_sd/generate.py` | Kaggle GPU | Loads SDXL from the cache, renders every scene of every story. |
| `kaggle_cache/cache_model.py` | Kaggle CPU | One-time job: downloads SDXL (~7 GB) and saves it as job output for reuse. |
| `scenes/*.json` | — | Input. One file per story. |
| `images/` | — | Output, downloaded from Kaggle. Gitignored. |

`kernel-metadata.json` and `scenes/dataset-metadata.json` are **generated** by
`run_kaggle.py` from the user's Kaggle username. Don't hand-edit them or commit them.

## Commands

```bash
source .venv/bin/activate                # always; the scripts shell out to `kaggle`
python run_kaggle.py --setup-model       # ONE TIME: cache the model on Kaggle
python run_kaggle.py                     # upload scenes → generate → download
python run_kaggle.py --download          # re-download the last finished run
kaggle kernels status <user>/bible-scene-generator   # check a job manually
```

Logs for a failed run: kaggle.com → Your Work → Code → `bible-scene-generator`.

## Story file format

```json
{
  "story": "joseph_part1",              // ID → folder name; must be unique
  "title": "Joseph and His Brothers (Part 1)",
  "passage": "Genesis 37",
  "format": "short",                    // "short" = 768x1344 (9:16), "long" = 1344x768
  "characters": { "JOSEPH": "a 17-year-old young man with short black curly hair..." },
  "scenes": [
    { "id": 1, "narration": "...", "image_prompt": "{JOSEPH} standing in a field", "seed": 555 }
  ]
}
```

- `{NAME}` placeholders are replaced with the exact `characters` text. **This is how
  character consistency works**, so never inline a character description in a prompt.
- `seed` is optional; default is `1234 + id`. Same seed + prompt = same image.
  To re-roll one scene, set a new seed on that scene only.
- Optional per-story overrides: `style`, `negative`.
- `generate.py` identifies story files as any JSON with a `scenes` list.

## Rules for working in this repo

**Content**
- Narration is quoted from **public-domain translations only**: KJV, ASV, or World
  English Bible. Never NIV/ESV/NLT (copyrighted).
- Stay faithful to the text. Don't invent dialogue or events and present them as scripture.
- Narrator voice only. No AI avatar posing as a pastor or giving spiritual counsel
  (YouTube won't monetize AI personas on sensitive topics).
- No copyrighted characters or film/TV likenesses in prompts.

**Look and feel**
- `STYLE` and `NEGATIVE` in `generate.py` are the channel's brand. Don't change them
  casually: it breaks visual consistency across episodes. Ask first.

**Kaggle / GPU**
- Target hardware is a **T4 (16 GB, fp16, no bf16)**. Keep pipelines in `float16`,
  keep `enable_vae_slicing()`, and test anything bigger than SDXL for memory first.
- Load the model **once** per job and batch all stories through it.
- Never download weights inside `generate.py` in the normal path. Cache them via a
  `kaggle_cache` job and attach with `kernel_sources`. `HF_HUB_OFFLINE=1` enforces this.
- Only files in `/kaggle/working/` come back to the laptop.

**Models and licences** (this channel is monetized)
- OK: SDXL 1.0, Z-Image Turbo (Apache 2.0), FLUX.2 klein 4B (Apache 2.0).
- Not OK: FLUX.1 dev, FLUX.2 dev, or anything else with a non-commercial licence.
- Swapping models should only touch `generate.py` + a matching cache job.

**Secrets**
- `~/.kaggle/kaggle.json` is a credential. Never copy it into the repo, print it, or commit it.

## Gotchas

- `ModuleNotFoundError: six` after `pip install kaggle`: run `pip install six`.
- GPU or internet errors on Kaggle: the account's phone number isn't verified.
- Dataset "not ready": Kaggle needs a minute after upload. `upload_scenes()` waits for it.
- Black images: fp16 VAE overflow. That's why we use `madebyollin/sdxl-vae-fp16-fix`.
- Faces drift between scenes: tighten the `characters` description before reaching
  for IP-Adapter or LoRAs.

## Roadmap

- [x] Image generation on Kaggle with cached model
- [ ] `voice.py`: Kokoro TTS per scene → `images/<story>/audio/scene_XX.wav`
- [ ] `assemble.py`: Ken Burns motion + crossfades + music bed (MoviePy/FFmpeg)
- [ ] Captions: Whisper timings → burned-in subtitles
- [ ] `script.py`: Gemini drafts `scenes/*.json` from a passage, for human review
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
