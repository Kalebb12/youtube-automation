"""
Runs ON YOUR LAPTOP (CPU). Step 2: speaks each scene's narration with Kokoro TTS.

Reads   scenes/<story>.json (falls back to images/<story>/story.json)
Writes  images/<story>/audio/scene_XX.wav

Usage:
    python voice.py                     # every story in images/
    python voice.py joseph_part1        # just one
    python voice.py --voice am_michael  # override the voice
    python voice.py --voices            # list voices

Cached: a scene is only re-spoken when its text, voice or speed changes.
Story options: "voice": "bm_george", "speed": 0.95
"""
import argparse, hashlib, os, sys, urllib.request
from common import HERE, duration, load_story, story_dirs

MODELS_DIR = os.path.join(HERE, "models")
MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
MODEL_FILES = ["kokoro-v1.0.onnx", "voices-v1.0.bin"]
DEFAULT_VOICE, DEFAULT_SPEED = "bm_george", 0.95   # calm British male storyteller

_tts = None
def tts():
    global _tts
    if _tts is None:
        os.makedirs(MODELS_DIR, exist_ok=True)
        for f in MODEL_FILES:
            p = os.path.join(MODELS_DIR, f)
            if not os.path.exists(p):
                print(f"Downloading {f} (one time, ~350 MB total)...")
                urllib.request.urlretrieve(MODEL_URL + f, p + ".part")
                os.replace(p + ".part", p)
        from kokoro_onnx import Kokoro
        _tts = Kokoro(*(os.path.join(MODELS_DIR, f) for f in MODEL_FILES))
    return _tts


def speak(text, voice, speed, out_wav):
    """Returns True if it (re)generated the file, False if the cached one was reused."""
    key = hashlib.sha1(f"{voice}|{speed}|{text}".encode()).hexdigest()[:12]
    stamp = out_wav + ".key"
    if os.path.exists(out_wav) and os.path.exists(stamp) and open(stamp).read() == key:
        return False
    import soundfile as sf
    lang = "en-gb" if voice.startswith("b") else "en-us"
    samples, rate = tts().create(text, voice=voice, speed=speed, lang=lang)
    sf.write(out_wav, samples, rate)
    open(stamp, "w").write(key)
    return True


def voice_story(story_dir, voice_override=None):
    story = load_story(story_dir)
    voice = voice_override or story.get("voice", DEFAULT_VOICE)
    speed = float(story.get("speed", DEFAULT_SPEED))
    audio_dir = os.path.join(story_dir, "audio")
    os.makedirs(audio_dir, exist_ok=True)
    print(f"\n=== {os.path.basename(story_dir)}: voice {voice}, speed {speed} ===")
    for s in story["scenes"]:
        wav = os.path.join(audio_dir, f"scene_{s['id']:02d}.wav")
        made = speak(s["narration"], voice, speed, wav)
        print(f"  scene {s['id']:02d}: {duration(wav):4.1f}s {'(new)' if made else '(cached)'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stories", nargs="*", help="story names (default: all in images/)")
    ap.add_argument("--voice", help=f"Kokoro voice (default {DEFAULT_VOICE}, or the story's \"voice\")")
    ap.add_argument("--voices", action="store_true", help="list available voices and exit")
    args = ap.parse_args()
    if args.voices:
        print(", ".join(sorted(tts().get_voices())))
        sys.exit(0)
    for d in story_dirs(args.stories):
        voice_story(d, args.voice)
