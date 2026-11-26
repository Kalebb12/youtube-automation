"""
Exact CLIP token counting on the laptop (stdlib only), so script.py can keep image prompts
inside SDXL's 77-token window. Everything past 75 tokens is silently dropped by SDXL - and
since the channel style goes last, the style is what gets lost.

Implements CLIP's byte-pair encoding (same algorithm as OpenAI's simple_tokenizer) with
SDXL's own merges.txt, downloaded once into models/ (~500 KB).
"""
import functools, html, os, re, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MERGES = os.path.join(HERE, "models", "clip_merges.txt")
MERGES_URL = ("https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/"
              "resolve/main/tokenizer/merges.txt")
MAX_TOKENS = 75          # 77 minus the start/end tokens
PAT = re.compile(r"<\|startoftext\|>|<\|endoftext\|>|'s|'t|'re|'ve|'m|'ll|'d|[^\W\d_]+|\d|[^\s\w]+|_+",
                 re.IGNORECASE)


@functools.lru_cache(maxsize=1)
def _bpe_ranks():
    if not os.path.exists(MERGES):
        os.makedirs(os.path.dirname(MERGES), exist_ok=True)
        print("Downloading CLIP tokenizer merges (one time, ~500 KB)...")
        urllib.request.urlretrieve(MERGES_URL, MERGES + ".part")
        os.replace(MERGES + ".part", MERGES)
    lines = open(MERGES, encoding="utf-8").read().split("\n")
    merges = [tuple(l.split()) for l in lines[1:49152 - 256 - 2 + 1] if l.strip()]
    return {m: i for i, m in enumerate(merges)}


@functools.lru_cache(maxsize=1)
def _byte_encoder():
    bs = list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1)) + \
         list(range(ord("®"), ord("ÿ") + 1))
    cs, n = bs[:], 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return dict(zip(bs, map(chr, cs)))


@functools.lru_cache(maxsize=20000)
def _bpe(token):
    ranks = _bpe_ranks()
    word = tuple(token[:-1]) + (token[-1] + "</w>",)
    while len(word) > 1:
        pairs = {(word[i], word[i + 1]) for i in range(len(word) - 1)}
        best = min(pairs, key=lambda p: ranks.get(p, float("inf")))
        if best not in ranks:
            break
        first, second = best
        new, i = [], 0
        while i < len(word):
            if i < len(word) - 1 and word[i] == first and word[i + 1] == second:
                new.append(first + second)
                i += 2
            else:
                new.append(word[i])
                i += 1
        word = tuple(new)
    return len(word)


def count(text):
    """Number of CLIP tokens in `text` (without the start/end tokens)."""
    text = " ".join(html.unescape(html.unescape(text)).split()).lower()
    enc = _byte_encoder()
    return sum(_bpe("".join(enc[b] for b in t.encode("utf-8"))) for t in PAT.findall(text))


def overflow(text):
    """Tokens past the limit (0 if it fits)."""
    return max(0, count(text) - MAX_TOKENS)
