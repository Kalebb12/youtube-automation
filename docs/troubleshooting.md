# Troubleshooting

Every problem below actually happened while building this pipeline.

## Drafting (`script.py`)

**`Gemini API error: 404 ... no longer available`**
The default model was retired. `python script.py --list-models`, then `--model <name>` or
`GEMINI_MODEL=<name>` in `.env`. (2.5-flash was retired for new users in Nov 2026; the default
is now `gemini-3.8-flash`.)

**`Gemini busy (503) - retrying...` then `Gemini is overloaded`**
The free tier is often saturated. `script.py` retries for ~2 minutes. Try another `--model`,
try later, or use `--provider claude`.

**`Claude: no valid credentials`**
Run `ant auth login`, or put `ANTHROPIC_API_KEY=...` in `.env`. OAuth logins expire; log in again.

**`attempt 1: N problem(s): ...` then success**
Normal: the draft failed validation (too long, misquote, unknown character) and was sent back
with the problems listed.

**`is APPROVED - not overwriting it`**
Protection for reviewed stories. Use a new `--id`, or `--force` if you really mean it.

**`quote is not word-for-word from the source passage`**
The model paraphrased inside quotation marks. It's retried automatically; if you edit quotes by
hand, copy them exactly from the translation.

## Images (`run_kaggle.py` / Kaggle)

### Job stuck in `queued`
Kaggle hasn't given the job a GPU. Either the free T4s are busy (try off-peak) or this week's
~30 h GPU quota is used up (kaggle.com → Settings → Accelerator usage). After 10 minutes the
script prints a note. Ctrl+C is safe: the job keeps running on Kaggle; fetch the result later
with `python run_kaggle.py --download`.

**`Job was cancelled`** - cancelled on kaggle.com, or Kaggle dropped it from the queue (seen after
~30 min queued). Nothing was generated and no GPU time was used. Run again.

**`No approved stories to generate`** - every story is still a draft. Review one and set
`"status": "approved"`.

**`These approved stories are invalid`** - fix the listed problems (e.g. `{NOBODY}` used but not
defined in `characters`).

**`Model cache not found`** - run `python run_kaggle.py --setup-model` once (CPU job, no GPU hours).

**No internet inside the Kaggle job** (`Failed to resolve 'pypi.org'`) - verify your phone number
on Kaggle, and if needed open the notebook on kaggle.com → Settings → Internet: On → Save Version.

**`'StableDiffusionXLPipeline' object has no attribute 'enable_vae_slicing'`** - fixed in
`generate.py` (newer diffusers moved it to `pipe.vae.enable_slicing()`).

**Model load takes ~8 minutes** - `/kaggle/input` is fetched lazily; `generate.py` pre-reads the
weights sequentially first (~30 s). Don't remove that block.

**Two people blended into one face / Joseph has a beard** - SDXL mixes described people (FLUX.2
klein much less). Give each image one main subject; make descriptions distinct ("beardless",
"smooth youthful face"); re-roll with a `seed`.

**`truncated because CLIP can only handle sequences up to 77 tokens`** (SDXL) - the prompt was too
long and the end (the channel style) was dropped. `script.py` prevents this for new drafts;
`run_kaggle.py` warns about old ones.

**`prompt + style is N CLIP tokens, limit 75`** - shorten the shot or the character description.

**`Model cache not found ... flux2-klein`** - `python run_kaggle.py --setup-model flux2-klein` once.

**FLUX.2 klein is slow** - the T4 has no native bf16, so it's emulated; 4 steps keep it bearable.
**`WARNING scene_XX.png is black`** - numeric overflow; tell Claude, the dtype may need changing.

**Black images** - fp16 VAE overflow; the pipeline uses `madebyollin/sdxl-vae-fp16-fix` for this.

## Laptop (`voice.py` / `assemble.py`)

**`kaggle: No such file or directory`** - run with the venv active, or call `.venv/bin/python`;
`run_kaggle.py` also finds `.venv/bin/kaggle` on its own.

**`NOTE: ... is still a DRAFT`** - informational; previews work on drafts.

**`NOTE: image prompts for scenes [...] changed since the images were made`** - re-render with
`run_kaggle.py` if you want the images to match.

**`WARNING: Ns over target`** - the narration is longer than the channel's target; trim it.

**`(no music: add tracks to music/<channel>/ ...)`** - the pool is empty; the video is narration
only. `python music.py list` shows what's found where.

**`Music track not found`** - a story's `track` names a file that isn't in `music/<channel>/`,
its mood folder, or `music/`. Fix the name or `python music.py set <story> --clear`.

**Music too loud / too quiet** - change `volume_db` (story or channel), audition with
`python music.py preview <story> --volume-db -22`. Tracks are levelled first, so the same number
works for any track.

**Warping / smeared edges around people** - parallax too strong for that image. Lower it:
`"motion": {"strength": 0.6}` in the story, or `python assemble.py <story> --strength 0.6`, or `--flat`.

**Depth looks wrong after replacing an image** - the cache is keyed on the file's size and time;
delete `images/<story>/depth/` to force a recompute.

**Voice model download stalls** - the model files come from GitHub releases; delete the partial
file in `models/` and run again (or download with `curl -C -` to resume).
