"""
Runs ON YOUR LAPTOP. Background music: choosing tracks, mixing settings, quick previews.
assemble.py uses this module for the final mix; you use it to audition and save settings.

Usage:
    python music.py list [channel]                    # tracks per channel / mood, with lengths
    python music.py preview joseph_part2              # audio-only preview in seconds -> images/<story>/music_preview.mp3
    python music.py preview joseph_part2 --track calm_strings.mp3 --volume-db -16 --start 12
    python music.py set joseph_part2 --track calm_strings.mp3 --volume-db -16   # save into scenes/<story>.json
    python music.py set joseph_part2 --none           # this story has no music
    python music.py set joseph_part2 --clear          # back to channel defaults

Where tracks live (royalty-free only):
    music/<channel>/*.mp3           the channel's pool
    music/<channel>/<mood>/*.mp3    optional mood folders, picked with "mood": "<mood>"
    music/*.mp3                     fallback for every channel

Settings, each level overriding the one before:
    built-in defaults  <  channel profile "music"  <  story "music"  <  command-line flags

    track      file name in the pool (or a path), or "none" for silence
    mood       sub-folder of music/<channel>/ to pick from
    pick       "rotate" (a different track per story, stable across re-runs) or "first"
    volume_db  music level under the voice, after levelling every track to the same loudness
    start      seconds into the track to start (skip a slow intro)
    fade_in    seconds
    fade_out   seconds
    duck       true = music dips automatically while the narrator speaks
"""
import argparse, glob, hashlib, json, os, sys
from common import (HERE, SCENES_DIR, SCENE_LEAD_IN, SCENE_TAIL, duration,
                    load_channel, load_story, run, story_dirs)

MUSIC_DIR = os.path.join(HERE, "music")
AUDIO_EXT = (".mp3", ".wav", ".m4a", ".ogg", ".flac")
DEFAULTS = {"track": None, "mood": None, "pick": "rotate", "volume_db": -20.0, "start": 0.0,
            "fade_in": 1.5, "fade_out": 2.5, "duck": True}
KEYS = tuple(DEFAULTS)
VOICE_LUFS = -16          # final mix loudness (YouTube-friendly)


# ---------- Settings ----------
def resolve(story, channel, overrides=None):
    """Merge defaults < channel "music" < story "music" < overrides (None values ignored)."""
    s = dict(DEFAULTS)
    for layer in (channel.get("music"), story.get("music"), overrides):
        if layer:
            s.update({k: v for k, v in layer.items() if k in KEYS and v is not None})
    return s


def pool(channel_name, mood=None):
    """Tracks to choose from: music/<channel>/<mood>/, else music/<channel>/, else music/."""
    dirs = []
    if mood:
        dirs.append(os.path.join(MUSIC_DIR, channel_name, mood))
    dirs += [os.path.join(MUSIC_DIR, channel_name), MUSIC_DIR]
    for d in dirs:
        files = sorted(f for f in glob.glob(os.path.join(d, "*")) if f.lower().endswith(AUDIO_EXT))
        if files:
            return files
    return []


def pick_track(settings, channel_name, story_id):
    """Path of the track to use, or None for no music."""
    track = settings.get("track")
    if track == "none":
        return None
    if track:
        candidates = [track, os.path.join(MUSIC_DIR, channel_name, track), os.path.join(MUSIC_DIR, track)]
        if settings.get("mood"):
            candidates.insert(1, os.path.join(MUSIC_DIR, channel_name, settings["mood"], track))
        for c in candidates:
            if os.path.isfile(c):
                return c
        sys.exit(f"Music track not found: {track} (looked in music/{channel_name}/ and music/)")
    files = pool(channel_name, settings.get("mood"))
    if not files:
        return None
    if settings.get("pick") == "first":
        return files[0]
    # rotate: stable per story, different across stories
    return files[int(hashlib.sha1(story_id.encode()).hexdigest(), 16) % len(files)]


def describe(settings, track):
    if not track:
        return "none"
    shown = os.path.relpath(track, HERE) if os.path.abspath(track).startswith(HERE + os.sep) else track
    return (f"{shown}  (volume {settings['volume_db']:+.0f} dB, start {settings['start']:g}s, "
            f"fade {settings['fade_in']:g}/{settings['fade_out']:g}s, ducking {'on' if settings['duck'] else 'off'})")


# ---------- FFmpeg ----------
def mix_args(settings, track, total, voice_label, music_index):
    """Extra input args + audio filter_complex section that mixes `track` under `voice_label`.
    Output label: [a]. Music is levelled to a fixed loudness first, so volume_db means the
    same thing for every track."""
    inputs = ["-ss", f"{float(settings['start']):.3f}", "-stream_loop", "-1", "-i", track]
    fo = min(float(settings["fade_out"]), total / 2)
    fmt = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    chain = (f"[{music_index}:a]{fmt},loudnorm=I={VOICE_LUFS}:TP=-2,aresample=48000,{fmt},"
             f"volume={float(settings['volume_db']):.1f}dB,"
             f"afade=t=in:d={float(settings['fade_in']):.2f},"
             f"afade=t=out:st={max(0.0, total - fo):.2f}:d={fo:.2f}[m];")
    if settings["duck"]:
        # narration drives a compressor on the music: it dips under speech, swells in pauses
        chain += (f"{voice_label}{fmt},asplit=2[vo][key];"
                  f"[m][key]sidechaincompress=threshold=0.02:ratio=6:attack=40:release=600[md];"
                  f"[vo][md]")
    else:
        chain += f"{voice_label}{fmt}[vo];[vo][m]"
    chain += f"amix=inputs=2:duration=first:normalize=0,loudnorm=I={VOICE_LUFS}:TP=-1.5[a]"
    return inputs, chain


def narration_track(story_dir, story, out_wav):
    """All scene narrations with the same gaps the video uses -> one wav. Returns its length."""
    args, parts, total = ["ffmpeg", "-y"], [], 0.0
    for i, s in enumerate(story["scenes"]):
        wav = os.path.join(story_dir, "audio", f"scene_{s['id']:02d}.wav")
        if not os.path.exists(wav):
            sys.exit(f"Missing narration {wav} - run python voice.py {os.path.basename(story_dir)} first")
        length = SCENE_LEAD_IN + duration(wav) + SCENE_TAIL
        total += length
        args += ["-i", wav]
        parts.append(f"[{i}:a]adelay={int(SCENE_LEAD_IN * 1000)}:all=1,apad,atrim=0:{length:.3f}[n{i}]")
    n = len(parts)
    graph = ";".join(parts) + ";" + "".join(f"[n{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[out]"
    run(args + ["-filter_complex", graph, "-map", "[out]", "-ar", "48000", "-ac", "2", out_wav])
    return total


# ---------- Commands ----------
def cmd_list(channel_name):
    names = [channel_name] if channel_name else sorted(
        {os.path.basename(d) for d in glob.glob(os.path.join(MUSIC_DIR, "*")) if os.path.isdir(d)}
        | {os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(HERE, "channels", "*.json"))})
    shared = pool("__none__")
    for name in names:
        ch_dir = os.path.join(MUSIC_DIR, name)
        print(f"\n[{name}]  music/{name}/")
        found = False
        for d in [ch_dir] + sorted(p for p in glob.glob(os.path.join(ch_dir, "*")) if os.path.isdir(p)):
            files = sorted(f for f in glob.glob(os.path.join(d, "*")) if f.lower().endswith(AUDIO_EXT))
            if not files:
                continue
            found = True
            label = "(default pool)" if d == ch_dir else f'mood "{os.path.basename(d)}"'
            print(f"  {label}")
            for f in files:
                print(f"    {os.path.basename(f):40s} {duration(f) / 60:5.1f} min")
        if not found:
            mood = ""
            try:
                mood = load_channel(name).get("music_mood", "")
            except SystemExit:
                pass
            print(f"  (empty{' - uses music/ fallback' if shared else ''})" + (f"  wanted mood: {mood}" if mood else ""))
    if shared:
        print("\n[shared fallback]  music/")
        for f in shared:
            print(f"    {os.path.basename(f):40s} {duration(f) / 60:5.1f} min")
    if not shared and not any(pool(n) for n in names):
        print("\nNo tracks yet. Add royalty-free mp3s (e.g. YouTube Audio Library) to music/<channel>/.")


def cmd_preview(story_name, overrides, seconds):
    story_dir = story_dirs([story_name])[0]
    story, channel = load_story(story_dir)
    settings = resolve(story, channel, overrides)
    track = pick_track(settings, channel["name"], story["story"])
    if not track:
        sys.exit(f"No music for {story_name}: add tracks to music/{channel['name']}/ or pass --track")
    print(f"Music: {describe(settings, track)}")
    voice_wav = os.path.join(story_dir, ".music_voice.wav")
    total = narration_track(story_dir, story, voice_wav)
    if seconds:
        total = min(total, seconds)
    inputs, chain = mix_args(settings, track, total, "[0:a]", 1)
    out = os.path.join(story_dir, "music_preview.mp3")
    run(["ffmpeg", "-y", "-i", voice_wav, *inputs, "-filter_complex", chain, "-map", "[a]",
         "-t", f"{total:.3f}", "-c:a", "libmp3lame", "-q:a", "3", out])
    os.remove(voice_wav)
    print(f"Preview ({total:.0f}s): {out}")
    print(f"Happy with it? Save the same flags: python music.py set {story_name} [--track ... --volume-db ...]")


def cmd_set(story_name, overrides, none, clear):
    path = os.path.join(SCENES_DIR, f"{story_name}.json")
    if not os.path.exists(path):
        sys.exit(f"No such story: scenes/{story_name}.json")
    story = json.load(open(path))
    if clear:
        story.pop("music", None)
    else:
        block = dict(story.get("music") or {})
        if none:
            block = {"track": "none"}
        block.update({k: v for k, v in overrides.items() if v is not None})
        story["music"] = block
    with open(path, "w", encoding="utf-8") as f:
        json.dump(story, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"scenes/{story_name}.json music: {story.get('music', '(channel defaults)')}")
    print(f"Rebuild the video: python assemble.py {story_name}")


def add_mix_flags(ap):
    ap.add_argument("--track", help="file in music/<channel>/ (or a path), or 'none'")
    ap.add_argument("--mood", help="pick from music/<channel>/<mood>/")
    ap.add_argument("--volume-db", type=float, help="music level under the voice, e.g. -20 (quieter) .. -12 (louder)")
    ap.add_argument("--start", type=float, help="seconds into the track to start")
    ap.add_argument("--fade-in", type=float, help="seconds")
    ap.add_argument("--fade-out", type=float, help="seconds")
    ap.add_argument("--no-duck", dest="duck", action="store_false", default=None,
                    help="don't dip the music under the narration")


def overrides_from(args):
    return {k: getattr(args, k, None) for k in ("track", "mood", "volume_db", "start", "fade_in", "fade_out", "duck")}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Background music tools")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list", help="show available tracks")
    p.add_argument("channel", nargs="?")
    p = sub.add_parser("preview", help="quick audio-only preview of narration + music")
    p.add_argument("story")
    p.add_argument("--seconds", type=float, help="only the first N seconds")
    add_mix_flags(p)
    p = sub.add_parser("set", help="save music settings into scenes/<story>.json")
    p.add_argument("story")
    p.add_argument("--none", action="store_true", help="no music for this story")
    p.add_argument("--clear", action="store_true", help="remove the story's music settings")
    add_mix_flags(p)
    args = ap.parse_args()
    if args.cmd == "list":
        cmd_list(args.channel)
    elif args.cmd == "preview":
        cmd_preview(args.story, overrides_from(args), args.seconds)
    else:
        cmd_set(args.story, overrides_from(args), args.none, args.clear)
