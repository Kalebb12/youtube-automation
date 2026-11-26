"""
Runs ON YOUR LAPTOP (CPU). Turns a still image into a slow, moving shot for assemble.py:

  - depth parallax ("2.5D"): a small depth model (Depth Anything V2 Small, Apache 2.0, ONNX on CPU)
    estimates what is near and far; as the camera drifts, near things move more than the
    background, so the still reads as a real camera move;
  - an eased camera move per scene (zoom in / zoom out / pan left / pan right), never the
    same move twice in a row;
  - frames are written straight into ffmpeg (no temp images).

Depth maps are cached next to the images (images/<story>/depth/scene_XX.npy).
The depth model (~100 MB) downloads once into models/.

Settings (channel profile "motion" < story "motion"; a scene may set "move"):
    parallax      true/false  depth parallax on (false = flat Ken Burns)
    strength      parallax amount, 1.0 = default (0 = none, 2 = strong)
    zoom          how far zoom moves travel, e.g. 0.08 = 8%
    transition    crossfade style between scenes (ffmpeg xfade): fade, dissolve, fadeblack, smoothleft, ...
    transition_s  crossfade length in seconds
    fade_in       fade from black at the start (s)
    fade_out      fade to black at the end (s)
"""
import os, subprocess, sys, urllib.request
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEPTH_MODEL = os.path.join(HERE, "models", "depth_anything_v2_small.onnx")
DEPTH_URL = "https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model.onnx"
DEFAULTS = {"parallax": True, "strength": 1.0, "zoom": 0.08, "transition": "fade",
            "transition_s": 0.7, "fade_in": 0.6, "fade_out": 1.2}
KEYS = tuple(DEFAULTS)
MOVES = ("zoom_in", "pan_right", "zoom_out", "pan_left")      # default cycle
MARGIN = 0.14          # extra canvas around the frame so moves/parallax never show an edge
PARALLAX = 0.022       # parallax shift at strength 1.0, as a fraction of frame width


def resolve(story, channel):
    s = dict(DEFAULTS)
    for layer in (channel.get("motion"), story.get("motion")):
        if layer:
            s.update({k: v for k, v in layer.items() if k in KEYS and v is not None})
    return s


def move_for(scene, index):
    """The scene's explicit "move", else the default cycle (never repeats back to back)."""
    m = scene.get("move")
    return m if m in MOVES else MOVES[index % len(MOVES)]


# ---------- Depth ----------
_session = None
def _depth_session():
    global _session
    if _session is None:
        if not os.path.exists(DEPTH_MODEL):
            os.makedirs(os.path.dirname(DEPTH_MODEL), exist_ok=True)
            print("Downloading depth model (one time, ~100 MB)...", flush=True)
            urllib.request.urlretrieve(DEPTH_URL, DEPTH_MODEL + ".part")
            os.replace(DEPTH_MODEL + ".part", DEPTH_MODEL)
        import onnxruntime as ort
        _session = ort.InferenceSession(DEPTH_MODEL, providers=["CPUExecutionProvider"])
    return _session


def depth_map(img_path, cache_path):
    """Relative depth in [0, 1], 1 = nearest, same size as the image. Cached as .npy."""
    import cv2
    stamp = f"{os.path.getsize(img_path)}-{int(os.path.getmtime(img_path))}"
    if os.path.exists(cache_path) and os.path.exists(cache_path + ".key") \
            and open(cache_path + ".key").read() == stamp:
        return np.load(cache_path)
    img = cv2.cvtColor(cv2.imread(img_path), cv2.COLOR_BGR2RGB)
    h, w = img.shape[:2]
    # model input: sides multiple of 14, long side ~770, ImageNet normalisation
    scale = 770 / max(h, w)
    ih, iw = max(14, round(h * scale / 14) * 14), max(14, round(w * scale / 14) * 14)
    x = cv2.resize(img, (iw, ih), interpolation=cv2.INTER_CUBIC).astype(np.float32) / 255.0
    x = (x - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    x = x.transpose(2, 0, 1)[None]
    sess = _depth_session()
    out = sess.run(None, {sess.get_inputs()[0].name: x})[0].squeeze().astype(np.float32)
    out = cv2.resize(out, (w, h), interpolation=cv2.INTER_CUBIC)
    lo, hi = np.percentile(out, 2), np.percentile(out, 98)
    d = np.clip((out - lo) / max(hi - lo, 1e-6), 0, 1)           # larger = nearer (inverse depth)
    d = cv2.GaussianBlur(d, (0, 0), sigmaX=max(w, h) / 120)        # soft edges -> no tearing
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    np.save(cache_path, d.astype(np.float32))
    open(cache_path + ".key", "w").write(stamp)
    return d


# ---------- Rendering ----------
def _ease(t):
    return t * t * (3 - 2 * t)          # smoothstep: starts and ends gently


def render_clip(img_path, depth, out_path, w, h, seconds, fps, move, settings):
    """Write a silent clip of `seconds` with an eased camera move + depth parallax."""
    import cv2
    src = cv2.imread(img_path)
    # cover the output frame plus a margin, keeping aspect
    cw, ch = int(w * (1 + MARGIN)), int(h * (1 + MARGIN))
    s = max(cw / src.shape[1], ch / src.shape[0])
    base = cv2.resize(src, (round(src.shape[1] * s), round(src.shape[0] * s)), interpolation=cv2.INTER_CUBIC)
    bh, bw = base.shape[:2]
    if depth is None or not settings["parallax"]:
        dep = np.full((bh, bw), 0.5, np.float32)
    else:
        dep = cv2.resize(depth, (bw, bh), interpolation=cv2.INTER_LINEAR)
    dep = dep - float(dep.mean())                   # centred: background moves one way, foreground the other

    zoom = float(settings["zoom"])
    par = PARALLAX * float(settings["strength"]) * w
    pan = (bw - w / 1.04) / 2 * 0.85                # how far a pan can travel inside the margin
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    xs -= w / 2
    ys -= h / 2

    frames = max(1, round(seconds * fps))
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
         "-r", str(fps), "-i", "-", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
         "-pix_fmt", "yuv420p", out_path], stdin=subprocess.PIPE)
    try:
        for f in range(frames):
            p = _ease(f / max(1, frames - 1))
            if move == "zoom_in":
                z, cx, drift = 1.0 + zoom * p, 0.0, (p - 0.5) * 2
            elif move == "zoom_out":
                z, cx, drift = 1.0 + zoom * (1 - p), 0.0, (0.5 - p) * 2
            elif move == "pan_right":
                z, cx, drift = 1.04, pan * (p - 0.5) * 2, (p - 0.5) * 2
            else:  # pan_left
                z, cx, drift = 1.04, pan * (0.5 - p) * 2, (0.5 - p) * 2
            # where each output pixel comes from in the base image, before parallax
            mx = xs / z + bw / 2 + cx
            my = ys / z + bh / 2
            # parallax: shift by local depth (sampled at the pre-parallax position), opposite to the camera
            d = cv2.remap(dep, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
            shift = par * drift
            mx = mx - d * shift
            my = my - d * shift * 0.25 * (1 if move.startswith("zoom") else 0)
            frame = cv2.remap(base, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    if proc.wait() != 0:
        sys.exit(f"ffmpeg failed while writing {out_path}")
