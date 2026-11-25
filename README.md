# Bible story pipeline — Step 1: images on Kaggle (free GPU)

## One-time setup (about 10 minutes)

1. Create an account at kaggle.com.
2. **Verify your phone number**: Settings -> Phone verification. (GPU and internet don't work without this.)
3. Settings -> API -> **Create New Token**. This downloads `kaggle.json`.
4. Put the token where the Kaggle tool looks for it:
   - Windows: `C:\Users\<you>\.kaggle\kaggle.json`
   - Mac/Linux: `~/.kaggle/kaggle.json` then run `chmod 600 ~/.kaggle/kaggle.json`
5. Install the Kaggle tool: `pip install -r requirements.txt`
6. Check it works: `kaggle kernels list --mine` (should print without errors)
7. **Cache the model on Kaggle (once):** `python run_kaggle.py --setup-model`
   This runs `kaggle_cache/cache_model.py` as a CPU-only Kaggle job (no GPU hours used). It downloads
   only the SDXL files we use (~7 GB, fp16 weights + the fp16-fix VAE, no duplicate copies) and saves
   them as that job's output. Takes ~10-20 min. You never need to repeat it unless you change models.

## Run it

From this folder:

```
python run_kaggle.py
```

It refuses to start if the model cache doesn't exist yet, so no GPU time is wasted - run `--setup-model` first.

What happens:
1. Every story in `scenes/` is uploaded to a private Kaggle dataset.
2. The GPU job (`kaggle_sd/generate.py`) starts on Kaggle.
3. The script checks every 30 s until it finishes (~1-2 min to load the cached model, then ~30 s per image).
4. Images download to `images/<story>/scene_01.png ...`, with `story.json` (the narration) next to them.

Kaggle shuts the machine down when the job ends - GPU hours only count while it runs.

The image job attaches the cache automatically and runs Hugging Face in offline mode, so it never downloads the model.
If the cache is somehow missing inside the job, it prints a WARNING and falls back to downloading.

If the job finished but the download failed: `python run_kaggle.py --download`

## Adding a story

Copy `scenes/joseph_part1.json`, rename it, and edit it.
- `characters`: describe each person once; use `{NAME}` in prompts so the wording is identical every scene.
- `format`: `"short"` (vertical 9:16) or `"long"` (horizontal 16:9).
- Add `"seed": 555` to a scene to re-roll just that image.
- Put several story files in `scenes/` to batch them in one GPU run.

## Changing the look

Edit `STYLE` at the top of `kaggle_sd/generate.py` once, then leave it alone so the channel stays consistent.

---

# Step 2 & 3: narration and video (on your laptop)

```
python voice.py                  # narration for every story in images/
python assemble.py               # -> images/<story>/<story>.mp4
python voice.py joseph_part1     # (or just one story)
```

`voice.py` speaks each scene's `narration` with Kokoro TTS (CPU) into `images/<story>/audio/`.
`assemble.py` shows each image for exactly that long with a slow zoom, burns in subtitles, and
writes `images/<story>/<story>.mp4` (1080x1920 for `"short"`, 1920x1080 for `"long"`).

- First run downloads the voice model (~350 MB) into `models/` - once.
- Narration is cached; only scenes whose text or voice changed are re-spoken.
- Narration is read from `scenes/<story>.json`, so fixing a typo doesn't need a GPU re-run.
- **Voice:** default `bm_george` (calm British male). `python voice.py --voices` lists others;
  `--voice am_michael` to try one, or set `"voice"` / `"speed"` in the story JSON.
- **Music:** drop an mp3 in `music/` and it's mixed quietly under the voice automatically
  (or `--music file.mp3`, `--no-music`). Royalty-free only (e.g. YouTube Audio Library).
- Narration must be exact KJV/ASV/WEB text (see CLAUDE.md).

Full flow for a new story: `scenes/<story>.json` -> `python run_kaggle.py` -> `python voice.py` -> `python assemble.py`.
