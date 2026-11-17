"""Render docs/demo/comic.html to PNG frames with headless Chromium (Playwright).

  python render.py seq 25   -> frames/seq/f0000.png ... (every frame at 25 fps)
  python render.py keys     -> frames/key-*.png (one still per scene)
  python render.py at 3 12  -> frames/probe/t*.png (stills at given seconds)

Set CHROME to use a specific Chrome/Chromium binary; otherwise Playwright's own is used.
"""
import os, sys, asyncio, pathlib
from playwright.async_api import async_playwright
HERE = pathlib.Path(__file__).resolve().parent
DURATION = 33.8
async def main(times, outdir, prefix):
    outdir = pathlib.Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        kw = {'executable_path': os.environ['CHROME']} if os.environ.get('CHROME') else {}
        b = await p.chromium.launch(**kw)
        pg = await b.new_page(viewport={'width':1280,'height':720}, device_scale_factor=1)
        await pg.goto((HERE/'comic.html').as_uri()+'?t=0')
        await pg.evaluate('document.fonts.ready')
        await pg.wait_for_timeout(300)
        for name, t in times:
            await pg.evaluate(f'setTime({t})')
            await pg.screenshot(path=str(outdir/f'{prefix}{name}.png'))
        await b.close()
if __name__ == '__main__':
    mode = sys.argv[1]
    if mode == 'keys':
        asyncio.run(main([('b1',7.3),('b2',13.8),('b3',20.3),('b4',27.3),('b5',33.7),('b0',0.0)], HERE/'frames', 'key-'))
    elif mode == 'at':
        ts = [float(x) for x in sys.argv[2:]]
        asyncio.run(main([(f'{t:06.2f}', t) for t in ts], HERE/'frames/probe', 't'))
    else:
        fps = float(sys.argv[2]); n = round(DURATION*fps)
        asyncio.run(main([(f'{i:04d}', i/fps) for i in range(n)], HERE/'frames/seq', 'f'))
