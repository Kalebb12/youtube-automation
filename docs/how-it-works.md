# How it works (and why it's built this way)

```
script.py ──▶ scenes/<id>.json (draft) ──you approve──▶ run_kaggle.py ──▶ Kaggle T4 (generate.py)
                                                                              │
          images/<story>/scene_XX.png ◀────────── kaggle kernels output ──────┘
               ├─ voice.py    → images/<story>/audio/scene_XX.wav
               └─ assemble.py → images/<story>/<story>.mp4
```

Each stage is its own script that reads and writes files, so any stage can be re-run alone.

## Design decisions

**Free by default.** Images run on Kaggle's free GPU (~30 h/week), voice and video on the laptop
CPU, drafts on Gemini's free tier. Claude is an opt-in paid alternative for drafting.

**Channels are data, not code.** Everything niche-specific lives in `channels/<name>.json`.
`generate.py` gets each story's look from its channel; `voice.py` its voice; `script.py` its
narrator and rules.

**Human approval gate.** Drafts are written as `"status": "draft"`; only `"approved"` stories are
uploaded to the GPU. Accuracy is the channel's moat, and a person reads every script.

**Faithfulness is checked, not just requested.** With `--passage`, any quoted text must appear
word for word in the fetched passage; quotes must be in quotation marks and recorded in `quote`;
the channel caps how many. Invalid drafts go back to the model with the problems listed.

**Character consistency.** Each person is described once in `characters` and referenced as
`{NAME}` in prompts, so the wording - and so the look - is identical in every scene.
`--continue-from` carries those descriptions across episodes, enforced in code.

**The model is cached once on Kaggle.** `kaggle_cache/cache_model.py` downloads only the SDXL
files we use (~7 GB: fp16 UNet, text encoders, tokenizers, configs + the fp16-fix VAE) as a CPU
job and saves them as output. Every image job attaches that output and runs Hugging Face offline,
so no GPU time goes to downloads. `run_kaggle.py` refuses to start without the cache.

**Pre-reading the weights.** Kaggle mounts attached outputs lazily; loading SDXL via memory-mapped
random reads took ~8 minutes. Reading each file sequentially first takes ~30 s.

**Stories uploaded flat.** Approved stories and `channel__<name>.json` profiles are staged in
`.kaggle_upload/` and uploaded as one flat dataset (dataset uploads don't keep folders).
`generate.py` checks every story's channel exists before loading the model.

**Narration timing drives the video.** Each scene lasts exactly as long as its narration plus a
short pause; subtitles are timed from the same durations, split into short chunks.

**Narration is read from `scenes/`**, not from the copy rendered with the images, so text fixes
never need the GPU. The stages warn when image prompts changed after rendering.

## File map

| Path | What |
|---|---|
| `channels/*.json` | channel profiles |
| `scenes/*.json` | stories (source of truth) |
| `script.py` | stage 0: draft with Gemini/Claude, validate, retry |
| `run_kaggle.py` | stage 1 controller: stage approved stories, upload, run, download |
| `kaggle_sd/generate.py` | runs on Kaggle: SDXL images per scene |
| `kaggle_cache/cache_model.py` | runs on Kaggle once: model cache |
| `voice.py` | stage 2: Kokoro TTS |
| `assemble.py` | stage 3: FFmpeg video |
| `common.py` | shared: `.env`, channels, validation, story loading, ffmpeg helpers |
| `images/`, `models/`, `music/`, `.env`, `.kaggle_upload/` | local only (gitignored) |
