"""
Runs ON YOUR LAPTOP (CPU). Step 3: images + narration -> finished video.

Reads   images/<story>/scene_XX.png, images/<story>/audio/scene_XX.wav (from voice.py)
Writes  images/<story>/<story>.mp4

Each scene is shown for exactly as long as its narration, with a slow zoom
(alternating in/out). Subtitles are timed from the narration lengths and burned in.
Format comes from the channel profile (story "format" overrides). Background music,
if any, is taken from music/<channel>/ first, then music/, and mixed quietly under the voice.

Usage:
    python assemble.py                      # every story in images/
    python assemble.py joseph_part1         # just one
    python assemble.py --music music/calm.mp3
    python assemble.py --no-music
"""
import argparse, glob, os, shutil, sys, textwrap
from common import HERE, duration, load_story, run, story_dirs, story_setting

MUSIC_DIR = os.path.join(HERE, "music")

# ---------- Look (keep fixed so every video matches) ----------
FPS = 30
SIZES = {"short": (1080, 1920), "long": (1920, 1080)}
LEAD_IN, TAIL = 0.35, 0.65        # silence before/after each scene's narration (s)
ZOOM = 0.10                       # how far the slow zoom travels (10%)
MUSIC_VOLUME = 0.12               # background music level under the voice
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
    chunks = [" ".join(words[i:i + words_per]) for i in range(0, len(words), words_per)]
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
def scene_clip(img, wav, length, w, h, idx, out):
    """Image -> clip of `length` seconds with a slow zoom, narration on top."""
    frames = max(1, round(length * FPS))
    big_w, big_h = w * 2, h * 2       # upscale first so zoompan's pixel steps don't jitter
    z = f"1+{ZOOM}*on/{frames}" if idx % 2 == 0 else f"{1 + ZOOM}-{ZOOM}*on/{frames}"
    vf = (f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase,crop={big_w}:{big_h},"
          f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={FPS},"
          f"format=yuv420p")
    af = f"adelay={int(LEAD_IN * 1000)}:all=1,apad,atrim=0:{length:.3f}"
    run(["ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-i", img, "-i", wav,
         "-vf", vf, "-af", af, "-t", f"{length:.3f}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-r", str(FPS),
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", out])


def pick_music(channel_name):
    """First audio file in music/<channel>/, else in music/, else None."""
    for d in (os.path.join(MUSIC_DIR, channel_name), MUSIC_DIR):
        files = sorted(f for f in glob.glob(os.path.join(d, "*"))
                       if f.lower().endswith((".mp3", ".wav", ".m4a", ".ogg")))
        if files:
            return files[0]
    return None


def assemble_story(story_dir, music, use_music=True):
    story, channel = load_story(story_dir)
    sid = os.path.basename(story_dir)
    fmt = story_setting(story, channel, "format", "short")
    if use_music and not music:
        music = pick_music(channel["name"])
        if not music and channel.get("music_mood"):
            print(f"  (no music: add a track to music/{channel['name']}/ - mood: {channel['music_mood']})")
    if music:
        print(f"  music: {music}")
    w, h = SIZES[fmt]
    work = os.path.join(story_dir, ".work")
    os.makedirs(work, exist_ok=True)

    print(f"\n=== {sid} [{channel['name']}]: {len(story['scenes'])} scenes, {fmt} {w}x{h} ===")
    clips, events, t = [], [], 0.0
    for i, s in enumerate(story["scenes"]):
        img = os.path.join(story_dir, f"scene_{s['id']:02d}.png")
        wav = os.path.join(story_dir, "audio", f"scene_{s['id']:02d}.wav")
        if not os.path.exists(img):
            sys.exit(f"Missing image {img} - run python run_kaggle.py first")
        if not os.path.exists(wav):
            sys.exit(f"Missing narration {wav} - run python voice.py {sid} first")
        speech = duration(wav)
        length = LEAD_IN + speech + TAIL
        clip = os.path.join(work, f"scene_{s['id']:02d}.mp4")
        scene_clip(img, wav, length, w, h, i, clip)
        clips.append(clip)
        events += captions(s["narration"], t + LEAD_IN, speech, WORDS_PER_CAPTION[fmt])
        t += length
        print(f"  scene {s['id']:02d}: {length:4.1f}s")

    listfile = os.path.join(work, "list.txt")
    with open(listfile, "w") as f:
        f.writelines(f"file '{c}'\n" for c in clips)
    joined = os.path.join(work, "joined.mp4")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listfile, "-c", "copy", joined])

    ass = os.path.join(work, "subs.ass")
    write_ass(ass, events, w, h, fmt)
    out = os.path.join(story_dir, f"{sid}.mp4")
    sub_filter = f"subtitles='{ass}'"
    cmd = ["ffmpeg", "-y", "-i", joined]
    if music:
        cmd += ["-stream_loop", "-1", "-i", music,
                "-filter_complex",
                f"[0:v]{sub_filter}[v];"
                f"[1:a]volume={MUSIC_VOLUME},afade=t=out:st={max(0, t - 2):.2f}:d=2[m];"
                f"[0:a][m]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5[a]",
                "-map", "[v]", "-map", "[a]"]
    else:
        cmd += ["-vf", sub_filter, "-af", "loudnorm=I=-16:TP=-1.5"]
    cmd += ["-t", f"{t:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", out]
    run(cmd)
    shutil.rmtree(work, ignore_errors=True)
    print(f"  -> {out}  ({t:.1f}s)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stories", nargs="*", help="story names (default: all in images/)")
    ap.add_argument("--music", help="background music file (default: music/<channel>/ then music/)")
    ap.add_argument("--no-music", action="store_true")
    args = ap.parse_args()

    for d in story_dirs(args.stories):
        assemble_story(d, args.music, use_music=not args.no_music)
