"""Cut a magenta-background cat image into transparent PNG(s).
  one cat : python3 -I tools/cutout.py src.png assets/cat1_white.png
  2x2 grid: python3 -I tools/cutout.py src.png assets --grid   (-> cat1_white, cat2_calico, cat3_grey, cat4_orange)
Writes light/dark previews next to the output as _prev_*.png when --preview is given."""
import sys
from PIL import Image
args = [a for a in sys.argv[1:] if not a.startswith('--')]; flags = {a for a in sys.argv[1:] if a.startswith('--')}
src, dst = args
im = Image.open(src).convert('RGB'); W, H = im.size; px = im.load()
pts = [(x, y) for cx, cy in [(5, 5), (W-15, 5), (5, H-15), (W-15, H-15)] for x in range(cx, cx+10) for y in range(cy, cy+10)]
bg = tuple(sum(px[p][i] for p in pts) / len(pts) for i in range(3)); bm = min(bg[0], bg[2]) - bg[1]
out = Image.new('RGBA', (W, H)); po = out.load()
for y in range(H):
    for x in range(W):
        r, g, b = px[x, y]
        t = max(0.0, min(1.0, (min(r, b) - g - 0.25 * bm) / (0.55 * bm)))   # 0 = cat, 1 = magenta background
        a = 1 - t
        if a <= 0: continue
        if a < 1: r, g, b = [max(0, min(255, (v - t * bv) / a)) for v, bv in zip((r, g, b), bg)]   # unmix bg colour from edge pixels
        po[x, y] = (int(r), int(g), int(b), int(a * 255))
def trim(q, name):
    bb = q.getchannel('A').point(lambda v: 255 if v > 24 else 0).getbbox()
    if bb[0] <= 1 or bb[1] <= 1 or bb[2] >= q.width - 1 or bb[3] >= q.height - 1: print(name, 'WARNING: touches image edge')
    c = q.crop((max(bb[0] - 6, 0), max(bb[1] - 6, 0), min(bb[2] + 6, q.width), min(bb[3] + 6, q.height)))
    return c
def save(c, path):
    c.save(path, optimize=True); print(path, c.size)
    if '--preview' in flags:
        for tag, col in [('light', (243, 242, 241)), ('dark', (22, 22, 22))]:
            pv = Image.new('RGB', c.size, col); pv.paste(c, mask=c.getchannel('A')); pv.save(path.replace('.png', f'_prev_{tag}.png'))
if '--grid' in flags:
    for n, (qx, qy) in zip(['cat1_white', 'cat2_calico', 'cat3_grey', 'cat4_orange'], [(0, 0), (1, 0), (0, 1), (1, 1)]):
        save(trim(out.crop((qx * W // 2, qy * H // 2, (qx + 1) * W // 2, (qy + 1) * H // 2)), n), f'{dst}/{n}.png')
else: save(trim(out, dst), dst)
