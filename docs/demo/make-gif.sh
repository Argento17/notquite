#!/bin/bash
# Re-make docs/workorder-demo.gif from docs/demo/comic.html.
# Needs: python3 with playwright (pip install playwright && playwright install chromium) and ffmpeg.
# Optional: PYTHON=<python with playwright>, CHROME=<chrome or chromium binary>.
set -euo pipefail
cd "$(dirname "$0")"
rm -rf frames/seq
"${PYTHON:-python3}" render.py seq 25
ffmpeg -y -loglevel error -framerate 25 -i frames/seq/f%04d.png \
  -vf "split[a][b];[a]palettegen=max_colors=160:stats_mode=full[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" \
  ../workorder-demo.gif
rm -rf frames
echo "wrote docs/workorder-demo.gif"
