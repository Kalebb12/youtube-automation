# Making an episode, start to finish

Worked example: **Joseph and His Brothers, Part 2** (Genesis 37:12-24), continuing Part 1.
Total time: ~2 min drafting + your review + ~3 min GPU + ~5 min laptop.

## 1. Draft the script

```bash
source .venv/bin/activate
python script.py --provider claude --channel bible \
    --passage "Genesis 37:12-24" \
    --continue-from joseph_part1 \
    --brief "Joseph and His Brothers (Part 2): the brothers see him coming, plot to kill him, Reuben intervenes, and they throw him into the pit" \
    --id joseph_part2
```

What happens:

1. The passage is fetched from bible-api.com in the channel's translation (KJV).
2. `--continue-from joseph_part1` loads Part 1: its characters are given to the model to reuse
   **word for word**, and its narration is given as "previously on", so Part 2 doesn't repeat it.
3. The model (Claude Opus 5.5 here; Gemini if you leave out `--provider`) writes JSON matching
   the story schema, in the channel's narrator voice and rules.
4. The draft is checked: placeholders point at real characters, quotes are marked and appear
   word for word in the passage, quote count/translation follow the channel, and the narration
   fits the channel's `target_duration_s`. Problems go back to the model (up to 3 tries).
5. Recurring characters are overwritten with Part 1's exact descriptions, so even a model that
   "improves" Joseph's hair can't break the series.
6. `scenes/joseph_part2.json` is written with `"status": "draft"`.

**Which provider?** Gemini is free but its free tier is often overloaded (503) - `script.py` retries
for ~2 minutes, then gives up. Claude costs about $0.05-0.10 per draft and answers in under a
minute. Put `LLM_PROVIDER=claude` in `.env` to make it the default.

## 2. Review it (required)

Open `scenes/joseph_part2.json` next to the passage. Check:

- [ ] Every event, speaker and detail is in the passage. Nothing invented (motives, dialogue,
      names, numbers, doctrine).
- [ ] The hook (scene 1) is drawn from the story, not a question to the viewer.
- [ ] Anything in quotation marks is exact public-domain text and has a matching `quote` field.
      Exact phrases *without* quotation marks (e.g. "Shed no blood") are your call.
- [ ] Characters reused from the previous part are unchanged; new ones have a purely visual
      description.
- [ ] Each `image_prompt` has one main subject. SDXL blends two described people into one face.
      Prefer "close-up of {BROTHERS}" or "{JOSEPH} alone" over two named characters together.
- [ ] Narration reads well aloud. Kokoro can stumble on archaic words - spell them how they sound
      if needed.

Edit anything you like, then set `"status": "approved"`. Nothing reaches the GPU until you do.

## 3. Render the images (Kaggle GPU)

```bash
python run_kaggle.py joseph_part2      # or no name: every approved story
```

- Uploads the approved story plus the channel profiles, starts the GPU job, polls every 30 s,
  then downloads `images/joseph_part2/scene_01.png ...` and `story.json`.
- Normally ~1 min to start, ~30 s to load the model, ~30 s per image.
- If it sits in `queued` for a long time, see [troubleshooting](troubleshooting.md#job-stuck-in-queued).

Look at the images. To redo one, add `"seed": 555` (any number) to that scene, or adjust its
`image_prompt`, and run again.

## 4. Narration

```bash
python voice.py joseph_part2
```

Speaks each scene with the channel's voice (Bible: `bm_george`, speed 0.95) into
`images/joseph_part2/audio/`. Prints the total length against the channel target. Cached: only
scenes whose text or voice changed are re-spoken.

## 5. Video

```bash
python assemble.py joseph_part2
```

Each image stays up for its narration plus a short pause, with a slow zoom (alternating in/out),
burned-in subtitles placed clear of the Shorts UI, optional music from `music/bible/`, and
YouTube loudness. Output: `images/joseph_part2/joseph_part2.mp4` (1080x1920).

## 6. Watch it before it goes anywhere

Every video gets watched in full before publishing: pronunciation, subtitle timing, any image
that contradicts the narration.

## Fixing things later

| You changed... | Re-run |
|---|---|
| narration text | `voice.py` + `assemble.py` (no GPU) |
| an image prompt, seed, character description | `run_kaggle.py` + `assemble.py` (voice is cached) |
| the voice or speed | `voice.py` + `assemble.py` |
| music | `assemble.py` |

`voice.py` and `assemble.py` warn you if image prompts changed since the images were rendered.

## The next part

```bash
python script.py --provider claude --channel bible --passage "Genesis 37:25-36" \
    --continue-from joseph_part2 --brief "Joseph and His Brothers (Part 3): sold to the Ishmeelites"
```
