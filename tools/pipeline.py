"""Open-data pipeline - the ONE command to run: download -> parse -> stage -> validate -> canonical -> analysis, everything as CSV.

  python3 -I tools/pipeline.py full            # everything below, in order (download takes hours; resumable)
  python3 -I tools/pipeline.py fetch           # download only (data.go.th, NSO tables; add --osm for OpenStreetMap counts)
  python3 -I tools/pipeline.py all             # parse, stage, validate, external, canonical, curated  (needs the downloads)
  python3 -I tools/pipeline.py analyze         # controlled models on the curated panel
  python3 -I tools/pipeline.py model           # rebuild model.js (the quiz) from the survey files + canonical province data
  python3 -I tools/pipeline.py parse|stage|validate|external|canonical|curated   # single stages
  python3 -I tools/pipeline.py parse --topics temple,diabetes --limit 40         # quick test on a subset (do not use `all` with a subset: it rewrites staging)

The downloaders and the analysis live in tools/steps/ (fetch_opendata.py, fetch_nso_catalog.py, fetch_osm_poi.py, analyze.py); this script calls them.

Input : data/open/manifest.csv + data/open/csv/**  (made by the fetch stage)
Output: data/pipeline/01_parsed/profile.csv        one row per source file: detected layout, header, roles, problems
        data/pipeline/02_staging/obs_<topic>.csv   every observation in one long format (province/district x year x indicator)
        data/pipeline/03_validated/obs_valid.csv   staging + qc_status (pass / warn / reject) + qc_flags
        data/pipeline/03_validated/quality_*.csv   rule counts, per-file status, coverage
        data/canonical/*.csv                       dim_province, fact_province_year, indicator_catalog, panel_curated, target_dm_province_year,
                                                   model_results, quality_report.md

Principles: nothing is silently dropped (rejects are kept with a reason), nothing is invented (derived values are
labelled), province is resolved from file content first, then from the publishing office. Files are untrusted data:
only parsed as text. Stdlib only.
"""
import argparse, collections, csv, hashlib, io, json, math, re, statistics, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPEN, PIPE, CANON = ROOT / 'data' / 'open', ROOT / 'data' / 'pipeline', ROOT / 'data' / 'canonical'
csv.field_size_limit(10 ** 8)

# ---------------------------------------------------------------- reference data
PROV = [(10, 'กรุงเทพมหานคร'), (11, 'สมุทรปราการ'), (12, 'นนทบุรี'), (13, 'ปทุมธานี'), (14, 'พระนครศรีอยุธยา'), (15, 'อ่างทอง'), (16, 'ลพบุรี'),
        (17, 'สิงห์บุรี'), (18, 'ชัยนาท'), (19, 'สระบุรี'), (20, 'ชลบุรี'), (21, 'ระยอง'), (22, 'จันทบุรี'), (23, 'ตราด'), (24, 'ฉะเชิงเทรา'),
        (25, 'ปราจีนบุรี'), (26, 'นครนายก'), (27, 'สระแก้ว'), (30, 'นครราชสีมา'), (31, 'บุรีรัมย์'), (32, 'สุรินทร์'), (33, 'ศรีสะเกษ'),
        (34, 'อุบลราชธานี'), (35, 'ยโสธร'), (36, 'ชัยภูมิ'), (37, 'อำนาจเจริญ'), (38, 'บึงกาฬ'), (39, 'หนองบัวลำภู'), (40, 'ขอนแก่น'),
        (41, 'อุดรธานี'), (42, 'เลย'), (43, 'หนองคาย'), (44, 'มหาสารคาม'), (45, 'ร้อยเอ็ด'), (46, 'กาฬสินธุ์'), (47, 'สกลนคร'), (48, 'นครพนม'),
        (49, 'มุกดาหาร'), (50, 'เชียงใหม่'), (51, 'ลำพูน'), (52, 'ลำปาง'), (53, 'อุตรดิตถ์'), (54, 'แพร่'), (55, 'น่าน'), (56, 'พะเยา'),
        (57, 'เชียงราย'), (58, 'แม่ฮ่องสอน'), (60, 'นครสวรรค์'), (61, 'อุทัยธานี'), (62, 'กำแพงเพชร'), (63, 'ตาก'), (64, 'สุโขทัย'),
        (65, 'พิษณุโลก'), (66, 'พิจิตร'), (67, 'เพชรบูรณ์'), (70, 'ราชบุรี'), (71, 'กาญจนบุรี'), (72, 'สุพรรณบุรี'), (73, 'นครปฐม'),
        (74, 'สมุทรสาคร'), (75, 'สมุทรสงคราม'), (76, 'เพชรบุรี'), (77, 'ประจวบคีรีขันธ์'), (80, 'นครศรีธรรมราช'), (81, 'กระบี่'), (82, 'พังงา'),
        (83, 'ภูเก็ต'), (84, 'สุราษฎร์ธานี'), (85, 'ระนอง'), (86, 'ชุมพร'), (90, 'สงขลา'), (91, 'สตูล'), (92, 'ตรัง'), (93, 'พัทลุง'),
        (94, 'ปัตตานี'), (95, 'ยะลา'), (96, 'นราธิวาส')]
assert len(PROV) == 77
CODE2NAME = dict(PROV)
NAME2CODE = {n: c for c, n in PROV}
ALIAS = {'กรุงเทพ': 10, 'กรุงเทพฯ': 10, 'กทม': 10, 'อยุธยา': 14, 'ศรีษะเกษ': 33, 'ประจวบ': 77, 'ประจวบฯ': 77, 'นครศรีฯ': 80, 'โคราช': 30,
         'ปทุมธาณี': 13, 'บุรีรัมย': 31, 'สุราษฏร์ธานี': 84, 'สุราษฎร์': 84, 'อุบล': 34, 'nakhonratchasima': 30, 'bangkok': 10}
# 23 provinces with a sea coast (17 Gulf of Thailand + 6 Andaman) - general geographic knowledge, not from the downloaded data
COASTAL = {10, 11, 20, 21, 22, 23, 24, 74, 75, 76, 77, 86, 84, 80, 90, 94, 96, 85, 82, 83, 81, 92, 91}
assert len(COASTAL) == 23
TH_DIGITS = str.maketrans('๐๑๒๓๔๕๖๗๘๙', '0123456789')
MISSING = {'', '-', '--', '---', '.', '..', 'n/a', 'na', 'null', 'none', 'nan', 'ไม่มีข้อมูล', 'ไม่มี', 'ไม่ระบุ', 'x', 'xx'}
TOTAL_WORDS = ('รวม', 'ทั้งจังหวัด', 'ทั้งสิ้น', 'total', 'ยอดรวม')
CUR_YEAR_BE = 2569


def norm_prov(s):
    """Province name/code text -> province code (int) or None."""
    if s is None: return None
    t = str(s).translate(TH_DIGITS).strip().replace('​', '')
    if re.fullmatch(r'\d{2}', t) and int(t) in CODE2NAME: return int(t)
    t = re.sub(r'^(ที่ทำการปกครอง)?(จังหวัด|จ\.)\s*', '', t).replace(' ', '').replace('.', '')
    t = re.sub(r'(มหานคร)$', 'มหานคร', t)
    if t in NAME2CODE: return NAME2CODE[t]
    return ALIAS.get(t.lower()) or ALIAS.get(t)


def to_num(s):
    if s is None: return None
    t = str(s).translate(TH_DIGITS).strip().replace(',', '').replace('%', '').replace(' ', '')
    if t.lower() in MISSING: return None
    try:
        v = float(t)
        return v if math.isfinite(v) else None
    except ValueError: return None


def year_of(s):
    """-> (year_be or None, note). Accepts B.E./C.E., 'พ.ศ. 2560', fiscal '2560/2561' (uses the first year)."""
    t = str(s or '').translate(TH_DIGITS)
    m = re.search(r'(?<!\d)(25\d\d|19\d\d|20\d\d)(?!\d)', t)
    if not m: return None, ''
    y = int(m.group(1)); note = 'fiscal_range' if re.search(r'\d{4}\s*[/\-–]\s*\d{2,4}', t) else ''
    return (y + 543 if y < 2400 else y), (note + (' ce_converted' if y < 2400 else '')).strip()


# ---------------------------------------------------------------- stage 1: parse
ROLE_PATTERNS = [   # order matters: first match wins
    ('areacode', r'^(areacode|รหัสพื้นที่|รหัสพื้นที่ตามกระทรวงมหาดไทย)$'),
    ('month', r'^(เดือน|month|เดือนที่)$'),
    ('year', r'^(ปี|ปี ?พ\.?ศ\.?|ปีงบประมาณ|พ\.?ศ\.?|year|b_year|yr|ปีที่|ปีข้อมูล)$'),
    ('subdistrict', r'(ตำบล|แขวง|sub ?district|tambon)'),
    ('district', r'(อำเภอ|^เขต$|^district$|amphoe|amphur)'),
    ('province', r'^(จังหวัด|province|prov|changwat|provincename|prov_name|province_name|ชื่อจังหวัด)$'),
    ('unit', r'^(หน่วย|หน่วยนับ|หน่วยวัด|unit|หน่วยของข้อมูล)$'),
    ('ignore', r'(ที่มา|แหล่ง|หมายเหตุ|หน่วยงาน|remark|source|note|เจ้าของ|ผู้จัดทำ|^id$|ลำดับ|^no$|ข้อมูล ?ณ|date|^lat|^lng|^long|latitud|พิกัด|ไปรษณีย์|zip|postal|โทร|tel|เลขที่|หมู่|ถนน|ซอย|^รหัส|code$|^dcode|^scode)'),
    ('value', r'^(ค่าข้อมูล|ค่าของข้อมูล|ค่า|จำนวน|มูลค่า|value|ปริมาณ|จำนวนทั้งสิ้น|total|รวม|ผลรวม)$'),
    ('item', r'(รายการ|ประเภท|รายละเอียด|ตัวชี้วัด|indicator|item|ชื่อชุดข้อมูล|ชื่อข้อมูล|หมวด|กลุ่ม|ชนิด)'),
]
YEAR_HEAD = re.compile(r'^(?:พ\.?ศ\.?|ปี|ปี ?พ\.?ศ\.?)?\s*(25\d\d|20\d\d)(?:\s*/\s*\d+)?$')


def role_of(h):
    t = re.sub(r'\s+', ' ', str(h or '')).strip().lower()
    for role, pat in ROLE_PATTERNS:
        if re.search(pat, t): return role
    return None


def read_table(path):
    """Read a csv; locate the header row (skipping title lines); drop empty rows/cols. -> (header, rows, notes)."""
    notes = []
    text = Path(path).read_text(encoding='utf-8', errors='replace')
    if not text.strip(): return [], [], ['empty file']
    head = text[:4096]; delim = max([',', ';', '\t', '|'], key=head.count)
    rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), delimiter=delim)]
    rows = [r for r in rows if any(r)]
    if not rows: return [], [], ['no non-empty rows']
    width = max(sum(1 for c in r if c) for r in rows[:30])
    hi = 0
    for i, r in enumerate(rows[:15]):
        ne = [c for c in r if c]
        if len(ne) >= max(2, int(0.6 * width)) and sum(1 for c in ne if to_num(c) is None) >= 0.5 * len(ne): hi = i; break
    if hi: notes.append(f'skipped {hi} title row(s)')
    header = [re.sub(r'\s+', ' ', c).strip() or f'col{j}' for j, c in enumerate(rows[hi])]
    body = [r + [''] * (len(header) - len(r)) for r in rows[hi + 1:]]
    body = [r[:len(header)] for r in body]
    return header, body, notes


def roles_has_year(roles): return any(v in ('year', 'month') for v in roles.values())


def m_has_geo(m): return bool(m and norm_prov(m.get('province')))


def classify(header, body, mrow):
    """Decide the layout of a table and which column plays which role."""
    roles = {i: role_of(h) for i, h in enumerate(header)}
    by = collections.defaultdict(list)
    for i, r in roles.items():
        if r: by[r].append(i)
    year_cols = [i for i, h in enumerate(header) if YEAR_HEAD.match(h.translate(TH_DIGITS))]
    n = max(1, len(body))
    numcols = [i for i in range(len(header)) if sum(1 for r in body[:200] if to_num(r[i]) is not None) >= 0.7 * min(n, 200)]
    info = dict(roles=roles, by=dict(by), year_cols=year_cols, numcols=numcols)
    low = [h.lower() for h in header]
    if 'areacode' in low and 'b_year' in low: return 'hdc_dbexport', info
    txt = [i for i in range(len(header)) if i not in numcols and roles.get(i) not in ('province', 'district', 'subdistrict', 'ignore', 'year', 'month', 'unit')]
    # one row per entity (a temple, a licence, a person) rather than an aggregated figure: unique names, or many repeated label combinations
    keys = [tuple(r[i] for i in range(len(header)) if i not in numcols) for r in body]
    record_level = len(body) >= 50 and not roles_has_year(roles) and (len(body) >= 2000 or any(len({r[i] for r in body}) / len(body) >= 0.8 for i in txt) or 1 - len(set(keys)) / len(body) >= 0.2)
    info['record_level'] = record_level
    if record_level and (by.get('province') or by.get('district') or by.get('subdistrict') or m_has_geo(mrow)): return 'record_list', info
    if len(year_cols) >= 2: return 'wide_year', info
    value_cols = by.get('value') or []
    if by.get('year') and (value_cols or [i for i in numcols if i not in by['year'] + by.get('month', [])]): return 'long', info
    if (by.get('province') or by.get('district')) and (value_cols or numcols): return 'snapshot', info
    if by.get('province'): return 'record_list', info
    return 'unknown', info


def stage_parse(rows, args):
    PIPE.joinpath('01_parsed').mkdir(parents=True, exist_ok=True)
    out = []
    for n, m in enumerate(rows, 1):
        for p in filter(None, m['local_csv'].split(' | ')):
            path = ROOT / p
            rec = dict(topic=m['topic'], dataset_id=m['dataset_id'], dataset_name=m['dataset_name'], organization=m['organization'], province_hint=m['province'],
                       file=p, n_rows='', n_cols='', layout='', header='', year_cols='', status='ok', notes='')
            try:
                with open(path, encoding='utf-8', errors='replace') as fh: head2k = fh.read(2000).lower()
                if 'areacode' in head2k:   # HDC exports can be huge: only look at the head
                    with open(path, encoding='utf-8', newline='') as f: head = next(csv.reader(f))
                    header, body, notes = [h.strip() for h in head], [], ['hdc head only']
                    lay = 'hdc_dbexport' if 'b_year' in [h.lower() for h in header] else 'unknown'
                    rec.update(layout=lay, n_cols=len(header), header='|'.join(header)[:300],
                               n_rows=sum(1 for _ in open(path, encoding='utf-8', errors='replace')) - 1, notes='; '.join(notes))
                else:
                    header, body, notes = read_table(path)
                    if not header: rec.update(status='unparsable', notes='; '.join(notes)); out.append(rec); continue
                    lay, info = classify(header, body, m)
                    rec.update(layout=lay, n_rows=len(body), n_cols=len(header), header='|'.join(header)[:300], notes='; '.join(notes),
                               year_cols=','.join(header[i] for i in info['year_cols']))
            except Exception as e:
                rec.update(status='error', notes=f'{type(e).__name__}: {e}'[:200])
            out.append(rec)
        if n % 1000 == 0: print(f'  parsed {n}/{len(rows)}', flush=True)
    with open(PIPE / '01_parsed' / 'profile.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, list(out[0])); w.writeheader(); w.writerows(out)
    c = collections.Counter(r['layout'] or r['status'] for r in out)
    print('layouts:', dict(c.most_common()))
    return out


# ---------------------------------------------------------------- stage 2: stage (unify into long observations)
OBS_FIELDS = ['topic', 'dataset_id', 'dataset_name', 'dataset_title', 'source_file', 'row_no', 'layout', 'province_code', 'province_src', 'geo_level', 'district',
              'year_be', 'period', 'indicator_label', 'unit', 'value', 'n_rows', 'flags']

# curated indicator rules: (topic, regex on "dataset title + label", indicator_key, additive?)   first match wins
INDICATORS = [
    ('electricity', r'ผู้ใช้ไฟฟ้า|จำนวนผู้ใช้|consumer', 'electricity_users', True),
    ('electricity', r'หน่วยจำหน่าย|การใช้ไฟฟ้า|ไฟฟ้าที่จำหน่าย|กิโลวัตต์|kwh|gwh|ล้านหน่วย', 'electricity_sold_units', True),
    ('temple', r'วัด|ศาสนสถาน', 'temple_count', True),
    ('gas_station', r'สถานีบริการ|ปั๊ม', 'gas_station_count', True),
    ('marriage_divorce', r'หย่า', 'divorce_count', True),
    ('marriage_divorce', r'สมรส', 'marriage_count', True),
    ('crime', r'คดี|อาชญากรรม|ความผิด', 'crime_cases', True),
    ('lottery_gambling', r'สลาก|หวย|พนัน', 'lottery_gambling', True),
    ('income_gpp', r'ผลิตภัณฑ์.*ต่อหัว|ต่อหัว', 'gpp_per_capita', False),
    ('income_gpp', r'ผลิตภัณฑ์.*จังหวัด|gpp', 'gpp_total', True),
    ('income_gpp', r'รายได้.*(ครัวเรือน|เฉลี่ย)|income', 'household_income', False),
    ('convenience_retail', r'สะดวกซื้อ|เซเว่น|7-?eleven|ค้าปลีก', 'convenience_retail_count', True),
    ('sports_gym', r'สนามกีฬา|ลานกีฬา|ฟิตเนส|ออกกำลัง', 'sports_facility_count', True),
    ('weather', r'ฝน|rain', 'rainfall_mm', False),
    ('weather', r'อุณหภูมิ|temp', 'temperature_c', False),
    ('internet_wifi', r'อินเทอร์เน็ต|internet|wifi', 'internet_use', False),
    ('food_restaurant', r'ร้านอาหาร|แผงลอย|ผู้ประกอบการอาหาร', 'food_outlet_count', True),
    ('otop_souvenir', r'otop|ผลิตภัณฑ์.*(ตำบล|ชุมชน)', 'otop_value', True),
    ('agriculture', r'ครัวเรือน.*เกษตร|เกษตรกร', 'farm_households', True),
    ('housing', r'บ้าน|ที่อยู่อาศัย', 'housing_count', True),
]


def indicator_key(topic, title, label):
    text = f'{title} {label}'.lower()
    for t, pat, key, _ in INDICATORS:
        if t == topic and re.search(pat, text): return key
    return None


def hdc_dict(rows):
    """dataset_name -> {column: (type, comment)} from the data_dict.csv files."""
    out = {}
    for m in rows:
        if m['resource_name'].lower().startswith('data_dict') and m['local_csv']:
            try:
                with open(ROOT / m['local_csv'].split(' | ')[0], encoding='utf-8', newline='') as f:
                    out[m['dataset_name']] = {r['COLUMN_NAME']: (r.get('COLUMN_TYPE', ''), r.get('COLUMN_COMMENT', '')) for r in csv.DictReader(f)}
            except Exception: pass
    return out


def stage_hdc(path, rec, dd, flags):
    """HDC hospital-level DB export -> province x year sums (areacode[:2] = province code)."""
    agg = collections.defaultdict(float); cnt = collections.Counter(); bad = 0; viol = 0
    with open(path, encoding='utf-8', newline='') as f:
        rd = csv.DictReader(f)
        cols = [c for c in rd.fieldnames if c not in ('id', 'hospcode', 'areacode', 'date_com', 'b_year') and (dd.get(c, ('Number',))[0] == 'Number')]
        for r in rd:
            code = norm_prov((r.get('areacode') or '')[:2]); y, _ = year_of(r.get('b_year'))
            if code is None or y is None: bad += 1; continue
            cnt[(code, y)] += 1
            for c in cols:
                v = to_num(r.get(c))
                if v is not None: agg[(code, y, c)] += v
            for k in range(1, 9):   # consistency: patients in an age group cannot exceed the population of that group
                a, b = to_num(r.get(f'result_group{k}')), to_num(r.get(f'pop_group{k}'))
                if a is not None and b is not None and a > b + 1e-9: viol += 1
    for (code, y, c), v in agg.items():
        yield dict(province_code=code, province_src='areacode', geo_level='province', district='', year_be=y, period='', indicator_label=f'{c}: {dd.get(c, ("", ""))[1]}'.strip(': '),
                   unit='count', value=v, n_rows=cnt[(code, y)], flags=('agg_hospital_sum' + (f' hdc_group_violations={viol}' if viol else '')))
    if bad: yield dict(province_code='', province_src='', geo_level='', district='', year_be='', period='', indicator_label='(rows with unusable areacode/year)', unit='rows', value=bad,
                       n_rows=bad, flags='hdc_bad_rows')


NOGEO = [0]


def stage_table(m, rec, path, layout):
    header, body, _ = read_table(path)
    lay, info = classify(header, body, m)
    by, roles = info['by'], info['roles']
    pc = (m['province'] and norm_prov(m['province'])) or None
    title = m['title']
    ym = re.search(r'(?<!\d)(25\d\d)(?!\d)', m['last_year'] or '')
    ext_year = int(ym.group(1)) if ym and m['first_year'] == m['last_year'] else None
    um = re.match(r'(\d{4})', m.get('updated') or ''); snap_year = (int(um.group(1)) + 543 if int(um.group(1)) < 2400 else int(um.group(1))) if um else None
    first = lambda role: (by.get(role) or [None])[0]
    ign = set(by.get('ignore', [])) | set(by.get('unit', []))
    skip = ign | set(by.get('year', [])) | set(by.get('month', [])) | set(by.get('province', [])) | set(by.get('district', [])) | set(by.get('subdistrict', [])) | set(by.get('areacode', []))

    def geo(r, flags):
        """-> (province_code, src, level, district)"""
        code, src = None, ''
        if first('province') is not None:
            code = norm_prov(r[first('province')]); src = 'content' if code else ''
            if code is None and r[first('province')]: flags.append('province_name_unresolved')
        if code is None and first('areacode') is not None:
            code = norm_prov(re.sub(r'\D', '', r[first('areacode')])[:2]); src = 'areacode' if code else ''
        if code is None and pc: code, src = pc, 'publisher'
        level, dist = 'province', ''
        d = first('district')
        if d is not None and r[d].strip():
            dist = r[d].strip()
            level = 'province' if any(w in dist.replace(' ', '') for w in TOTAL_WORDS) else 'district'
        sd = first('subdistrict')
        if sd is not None and r[sd].strip() and not any(w in r[sd].replace(' ', '') for w in TOTAL_WORDS) and level != 'province': level, dist = 'subdistrict', f'{dist}/{r[sd].strip()}'
        return code, src, level, dist

    if not (by.get('province') or by.get('district') or by.get('subdistrict') or by.get('areacode') or pc):
        NOGEO[0] += 1; return   # national / regional table without province: cannot be placed
    if lay == 'long':
        vcols = by.get('value') or [i for i in info['numcols'] if i not in skip]
        if not vcols: return
        vcol = vcols[0]
        dims = [i for i in range(len(header)) if i not in skip and i != vcol and to_num(body[0][i] if body else '') is None]
        for k, r in enumerate(body, 1):
            flags = []
            v = to_num(r[vcol]); y, yn = year_of(r[first('year')]) if first('year') is not None else (None, '')
            if yn: flags.append(yn)
            if y is None and ext_year: y, flags = ext_year, flags + ['year_from_metadata']
            code, src, level, dist = geo(r, flags)
            label = ' / '.join(x for x in (r[i] for i in dims) if x) or header[vcol]
            unit = r[first('unit')] if first('unit') is not None else ''
            per = r[first('month')] if first('month') is not None else ''
            yield dict(province_code=code or '', province_src=src, geo_level=level, district=dist, year_be=y or '', period=per, indicator_label=label, unit=unit,
                       value='' if v is None else v, n_rows=1, flags=' '.join(flags), row_no=k)
    elif lay == 'wide_year':
        ycols = info['year_cols']
        ent = [i for i in range(len(header)) if i not in ycols and i not in ign and to_num(body[0][i] if body else '') is None]
        for k, r in enumerate(body, 1):
            flags = []; code, src, level, dist = geo(r, flags)
            label = ' / '.join(x for x in (r[i] for i in ent if i not in set(by.get('district', []) + by.get('province', []))) if x) or title
            unit = r[first('unit')] if first('unit') is not None else ''
            for yc in ycols:
                y, yn = year_of(header[yc]); v = to_num(r[yc])
                yield dict(province_code=code or '', province_src=src, geo_level=level, district=dist, year_be=y or '', period='', indicator_label=label, unit=unit,
                           value='' if v is None else v, n_rows=1, flags=' '.join(flags + ([yn] if yn else [])), row_no=k)
    elif lay == 'snapshot':
        vcols = by.get('value') or [i for i in info['numcols'] if i not in skip]
        if not vcols: return
        dims = [i for i in range(len(header)) if i not in skip and i not in vcols and to_num(body[0][i] if body else '') is None]
        for k, r in enumerate(body, 1):
            flags = ['year_from_metadata' if ext_year else 'year_from_update_date']; code, src, level, dist = geo(r, flags)
            base = ' / '.join(x for x in (r[i] for i in dims) if x)
            for vc in vcols:
                v = to_num(r[vc])
                yield dict(province_code=code or '', province_src=src, geo_level=level, district=dist, year_be=ext_year or snap_year or '', period='',
                           indicator_label=(base + ' / ' if base else '') + (header[vc] if len(vcols) > 1 else title or header[vc]),
                           unit=r[first('unit')] if first('unit') is not None else '', value='' if v is None else v, n_rows=1, flags=' '.join(flags), row_no=k)
    elif lay == 'record_list':
        cnt = collections.Counter()
        for r in body:
            code, src, _, _ = geo(r, []); cnt[(code, src)] += 1
        for (code, src), n in cnt.items():
            yield dict(province_code=code or '', province_src=src, geo_level='province', district='', year_be=ext_year or snap_year or '', period='', indicator_label=f'{title} (record count)',
                       unit='records', value=n, n_rows=n, flags='record_count' + ('' if ext_year else ' year_from_update_date'), row_no='')


def stage_stage(rows, profile, args):
    d = PIPE / '02_staging'; d.mkdir(parents=True, exist_ok=True)
    for old in d.glob('obs_*.csv'): old.unlink()
    dd = hdc_dict(rows); bymf = {}
    for m in rows:
        for p in filter(None, m['local_csv'].split(' | ')): bymf[p] = m
    writers, files, stats = {}, {}, collections.Counter()
    for k, rec in enumerate(profile, 1):
        lay = rec['layout']
        if rec['status'] != 'ok' or lay in ('unknown', ''): stats['unsupported:' + (lay or rec['status'])] += 1; continue
        m = bymf[rec['file']]; path = ROOT / rec['file']
        try:
            gen = stage_hdc(path, rec, dd.get(m['dataset_name'], {}), []) if lay == 'hdc_dbexport' else stage_table(m, rec, path, lay)
            n = 0
            for o in gen:
                o.setdefault('row_no', ''); o.update(topic=m['topic'], dataset_id=m['dataset_id'], dataset_name=m['dataset_name'], dataset_title=m['title'], source_file=rec['file'], layout=lay)
                t = m['topic']
                if t not in writers:
                    files[t] = open(d / f'obs_{t}.csv', 'w', newline='', encoding='utf-8-sig'); writers[t] = csv.DictWriter(files[t], OBS_FIELDS, extrasaction='ignore'); writers[t].writeheader()
                writers[t].writerow(o); n += 1
            stats['staged:' + lay] += 1; stats['obs'] += n
            if n == 0: stats['staged_but_empty'] += 1
        except Exception as e:
            stats['error:' + type(e).__name__] += 1
            print(f'  stage error {rec["file"]}: {e}', file=sys.stderr)
        if k % 500 == 0: print(f'  staged {k}/{len(profile)} files, {stats["obs"]} observations', flush=True)
    for f in files.values(): f.close()
    stats['unsupported:no_geography'] += NOGEO[0]
    print('staging:', dict(stats))
    return stats


# ---------------------------------------------------------------- stage 3: validate
def is_additive(unit, label):
    t = f'{unit} {label}'
    return not re.search(r'องศา|°|%|ร้อยละ|อัตรา|ต่อ|เฉลี่ย|per|rate|average|mean|ดัชนี', t, re.I)


def read_rows(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        yield from csv.DictReader(f)


def stage_validate(args):
    """Rules per observation. pass/warn rows -> obs_valid.csv, reject rows -> obs_reject.csv (kept, with reasons). Streams topic by topic."""
    V = PIPE / '03_validated'; V.mkdir(parents=True, exist_ok=True)
    fields = OBS_FIELDS + ['qc_status', 'qc_flags']
    rules, perfile, qs = collections.Counter(), collections.defaultdict(collections.Counter), collections.Counter()
    with open(V / 'obs_valid.csv', 'w', newline='', encoding='utf-8-sig') as fv, open(V / 'obs_reject.csv', 'w', newline='', encoding='utf-8-sig') as fr:
        wv, wr = csv.DictWriter(fv, fields, extrasaction='ignore'), csv.DictWriter(fr, fields, extrasaction='ignore'); wv.writeheader(); wr.writeheader()
        for f in sorted((PIPE / '02_staging').glob('obs_*.csv')):
            seen, grp = {}, collections.defaultdict(list)   # pass 1: duplicate keys with different values + per-indicator-year distributions
            for r in read_rows(f):
                k = hash((r['source_file'], r['province_code'], r['district'], r['year_be'], r['period'], r['indicator_label']))
                e = seen.get(k)
                if e is None: seen[k] = [r['value'], False]
                elif e[0] != r['value']: e[1] = True
                if r['geo_level'] == 'province' and r['province_code'] and r['year_be'] and r['value'] != '':
                    grp[(r['indicator_label'][:80], r['year_be'], r['unit'])].append(float(r['value']))
            med = {}
            for k, v in grp.items():
                if len(v) >= 12:
                    m = statistics.median(v); mad = statistics.median([abs(x - m) for x in v]) or None
                    if mad: med[k] = (m, mad)
            del grp
            for r in read_rows(f):   # pass 2: apply rules
                flags = r['flags'].split() if r['flags'] else []; reasons = []
                if r['province_code'] == '': reasons.append('reject:province_unresolved')
                y = int(r['year_be']) if r['year_be'] else None
                if y is None: reasons.append('reject:year_missing')
                elif not (2540 <= y <= CUR_YEAR_BE): reasons.append('reject:year_out_of_range')
                if r['value'] == '': reasons.append('reject:value_missing')
                else:
                    v = float(r['value'])
                    if v < 0 and r['unit'] and is_additive(r['unit'], r['indicator_label']): reasons.append('warn:negative_additive')
                    k = (r['indicator_label'][:80], r['year_be'], r['unit'])
                    if r['geo_level'] == 'province' and k in med:
                        m, mad = med[k]
                        if abs(v - m) / (1.4826 * mad) > 8: reasons.append('warn:outlier')
                if seen[hash((r['source_file'], r['province_code'], r['district'], r['year_be'], r['period'], r['indicator_label']))][1]: reasons.append('warn:conflicting_duplicate')
                if 'year_from_metadata' in flags: reasons.append('warn:year_from_metadata')
                if 'year_from_update_date' in flags: reasons.append('warn:year_from_update_date')
                if 'hdc_group_violations' in r['flags']: reasons.append('warn:hdc_patients_exceed_population')
                status = 'reject' if any(x.startswith('reject') for x in reasons) else ('warn' if reasons else 'pass')
                for x in reasons: rules[x] += 1
                perfile[r['source_file']][status] += 1; qs[status] += 1
                r['qc_status'], r['qc_flags'] = status, ' '.join(reasons)
                (wr if status == 'reject' else wv).writerow(r)
            del seen
            print(f'  validated {f.name}', flush=True)
    with open(V / 'quality_rules.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['rule', 'observations']); [w.writerow([k, v]) for k, v in rules.most_common()]
    with open(V / 'quality_files.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['source_file', 'pass', 'warn', 'reject', 'reject_share'])
        for k, c in sorted(perfile.items()):
            tot = sum(c.values()); w.writerow([k, c['pass'], c['warn'], c['reject'], round(c['reject'] / tot, 3)])
    (V / 'quality_summary.json').write_text(json.dumps(dict(status=qs, rules=rules), ensure_ascii=False), encoding='utf-8')
    print('validation:', dict(qs), '| rules:', dict(rules.most_common(8)), flush=True)


# ---------------------------------------------------------------- stage 4: canonical
def write_csv(path, rows, fields=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or (list(rows[0]) if rows else [])
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fields); w.writeheader(); w.writerows(rows)


def build_facts():
    """Province-year facts from obs_valid.csv: direct totals, else sum of finer rows (additive units only), monthly rows -> year."""
    keyed, meta, dm = collections.defaultdict(list), {}, collections.defaultdict(dict)
    for r in read_rows(PIPE / '03_validated' / 'obs_valid.csv'):
        if 'conflicting_duplicate' in r['qc_flags']: continue
        pc, y = int(r['province_code']), int(r['year_be']); v = float(r['value'])
        if r['dataset_name'] == 's_dm_pop_age' and r['geo_level'] == 'province':
            m = re.match(r'(pop|result)_group(\d)', r['indicator_label'])
            if m: dm[(pc, y)][(m.group(1), int(m.group(2)))] = v
        k = (pc, y, r['dataset_id'], r['indicator_label'], r['unit'])
        keyed[k].append((r['geo_level'], r['period'], v, r['qc_status']))
        if k not in meta: meta[k] = (r['topic'], r['dataset_title'], r['dataset_name'])
    facts = []
    for k, rs in keyed.items():
        pc, y, ds, label, unit = k; topic, title, name = meta[k]; additive = is_additive(unit, label)
        direct = [x for x in rs if x[0] == 'province']
        fine = [x for x in rs if x[0] == 'district'] or [x for x in rs if x[0] == 'subdistrict']
        if direct: use, how = direct, 'direct'
        elif fine and additive: use, how = fine, 'sum_districts'
        else: continue
        periods = {x[1] for x in use}
        vals = [x[2] for x in use]
        if len(periods) > 1 or (periods and '' not in periods):
            v = sum(vals) if additive else sum(vals) / len(vals); how += '+sum_periods' if additive else '+mean_periods'
            if len(periods) < 12: how += '(partial_year)'
        elif len(vals) > 1: v = sum(vals) if additive else sum(vals) / len(vals); how += '+sum_rows' if additive else '+mean_rows'
        else: v = vals[0]
        key = indicator_key(topic, title, label)
        facts.append(dict(province_code=pc, province_th=CODE2NAME[pc], year_be=y, year_ce=y - 543, topic=topic, indicator_id=key or f'{topic}.other', indicator_label=label[:160], unit=unit,
                          value=round(v, 6), additive=int(additive), method=how, n_obs=len(use), qc_status='warn' if any(x[3] == 'warn' for x in use) else 'pass', dataset_id=ds, dataset_name=name))
    return facts, dm


def diabetes_target(dm):
    """Registered-diabetes prevalence per province-year from the HDC age-group tables (patients / population)."""
    ok = {k: v for k, v in dm.items() if all(('pop', g) in v and ('result', g) in v for g in range(1, 6))}
    if not ok: return []
    ref_year = max((y for (_, y) in ok if sum(1 for (p, yy) in ok if yy == y) >= 70), default=None)
    std = None
    if ref_year:
        tot = {g: sum(v[('pop', g)] for (p, y), v in ok.items() if y == ref_year) for g in range(2, 6)}; s = sum(tot.values()); std = {g: tot[g] / s for g in tot}
    out = []
    for (pc, y), v in sorted(ok.items()):
        pop15 = sum(v[('pop', g)] for g in range(2, 6)); dm15 = sum(v[('result', g)] for g in range(2, 6))
        if pop15 <= 0: continue
        asp = sum(std[g] * (v[('result', g)] / v[('pop', g)]) for g in range(2, 6) if v[('pop', g)] > 0) if std else ''
        out.append(dict(province_code=pc, province_th=CODE2NAME[pc], year_be=y, year_ce=y - 543, pop_all_ages=sum(v[('pop', g)] for g in range(1, 6)),
                        pop_15plus=pop15, dm_patients_15plus=dm15, dm_patients_all_ages=sum(v[('result', g)] for g in range(1, 6)),
                        dm_prev_pct_15plus_crude=round(100 * dm15 / pop15, 3), dm_prev_pct_15plus_age_standardised=round(100 * asp, 3) if asp != '' else '',
                        std_population_year_be=ref_year or '', **{f'dm_prev_pct_group{g}': round(100 * v[('result', g)] / v[('pop', g)], 3) if v[('pop', g)] else '' for g in range(1, 6)}))
    return out


def stage_canonical(args):
    CANON.mkdir(parents=True, exist_ok=True)
    write_csv(CANON / 'dim_province.csv', [dict(province_code=c, province_th=n, coastal=int(c in COASTAL)) for c, n in PROV])
    facts, dm = build_facts()
    facts.sort(key=lambda r: (r['indicator_id'], r['indicator_label'], r['province_code'], r['year_be']))
    write_csv(CANON / 'fact_province_year.csv', facts)
    idx = collections.defaultdict(lambda: collections.defaultdict(dict))   # (id,label,unit) -> year -> {province: value}
    nwarn, ndata = collections.Counter(), collections.Counter()
    for r in facts:
        k = (r['indicator_id'], r['indicator_label'], r['unit']); idx[k][r['year_be']][r['province_code']] = r['value']
        ndata[k] += 1; nwarn[k] += r['qc_status'] == 'warn'
    catalog = []
    for k, yrs in idx.items():
        latest = max(yrs)
        catalog.append(dict(indicator_id=k[0], indicator_label=k[1], unit=k[2], n_provinces_any=len({p for y in yrs.values() for p in y}), latest_year_be=latest,
                            n_provinces_latest_year=len(yrs[latest]), years=f'{min(yrs)}-{latest}', n_facts=ndata[k], share_warn=round(nwarn[k] / ndata[k], 3)))
    catalog.sort(key=lambda r: (-r['n_provinces_latest_year'], r['indicator_id']))
    write_csv(CANON / 'indicator_catalog.csv', catalog)
    tgt = diabetes_target(dm)
    write_csv(CANON / 'target_dm_province_year.csv', tgt)
    best = {}   # one label per mapped indicator key: the one with the best latest-year coverage
    for c in catalog:
        if c['indicator_id'].endswith('.other') or c['n_provinces_latest_year'] < 40: continue
        if c['indicator_id'] not in best: best[c['indicator_id']] = c
    panel = {c: dict(province_code=c, province_th=n, coastal=int(c in COASTAL)) for c, n in PROV}
    if tgt:
        ly = max(r['year_be'] for r in tgt)
        for r in tgt:
            if r['year_be'] == ly: panel[r['province_code']].update(dm_prev_crude_pct=r['dm_prev_pct_15plus_crude'], dm_prev_age_std_pct=r['dm_prev_pct_15plus_age_standardised'], dm_year_be=ly)
    for iid, c in best.items():
        for pc, v in idx[(iid, c['indicator_label'], c['unit'])][c['latest_year_be']].items():
            panel[pc][iid] = v; panel[pc][f'{iid}__year_be'] = c['latest_year_be']
    allcols = []
    for p in panel.values():
        for k in p:
            if k not in allcols: allcols.append(k)
    write_csv(CANON / 'panel_wide_UNVERIFIED.csv', list(panel.values()), allcols)
    return facts, catalog, tgt, best


def write_report(profile, stats, facts, catalog, tgt, best):
    V = json.loads((PIPE / '03_validated' / 'quality_summary.json').read_text(encoding='utf-8'))
    lay = collections.Counter(r['layout'] or r['status'] for r in profile)
    L = ['# Pipeline quality report', '', f'Source files parsed: **{len(profile)}**', '', '## 1. Parsing (layout detected per file)', '', '| layout | files |', '|---|---|']
    L += [f'| {k} | {v} |' for k, v in lay.most_common()]
    L += ['', '## 2. Staging', '', f'Observations staged: **{sum(V["status"].values()):,}** (see 03_validated). Files with layout `unknown`/`unparsable` are *not* staged and listed in `profile.csv`.',
          '', '## 3. Validation', '', '| qc_status | observations |', '|---|---|'] + [f'| {k} | {v:,} |' for k, v in sorted(V['status'].items(), key=lambda x: -x[1])]
    L += ['', '| rule | observations |', '|---|---|'] + [f'| {k} | {v:,} |' for k, v in sorted(V['rules'].items(), key=lambda x: -x[1])]
    L += ['', '`reject:*` rows are kept in `03_validated/obs_reject.csv`; `warn:*` rows are kept but `conflicting_duplicate` is excluded from the canonical facts.',
          '', '## 4. Canonical output', '', f'- province-year facts: **{len(facts):,}** ({len({r["indicator_id"] for r in facts})} indicator keys, {len(catalog):,} distinct indicator labels)',
          f'- diabetes target rows: **{len(tgt)}**' + (f' (province-years {min(r["year_be"] for r in tgt)}-{max(r["year_be"] for r in tgt)})' if tgt else ''),
          f'- panel_wide indicators (>=40 provinces in latest year): **{len(best)}**', '', '### Coverage of mapped indicators (latest year)', '', '| indicator | provinces (of 77) | latest year | label |', '|---|---|---|---|']
    L += [f'| {c["indicator_id"]} | {c["n_provinces_latest_year"]} | {c["latest_year_be"]} | {c["indicator_label"][:60]} |' for c in sorted(best.values(), key=lambda c: -c['n_provinces_latest_year'])]
    if tgt:
        ly = max(r['year_be'] for r in tgt); cur = [r for r in tgt if r['year_be'] == ly]
        L += ['', f'### Diabetes target, year {ly}: {len(cur)} provinces', '',
              f'- crude prevalence (15+): min {min(r["dm_prev_pct_15plus_crude"] for r in cur)}%, median {statistics.median(r["dm_prev_pct_15plus_crude"] for r in cur)}%, max {max(r["dm_prev_pct_15plus_crude"] for r in cur)}%']
    L += ['', '## 5. Known limitations', '',
          '- Layouts not yet supported are listed in `profile.csv` (`layout = unknown`); they are *excluded*, not guessed.',
          '- Indicator keys come from a hand-written rule table (`INDICATORS` in tools/pipeline.py). Labels that match no rule stay as `<topic>.other` and are NOT put in panel_wide.',
          '- District rows are summed to province only for additive units; rates/percentages are never summed.',
          '- HDC diabetes counts are *registered patients from service records / population from civil registration*, not a survey prevalence.',
          '- **Bangkok has no diabetes target**: HDC does not hold complete Bangkok age-group tables, so Bangkok is missing from `target_dm_province_year.csv` (76 provinces) and its per-capita values are skipped.',
          '- Provinces are resolved from file content first, then from the publishing office; unresolved rows are rejected.',
          '- Undated snapshots take the dataset\'s last-update year (`warn:year_from_update_date`).']
    (CANON / 'quality_report.md').write_text('\n'.join(L), encoding='utf-8')


# ---------------------------------------------------------------- external: NSO tables downloaded by tools/steps/fetch_nso_catalog.py
NSO_CSV = ROOT / 'data' / 'external' / 'nso' / 'csv'
REGION6 = {  # 6-region scheme (North 9, Northeast 20, Central 22, East 7, West 5, South 14); general knowledge, not from the downloaded data
    'north': [50, 51, 52, 53, 54, 55, 56, 57, 58], 'northeast': list(range(30, 50)),
    'central': [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 26, 60, 61, 62, 64, 65, 66, 67, 72, 73, 74, 75],
    'east': [20, 21, 22, 23, 24, 25, 27], 'west': [63, 70, 71, 76, 77], 'south': [80, 81, 82, 83, 84, 85, 86, 90, 91, 92, 93, 94, 95, 96]}
REGION_OF = {c: r for r, cs in REGION6.items() for c in cs}
assert len(REGION_OF) == 77 and set(REGION_OF) == set(CODE2NAME)


def _nso(name):
    with open(NSO_CSV / f'{name}-ข้อมูล.csv', encoding='utf-8', newline='') as f: return list(csv.reader(f))


def stage_external():
    """NSO wide tables -> data/canonical/external_nso_province_year.csv (province, year_be, variable, value, unit, source_table)."""
    out = []
    def add(code, y, var, v, unit, src):
        if code is not None and v is not None: out.append(dict(province_code=code, province_th=CODE2NAME[code], year_be=y, variable=var, value=round(v, 6), unit=unit, source_table=src))
    for name, var, unit in (('ib', 'divorces_registered', 'persons'), ('ya', 'marriages_registered', 'couples')):   # NSO titles: หย่า (คน) / สมรส (คู่)
        rows = _nso(name); hi = next(i for i, r in enumerate(rows) if r and r[0].strip() == 'ภาค')
        years = [int(re.sub(r'\D', '', c)) for c in rows[hi][2:] if re.sub(r'\D', '', c)]
        for r in rows[hi + 1:]:
            code = norm_prov(r[1]) if len(r) > 1 else None
            for y, cell in zip(years, r[2:]): add(code, y, var, to_num(cell), unit, f'nso:{name}')
    rows = _nso('i7'); hi = next(i for i, r in enumerate(rows) if r and r[0].strip() == 'ภาค'); years = [int(c) for c in rows[hi][3:] if c.strip().isdigit()]
    items = {'ประชากรรวม': ('pop_registry', 'persons'), 'ประชากรชาย': ('pop_male', 'persons'), 'ประชากรหญิง': ('pop_female', 'persons'), 'เนื้อที่ (ตร.กม.)': ('area_km2', 'km2'),
             'ความหนาแน่น ต่อ ตร.กม.': ('density_per_km2', 'per km2'), 'จำนวนบ้าน': ('houses', 'houses')}
    code = None
    for r in rows[hi + 1:]:
        if len(r) > 2 and r[1].strip(): code = norm_prov(r[1])
        if len(r) > 2 and r[2].strip() in items:
            var, unit = items[r[2].strip()]
            for y, cell in zip(years, r[3:]): add(code, y, var, to_num(cell), unit, 'nso:i7')
    rows = _nso('AQ'); hi = next(i for i, r in enumerate(rows) if r and r[0].strip() == 'ภาค')
    prov = admin = yr = None; tot, con, pct = collections.defaultdict(list), collections.defaultdict(list), collections.defaultdict(list)
    for r in rows[hi + 1:]:
        if len(r) < 8: continue
        if r[1].strip(): prov, admin, yr = norm_prov(r[1]), None, None   # a new area block starts: forget the previous block's admin type / year
        if r[2].strip(): admin = r[2].strip()
        if r[3].strip().isdigit(): yr = int(r[3])
        h, c, pc = to_num(r[5]), to_num(r[6]), to_num(r[7])
        if prov is None or yr is None or h is None: continue
        tot[(prov, yr, admin)].append(h)
        if admin in (None, '', 'รวม') and pc is not None: pct[(prov, yr)].append(pc)   # province rows carry no urban/rural split, only the all-areas figure
    for (c, y), v in pct.items(): add(c, y, 'internet_households_pct', statistics.fmean(v), 'percent', 'nso:AQ(mean of quarters)')
    # Ministry of Energy: electricity use / users per province (data.go.th dataset_41_075), one resource per fiscal year, year in the resource name (-61 ... -66)
    for m in read_rows(OPEN / 'manifest.csv'):
        if m['dataset_name'] != 'dataset_41_075' or not m['local_csv']: continue
        ym = re.search(r'-(\d\d)$', m['resource_name'].strip()); kind = 'kwh' if m['resource_name'].startswith('ปริมาณ') else 'users' if m['resource_name'].startswith('จำนวนผู้ใช้') else None
        if not ym or not kind: continue
        year = 2500 + int(ym.group(1))
        with open(ROOT / m['local_csv'].split(' | ')[0], encoding='utf-8', newline='') as f:
            rd = csv.reader(f); header = next(rd)
            for r in rd:
                code = norm_prov(r[1]) if len(r) > 1 else None
                if code is None: continue
                vals = [to_num(c) for c in r[2:]]
                res, tot = vals[0], sum(v for v in vals if v is not None)   # first column = residential ("บ้านอยู่อาศัย"); total = all customer classes
                add(code, year, f'electricity_{kind}_residential', res, kind, 'moen:dataset_41_075'); add(code, year, f'electricity_{kind}_total', tot, kind, 'moen:dataset_41_075')
    write_csv(CANON / 'external_nso_province_year.csv', out)
    n = collections.Counter(r['variable'] for r in out)
    print('external NSO variables:', dict(n)); return out


# ---------------------------------------------------------------- curated panel (hand-verified variables only)
# Automatic keyword mapping cannot be trusted (e.g. "คดี" matched a lawyer-fee dataset, "เซเว่น" matched a church). So the analysis panel uses an explicit,
# reviewed list. Each entry must be checked against data/open/manifest.csv (publisher, licence) before it is used for anything serious.
PANEL_SPEC = [
    # name, dataset_name, label regex, unit, per_capita?, note
    ('gpp_per_capita_baht', 'dataset_66_84', r'^ผลิตภัณฑ์มวลรวมภายในจังหวัด ณ ราคาตลาด ต่อหัวต่อหนึ่งปี', 'บาท', False, 'GPP per head (NESDC figures re-published by a provincial office); wealth/urbanisation proxy'),
    ('household_income_baht_month', '0705_08_0031', r'^รายได้ทั้งสิ้นต่อเดือน / รวมทั้งสิ้น / รวมทั้งสิ้น$', 'บาท', False, 'NSO socio-economic survey: mean total household income per month'),
    ('gas_station_traders_per_100k', 'doeb04062_11', r'ผู้ค้าน้ำมันตามมาตรา 11', 'records', True, 'licensed fuel traders (section 11) per 100k residents; a proxy for stations, not an exact count'),
    ('buddhist_temples_per_100k', 'mr_30309_0331', r'ศาสนาพุทธ$', 'แห่ง', True, 'Dept. of Religious Affairs, Buddhist religious sites per 100k residents'),
    ('sports_facilities_per_100k', 'mots_stadiums', r'สนามกีฬา \(record count\)', '', True, 'records in the sports-stadium registry per 100k residents; registry completeness unknown'),
]


def source_path(sid):
    """source_id (tools/sources.csv) -> csv file on disk: downloaded sources live in data/sources/<id>.csv, builtin ones keep their own path."""
    for r in read_rows(ROOT / 'tools' / 'sources.csv'):
        if r['source_id'].strip() == sid: return ROOT / r['path'].strip() if r['kind'].strip() == 'builtin' else ROOT / 'data' / 'sources' / f'{sid}.csv'
    return None


def custom_variables(panel, pop, spec_rows):
    """Province variables declared in tools/province_variables.csv (one row each) -> new columns of the panel. Nothing here is hard-coded:
    source_id (tools/sources.csv), province_col, value_col, optional year_col / filter, agg = value|sum|mean|count, per = ''|100k|1000."""
    f = ROOT / 'tools' / 'province_variables.csv'
    if not f.exists(): return
    for r in read_rows(f):
        name = (r.get('variable') or '').strip()
        if not name or name.startswith('#'): continue
        def bad(why): spec_rows.append(dict(variable=name, status='FAILED', dataset=r['source_id'], note=why)); print(f'  custom variable {name}: {why}')
        path = source_path(r['source_id'].strip())
        if not path or not path.exists(): bad('source file missing - run pipeline.py fetch (or check source_id in tools/sources.csv)'); continue
        rows = list(read_rows(path))
        if not rows: bad('source is empty'); continue
        need = [c for c in (r['province_col'], r['value_col'] if r['agg'] != 'count' else None, r['year_col'] or None, r['filter_col'] or None) if c]
        miss = [c for c in need if c not in rows[0]]
        if miss: bad(f'column(s) not in the file: {miss}; available: {list(rows[0])[:12]}'); continue
        vals = collections.defaultdict(lambda: collections.defaultdict(list))   # year -> province -> values
        for x in rows:
            if r['filter_col'] and x[r['filter_col']].strip() != r['filter_value'].strip(): continue
            c = norm_prov(x[r['province_col']])
            if c is None or c not in panel: continue
            y = year_of(x[r['year_col']])[0] if r['year_col'] else 0
            if r['year_col'] and y is None: continue
            v = 1.0 if r['agg'] == 'count' else to_num(x[r['value_col']])
            if v is not None: vals[y][c].append(v)
        if not vals: bad('no usable rows (province names not recognised or values not numeric)'); continue
        yr = max((y for y in vals if len(vals[y]) >= 60), default=max(vals, key=lambda y: (len(vals[y]), y)))   # newest year that covers (nearly) all provinces
        agg = {'sum': sum, 'count': sum, 'mean': statistics.fmean, 'value': statistics.fmean}[r['agg'] or 'value']
        n = 0
        for c, v in vals[yr].items():
            x = agg(v)
            if r['per']:
                py = min(pop.get(c, {}), key=lambda k: abs(k - (yr or 2568)), default=None)
                if py is None or (yr and abs(py - yr) > 2): continue
                x = x * (100000 if r['per'] == '100k' else 1000) / pop[c][py]
            panel[c][name] = round(x, 4); n += 1
        spec_rows.append(dict(variable=name, status='ok', dataset=r['source_id'], label=r['value_col'], year_be=yr or '', n_provinces=n, note=r['note']))


def spearman(x, y):
    def rank(v):
        o = sorted(range(len(v)), key=lambda i: v[i]); r = [0.0] * len(v); i = 0
        while i < len(o):
            j = i
            while j + 1 < len(o) and v[o[j + 1]] == v[o[i]]: j += 1
            for k in range(i, j + 1): r[o[k]] = (i + j) / 2 + 1
            i = j + 1
        return r
    rx, ry = rank(x), rank(y); mx, my = statistics.fmean(rx), statistics.fmean(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry)); sxx = sum((a - mx) ** 2 for a in rx); syy = sum((b - my) ** 2 for b in ry)
    return sxy / math.sqrt(sxx * syy) if sxx and syy else float('nan')


def p_approx(r, n):
    """Two-sided p for a correlation via t -> normal approximation (fine for n ~ 70; exploratory only)."""
    if n < 5 or abs(r) >= 1: return float('nan')
    t = abs(r) * math.sqrt((n - 2) / (1 - r * r)); z = t * (1 - 1 / (4 * (n - 2))) / math.sqrt(1 + t * t / (2 * (n - 2)))
    return math.erfc(z / math.sqrt(2))


def stage_curated(args):
    tgt = list(read_rows(CANON / 'target_dm_province_year.csv'))
    by_year = collections.Counter(int(r['year_be']) for r in tgt)
    ty = max(y for y, n in by_year.items() if n >= 76 and y < CUR_YEAR_BE)   # latest *complete* year; the newest year is partial
    t = {int(r['province_code']): r for r in tgt if int(r['year_be']) == ty}
    ext = collections.defaultdict(dict)   # variable -> (province, year) -> value, from the NSO tables
    for r in read_rows(CANON / 'external_nso_province_year.csv'): ext[r['variable']][(int(r['province_code']), int(r['year_be']))] = float(r['value'])
    pop = {}
    for (c, y), v in ext['pop_registry'].items(): pop.setdefault(c, {})[y] = v
    cand = collections.defaultdict(lambda: collections.defaultdict(dict))   # spec name -> (dataset,label,year) -> province -> value
    for r in read_rows(CANON / 'fact_province_year.csv'):
        for name, ds, rx, unit, pc, note in PANEL_SPEC:
            if r['dataset_name'] == ds and re.search(rx, r['indicator_label']) and (not unit or r['unit'] == unit):
                lab = rx if rx.startswith('^') and not rx.endswith('$') else r['indicator_label']   # prefix pattern: merge labels that differ only by a region suffix
                cand[name][(ds, lab, int(r['year_be']))][int(r['province_code'])] = float(r['value'])
    panel = {c: dict(province_code=c, province_th=n, coastal=int(c in COASTAL)) for c, n in PROV}
    spec_rows = []
    for c, row in t.items():
        panel[c].update(dm_year_be=ty, dm_prev_age_std_pct=row['dm_prev_pct_15plus_age_standardised'], dm_prev_crude_pct=row['dm_prev_pct_15plus_crude'],
                        pop_all_ages=row['pop_all_ages'])
    # share of residents aged 60+ is rebuilt from the HDC age-group tables (population per group)
    grp = collections.defaultdict(dict)
    for r in read_rows(PIPE / '03_validated' / 'obs_valid.csv'):
        if r['dataset_name'] == 's_dm_pop_age' and r['year_be'] == str(ty) and r['geo_level'] == 'province' and r['qc_status'] != 'reject' and r['indicator_label'].startswith('pop_group'):
            grp[int(r['province_code'])][r['indicator_label'][:10]] = float(r['value'])
    for c, g in grp.items():
        if len(g) == 5 and c in panel and sum(g.values()) > 0: panel[c]['share_age_60plus_pct'] = round(100 * g['pop_group5'] / sum(g.values()), 3)
    for name, ds, rx, unit, per_cap, note in PANEL_SPEC:
        options = cand.get(name)
        if not options: spec_rows.append(dict(variable=name, status='NOT FOUND', dataset=ds, note=note)); continue
        key = max(options, key=lambda k: (len(options[k]), k[2]))   # most provinces, then newest year
        vals = options[key]; yr = key[2]
        for c, v in vals.items():
            if c not in panel: continue
            if per_cap:
                years = [y for y in pop.get(c, {}) if y <= max(yr, min(pop.get(c, {0: 0}))) ] or list(pop.get(c, {}))
                if not years: continue
                py = min(years, key=lambda y: abs(y - yr))
                if abs(py - yr) > 2: continue   # no population close to the variable's year (e.g. Bangkok is only partly in HDC): do not divide by a wrong number
                v = 100000 * v / pop[c][py]
            panel[c][name] = round(v, 4)
        spec_rows.append(dict(variable=name, status='ok', dataset=ds, label=key[1][:100], year_be=yr, n_provinces=len(vals), note=note))
    def latest(var, min_prov=70):
        yrs = collections.Counter(y for (_, y) in ext[var]); ok = [y for y, n in yrs.items() if n >= min_prov]
        return max(ok) if ok else None
    for var, name, per1000 in (('divorces_registered', 'divorce_rate_per_1000', True), ('marriages_registered', 'marriage_rate_per_1000', True),
                               ('density_per_km2', 'density_per_km2', False), ('internet_households_pct', 'internet_households_pct', False)):
        y = latest(var)
        if y is None: spec_rows.append(dict(variable=name, status='NOT FOUND', dataset='nso', note='')); continue
        n = 0
        for c in panel:
            v = ext[var].get((c, y))
            if v is None: continue
            if per1000:
                pp = ext['pop_registry'].get((c, y))
                if not pp: continue
                v = 1000 * v / pp
            panel[c][name] = round(v, 4); n += 1
        spec_rows.append(dict(variable=name, status='ok', dataset='nso:' + var, label=var, year_be=y, n_provinces=n, note='NSO (DOPA registry based) table; rate per 1,000 registered residents' if per1000 else 'NSO table'))
    ey = latest('electricity_kwh_total')   # electricity per resident (registry population of the same year)
    if ey:
        for var, name in (('electricity_kwh_residential', 'electricity_residential_kwh_per_capita'), ('electricity_kwh_total', 'electricity_total_kwh_per_capita')):
            n = 0
            for c in panel:
                v, pp = ext[var].get((c, ey)), ext['pop_registry'].get((c, ey))
                if v is not None and pp: panel[c][name] = round(v / pp, 3); n += 1
            spec_rows.append(dict(variable=name, status='ok', dataset='moen:dataset_41_075', label=var, year_be=ey, n_provinces=n, note='Ministry of Energy electricity use per province / NSO registry population (total includes industry: partly an industrial-activity proxy)'))
    custom_variables(panel, pop, spec_rows)
    for c in panel: panel[c]['region'] = REGION_OF[c]
    osm = ROOT / 'data' / 'external' / 'osm_poi_by_province.csv'
    if osm.exists():   # OpenStreetMap counts (volunteer-mapped: completeness varies); per 100k residents
        for r in read_rows(osm):
            c = int(r['province_code']); pp = pop.get(c, {}).get(2568) or pop.get(c, {}).get(max(pop.get(c, {0: 0})))
            if not pp: continue
            for k, v in r.items():
                if k in ('province_code', 'osm_timestamp') or v == '': continue
                panel[c][f'osm_{k}_per_100k'] = round(100000 * float(v) / pp, 4)
        spec_rows.append(dict(variable='osm_*_per_100k', status='ok', dataset='openstreetmap', label='POI counts', year_be='', n_provinces=sum(1 for p in panel.values() if any(k.startswith('osm_') for k in p)),
                              note='OpenStreetMap counts per 100k registry residents; mapping completeness differs between provinces'))
    cols = []
    for p in panel.values():
        for k in p:
            if k not in cols: cols.append(k)
    write_csv(CANON / 'panel_curated.csv', list(panel.values()), cols)
    write_csv(CANON / 'panel_curated_spec.csv', spec_rows)
    # exploratory Spearman correlations with the age-standardised diabetes prevalence (ecological, unadjusted)
    corr = []
    for name in [c for c in cols if c not in ('province_code', 'province_th', 'dm_year_be', 'dm_prev_age_std_pct', 'dm_prev_crude_pct', 'pop_all_ages', 'region')]:
        pairs = [(float(p['dm_prev_age_std_pct']), float(p[name])) for p in panel.values() if p.get('dm_prev_age_std_pct') not in (None, '') and p.get(name) not in (None, '')]
        if len(pairs) < 20: continue
        r = spearman([a for a, _ in pairs], [b for _, b in pairs]); corr.append(dict(variable=name, n_provinces=len(pairs), spearman_rho=round(r, 3), p_approx=round(p_approx(r, len(pairs)), 4)))
    write_csv(CANON / 'exploratory_correlations.csv', corr)
    print(f'curated panel: target year {ty}; ' + ', '.join(f'{r["variable"]}={r.get("n_provinces", "-")}' for r in spec_rows))
    for r in corr: print(f'  {r["variable"]:34} n={r["n_provinces"]:3d} rho={r["spearman_rho"]:+.2f} p~{r["p_approx"]}')


# ---------------------------------------------------------------- driver
STEPS = Path(__file__).resolve().parent / 'steps'
NSO_PATTERN = 'หย่า|สมรส|เนื้อที่ ความหนาแน่น|เชื่อมต่ออินเทอร์เน็ต จำแนกตามภาค จังหวัด'
BROWSER_UA = 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0'   # NSO's firewall answers 418 to non-browser agents


def run_step(script, *args):
    print('$', script, *args, flush=True)
    code = subprocess.run([sys.executable, '-I', str(STEPS / script if (STEPS / script).exists() else STEPS.parent / script), *args]).returncode
    if code: sys.exit(f'{script} failed with exit code {code}')


def stage_fetch(a):
    run_step('fetch_opendata.py')                                                      # data.go.th: search + download + convert to UTF-8 csv
    run_step('fetch_opendata.py', '--retry-failed', '--ua', BROWSER_UA, '--delay', '1.5')   # retry the files NSO blocked (418/403), politely
    run_step('fetch_nso_catalog.py', 'index')                                           # NSO catalogue index
    run_step('fetch_nso_catalog.py', 'get', NSO_PATTERN)                                # NSO tables used in the analysis
    run_step('fetch_sources.py')                                                        # your own datasets: rows with a url in tools/sources.csv
    if a.osm: run_step('fetch_osm_poi.py')                                              # optional: OpenStreetMap counts (public server is often busy; rerun resumes)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('stage', choices=['fetch', 'parse', 'stage', 'validate', 'external', 'canonical', 'curated', 'analyze', 'model', 'all', 'full'])
    ap.add_argument('--osm', action='store_true', help='with fetch/full: also download OpenStreetMap counts')
    ap.add_argument('--topics', help='comma-separated topics (default: all)')
    ap.add_argument('--limit', type=int, help='max files per topic (for quick tests)')
    a = ap.parse_args()
    if a.stage in ('fetch', 'full'): stage_fetch(a)
    if a.stage == 'fetch': return
    if a.stage == 'analyze': return run_step('analyze.py')
    if a.stage == 'model': return run_step('build.py')   # model.js for the quiz
    full = a.stage == 'full'
    if full: a.stage = 'all'
    with open(OPEN / 'manifest.csv', encoding='utf-8-sig') as f: rows = [r for r in csv.DictReader(f) if r['local_csv']]
    if a.topics: rows = [r for r in rows if r['topic'] in a.topics.split(',')]
    if a.limit:
        seen = collections.Counter(); keep = []
        for r in rows:
            if seen[r['topic']] < a.limit: keep.append(r); seen[r['topic']] += 1
        rows = keep
    profile = stats = None
    load_profile = lambda: list(read_rows(PIPE / '01_parsed' / 'profile.csv'))
    if a.stage in ('parse', 'all'): print('[1/4] parse', flush=True); profile = stage_parse(rows, a)
    if a.stage in ('stage', 'all'):
        print('[2/4] stage', flush=True); profile = profile or load_profile(); stats = stage_stage(rows, profile, a)
    if a.stage in ('validate', 'all'): print('[3/4] validate', flush=True); stage_validate(a)
    if a.stage in ('external', 'all'): stage_external()   # must run before canonical: the curated panel reads its output
    if a.stage == 'curated': stage_curated(a)
    if a.stage in ('canonical', 'all'):
        print('[4/4] canonical', flush=True); profile = profile or load_profile()
        facts, catalog, tgt, best = stage_canonical(a)
        write_report(profile, stats, facts, catalog, tgt, best)
        stage_curated(a)
        print(f'canonical: {len(facts)} facts, {len(catalog)} indicators, {len(tgt)} target rows -> {CANON.relative_to(ROOT)}/', flush=True)
    if full: run_step('analyze.py'); run_step('build.py')


if __name__ == '__main__':
    main()
