# Making an episode, start to finish

Worked example (a real run): **Shadrach, Meshach and Abednego in the fiery furnace**
(Daniel 3:13-28) on the Bible channel.

| Step | Where | Time it took |
|---|---|---|
| 1. Draft | laptop → Claude | 51 s |
| 2. Review + approve | you | - |
| 3. Images (6, FLUX.2 klein) | Kaggle T4 | 10.5 min (~90 s per image) + queue |
| 4. Narration | laptop | ~1.5 min |
| 5. Video | laptop | ~4-5 min |

One-time setup before your first episode: see the [README](../README.md#one-time-setup), including
`python run_kaggle.py --setup-model` (caches the image model on Kaggle).

## 1. Draft the script

```bash
source .venv/bin/activate
python script.py --provider claude --channel bible \
    --passage "Daniel 3:13-28" \
    --brief "Shadrach, Meshach and Abednego refuse to bow to Nebuchadnezzar's golden image, are thrown into the fiery furnace heated seven times hotter, and a fourth figure walks with them in the fire; they come out unburned" \
    --id fiery_furnace
```

What happens:

1. The passage is fetched from bible-api.com in the channel's translation (KJV).
2. The model (Claude Opus 5.5 here; Gemini if you leave out `--provider`) writes the story in the
   channel's narrator voice: hook first, storytelling (not verse-by-verse), and for each scene a
   structured **shot** - subject, action, setting, camera, lighting - planned for the channel's
   image model (see [images](images.md)).
3. The draft is checked: placeholders point at real characters, quotes are marked and appear
   word for word in the passage, the narration fits the channel's `target_duration_s`, and the
   image rules hold (characters per image, nothing a camera can't see, varied cameras, prompt
   length). Problems go back to the model with the exact issue (up to 3 tries).
4. `scenes/fiery_furnace.json` is written with `"status": "draft"`.

**Which provider?** Gemini is free but its free tier is often overloaded (503) - `script.py` retries
for ~2 minutes, then gives up. Claude costs about $0.05-0.10 per draft and answers in under a
minute. Put `LLM_PROVIDER=claude` in `.env` to make it the default.

## 2. Review it (required)

Open `scenes/fiery_furnace.json` next to the passage. Check:

- [ ] Every event, speaker and detail is in the passage. Nothing invented (motives, dialogue,
      names, numbers, doctrine).
- [ ] The hook (scene 1) is drawn from the story, not a question to the viewer.
- [ ] Anything in quotation marks is exact public-domain text and has a matching `quote` field
      (here: *"But if not, be it known unto thee, O king, that we will not serve thy gods"*, 3:18).
- [ ] Characters have purely visual descriptions that are easy to tell apart.
- [ ] Each `image_prompt` shows this scene's key moment, named characters in the foreground,
      extras in the background. Sensitive figures follow the channel rules (here the fourth man in
      the fire is a plain silhouette - no halo).
- [ ] Narration reads well aloud. Kokoro can stumble on archaic words - spell them how they sound
      if needed.

Edit anything you like, then set `"status": "approved"`. Nothing reaches the GPU until you do.

## 3. Render the images (Kaggle GPU)

```bash
python run_kaggle.py fiery_furnace      # or no name: every approved story
```

- Uploads the approved story plus the channel profiles, attaches the cached image model, starts
  the GPU job, polls every 30 s, then downloads `images/fiery_furnace/scene_01.png ...`.
- FLUX.2 klein on the T4: ~90 s to load, ~90 s per image (bf16 is emulated on the T4).
- If it sits in `queued` for a long time, see [troubleshooting](troubleshooting.md#job-stuck-in-queued).

Look at the images. To redo one, add `"seed": 555` (any number) to that scene, or adjust its
`image_prompt`, and run again.

## 4. Narration

```bash
python voice.py fiery_furnace
```

Speaks each scene with the channel's voice (Bible: `bm_george`, speed 0.95) into
`images/fiery_furnace/audio/`. Prints the total length against the channel target. Cached: only
scenes whose text or voice changed are re-spoken.

## 5. Video

```bash
python assemble.py fiery_furnace
```

Each image stays up for its narration plus a short pause, as a slow eased camera move with depth
parallax (see [motion](motion.md)), crossfading into the next scene; the whole video fades in from
and out to black. Subtitles are burned in (balanced chunks, clear of the Shorts UI), music from
`music/bible/` is ducked under the voice, and the mix is levelled to -16 LUFS.
Output: `images/fiery_furnace/fiery_furnace.mp4` (1080x1920, ~55 s, ~22 MB).

To pick the music first, audition it in seconds (see [music](music.md)):

```bash
python music.py preview fiery_furnace --volume-db -16      # → images/fiery_furnace/music_preview.mp3
python music.py set fiery_furnace --volume-db -16          # keep it
```

## 6. Watch it before it goes anywhere

Every video gets watched in full before publishing: pronunciation, subtitle timing, any image
that contradicts the narration.

## Fixing things later

| You changed... | Re-run |
|---|---|
| narration text | `voice.py` + `assemble.py` (no GPU) |
| an image prompt, seed, character description | `run_kaggle.py` + `assemble.py` (voice is cached) |
| the voice or speed | `voice.py` + `assemble.py` |
| motion (parallax, transitions, fades) | `assemble.py` (depth maps are cached) |
| music | `music.py preview` to audition, `music.py set`, then `assemble.py` |

`voice.py` and `assemble.py` warn you if image prompts changed since the images were rendered.

## Series: the next part

For a multi-part story, draft each later part with `--continue-from <previous id>`: the previous
episode's characters are reused word for word (so people look the same across episodes) and its
narration is given as "previously on", so the new part doesn't repeat it.

```bash
python script.py --provider claude --channel bible --passage "Genesis 37:25-36" \
    --continue-from joseph_part2 --brief "Joseph and His Brothers (Part 3): sold to the Ishmeelites"
```
