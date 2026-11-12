"""Crawl data.go.th (CKAN) for province-level datasets that might relate to diabetes risk, and save them as UTF-8 CSV.

  python3 -I tools/steps/fetch_opendata.py --dry-run                 # list what would be downloaded (no files)
  python3 -I tools/steps/fetch_opendata.py                           # download (resumable; skips files already on disk)
  python3 -I tools/steps/fetch_opendata.py --topics diabetes,temple  # only some topics
  python3 -I tools/steps/fetch_opendata.py --max-per-query 400       # go deeper (default 100 datasets per search term)

Output (all under data/open/, which is git-ignored):
  raw/<topic>/...        files exactly as downloaded (csv / xls / xlsx)
  csv/<topic>/...csv     UTF-8 CSV (xls/xlsx converted with LibreOffice if installed, every sheet -> one csv)
  manifest.csv           one row per resource: topic, province, years, licence, url, status, local paths
  coverage.csv           topic x province: how many datasets we hold (77 provinces)

Notes: only stdlib. Files are untrusted data: nothing is executed, only parsed as text / converted by LibreOffice.
Many datasets are published by *one* provincial office, so schemas differ: this script collects, it does not merge.
"""
import argparse, csv, glob, hashlib, json, re, shutil, subprocess, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

API = 'https://data.go.th/api/3/action/'
UA = 'DiaGlance-research/1.0 (+https://github.com/Moshika-May/DiaGlance)'   # no personal contact info on purpose
ROOT = Path(__file__).resolve().parent.parent.parent   # repo root (this file lives in tools/steps/)
OUT = ROOT / 'data' / 'open'
FORMATS = {'CSV', 'XLS', 'XLSX'}

# topic -> Thai search terms. Each term is searched separately; a dataset found by several terms is downloaded once.
TOPICS = {
    'diabetes':          ['เบาหวาน'],
    'ncd_obesity':       ['โรคไม่ติดต่อเรื้อรัง', 'ความดันโลหิตสูง', 'ภาวะอ้วน', 'ดัชนีมวลกาย'],
    'gas_station':       ['สถานีบริการน้ำมัน', 'ปั๊มน้ำมัน'],
    'electricity':       ['ผู้ใช้ไฟฟ้า', 'การใช้ไฟฟ้า'],
    'temple':            ['จำนวนวัด', 'ศาสนสถาน'],
    'crime':             ['คดีอาญา'],
    'marriage_divorce':  ['จดทะเบียนหย่า', 'จดทะเบียนสมรส'],
    'lottery_gambling':  ['สลากกินแบ่ง', 'การพนัน'],
    'stray_dog':         ['สุนัขจรจัด', 'สุนัขไม่มีเจ้าของ'],
    'population_age':    ['ประชากร ช่วงอายุ', 'โครงสร้างประชากร'],
    'income_gpp':        ['รายได้ครัวเรือน', 'ผลิตภัณฑ์มวลรวมจังหวัด', 'รายได้เฉลี่ยต่อคนต่อปี'],
    'agriculture':       ['ครัวเรือนเกษตร', 'พื้นที่เพาะปลูก'],
    'occupation':        ['อาชีพประชากร', 'ผู้มีงานทำ จำแนกตามอาชีพ'],
    'weather':           ['ปริมาณน้ำฝน', 'อุณหภูมิเฉลี่ย'],
    'sports_gym':        ['สนามกีฬา', 'ฟิตเนส', 'สถานที่ออกกำลังกาย', 'ลานกีฬา'],
    'convenience_retail': ['ร้านสะดวกซื้อ', 'ร้านค้าปลีก', 'ร้านขายของชำ', 'เซเว่น'],
    'housing':           ['ลักษณะที่อยู่อาศัย', 'จำนวนบ้าน'],
    'iq_mental':         ['ระดับสติปัญญา', 'ไอคิว'],
    'otop_souvenir':     ['OTOP', 'ของฝาก'],
    'food_restaurant':   ['ร้านอาหาร', 'แผงลอย', 'อาหารริมทาง'],
    'cinema_movie':      ['โรงภาพยนตร์', 'ภาพยนตร์', 'Netflix'],
    'internet_wifi':     ['อินเทอร์เน็ต', 'wifi', 'ครัวเรือนที่ใช้อินเทอร์เน็ต'],
    'games':             ['วิดีโอเกม', 'เกมออนไลน์', 'เกมมือถือ', 'อีสปอร์ต', 'อุตสาหกรรมเกม', 'เล่นเกม', 'เกมกีฬา'],   # video games first, sports games second
    'coastal':           ['ชายฝั่ง', 'จังหวัดชายทะเล'],
    'air_quality':       ['PM2.5', 'ฝุ่นละอองขนาดเล็ก', 'คุณภาพอากาศ'],
}
PROVINCES = ('กรุงเทพมหานคร สมุทรปราการ นนทบุรี ปทุมธานี พระนครศรีอยุธยา อ่างทอง ลพบุรี สิงห์บุรี ชัยนาท สระบุรี ชลบุรี ระยอง จันทบุรี ตราด '
             'ฉะเชิงเทรา ปราจีนบุรี นครนายก สระแก้ว นครราชสีมา บุรีรัมย์ สุรินทร์ ศรีสะเกษ อุบลราชธานี ยโสธร ชัยภูมิ อำนาจเจริญ บึงกาฬ หนองบัวลำภู '
             'ขอนแก่น อุดรธานี เลย หนองคาย มหาสารคาม ร้อยเอ็ด กาฬสินธุ์ สกลนคร นครพนม มุกดาหาร เชียงใหม่ ลำพูน ลำปาง อุตรดิตถ์ แพร่ น่าน พะเยา '
             'เชียงราย แม่ฮ่องสอน นครสวรรค์ อุทัยธานี กำแพงเพชร ตาก สุโขทัย พิษณุโลก พิจิตร เพชรบูรณ์ ราชบุรี กาญจนบุรี สุพรรณบุรี นครปฐม '
             'สมุทรสาคร สมุทรสงคราม เพชรบุรี ประจวบคีรีขันธ์ นครศรีธรรมราช กระบี่ พังงา ภูเก็ต สุราษฎร์ธานี ระนอง ชุมพร สงขลา สตูล ตรัง พัทลุง '
             'ปัตตานี ยะลา นราธิวาส').split()
assert len(PROVINCES) == 77 and len(set(PROVINCES)) == 77


def get_json(url, tries=3):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=40) as r:
                return json.load(r)
        except Exception as e:
            if k == tries - 1: raise
            time.sleep(2 * (k + 1))


def province_of(*texts):
    """Best-effort province from organisation/dataset titles: 'จังหวัดX' first, then a bare name of >=5 chars."""
    for t in texts:
        for p in PROVINCES:
            if f'จังหวัด{p}' in (t or '') or (p == 'กรุงเทพมหานคร' and 'กรุงเทพ' in (t or '')): return p
    for t in texts:
        for p in PROVINCES:
            if len(p) >= 5 and p in (t or ''): return p
    return ''


def search(term, cap, delay):
    """Yield dataset dicts for one search term (CKAN relevance order), only those having csv/xls/xlsx resources.
    One query per format: the portal's firewall answers 403 to filter queries containing OR / parentheses."""
    got_ids = set()
    for fmt in ('CSV', 'XLSX', 'XLS'):
        start, got = 0, 0
        while got < cap:
            q = urllib.parse.urlencode({'q': term, 'fq': f'res_format:{fmt}', 'rows': min(100, cap - got), 'start': start})
            res = get_json(API + 'package_search?' + q)['result']
            if not res['results']: break
            for d in res['results']:
                got += 1
                if d['id'] not in got_ids: got_ids.add(d['id']); yield d
            start += len(res['results'])
            if start >= res['count']: break
            time.sleep(delay)
        time.sleep(delay)


def decode_text(b):
    for enc in ('utf-8-sig', 'cp874'):   # Thai government CSVs are often TIS-620 / Windows-874
        try: return b.decode(enc), enc
        except UnicodeDecodeError: pass
    return b.decode('utf-8', 'replace'), 'utf-8-replace'


def download(url, dest, max_bytes, delay, ua=UA):
    """Return (status, nbytes). Skips HTML error pages and anything over max_bytes. Existing files are reused."""
    if dest.exists() and dest.stat().st_size > 0: return 'cached', dest.stat().st_size
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': ua}), timeout=60) as r:
            ctype = (r.headers.get('Content-Type') or '').lower()
            n = int(r.headers.get('Content-Length') or 0)
            if n > max_bytes: return 'skipped: too large', n
            buf = r.read(max_bytes + 1)
    except Exception as e:
        return f'error: {type(e).__name__} {str(e)[:60]}', 0
    time.sleep(delay)
    if len(buf) > max_bytes: return 'skipped: too large', len(buf)
    if 'text/html' in ctype or buf[:200].lstrip().lower().startswith((b'<!doctype', b'<html')): return 'error: got HTML page', len(buf)
    dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(buf)
    return 'ok', len(buf)


def csv_to_utf8(raw, csv_dir):
    csv_dir.mkdir(parents=True, exist_ok=True)
    text, enc = decode_text(raw.read_bytes()); dest = csv_dir / raw.name
    dest.write_text(text, encoding='utf-8'); return dest, enc


def convert_sheets(files, csv_dir, soffice):
    """xls/xlsx -> csv (UTF-8, every sheet its own file) in batches: starting LibreOffice once per batch, not per file."""
    csv_dir.mkdir(parents=True, exist_ok=True)
    todo = [f for f in files if not list(csv_dir.glob(glob.escape(f.stem) + '*.csv'))]
    for k in range(0, len(todo), 20):
        batch = todo[k:k + 20]
        try:   # filter options: UTF-8, sep ',', quote '"', export every sheet to its own file (-1)
            subprocess.run([soffice, '--headless', '--convert-to', 'csv:Text - txt - csv (StarCalc):44,34,76,1,,0,false,true,false,false,false,-1',
                            '--outdir', str(csv_dir)] + [str(f) for f in batch], capture_output=True, timeout=600)
        except subprocess.TimeoutExpired: print('   LibreOffice timed out on a batch, continuing', file=sys.stderr)


FIELDS = ['topic', 'query', 'dataset_id', 'dataset_name', 'title', 'organization', 'province', 'geo_coverage', 'first_year', 'last_year', 'license',
          'updated', 'resource_name', 'format', 'url', 'bytes', 'status', 'local_raw', 'local_csv', 'encoding']


def write_manifest(rows):
    tmp = OUT / 'manifest.csv.tmp'
    with open(tmp, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, FIELDS); w.writeheader(); w.writerows(rows)
    tmp.replace(OUT / 'manifest.csv')   # atomic: an interrupted run never leaves a half-written manifest


def write_coverage(rows, topics):
    held = {}
    for r in rows:
        if r['local_csv'] and r['province']: held.setdefault((r['topic'], r['province']), set()).add(r['dataset_id'])
    with open(OUT / 'coverage.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['topic', 'provinces_covered(of 77)', 'datasets_with_known_province', 'datasets_total'] + PROVINCES)
        for t in topics:
            ds = {r['dataset_id'] for r in rows if r['topic'] == t}
            w.writerow([t, sum((t, p) in held for p in PROVINCES), sum(len(v) for (tt, _), v in held.items() if tt == t), len(ds)] + [len(held.get((t, p), ())) for p in PROVINCES])


def retry_failed(a, soffice):
    """Re-download manifest rows that failed with 418/403 (bot-blocking firewalls) using --ua, politely and sequentially."""
    with open(OUT / 'manifest.csv', encoding='utf-8-sig') as f: rows = list(csv.DictReader(f))
    todo = [r for r in rows if re.search(r'Error (418|403)', r['status']) and (not a.hosts or any(h in r['url'] for h in a.hosts.split(',')))]
    print(f'retrying {len(todo)} blocked resources with UA: {a.ua}', flush=True)
    for n, r in enumerate(todo, 1):
        ext = {'CSV': '.csv', 'XLS': '.xls', 'XLSX': '.xlsx'}[r['format']]
        raw = OUT / 'raw' / r['topic'] / (re.sub(r'[^\w.-]+', '_', r['dataset_name'])[:100] + '__r' + hashlib.sha1(r['url'].encode()).hexdigest()[:8] + ext)
        r['status'], r['bytes'] = download(r['url'], raw, int(a.max_mb * 1e6), a.delay, a.ua)
        if r['status'] in ('ok', 'cached'):
            r['local_raw'] = str(raw.relative_to(ROOT)); csv_dir = OUT / 'csv' / r['topic']
            if r['format'] == 'CSV': dest, r['encoding'] = csv_to_utf8(raw, csv_dir); r['local_csv'] = str(dest.relative_to(ROOT))
            elif soffice:
                convert_sheets([raw], csv_dir, soffice)
                outs = sorted(csv_dir.glob(glob.escape(raw.stem) + '*.csv')); r['local_csv'] = ' | '.join(str(p.relative_to(ROOT)) for p in outs); r['encoding'] = 'libreoffice' if outs else 'conversion failed'
        if n % 25 == 0: print(f'   {n}/{len(todo)}  ok so far: {sum(x["status"] in ("ok", "cached") for x in todo[:n])}', flush=True)
        if n % 50 == 0: write_manifest(rows)
    write_manifest(rows); write_coverage(rows, list(TOPICS))
    print(f'done: {sum(x["status"] in ("ok", "cached") for x in todo)}/{len(todo)} recovered')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--topics', help='comma-separated subset of: ' + ','.join(TOPICS))
    ap.add_argument('--max-per-query', type=int, default=100, help='datasets per search term and format (default 100)')
    ap.add_argument('--max-mb', type=float, default=40, help='skip files larger than this (default 40 MB)')
    ap.add_argument('--max-total-gb', type=float, default=4, help='stop downloading after this much in total (default 4 GB)')
    ap.add_argument('--workers', type=int, default=4, help='parallel downloads (default 4; be polite to government servers)')
    ap.add_argument('--delay', type=float, default=0.4, help='seconds each worker waits after a download (default 0.4)')
    ap.add_argument('--dry-run', action='store_true', help='only write manifest.csv, download nothing')
    ap.add_argument('--retry-failed', action='store_true', help='re-download rows that earlier failed with HTTP 418/403 (needs --ua)')
    ap.add_argument('--ua', default=UA, help='User-Agent for file downloads (default: identifies this project). Some government firewalls answer 418/403 to non-browser agents.')
    ap.add_argument('--hosts', help='with --retry-failed: only URLs containing one of these comma-separated substrings')
    a = ap.parse_args()
    topics = a.topics.split(',') if a.topics else list(TOPICS)
    for t in topics: assert t in TOPICS, f'unknown topic {t}'
    soffice = shutil.which('soffice') or shutil.which('libreoffice')
    if not soffice: print('note: LibreOffice not found, xls/xlsx will be downloaded but not converted', file=sys.stderr)
    OUT.mkdir(parents=True, exist_ok=True)
    if a.retry_failed: return retry_failed(a, soffice)
    rows, seen, budget = [], set(), a.max_total_gb * 1e9
    prev = []   # keep manifest rows of topics we are not re-running, so a partial run never erases earlier work
    if (OUT / 'manifest.csv').exists():
        with open(OUT / 'manifest.csv', encoding='utf-8-sig') as f: prev = [r for r in csv.DictReader(f) if r['topic'] not in topics]
    for topic in topics:
        # 1) discover
        todo = []
        for term in TOPICS[topic]:
            print(f'[{topic}] searching "{term}" ...', flush=True)
            try: datasets = list(search(term, a.max_per_query, a.delay))
            except Exception as e: print(f'   search failed: {e}', file=sys.stderr); continue
            for d in datasets:
                if (topic, d['id']) in seen: continue
                seen.add((topic, d['id']))
                org = (d.get('organization') or {}).get('title', '')
                prov = province_of(org, d.get('title'))
                for i, r in enumerate(d.get('resources', [])):
                    fmt = (r.get('format') or '').upper()
                    if fmt not in FORMATS or not (r.get('url') or '').startswith('http'): continue
                    stem = re.sub(r'[^\w.-]+', '_', f"{d['name']}__{i}")[:120]
                    todo.append(dict(topic=topic, query=term, dataset_id=d['id'], dataset_name=d['name'], title=d.get('title', ''), organization=org,
                                     province=prov, geo_coverage=d.get('geo_coverage', ''), first_year=d.get('first_year_of_data', ''),
                                     last_year=d.get('last_year_of_data', ''), license=d.get('license_title', ''), updated=d.get('last_updated_date', ''),
                                     resource_name=r.get('name', ''), format=fmt, url=r['url'], bytes='', status='dry-run' if a.dry_run else 'pending',
                                     local_raw='', local_csv='', encoding='', _raw=OUT / 'raw' / topic / (stem + {'CSV': '.csv', 'XLS': '.xls', 'XLSX': '.xlsx'}[fmt])))
        print(f'   {len(todo)} resources found for {topic}', flush=True)
        if not a.dry_run:
            # 2) download in parallel (keeps within the total budget)
            def work(row):
                if budget <= 0: return 'skipped: total budget reached', 0
                return download(row['url'], row['_raw'], int(a.max_mb * 1e6), a.delay, a.ua)
            with ThreadPoolExecutor(a.workers) as ex:
                for n, (row, (st, nb)) in enumerate(zip(todo, ex.map(work, todo)), 1):
                    row['status'], row['bytes'] = st, nb
                    if st == 'ok': budget -= nb
                    if n % 25 == 0: print(f'   downloaded {n}/{len(todo)}', flush=True)
            # 3) normalise to UTF-8 csv; xls/xlsx via LibreOffice in batches
            csv_dir = OUT / 'csv' / topic
            sheets = [r['_raw'] for r in todo if r['status'] in ('ok', 'cached') and r['format'] != 'CSV']
            if sheets and soffice: convert_sheets(sheets, csv_dir, soffice)
            for row in todo:
                if row['status'] not in ('ok', 'cached'): continue
                row['local_raw'] = str(row['_raw'].relative_to(ROOT))
                try:
                    if row['format'] == 'CSV':
                        dest, row['encoding'] = csv_to_utf8(row['_raw'], csv_dir); row['local_csv'] = str(dest.relative_to(ROOT))
                    else:
                        outs = sorted(csv_dir.glob(glob.escape(row['_raw'].stem) + '*.csv'))
                        row['local_csv'] = ' | '.join(str(p.relative_to(ROOT)) for p in outs)
                        row['encoding'] = 'libreoffice' if outs else ('no LibreOffice' if not soffice else 'conversion failed')
                except Exception as e: row['encoding'] = f'convert error: {type(e).__name__}'
        for r in todo: r.pop('_raw')
        rows += todo
        write_manifest(prev + rows); write_coverage(prev + rows, list(TOPICS))   # saved after every topic, so an interrupted run keeps its progress
    rows = prev + rows
    ok = sum(r['status'] in ('ok', 'cached') for r in rows)
    print(f'\n{len(rows)} resources in manifest, {ok} downloaded. See {OUT.relative_to(ROOT)}/manifest.csv and coverage.csv')


if __name__ == '__main__':
    main()
