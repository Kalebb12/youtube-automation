# Story video pipeline

Brief in, narrated YouTube Short out: an LLM (Gemini free tier or Claude) drafts the story, you
approve it, Kaggle's free GPU paints the images (FLUX.2 klein), and your laptop adds narration,
parallax camera moves, crossfades, subtitles and music. Niche-agnostic: each channel is a profile
in `channels/`.

```bash
python script.py --channel bible --passage "Daniel 3:13-28" --brief "the fiery furnace"   # 0. draft
#   read scenes/<id>.json, fix it, set "status": "approved"                                #    you review
python run_kaggle.py                                                                       # 1. images
python voice.py                                                                            # 2. narration
python assemble.py                                                                         # 3. video
```

**Documentation: open [`docs/index.html`](docs/index.html) in a browser.** It covers setup, a full
worked episode, images, motion, music, channel profiles, troubleshooting and how it all works.

Instructions for Claude Code working in this repo live in [`CLAUDE.md`](CLAUDE.md).
