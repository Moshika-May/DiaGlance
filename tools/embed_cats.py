"""Embed img/cat1-4.webp into cats.js as data URIs, so the result card works when index.html is opened straight from disk
(file:// makes canvas refuse to export images loaded from separate files). Run after changing the cat images: python3 -I tools/embed_cats.py"""
import base64, json
from pathlib import Path
R = Path(__file__).resolve().parent.parent
(R / 'cats.js').write_text('const CATS=' + json.dumps(['data:image/webp;base64,' + base64.b64encode((R / f'img/cat{n}.webp').read_bytes()).decode() for n in range(1, 5)]) + ';')
