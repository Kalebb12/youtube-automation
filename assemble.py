"""
Runs ON YOUR LAPTOP (CPU). Step 3: images + narration -> finished video.

Reads   images/<story>/scene_XX.png, images/<story>/audio/scene_XX.wav (from voice.py)
Writes  images/<story>/<story>.mp4

Each scene is shown for exactly as long as its narration, as a slow eased camera move with
depth parallax (see motion.py), crossfading into the next scene; the video fades in from and
out to black. Subtitles are timed from the narration and burned in. Format comes from the
channel profile (story "format" overrides). Background music comes from music/<channel>/
(see music.py for tracks, moods, volume, ducking, previews).

Usage:
    python assemble.py                      # every story in images/
    python assemble.py joseph_part1         # just one
    python assemble.py joseph_part1 --track calm.mp3 --volume-db -16 --start 10
    python assemble.py --no-music
    python assemble.py --flat               # no depth parallax (plain Ken Burns)
    python assemble.py --transition dissolve --transition-s 1.0
"""
import argparse, glob, os, shutil, sys, textwrap
import motion
import music as bgm
from common import SCENE_LEAD_IN as LEAD_IN, SCENE_TAIL as TAIL
from common import duration, load_story, run, story_dirs, story_setting

# ---------- Look (keep fixed so every video matches) ----------
FPS = 30
SIZES = {"short": (1080, 1920), "long": (1920, 1080)}
SUB_FONT = "DejaVu Serif"
WORDS_PER_CAPTION = {"short": 6, "long": 10}


# ---------- Subtitles ----------
def ass_time(t):
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def captions(text, start, length, words_per):
    """Split narration into short chunks, timed by their share of the characters."""
    words = text.split()
    # even chunks (13 words at 6 per caption -> 5+4+4, never a lone trailing word)
    n = max(1, -(-len(words) // words_per))
    size, extra = divmod(len(words), n)
    chunks, i = [], 0
    for k in range(n):
        step = size + (1 if k < extra else 0)
        chunks.append(" ".join(words[i:i + step]))
        i += step
    total = sum(len(c) for c in chunks) or 1
    t, out = start, []
    for c in chunks:
        d = length * len(c) / total
        out.append((t, t + d, c))
        t += d
    return out


def write_ass(path, events, w, h, fmt):
    size = int(h * (0.036 if fmt == "short" else 0.05))
    margin_v = int(h * (0.16 if fmt == "short" else 0.07))   # clear of the Shorts UI
    head = textwrap.dedent(f"""\
        [Script Info]
        ScriptType: v4.00+
        PlayResX: {w}
        PlayResY: {h}
        WrapStyle: 0

        [V4+ Styles]
        Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
        Style: Default,{SUB_FONT},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,{max(2, size // 14)},{max(1, size // 30)},2,{int(w * 0.08)},{int(w * 0.08)},{margin_v},1

        [Events]
        Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
        """)
    with open(path, "w") as f:
        f.write(head)
        for a, b, t in events:
            f.write(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Default,,0,0,0,,{{\\fad(120,120)}}{t}\n")


# ---------- Video ----------
def assemble_story(story_dir, music_overrides=None, use_music=True, motion_overrides=None):
    story, channel = load_story(story_dir)
    sid = os.path.basename(story_dir)
    fmt = story_setting(story, channel, "format", "short")
    mov = motion.resolve(story, channel)
    mov.update({k: v for k, v in (motion_overrides or {}).items() if v is not None})
    mix = bgm.resolve(story, channel, music_overrides)
    track = bgm.pick_track(mix, channel["name"], story["story"]) if use_music else None
    w, h = SIZES[fmt]
    work = os.path.join(story_dir, ".work")
    os.makedirs(work, exist_ok=True)

    print(f"\n=== {sid} [{channel['name']}]: {len(story['scenes'])} scenes, {fmt} {w}x{h} ===")
    print(f"  motion: parallax {'on' if mov['parallax'] else 'off'} (strength {mov['strength']}), "
          f"{mov['transition']} {mov['transition_s']}s, fade in {mov['fade_in']}s / out {mov['fade_out']}s")
    if track:
        print(f"  music: {bgm.describe(mix, track)}")
    elif use_music and mix.get("track") != "none":
        print(f"  (no music: add tracks to music/{channel['name']}/"
              + (f" - mood: {channel['music_mood']})" if channel.get("music_mood") else ")"))

    scenes = story["scenes"]
    n = len(scenes)
    T = max(0.0, float(mov["transition_s"])) if n > 1 else 0.0
    lengths, events, t = [], [], 0.0
    for s in scenes:
        img = os.path.join(story_dir, f"scene_{s['id']:02d}.png")
        wav = os.path.join(story_dir, "audio", f"scene_{s['id']:02d}.wav")
        if not os.path.exists(img):
            sys.exit(f"Missing image {img} - run python run_kaggle.py first")
        if not os.path.exists(wav):
            sys.exit(f"Missing narration {wav} - run python voice.py {sid} first")
        speech = duration(wav)
        lengths.append(LEAD_IN + speech + TAIL)
        events += captions(s["narration"], t + LEAD_IN, speech, WORDS_PER_CAPTION[fmt])
        t += lengths[-1]
    total = t

    # 1. one silent moving clip per scene; all but the last run T longer to overlap the crossfade
    clips = []
    for i, (s, length) in enumerate(zip(scenes, lengths)):
        img = os.path.join(story_dir, f"scene_{s['id']:02d}.png")
        depth = None
        if mov["parallax"]:
            depth = motion.depth_map(img, os.path.join(story_dir, "depth", f"scene_{s['id']:02d}.npy"))
        move = motion.move_for(s, i)
        clip = os.path.join(work, f"scene_{s['id']:02d}.mp4")
        motion.render_clip(img, depth, clip, w, h, length + (T if i < n - 1 else 0), FPS, move, mov)
        clips.append(clip)
        print(f"  scene {s['id']:02d}: {length:4.1f}s  {move}", flush=True)

    # 2. narration on the same timeline (identical gaps), as one track
    voice = os.path.join(work, "voice.wav")
    bgm.narration_track(story_dir, story, voice)

    # 3. crossfades -> fades -> subtitles, + narration (+ music), one final encode
    ass = os.path.join(work, "subs.ass")
    write_ass(ass, events, w, h, fmt)
    cmd = ["ffmpeg", "-y"]
    for c in clips:
        cmd += ["-i", c]
    cmd += ["-i", voice]
    vi = len(clips)                                   # input index of the narration
    graph, last, offset = [], "[0:v]", 0.0
    for i in range(1, n):
        offset += lengths[i - 1]
        graph.append(f"{last}[{i}:v]xfade=transition={mov['transition']}:duration={T:.3f}:"
                     f"offset={offset:.3f}[x{i}]")
        last = f"[x{i}]"
    fo = min(float(mov["fade_out"]), total / 3)
    graph.append(f"{last}settb=AVTB,fps={FPS},fade=t=in:st=0:d={float(mov['fade_in']):.2f},"
                 f"fade=t=out:st={total - fo:.3f}:d={fo:.2f},subtitles='{ass}',format=yuv420p[v]")
    if track:
        inputs, chain = bgm.mix_args(mix, track, total, f"[{vi}:a]", vi + 1)
        cmd += inputs
        graph.append(chain)
    else:
        graph.append(f"[{vi}:a]loudnorm=I=-16:TP=-1.5[a]")
    out = os.path.join(story_dir, f"{sid}.mp4")
    cmd += ["-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", out]
    run(cmd)
    shutil.rmtree(work, ignore_errors=True)
    print(f"  -> {out}  ({total:.1f}s)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stories", nargs="*", help="story names (default: all in images/)")
    bgm.add_mix_flags(ap)
    ap.add_argument("--music", dest="track", help=argparse.SUPPRESS)   # old name for --track
    ap.add_argument("--no-music", action="store_true", help="no background music")
    ap.add_argument("--flat", action="store_true", help="no depth parallax (plain Ken Burns)")
    ap.add_argument("--strength", type=float, help="parallax strength (1.0 default)")
    ap.add_argument("--transition", help="crossfade style: fade, dissolve, fadeblack, smoothleft, circleopen, ...")
    ap.add_argument("--transition-s", type=float, help="crossfade length (s)")
    args = ap.parse_args()

    mo = {"parallax": False if args.flat else None, "strength": args.strength,
          "transition": args.transition, "transition_s": args.transition_s}
    for d in story_dirs(args.stories):
        assemble_story(d, bgm.overrides_from(args), use_music=not args.no_music, motion_overrides=mo)
