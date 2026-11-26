# Background music

Music is optional. With no tracks, videos are narration only. Everything below is done by
`music.py` (tools) and `assemble.py` (the final mix).

## 1. Add tracks

Royalty-free only: the channels are monetized. Good free sources: YouTube Audio Library
(Studio → Audio Library; check each track's licence and attribution rule), Pixabay Music.

```
music/
  bible/                    the Bible channel's pool
    calm_strings.mp3
    soft_piano.mp3
    tense/                  optional mood folder
      low_drums.mp3
  horror/
    drone_01.mp3
  shared_fallback.mp3       used by any channel with an empty pool
```

`music/` is gitignored (big binary files). Check what's there:

```bash
python music.py list            # every channel
python music.py list bible
```

## 2. How a track is chosen

1. A story's explicit `track` (a file name in the pool, or a path) - or `"none"` for silence.
2. Otherwise the pool: `music/<channel>/<mood>/` if a `mood` is set, else `music/<channel>/`,
   else `music/`.
3. `pick: "rotate"` (default) gives each story a different track from the pool, and the *same*
   one every time that story is rebuilt. `pick: "first"` always uses the first file.

## 3. How it's mixed

Each track is first levelled to a fixed loudness, so `volume_db` means the same for every track,
however loud the original file is. Then:

| Setting | Default | Meaning |
|---|---|---|
| `volume_db` | `-20` | Music level under the voice. `-24` = barely there, `-12` = prominent. |
| `duck` | `true` | Music dips while the narrator speaks (~9 dB) and swells back in the pauses. |
| `start` | `0` | Seconds into the track to start - skip a slow intro. |
| `fade_in` / `fade_out` | `1.5` / `2.5` | Seconds. |
| `mood` | - | Pick from `music/<channel>/<mood>/`. |
| `pick` | `rotate` | `rotate` or `first`. |
| `track` | - | A specific file, or `"none"`. |

Tracks shorter than the video loop. The final mix is normalised to -16 LUFS (YouTube's level).

## 4. Where settings live

Each level overrides the one before:

```
built-in defaults  <  channel profile "music"  <  story "music"  <  command-line flags
```

**Channel profile** (`channels/bible.json`) - the channel's house mix:

```json
"music": {"pick": "rotate", "volume_db": -20, "fade_in": 2, "fade_out": 3, "duck": true}
```

**Story** (`scenes/<story>.json`) - one episode's choice, e.g. a darker track for the pit scene:

```json
"music": {"mood": "tense", "volume_db": -17, "start": 12}
```

**Command line** - one-off, not saved:

```bash
python assemble.py fiery_furnace --track soft_piano.mp3 --volume-db -16 --start 10 --no-duck
python assemble.py fiery_furnace --no-music
```

## 5. Audition quickly, then save

A full video rebuild takes ~4-5 minutes. A preview takes ~20 seconds: it mixes the story's
narration (with the video's exact timing) and the music into an mp3, no video.

```bash
python music.py preview fiery_furnace                                  # current settings
python music.py preview fiery_furnace --track soft_piano.mp3 --volume-db -16 --start 10
python music.py preview fiery_furnace --mood tense --seconds 20        # just the first 20 s
# listen to images/fiery_furnace/music_preview.mp3

python music.py set fiery_furnace --track soft_piano.mp3 --volume-db -16 --start 10   # save it
python assemble.py fiery_furnace                                       # rebuild the video
```

Other `set` options: `--none` (this story has no music), `--clear` (back to channel defaults).
Story music settings are validated: unknown keys or wrong types are reported by `run_kaggle.py`
and `script.py` like any other story problem.
