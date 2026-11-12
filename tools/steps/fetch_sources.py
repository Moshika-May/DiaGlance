"""Download every row of tools/sources.csv that has kind=url, and turn it into a UTF-8 csv in data/sources/<source_id>.csv.

  python3 -I tools/steps/fetch_sources.py              # download what is missing
  python3 -I tools/steps/fetch_sources.py --force      # download again
  python3 -I tools/steps/fetch_sources.py --only my_source_id

To add a dataset: add one row to tools/sources.csv (source_id, kind=url, url, format=csv|xlsx|xls) - see tools/README.md. csv and xlsx are read with the
standard library; old .xls needs LibreOffice ('soffice') installed. A local file instead of a URL: kind=local and path=<file under data/>.
Output: data/sources/<source_id>.csv (first sheet) + data/sources/index.csv (what was fetched, when, how big, sha1)."""
import argparse, csv, hashlib, io, re, shutil, subprocess, sys, tempfile, time, urllib.request, zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / 'data' / 'sources'
UA = 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0'
NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def col_index(ref):   # 'AB12' -> 27
    n = 0
    for ch in re.match(r'[A-Z]+', ref).group(): n = n * 26 + ord(ch) - 64
    return n - 1


def xlsx_rows(path):
    """First worksheet of an .xlsx as a list of rows of strings (values only; formulas = their cached value; dates stay as serial numbers)."""
    z = zipfile.ZipFile(path); names = z.namelist(); shared = []
    if 'xl/sharedStrings.xml' in names:
        shared = [''.join(t.text or '' for t in si.iter(NS + 't')) for si in ET.fromstring(z.read('xl/sharedStrings.xml')).iter(NS + 'si')]
    sheet = sorted(n for n in names if re.match(r'xl/worksheets/sheet\d+\.xml$', n))[0]
    rows = []
    for row in ET.fromstring(z.read(sheet)).iter(NS + 'row'):
        cells = {}
        for c in row.iter(NS + 'c'):
            t, v = c.get('t'), c.find(NS + 'v')
            if t == 'inlineStr': val = ''.join(x.text or '' for x in c.iter(NS + 't'))
            elif v is None: continue
            elif t == 's': val = shared[int(v.text)]
            else: val = v.text or ''
            cells[col_index(c.get('r'))] = val
        rows.append([cells.get(i, '') for i in range(max(cells) + 1)] if cells else [])
    return rows


def decode(b):
    for enc in ('utf-8-sig', 'cp874', 'utf-16'):   # Thai government files are often Windows-874 (TIS-620)
        try: return b.decode(enc)
        except UnicodeDecodeError: pass
    return b.decode('utf-8', 'replace')


def to_rows(raw, fmt):
    if fmt == 'csv': return list(csv.reader(io.StringIO(decode(raw))))
    if fmt == 'xlsx':
        with tempfile.NamedTemporaryFile(suffix='.xlsx') as f: f.write(raw); f.flush(); return xlsx_rows(f.name)
    if fmt == 'xls':
        soffice = shutil.which('soffice') or shutil.which('libreoffice')
        if not soffice: sys.exit('old .xls needs LibreOffice (soffice) - or save the file as .xlsx / .csv and use that link')
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'in.xls').write_bytes(raw)
            subprocess.run([soffice, '--headless', '--convert-to', 'csv:Text - txt - csv (StarCalc):44,34,76,1', '--outdir', d, str(Path(d) / 'in.xls')], check=True, capture_output=True, timeout=180)
            return list(csv.reader(io.StringIO(decode((Path(d) / 'in.csv').read_bytes()))))
    sys.exit(f'unknown format {fmt!r} (use csv, xlsx or xls)')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0]); ap.add_argument('--force', action='store_true'); ap.add_argument('--only')
    a = ap.parse_args(); OUT.mkdir(parents=True, exist_ok=True)
    reg = list(csv.DictReader(open(ROOT / 'tools' / 'sources.csv', encoding='utf-8-sig')))
    idx_path = OUT / 'index.csv'; idx = {r['source_id']: r for r in csv.DictReader(open(idx_path, encoding='utf-8'))} if idx_path.exists() else {}
    for r in reg:
        sid = r['source_id'].strip()
        if r['kind'].strip() not in ('url', 'local') or (a.only and a.only != sid): continue
        dest = OUT / f'{sid}.csv'
        if dest.exists() and not a.force and sid in idx: print(f'skip   {sid} (already there; --force to redo)'); continue
        try:
            if r['kind'].strip() == 'local': raw = (ROOT / r['path'].strip()).read_bytes(); fmt = (r['format'] or Path(r['path']).suffix[1:]).strip().lower()
            else:
                req = urllib.request.Request(r['url'].strip(), headers={'User-Agent': UA})
                with urllib.request.urlopen(req, timeout=120) as resp: raw = resp.read()
                fmt = (r['format'] or Path(r['url'].split('?')[0]).suffix[1:]).strip().lower()
            rows = to_rows(raw, fmt)
        except Exception as e: print(f'FAILED {sid}: {type(e).__name__}: {e}', file=sys.stderr); continue
        with open(dest, 'w', encoding='utf-8-sig', newline='') as f: csv.writer(f).writerows(rows)
        idx[sid] = dict(source_id=sid, bytes=len(raw), sha1=hashlib.sha1(raw).hexdigest(), rows=len(rows), fetched=time.strftime('%Y-%m-%d %H:%M'))
        print(f'ok     {sid}: {len(rows)} rows -> {dest.relative_to(ROOT)}')
    with open(idx_path, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, ['source_id', 'bytes', 'sha1', 'rows', 'fetched']); w.writeheader(); w.writerows(idx.values())


if __name__ == '__main__': main()
