# Images

Images are most of what a viewer sees, so the pipeline puts as much design into them as into the
narration: the drafting model plans each picture as a structured shot, the code composes the
prompt, and per-model rules are checked before anything reaches the GPU.

## Image models

Set per channel with `"image_model"` in `channels/<name>.json` (a story can override it).

| | `flux2-klein` (default) | `sdxl` |
|---|---|---|
| Model | FLUX.2 klein 4B (Black Forest Labs, Apache 2.0) | Stable Diffusion XL 1.0 |
| Prompt window | 512 tokens (Qwen3 text encoder) - full sentences | **77 CLIP tokens** - the rest is silently dropped |
| Named characters per image | up to 2 | 1 (it blends faces) |
| Negative prompt | none (distilled, no CFG) - say it positively | yes (channel `negative` + scene `avoid`) |
| Steps | 4 | 30 |
| On the T4 | bf16 (emulated) + CPU offload, ~13 GB | fp16, fits easily |
| Cache | `flux2-klein-cache`, ~16 GB | `sdxl-model-cache`, ~7 GB |

Each model is cached once on Kaggle by its own CPU-only notebook:

```bash
python run_kaggle.py --setup-model flux2-klein    # or: sdxl, or no name = every model the channels use
```

`run_kaggle.py` only attaches the caches the approved stories need, and refuses to start if one
is missing. One Kaggle job can render stories from channels on different models: `generate.py`
groups them and loads each model once.

## How a shot is drafted

For each scene the drafting model (Gemini or Claude) fills a `shot`:

| Field | Rule |
|---|---|
| `subject` | `{NAME}` placeholder(s) - never the description itself. Minor figures: short generic text. |
| `action` | A visible pose or action. No thoughts, plans, sounds or time passing. |
| `setting` | The place plus one or two concrete details. |
| `camera` | One of: extreme wide, wide, medium, medium close-up, close-up, extreme close-up, low-angle, high-angle, top-down, over-the-shoulder, silhouette. Never the same in two consecutive scenes; scene 1 is the hook/thumbnail. |
| `lighting` | Time of day and light quality, matching the mood. |
| composition | Written into the fields above: named characters in the foreground, sharp and large; crowds and guards in the background, smaller, soft-focus, in plain muted clothing that can't be mistaken for a named character - and only as many as the moment needs. FLUX also gets "plain surfaces, no writing" (it has no negative prompt to say "no text"). |
| `avoid` | SDXL: up to 3 things it tends to get wrong ("beard") → the scene's `negative`. FLUX: always empty. |

The code composes `image_prompt` in priority order - who, doing what, where, framing, light - as
comma phrases for SDXL and sentences for FLUX. The channel `style` is appended at render time.

The channel's `image_guidance` (e.g. Bible: period-accurate clothing, no halos, violence implied)
is sent with every draft.

## Checks (before the GPU)

`common.image_problems()` - errors in `script.py` (the draft goes back to the model with the exact
problem), warnings in `run_kaggle.py`:

- too many named characters in one image for the model;
- words a camera can't see ("thinking", "remembers", "plans", ...);
- consecutive scenes with the same camera;
- character descriptions too long (SDXL: 20 CLIP tokens; FLUX: 40);
- **SDXL only:** prompt + style over 75 CLIP tokens (counted exactly with `clip_tokens.py`, the same
  byte-pair encoding SDXL uses) and negative prompt over 75;
- **FLUX only:** prompt + style over 300 words.

Why the SDXL budget matters: before these checks, 4 of Joseph Part 2's 6 prompts overflowed, and
what SDXL dropped was the channel style - so those images didn't look like the channel.

## Fixing an image

- Re-roll: add `"seed": 555` (any number) to the scene.
- Adjust the scene's `image_prompt` directly; keep `{NAME}` placeholders.
- A character looks wrong everywhere: tighten their entry in `characters` (most distinctive first).
- Then `python run_kaggle.py <story>`; `voice.py` is cached, so only `assemble.py` needs re-running.
