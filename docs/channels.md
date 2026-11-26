# Channel profiles

A channel profile (`channels/<name>.json`) holds everything that makes a channel *that* channel.
The code has nothing niche-specific in it: a new niche is a new profile, no code changes.

Included: `bible.json` (Bible stories) and `horror.json` ("Midnight Tales", a worked second example).

## Creating a channel

1. Copy the closest profile: `cp channels/horror.json channels/history.json`
2. Edit every field (reference below). Take the most care over `story_rules` and `style`.
3. Draft a test story: `python script.py --channel history --brief "..."`
4. Render it and look hard at the images before settling on `style` - after the first published
   video, treat `style` and `negative` as frozen.

## Field reference

| Field | Example (bible) | What it controls |
|---|---|---|
| `name` | `"bible"` | Id. Must equal the file name. Stories point at it with `"channel"`. |
| `display_name` | `"Bible Stories"` | Shown to the drafting model as the channel's name. |
| `language` | `"en"` | Narration language (informational for now). |
| `narrator.persona` | `"a warm, unhurried storyteller..."` | Who is telling the story. |
| `narrator.tone` | `"reverent but vivid and human; plain modern English..."` | How it sounds. |
| `story_rules` | list of strings | Content rules the draft must follow: faithfulness, rating, how to open, how to end. Sent to the model as-is. |
| `max_quotes` | `1` | How many direct quotes a story may contain. `0` = never quote. Enforced by the validator. |
| `quote_translations` | `["KJV", "ASV", "WEB"]` | Allowed sources for quotes (public domain only). Enforced. |
| `passage_translation` | `"kjv"` | bible-api.com translation id fetched for `--passage`. |
| `image_model` | `"flux2-klein"` | `flux2-klein` (FLUX.2 klein 4B) or `sdxl`. See [images](images.md). |
| `style` | `"biblical illustration, painterly digital art, ..."` | Appended to every image prompt. **The channel's visual brand.** |
| `negative` | `"text, watermark, logo, modern clothing, ..."` | Negative prompt for every image (SDXL only; FLUX.2 klein has none). |
| `image_guidance` | list of strings | Niche rules for the pictures, sent to the drafting model (period accuracy, what to keep unseen, ...). |
| `voice.engine` | `"kokoro"` | TTS engine (only Kokoro so far). |
| `voice.id` | `"bm_george"` | Kokoro voice. `python voice.py --voices` lists them. `a*` = American, `b*` = British. |
| `voice.speed` | `0.95` | Speaking speed. |
| `voice.lang` | `"en-gb"` | Pronunciation (defaults from the voice prefix). |
| `format` | `"short"` | `short` = 9:16 (1080x1920), `long` = 16:9 (1920x1080). |
| `target_duration_s` | `55` | Sets the draft's word budget (~2.3 words/s) and the scene count (~9 s per scene). `voice.py` warns at +15%. |
| `motion` | `{"parallax": true, "strength": 1.0, "transition": "fade", ...}` | Camera moves, parallax, crossfades, fades. See [motion](motion.md). |
| `music_mood` | `"gentle, reverent orchestral..."` | A note to you for picking tracks for `music/<name>/`. |
| `music` | `{"pick": "rotate", "volume_db": -20, "duck": true, ...}` | The channel's house mix: volume, ducking, fades, track rotation. See [music](music.md). |

## Writing good `story_rules`

- Say what to do *and* what not to do: "Retell faithfully... Do not invent events, dialogue,
  motives, names, numbers or doctrine."
- Give the hook rule explicitly ("Open with a hook drawn from the story itself, not a question to
  the viewer").
- Give the ending rule ("No sermon or moral at the end; a cliffhanger is fine for multi-part stories").
- State the rating and the hard lines (PG-13, no real crimes, no copyrighted characters).

## Writing a good `style`

- Medium + lighting + palette + setting: "painterly digital art, warm cinematic lighting, rich
  earthy colours, ancient Middle East setting".
- No subject matter (that's the scene's job) and no artist names.
- Put era/genre leaks in `negative` ("modern clothing, modern buildings" for Bible; "cute, bright
  cheerful colours" for horror).

## Per-story overrides

A story may set `style`, `negative`, `format`, `voice`, `speed` or `music` to override its channel for
that one video. Use sparingly: the point of a channel is consistency.
