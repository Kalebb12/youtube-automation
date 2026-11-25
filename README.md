# Story video pipeline (free: Gemini free tier + Kaggle GPU + your laptop)

Brief in, narrated YouTube Short out. Works for any niche: each channel is a profile
in `channels/` (`bible.json` and `horror.json` included).

```
python script.py --channel bible --passage "Genesis 37:12-24" --brief "Joseph part 2"   # 0. draft (Gemini)
#   read scenes/<id>.json, fix it, set "status": "approved"                              #    YOU review
python run_kaggle.py                                                                     # 1. images (Kaggle GPU)
python voice.py                                                                          # 2. narration (laptop)
python assemble.py                                                                       # 3. video (laptop)
```

Final video: `images/<story>/<story>.mp4`.

## One-time setup

1. Create an account at kaggle.com.
2. **Verify your phone number**: Settings -> Phone verification. (GPU and internet don't work without this.)
3. Settings -> API -> **Create New Token**. This downloads `kaggle.json`.
4. Put the token where the Kaggle tool looks for it:
   - Windows: `C:\Users\<you>\.kaggle\kaggle.json`
   - Mac/Linux: `~/.kaggle/kaggle.json` then run `chmod 600 ~/.kaggle/kaggle.json`
5. Install everything: `pip install -r requirements.txt` (and `sudo apt install ffmpeg` if you don't have it)
6. Check it works: `kaggle kernels list --mine` (should print without errors)
7. **Cache the model on Kaggle (once):** `python run_kaggle.py --setup-model`
   A CPU-only Kaggle job (no GPU hours) downloads only the SDXL files we use (~7 GB) and saves
   them as its output. ~10-20 min. Never needed again unless you change models.
8. **Gemini key (free):** create one at https://aistudio.google.com/apikey, then
   `export GEMINI_API_KEY=...` (add it to `~/.bashrc` to keep it). Never commit it.

## 0. Draft a story — `script.py`

```
python script.py --channel bible  --passage "Genesis 37:1-11" --brief "Joseph's dreams, part 1"
python script.py --channel horror --brief "a lighthouse keeper hears knocking from below the floor"
python script.py --channel horror --brief "retell this faithfully" --source-file my_tale.txt
```

Gemini writes `scenes/<id>.json` in the channel's narrator voice: hook first, storytelling
narration (not verse-by-verse), one image prompt per scene with `{CHARACTER}` placeholders,
and a `characters` block. Length comes from the channel's `target_duration_s`.

- `--passage` fetches the KJV text from bible-api.com; the draft must retell it faithfully, and
  any quoted words are checked word for word against it (the Bible channel allows one quote).
- Bad drafts (unknown characters, misquotes, too many quotes...) are sent back to Gemini with the
  problems listed, up to 3 tries.
- Options: `--id my_story`, `--scenes 6`, `--model <name>` (`--list-models` shows yours).

**Review step (required):** open the file, check every fact against the source, edit freely,
then change `"status": "draft"` to `"status": "approved"`. `script.py` won't overwrite an approved
story without `--force`.

## 1. Images — `run_kaggle.py`

```
python run_kaggle.py                 # every approved story
python run_kaggle.py joseph_part1    # just these (must be approved)
python run_kaggle.py --download      # re-download the last finished run
```

- Only `"status": "approved"` stories are uploaded; drafts are listed as skipped. With nothing
  approved, nothing is sent to Kaggle. Approved stories are validated first.
- The channel profiles go up with them, so each story gets its channel's look (`style`/`negative`).
- Refuses to start if the model cache doesn't exist (run `--setup-model` first).
- ~1 min to load the model, then ~30 s per image. Images land in `images/<story>/`.

## 2. Narration — `voice.py`  ·  3. Video — `assemble.py`

```
python voice.py [story]              # -> images/<story>/audio/scene_XX.wav
python assemble.py [story]           # -> images/<story>/<story>.mp4
```

- Voice and speed come from the channel profile (`voice.py --voices` lists Kokoro voices;
  `--voice am_michael` to try one). First run downloads the voice model (~350 MB) once.
- Narration is cached and read from `scenes/`, so fixing a typo doesn't need a GPU re-run.
- Each image stays up exactly as long as its narration, with a slow zoom and burned-in subtitles.
- **Music:** put royalty-free tracks in `music/<channel>/` (or `music/`); the first one is mixed
  quietly under the voice. `--music file.mp3` / `--no-music` to override.

## Story file

```json
{
  "story": "joseph_part1", "channel": "bible", "title": "...", "status": "approved",
  "source": {"passage": "Genesis 37", "translation": "KJV"},
  "characters": {"JOSEPH": "a beardless 17-year-old ... robe of many colours"},
  "scenes": [
    {"id": 1, "refs": ["Genesis 37:3"], "narration": "...",
     "quote": {"text": "a coat of many colours", "ref": "Genesis 37:3", "translation": "KJV"},
     "image_prompt": "{JOSEPH} holding up a beautiful new coat ...", "seed": 555}
  ]
}
```

- Describe each person once in `characters`; use `{NAME}` in prompts so they look the same every scene.
  Keep to one main character per image — SDXL blends people together.
- Add `"seed": 555` to a scene to re-roll just that image.
- A story can override its channel's `style`, `negative`, `format`, `voice`, `speed`.
- Direct quotes must be public-domain (KJV/ASV/WEB for Bible), in quotation marks, and listed in `quote`.

## New channel / niche

Copy `channels/horror.json` to `channels/<name>.json` and edit: narrator persona and tone, story
rules, quote policy, image `style` + `negative`, voice, format, target length, music mood.
Then `python script.py --channel <name> --brief "..."`. No code changes needed.
Pick the `style` once and leave it — it's the channel's visual brand.
