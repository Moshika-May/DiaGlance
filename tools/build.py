"""Build model.js (everything the quiz needs) from data, following the recipes in tools/model_spec.json. Run from anywhere: python3 -I tools/build.py

  survey files (tools/sources.csv -> path)  --model_spec.json: which column answers which question-->  P(x|D), P(x|not D) per answer, hold-out check, TAU
  data/canonical/target_dm_province_year.csv  -> province odds ratios, trend, ranking
  data/canonical/panel_curated.csv + tools/app_province.csv -> province context table shown on the result page
  tools/provinces.csv                          -> English names, neighbouring provinces
To change what the quiz does with new data, edit those spec/csv files, not this script (see tools/README.md)."""
import csv, json, math, re, collections
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent   # repo root, so it works from any cwd
TOOLS = ROOT / 'tools'
SPEC = json.load(open(TOOLS / 'model_spec.json', encoding='utf-8'))
rd = lambda p: csv.DictReader(open(p, encoding='utf-8-sig', newline=''))
SRC = {r['source_id'].strip(): r for r in rd(TOOLS / 'sources.csv')}
def src_file(sid):
    r = SRC[sid]; return ROOT / r['path'].strip() if r['kind'].strip() in ('builtin', 'local') and r['path'].strip() else ROOT / 'data' / 'sources' / f'{sid}.csv'
YN = {'Yes': 'y', 'No': 'n'}

def code(rule, r):
    """One answer code for one survey row according to a rule from model_spec.json (None = missing / not usable)."""
    t = rule['type']
    if t == 'anyyes':
        v = [r[c] for c in rule['cols']]
        return 'y' if 'Yes' in v else ('n' if v[0] in YN else None)
    v = r[rule['col']]
    if t == 'yn': return YN.get(v)
    if t == 'raw': return v or None
    if t == 'map': return rule['map'].get(v, rule.get('default')) if v else None
    if t == 'num':
        if rule.get('extract') == 'first_int':
            m = re.search(r'\d+', v or ''); x = int(m.group()) if m else None
        else:
            try: x = float(v)
            except ValueError: x = None
        if x is None: return None
        for (op, lim), c in zip(rule['thresholds'], rule['codes']):
            if x < lim if op == '<' else x <= lim: return c
        return rule['codes'][-1]
    raise ValueError(f'unknown rule type {t!r}')

F = SPEC['factors']
DATA = [f for f in F if f.get('from')]            # factors estimated from the survey files
KEYS = [f['k'] for f in DATA]
OPTS = {f['k']: [o['v'] for o in f['o']] for f in DATA}
cnt = {k: {o: [0, 0] for o in OPTS[k]} for k in KEYS}   # [n_not_D, n_D]
tot = [0, 0]
ROWS = []   # (d, is-validation-file, [answer code per KEYS]) kept for the hold-out check below
for ds in SPEC['datasets']:
    oc = ds['outcome']
    if not src_file(ds['source_id']).exists(): raise SystemExit(f"missing survey file {src_file(ds['source_id']).relative_to(ROOT)}\n  download it from {SRC[ds['source_id']]['url']} (or the Kaggle extract named in tools/README.md) and save it at that path")
    for r in rd(src_file(ds['source_id'])):
        v = r[oc['col']]
        if not v or any(s in v.lower() for s in oc.get('exclude_contains', [])): continue   # unknown / gestational
        d = int(v == oc['positive']); tot[d] += 1; xs = []
        for f in DATA:
            rule = f['from'].get(ds['id']); x = code(rule, r) if rule else None
            if x in cnt[f['k']]: cnt[f['k']][x][d] += 1
            xs.append(x if x in cnt[f['k']] else None)
        ROWS.append((d, bool(ds.get('validate')), xs))

out = {'prior': SPEC['prior'], 'prevalence': tot[1] / sum(tot), 'q': [], 'n': sum(tot)}
for f in F:
    q = {k: v for k, v in f.items() if k not in ('from', 'source', 'o')}
    if f.get('from'):
        k = f['k']; nN, nD = (sum(cnt[k][o][d] for o in OPTS[k]) for d in (0, 1)); m = len(OPTS[k])
        q['o'] = [{**o, 'pD': (cnt[k][o['v']][1] + 1) / (nD + m), 'pN': (cnt[k][o['v']][0] + 1) / (nN + m)} for o in f['o']]   # Laplace-smoothed
    elif any('or' in o for o in f['o']):   # assumed factor given as odds ratios: P(x|D) is proportional to P(x|not D) * OR
        z = sum(o['pN'] * o['or'] for o in f['o'])
        q['o'] = [{**{k: v for k, v in o.items() if k != 'or'}, 'pD': o['pN'] * o['or'] / z} for o in f['o']]
    else: q['o'] = f['o']                  # assumed factor with both likelihoods written out
    out['q'].append(q)

# ---- province layer: odds ratio vs the nation (prior adjustment), trend, rank, neighbours, context indicators
P = SPEC['province']; PROVS = {int(r['province_code']): r for r in rd(TOOLS / 'provinces.csv')}
def provinces():
    T = ROOT / P['target']
    if not T.exists(): print('NOTE: target file missing -> no province question (run tools/pipeline.py first)'); return [], {}
    ser = collections.defaultdict(dict); pop = {}
    for r in rd(T):
        c, y, a = int(r['province_code']), int(r['year_ce']), r['dm_prev_pct_15plus_age_standardised']; w = float(r['pop_15plus'] or 0)
        if a and w >= P['min_pop15']: ser[c][y] = (float(a), w)
    lo, hi = P['years']; odds = lambda p: p / (1 - p)
    mean = {c: sum(a for y, (a, _) in s.items() if lo <= y <= hi) / sum(1 for y in s if lo <= y <= hi) / 100 for c, s in ser.items() if any(lo <= y <= hi for y in s)}
    wt = {c: sum(w for y, (_, w) in s.items() if lo <= y <= hi) for c, s in ser.items() if c in mean}
    nat = sum(mean[c] * wt[c] for c in mean) / sum(wt.values())
    years = list(range(2013, 2026)); natser = []
    for y in years:
        pts = [(s[y][0], s[y][1]) for s in ser.values() if y in s]
        natser.append(round(sum(a * w for a, w in pts) / sum(w for _, w in pts), 2) if len(pts) >= 60 else None)
    rank = {c: n + 1 for n, c in enumerate(sorted(mean, key=lambda c: -mean[c]))}
    panel = {int(r['province_code']): r for r in rd(ROOT / 'data/canonical/panel_curated.csv')} if (ROOT / 'data/canonical/panel_curated.csv').exists() else {}
    ctx = [r for r in rd(TOOLS / 'app_province.csv')] if (TOOLS / 'app_province.csv').exists() else []
    med = {}
    for r in ctx:
        v = sorted(float(p[r['column']]) for p in panel.values() if p.get(r['column']) not in (None, '')); med[r['column']] = v[len(v) // 2] if v else None
    res = []
    for c, pr in sorted(PROVS.items()):
        res.append({'c': str(c), 'th': pr['th'], 'en': pr['en'], 'nodata': c not in mean,
                    'or': round(math.exp(P['shrink'] * math.log(odds(mean[c]) / odds(nat))), 4) if c in mean else 1.0,
                    'dm': round(mean[c] * 100, 2) if c in mean else None, 'rk': rank.get(c),
                    'nb': [int(x) for x in pr['neighbours'].split()],
                    'tr': [round(ser[c][y][0], 2) if y in ser.get(c, {}) else None for y in years],
                    'x': {r['column']: float(panel[c][r['column']]) if panel.get(c, {}).get(r['column']) not in (None, '') else None for r in ctx}})
    print('province national ref %.2f%%, OR range %.2f-%.2f' % (nat * 100, min(x['or'] for x in res), max(x['or'] for x in res)))
    return res, {'years': years, 'nat': natser, 'natMean': round(nat * 100, 2), 'nRank': len(mean),
                 'cols': [{'k': r['column'], 'th': r['th'], 'en': r['en'], 'u': r['unit'], 'ue': r['unit_en'], 'd': int(r['decimals'] or 0), 'med': med[r['column']]} for r in ctx]}
out['prov'], out['ctx'] = provinces()

# ---- hold-out check and TAU. Naive Bayes multiplies likelihood ratios as if the answers were independent, but BMI / walking / sick-days / heart disease
# overlap, so the product over-states the evidence. Fit one exponent TAU (0<TAU<=1) on a held-out 20% of the validation file(s):
#   P = sigmoid(logit(prior) + TAU * sum ln LR)
def lrtab(rows):   # Laplace-smoothed ln LR per (factor, answer), from the given rows only
    c = {k: {o: [0, 0] for o in OPTS[k]} for k in KEYS}
    for d, _, xs in rows:
        for k, x in zip(KEYS, xs):
            if x is not None: c[k][x][d] += 1
    t = {}
    for k in KEYS:
        nN, nD = (sum(c[k][o][d] for o in OPTS[k]) for d in (0, 1)); m = len(OPTS[k])
        for o in OPTS[k]: t[k, o] = math.log(((c[k][o][1] + 1) / (nD + m)) / ((c[k][o][0] + 1) / (nN + m)))
    return t
def auc(sc):   # rank-sum AUC; sc = [(score, d)]
    sc = sorted(sc); r = 0.0; n1 = sum(d for _, d in sc); i = 0
    while i < len(sc):
        j = i
        while j < len(sc) and sc[j][0] == sc[i][0]: j += 1
        r += (i + j + 1) / 2 * sum(d for _, d in sc[i:j]); i = j
    return (r - n1 * (n1 + 1) / 2) / (n1 * (len(sc) - n1))
train = [r for n, r in enumerate(ROWS) if n % 5]; val = [r for n, r in enumerate(ROWS) if n % 5 == 0 and r[1]]
T = lrtab(train); p0 = sum(r[0] for r in train) / len(train); lg0 = math.log(p0 / (1 - p0))
def score(rows, keys): return [(sum(T[k, x] for k, x in zip(KEYS, xs) if x is not None and k in keys), d) for d, _, xs in rows]
def logloss(sc, tau):
    s = 0
    for z, d in sc:
        p = 1 / (1 + math.exp(-(lg0 + tau * z))); p = min(max(p, 1e-9), 1 - 1e-9); s -= math.log(p if d else 1 - p)
    return s / len(sc)
ALL = {k for k in KEYS if k != 'alc'}   # alcohol is 2020-only and (reverse causation) unhelpful for prediction
sc_all = score(val, ALL)
TAU = min((t / 100 for t in range(20, 101, 5)), key=lambda t: logloss(sc_all, t))
rep = [f'hold-out: {len(val)} respondents (20% of the validation file), prevalence in training pool {p0:.3f}', f'AUC with {len(ALL)} factors {auc(sc_all):.3f}',
       f'log-loss: tau=1 {logloss(sc_all, 1):.4f} | tau={TAU:.2f} {logloss(sc_all, TAU):.4f} | prior only {logloss(sc_all, 0):.4f}', 'calibration at tau (predicted vs observed by decile):']
dec = sorted((lg0 + TAU * z, d) for z, d in sc_all); step = len(dec) // 10
for q in range(10):
    ch = dec[q * step:(q + 1) * step if q < 9 else None]
    rep.append(f'  decile {q + 1}: predicted {sum(1 / (1 + math.exp(-z)) for z, _ in ch) / len(ch):.3f}  observed {sum(d for _, d in ch) / len(ch):.3f}')
print('\n'.join(rep)); out['tau'] = TAU
(ROOT / 'data/canonical').mkdir(parents=True, exist_ok=True); open(ROOT / 'data/canonical/nb_validation.txt', 'w').write('\n'.join(rep) + '\n')

open(ROOT / 'model.js', 'w').write('const MODEL=' + json.dumps(out, ensure_ascii=False, separators=(',', ':')) + ';')
print(tot, out['prevalence'])
