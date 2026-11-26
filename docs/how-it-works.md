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

**Each image model is cached once on Kaggle.** `kaggle_cache/cache_model.py` downloads only the
files a model needs (FLUX.2 klein 4B ~16 GB; SDXL ~7 GB) as a CPU job and saves them as output -
one cache notebook per model, because Kaggle caps a notebook's output at ~20 GB. Every image job attaches that output and runs Hugging Face offline,
so no GPU time goes to downloads. `run_kaggle.py` refuses to start without the cache.

**Images get as much design as the narration.** The drafting model fills a structured shot per
scene (subject, action, setting, camera, lighting) under per-model rules - see [images](images.md).

**Pre-reading the weights.** Kaggle mounts attached outputs lazily; loading SDXL via memory-mapped
random reads took ~8 minutes. Reading each file sequentially first takes ~30 s.

**Stories uploaded flat.** Approved stories and `channel__<name>.json` profiles are staged in
`.kaggle_upload/` and uploaded as one flat dataset (dataset uploads don't keep folders).
`generate.py` checks every story's channel exists before loading the model.

**Music is layered settings, not a file copy.** Defaults < channel `music` < story `music` < flags.
Tracks are levelled to one loudness before `volume_db` applies, ducked under the narration with a
sidechain compressor, and rotated per story (stable on re-runs).

**Stills that move.** `motion.py` estimates depth per image (small ONNX model on CPU, cached) and
renders an eased camera move where near things shift against the background; `assemble.py`
crossfades the scene clips with ffmpeg `xfade` and fades the ends. Free, no GPU.

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
| `kaggle_sd/generate.py` | runs on Kaggle: images per scene (FLUX.2 klein or SDXL, grouped by model) |
| `clip_tokens.py` | exact CLIP token counting for SDXL's 77-token window |
| `kaggle_cache/cache_model.py` | runs on Kaggle once: model cache |
| `voice.py` | stage 2: Kokoro TTS |
| `assemble.py` | stage 3: FFmpeg video |
| `motion.py` | depth parallax + camera moves, frames piped to ffmpeg |
| `music.py` | music tools (list / preview / set) + the mix used by `assemble.py` |
| `common.py` | shared: `.env`, channels, validation, story loading, ffmpeg helpers |
| `images/`, `models/`, `music/`, `.env`, `.kaggle_upload/` | local only (gitignored) |
