# Workorder 0.17.0 demo comic

Source of `docs/workorder-demo.gif` (1280x720, 25 fps, 33.8 s).

**Re-make the GIF with one command** (from the repo root):

    bash docs/demo/make-gif.sh

Needs Python with Playwright (`pip install playwright && playwright install chromium`) and ffmpeg.
Set `PYTHON=` or `CHROME=` to point at a specific interpreter or browser.

## Files

- `comic.html`: the comic. `setTime(t)` draws any moment; open with `?t=12.5` to freeze a frame or `?play` to loop it.
- `render.py`: screenshots `comic.html` with headless Chromium (`seq 25` = every frame, `keys` = one still per scene, `at 3 12` = stills at given seconds).
- `make-gif.sh`: renders every frame, then builds the GIF with ffmpeg (palettegen max_colors=160 stats_mode=full, paletteuse bayer 4 diff_mode=rectangle).
- `run.sh` -> `transcript.txt`: the real Workorder 0.17.0 run behind every terminal line in the comic. Excerpts mark skipped lines with a grey "⋯".
- `fonts/`: Dela Gothic One, IBM Plex Mono and Shantell Sans (all SIL Open Font License), loaded locally so renders are repeatable.

## Scenes

0-7.5 s define the task (`workorder new`) / 7.5-14.0 s the agent claims DONE and is refused (test fails) /
14.0-20.5 s fix, DONE with evidence recorded / 20.5-27.5 s next session runs `workorder show` / 27.5-33.8 s end card.

## For the next release

Re-run `run.sh` with the new version, copy any changed terminal lines into `comic.html`, then run `make-gif.sh`.
