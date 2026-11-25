"""
Runs ON YOUR LAPTOP (CPU only). Step 2: turns images/<story>/ into a finished video.

For every scene it:
  1. speaks the scene's "narration" with Kokoro TTS  -> audio/<story>/scene_XX.wav
  2. shows the scene image for as long as the narration, with a slow zoom/pan
  3. burns in subtitles that follow the narration
Then joins all scenes (plus optional background music) into videos/<story>.mp4

Usage:
    python make_video.py                    # every story in images/
    python make_video.py joseph_part1       # just one story
    python make_video.py --voice am_michael # try another voice (list: --voices)
    python make_video.py --music music/calm.mp3

Narration audio is cached: only scenes whose text/voice changed are re-spoken.
Story options (in the scene JSON):  "voice": "bm_george",  "speed": 0.95
"""
import argparse, glob, hashlib, json, os, shutil, subprocess, sys, textwrap, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGES_DIR = os.path.join(HERE, "images")
AUDIO_DIR = os.path.join(HERE, "audio")
VIDEOS_DIR = os.path.join(HERE, "videos")
MODELS_DIR = os.path.join(HERE, "models")
MUSIC_DIR = os.path.join(HERE, "music")
MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
MODEL_FILES = ["kokoro-v1.0.onnx", "voices-v1.0.bin"]

# ---------- Look (keep fixed so every video matches) ----------
DEFAULT_VOICE, DEFAULT_SPEED = "bm_george", 0.95   # calm British male storyteller
FPS = 30
SIZES = {"short": (1080, 1920), "long": (1920, 1080)}
LEAD_IN, TAIL = 0.35, 0.65        # silence before/after each scene's narration (s)
ZOOM = 0.10                       # how far the slow zoom travels (10%)
MUSIC_VOLUME = 0.12               # background music level under the voice
SUB_FONT = "DejaVu Serif"
WORDS_PER_CAPTION = {"short": 6, "long": 10}


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ffmpeg failed:\n{' '.join(cmd)}\n\n{r.stderr[-3000:]}")
    return r.stdout


def duration(path):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "default=nw=1:nk=1", path]).strip())


# ---------- TTS ----------
def ensure_models():
    os.makedirs(MODELS_DIR, exist_ok=True)
    for f in MODEL_FILES:
        p = os.path.join(MODELS_DIR, f)
        if not os.path.exists(p):
            print(f"Downloading {f} (one time)...")
            urllib.request.urlretrieve(MODEL_URL + f, p + ".part")
            os.replace(p + ".part", p)


_tts = None
def tts():
    global _tts
    if _tts is None:
        ensure_models()
        from kokoro_onnx import Kokoro
        _tts = Kokoro(os.path.join(MODELS_DIR, MODEL_FILES[0]), os.path.join(MODELS_DIR, MODEL_FILES[1]))
    return _tts


def speak(text, voice, speed, out_wav):
    """Write narration to out_wav unless an identical one is cached."""
    key = hashlib.sha1(f"{voice}|{speed}|{text}".encode()).hexdigest()[:12]
    stamp = out_wav + ".key"
    if os.path.exists(out_wav) and os.path.exists(stamp) and open(stamp).read() == key:
        return
    import soundfile as sf
    samples, rate = tts().create(text, voice=voice, speed=speed, lang="en-gb" if voice.startswith("b") else "en-us")
    sf.write(out_wav, samples, rate)
    open(stamp, "w").write(key)


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
    margin_v = int(h * (0.16 if fmt == "short" else 0.07))
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
    """Image -> clip of `length` seconds with a slow zoom (alternating in/out), narration on top."""
    frames = max(1, round(length * FPS))
    # Scale up 2x first so zoompan's integer-pixel steps don't look jittery
    big_w, big_h = w * 2, h * 2
    z = f"1+{ZOOM}*on/{frames}" if idx % 2 == 0 else f"{1 + ZOOM}-{ZOOM}*on/{frames}"
    vf = (f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase,crop={big_w}:{big_h},"
          f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={FPS},"
          f"format=yuv420p")
    af = f"adelay={int(LEAD_IN * 1000)}:all=1,apad,atrim=0:{length:.3f}"
    run(["ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-i", img, "-i", wav,
         "-vf", vf, "-af", af, "-t", f"{length:.3f}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-r", str(FPS),
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", out])


def build_story(story_dir, voice_override, music):
    story = json.load(open(os.path.join(story_dir, "story.json")))
    sid = os.path.basename(story_dir)
    fmt = story.get("format", "short")
    w, h = SIZES[fmt]
    voice = voice_override or story.get("voice", DEFAULT_VOICE)
    speed = float(story.get("speed", DEFAULT_SPEED))
    audio_dir = os.path.join(AUDIO_DIR, sid)
    work = os.path.join(VIDEOS_DIR, f".{sid}_work")
    os.makedirs(audio_dir, exist_ok=True)
    os.makedirs(work, exist_ok=True)

    print(f"\n=== {sid}: {len(story['scenes'])} scenes, {fmt} {w}x{h}, voice {voice} ===")
    clips, events, t = [], [], 0.0
    for i, s in enumerate(story["scenes"]):
        img = os.path.join(story_dir, f"scene_{s['id']:02d}.png")
        if not os.path.exists(img):
            sys.exit(f"Missing image {img} - run python run_kaggle.py first")
        wav = os.path.join(audio_dir, f"scene_{s['id']:02d}.wav")
        speak(s["narration"], voice, speed, wav)
        speech = duration(wav)
        length = LEAD_IN + speech + TAIL
        clip = os.path.join(work, f"scene_{s['id']:02d}.mp4")
        scene_clip(img, wav, length, w, h, i, clip)
        clips.append(clip)
        events += captions(s["narration"], t + LEAD_IN, speech, WORDS_PER_CAPTION[fmt])
        t += length
        print(f"  scene {s['id']:02d}: {speech:4.1f}s narration")

    # Join scenes, then burn subtitles (+ music) in one final encode
    listfile = os.path.join(work, "list.txt")
    with open(listfile, "w") as f:
        f.writelines(f"file '{c}'\n" for c in clips)
    joined = os.path.join(work, "joined.mp4")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listfile, "-c", "copy", joined])

    ass = os.path.join(work, "subs.ass")
    write_ass(ass, events, w, h, fmt)
    out = os.path.join(VIDEOS_DIR, f"{sid}.mp4")
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
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stories", nargs="*", help="story names (default: all in images/)")
    ap.add_argument("--voice", help=f"Kokoro voice (default {DEFAULT_VOICE}, or the story's \"voice\")")
    ap.add_argument("--music", help="background music file (default: first file in music/, if any)")
    ap.add_argument("--no-music", action="store_true")
    ap.add_argument("--voices", action="store_true", help="list available voices and exit")
    args = ap.parse_args()

    if args.voices:
        print(", ".join(sorted(tts().get_voices())))
        sys.exit(0)

    music = None
    if not args.no_music:
        music = args.music or next(iter(sorted(
            f for f in glob.glob(os.path.join(MUSIC_DIR, "*"))
            if f.lower().endswith((".mp3", ".wav", ".m4a", ".ogg")))), None)
        if music:
            print(f"Background music: {music}")

    dirs = sorted(os.path.dirname(p) for p in glob.glob(os.path.join(IMAGES_DIR, "*", "story.json")))
    if args.stories:
        dirs = [d for d in dirs if os.path.basename(d) in args.stories]
    if not dirs:
        sys.exit("No stories found in images/ - run python run_kaggle.py first")
    os.makedirs(VIDEOS_DIR, exist_ok=True)
    for d in dirs:
        build_story(d, args.voice, music)
