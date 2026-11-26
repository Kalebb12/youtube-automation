# Motion: camera moves, parallax, crossfades

Every scene is a still image, but none of them sits still. `assemble.py` (using `motion.py`) turns
each image into a slow camera move with depth parallax, crossfades between scenes, and fades the
whole video in from black and out to black. It all runs on your laptop CPU - no GPU, no cost.

## What you get

- **Depth parallax ("2.5D").** A small depth model (Depth Anything V2 Small, Apache 2.0, ~100 MB,
  downloaded once into `models/`) works out what is near and far in each image. As the camera
  drifts, near things (the three men) move more than the background (the furnace), so the still
  reads as a real camera move. Depth maps are cached in `images/<story>/depth/`.
- **Eased camera moves.** Each scene gets one of `zoom_in`, `pan_right`, `zoom_out`, `pan_left`, in
  that cycle so no two neighbours match. Moves start and stop gently (smoothstep).
- **Crossfades** between scenes instead of hard cuts. Narration timing is unchanged: the next image
  starts fading in exactly at the scene boundary.
- **Fade in / fade out** of the whole video.

Speed: about 12 s of CPU per 5 s of video, so ~2-3 minutes for a Short (first run adds ~5 s per
image for depth, plus the one-time model download).

## Settings

Channel profile `"motion"` < story `"motion"` < command-line flags. A scene can pin its move.

| Setting | Bible default | Meaning |
|---|---|---|
| `parallax` | `true` | Depth parallax on. `false` = flat Ken Burns. |
| `strength` | `1.0` | Parallax amount. `0.5` subtle, `1.5` strong. Too high shows warping at edges. |
| `zoom` | `0.08` | How far zoom moves travel (8%). |
| `transition` | `"fade"` | Crossfade style - any ffmpeg `xfade` transition: `fade`, `dissolve`, `fadeblack`, `fadewhite`, `smoothleft`, `smoothright`, `circleopen`, `radial`, ... |
| `transition_s` | `0.7` | Crossfade length (s). `0` = hard cuts. |
| `fade_in` | `0.6` | Fade from black at the start (s). |
| `fade_out` | `1.2` | Fade to black at the end (s). |

Horror uses stronger parallax (`1.3`), `fadeblack` transitions and slower fades.

Per scene, in `scenes/<story>.json`:

```json
{"id": 5, "move": "zoom_in", ...}       // zoom_in | zoom_out | pan_left | pan_right
```

Per story:

```json
"motion": {"strength": 1.4, "transition": "dissolve", "transition_s": 1.0}
```

One-off from the command line:

```bash
python assemble.py fiery_furnace --strength 1.4 --transition dissolve --transition-s 1.0
python assemble.py fiery_furnace --flat            # no parallax
```

## Why not AI video (Wan 2.2)?

We checked: Wan2.2's only model in reach (TI2V-5B) needs ~24 GB of GPU memory even with offloading,
against the free Kaggle T4's 16 GB, and takes ~9 minutes per 5-second clip on an RTX 4090 - very
roughly an hour or more per clip on a T4, i.e. most of a week's free GPU quota per video. The 14B
image-to-video model is 126 GB. Depth parallax gives most of the "moving shot" feel for free.
It remains a possible experiment for the hook scene only.
