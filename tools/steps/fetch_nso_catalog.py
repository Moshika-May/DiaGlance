"""Index the National Statistical Office (NSO) "important statistics" catalogue and download the xls tables whose titles match a pattern.

  python3 -I tools/steps/fetch_nso_catalog.py index                       # crawl the listing pages -> data/external/nso_catalog.csv
  python3 -I tools/steps/fetch_nso_catalog.py get "หย่า|สมรส|ดื่มสุรา"     # download matching tables (xls) and convert to csv
  python3 -I tools/steps/fetch_nso_catalog.py get "..." --dry-run          # only list what would be downloaded

NSO's firewall answers 418 to non-browser User-Agents, so --ua defaults to a plain browser string (authorised by the project owner for
public statistics). Requests are sequential with a pause. Outputs: data/external/nso/raw/*.xls(x) and data/external/nso/csv/*.csv (UTF-8).
"""
import argparse, csv, html, re, shutil, subprocess, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent   # repo root (this file lives in tools/steps/)
EXT = ROOT / 'data' / 'external'
BASE = 'https://www.nso.go.th/nsoweb/nso/statistics_and_indicators'
FF = 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0'


def fetch(url, ua, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': ua}), timeout=timeout) as r: return r.read(), (r.headers.get('Content-Type') or '')


def parse_page(t):
    """-> list of dict(title, years, folder, published, xls_id). Entries look like <h6>TITLE <span>views</span></h6> ... downloadFile/stat_impt/<id>/file_xls_th"""
    out = []
    for blk in re.split(r'<div class="col-12 mb-3">', t)[1:]:
        m = re.search(r'<h6[^>]*>\s*(.*?)\s*<span', blk, re.S)
        x = re.search(r'downloadFile/(stat_impt|stat_main_nso)/([^/"]+)/file_xls_th', blk)
        if not m: continue
        yrs = re.search(r'ปี\s*:\s*([\d\- ]+)', re.sub(r'<[^>]+>', ' ', blk))
        pub = re.search(r'วันที่เผยแพร่\s*:\s*([^<]+)', blk)
        folder = re.search(r'bi-folder"></i>\s*([^<]+)', blk)
        out.append(dict(title=html.unescape(re.sub(r'\s+', ' ', m.group(1))), years=(yrs.group(1).strip() if yrs else ''), published=(pub.group(1).strip() if pub else ''),
                        folder=(folder.group(1).strip() if folder else ''), kind=(x.group(1) if x else ''), xls_id=(x.group(2) if x else '')))
    return out


def cmd_index(a):
    EXT.mkdir(parents=True, exist_ok=True); rows, seen, page, empty = [], set(), 1, 0
    while page <= a.max_pages:
        body, _ = fetch(f'{BASE}?page={page}', a.ua)
        items = [e for e in parse_page(body.decode('utf-8', 'replace')) if (e['kind'], e['xls_id']) not in seen]
        for e in items: seen.add((e['kind'], e['xls_id'])); rows.append(e)
        print(f'page {page}: {len(items)} new entries ({len(rows)} total)', flush=True)
        empty = empty + 1 if not items else 0
        if empty >= 2: break
        page += 1; time.sleep(a.delay)
    with open(EXT / 'nso_catalog.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, ['title', 'years', 'published', 'folder', 'kind', 'xls_id']); w.writeheader(); w.writerows(rows)
    print(f'{len(rows)} tables indexed -> data/external/nso_catalog.csv')


def cmd_get(a):
    rows = list(csv.DictReader(open(EXT / 'nso_catalog.csv', encoding='utf-8-sig')))
    pick = [r for r in rows if r['xls_id'] and re.search(a.pattern, r['title'])]
    print(f'{len(pick)} tables match /{a.pattern}/')
    raw, out = EXT / 'nso' / 'raw', EXT / 'nso' / 'csv'; raw.mkdir(parents=True, exist_ok=True); out.mkdir(parents=True, exist_ok=True)
    soffice = shutil.which('soffice') or shutil.which('libreoffice')
    log = []
    for r in pick:
        print(f'  - {r["title"][:90]} ({r["years"]})')
        if a.dry_run: continue
        url = f'https://www.nso.go.th/nsoweb/downloadFile/{r["kind"]}/{r["xls_id"]}/file_xls_th'
        try:
            body, ctype = fetch(url, a.ua)
        except Exception as e:
            print('      failed:', e); log.append((r['title'], 'error')); continue
        if body[:200].lstrip().lower().startswith((b'<!doctype', b'<html')): print('      got HTML, skipped'); continue
        ext = '.xlsx' if body[:2] == b'PK' else '.xls'
        p = raw / f'{r["xls_id"]}{ext}'; p.write_bytes(body); time.sleep(a.delay)
        if soffice:
            subprocess.run([soffice, '--headless', '--convert-to', 'csv:Text - txt - csv (StarCalc):44,34,76,1,,0,false,true,false,false,false,-1', '--outdir', str(out), str(p)], capture_output=True, timeout=300)
    if not a.dry_run:
        with open(EXT / 'nso' / 'downloaded.csv', 'a', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f); [w.writerow([r['xls_id'], r['title'], r['years']]) for r in pick]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=['index', 'get']); ap.add_argument('pattern', nargs='?', default='')
    ap.add_argument('--ua', default=FF); ap.add_argument('--delay', type=float, default=1.5); ap.add_argument('--max-pages', type=int, default=60)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    cmd_index(a) if a.cmd == 'index' else cmd_get(a)


if __name__ == '__main__':
    main()
