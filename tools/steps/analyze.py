"""Exploratory province-level analysis: diabetes prevalence vs candidate variables, WITH controls.

  python3 -I tools/steps/analyze.py          # reads data/canonical/panel_curated.csv, writes model_results.csv + analysis_report.md

Design (deliberately conservative):
  Y  = registered diabetes prevalence, age-standardised, adults 15+ (HDC; 76 provinces, Bangkok missing)
  Controls = region (6-region scheme, fixed effects), log population density, share of residents aged 60+
  For every candidate X: Y ~ controls + X (X standardised, so beta = change in prevalence points per +1 SD). HC3 robust standard errors.
  p-values are adjusted over all candidates (Holm = family-wise, BH = false discovery rate), because trying many variables produces
  'significant' ones by chance. A leverage check refits without the 5 most influential provinces.
This is an ECOLOGICAL analysis (76 provinces): it cannot show that anything causes diabetes in a person, and it cannot see spatial spill-over.
"""
import collections, csv, math, statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent   # repo root (this file lives in tools/steps/)
CANON = ROOT / 'data' / 'canonical'

# ---------------------------------------------------------------- small linear-algebra / stats helpers (no numpy available)
def inv(A):
    n = len(A); M = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) < 1e-12: raise ValueError('singular matrix (perfectly collinear columns)')
        M[c], M[p] = M[p], M[c]; d = M[c][c]; M[c] = [x / d for x in M[c]]
        for r in range(n):
            if r != c:
                f = M[r][c]
                if f: M[r] = [a - f * b for a, b in zip(M[r], M[c])]
    return [row[n:] for row in M]


def betacf(a, b, x):
    tiny = 1e-30; qab, qap, qam = a + b, a + 1, a - 1; c = 1.0; d = 1 - qab * x / qap; d = tiny if abs(d) < tiny else d; d = 1 / d; h = d
    for m in range(1, 300):
        m2 = 2 * m; aa = m * (b - m) * x / ((qam + m2) * (a + m2)); d = 1 + aa * d; d = tiny if abs(d) < tiny else d; c = 1 + aa / c; c = tiny if abs(c) < tiny else c; d = 1 / d; h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2)); d = 1 + aa * d; d = tiny if abs(d) < tiny else d; c = 1 + aa / c; c = tiny if abs(c) < tiny else c; d = 1 / d; delta = d * c; h *= delta
        if abs(delta - 1) < 3e-12: break
    return h


def betai(a, b, x):
    if x <= 0: return 0.0
    if x >= 1: return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x))
    return bt * betacf(a, b, x) / a if x < (a + 1) / (a + b + 2) else 1 - bt * betacf(b, a, 1 - x) / b


def p_two_sided(t, df): return betai(df / 2, 0.5, df / (df + t * t))


def ols(X, y):
    """X: list of rows (with intercept column). Returns beta, HC3 se, r2, adj_r2, hat leverages."""
    n, k = len(X), len(X[0])
    XtX = [[sum(X[r][i] * X[r][j] for r in range(n)) for j in range(k)] for i in range(k)]; A = inv(XtX)
    Xty = [sum(X[r][i] * y[r] for r in range(n)) for i in range(k)]; beta = [sum(A[i][j] * Xty[j] for j in range(k)) for i in range(k)]
    fit = [sum(b * x for b, x in zip(beta, row)) for row in X]; e = [yy - f for yy, f in zip(y, fit)]
    h = [sum(X[r][i] * sum(A[i][j] * X[r][j] for j in range(k)) for i in range(k)) for r in range(n)]
    meat = [[0.0] * k for _ in range(k)]
    for r in range(n):
        w = (e[r] / max(1 - h[r], 1e-6)) ** 2   # h≈1 means one observation determines its own fit (e.g. the only coastal province in a region): flagged below
        for i in range(k):
            for j in range(k): meat[i][j] += w * X[r][i] * X[r][j]
    V = [[sum(A[i][a] * meat[a][b] * A[b][j] for a in range(k) for b in range(k)) for j in range(k)] for i in range(k)]
    ybar = statistics.fmean(y); sst = sum((v - ybar) ** 2 for v in y); sse = sum(v * v for v in e)
    r2 = 1 - sse / sst; adj = 1 - (1 - r2) * (n - 1) / (n - k)
    return beta, [math.sqrt(max(V[i][i], 0)) for i in range(k)], r2, adj, h


def z(v):
    m, s = statistics.fmean(v), statistics.pstdev(v); return [(a - m) / s for a in v] if s else [0.0] * len(v)


def holm(ps):
    order = sorted(range(len(ps)), key=lambda i: ps[i]); out = [None] * len(ps); run = 0.0
    for rank, i in enumerate(order): run = max(run, min(1.0, (len(ps) - rank) * ps[i])); out[i] = run
    return out


def bh(ps):
    n = len(ps); order = sorted(range(n), key=lambda i: ps[i], reverse=True); out = [None] * n; run = 1.0
    for rank, i in enumerate(order): run = min(run, ps[i] * n / (n - rank)); out[i] = run
    return out


def spearman(x, y):
    def rk(v):
        o = sorted(range(len(v)), key=lambda i: v[i]); r = [0.0] * len(v); i = 0
        while i < len(o):
            j = i
            while j + 1 < len(o) and v[o[j + 1]] == v[o[i]]: j += 1
            for k in range(i, j + 1): r[o[k]] = (i + j) / 2 + 1
            i = j + 1
        return r
    a, b = rk(x), rk(y); ma, mb = statistics.fmean(a), statistics.fmean(b)
    return sum((p - ma) * (q - mb) for p, q in zip(a, b)) / math.sqrt(sum((p - ma) ** 2 for p in a) * sum((q - mb) ** 2 for q in b))


# ---------------------------------------------------------------- analysis
CANDIDATES = [  # (column, label, transform)   transform: 'log' | 'log1p' | 'raw'
    ('gas_station_traders_per_100k', 'Fuel traders per 100k (DOEB licences)', 'log'),
    ('buddhist_temples_per_100k', 'Buddhist religious sites per 100k', 'log'),
    ('sports_facilities_per_100k', 'Sports facilities per 100k (registry)', 'log'),
    ('household_income_baht_month', 'Household income per month (NSO)', 'log'),
    ('gpp_per_capita_baht', 'GPP per capita', 'log'),
    ('divorce_rate_per_1000', 'Divorce rate per 1,000', 'raw'),
    ('marriage_rate_per_1000', 'Marriage rate per 1,000', 'raw'),
    ('internet_households_pct', 'Households with internet (%)', 'raw'),
    ('electricity_residential_kwh_per_capita', 'Residential electricity per resident, kWh (MoEn)', 'log'),
    ('electricity_total_kwh_per_capita', 'Total electricity per resident, kWh (MoEn; incl. industry)', 'log'),
    ('coastal', 'Coastal province (0/1)', 'raw'),
]
OSM_COLS = [  # added when the OpenStreetMap download is complete enough
    ('osm_seven_eleven_wikidata_per_100k', '7-Eleven stores per 100k (OSM)'), ('osm_convenience_stores_per_100k', 'Convenience stores per 100k (OSM)'),
    ('osm_fast_food_per_100k', 'Fast-food outlets per 100k (OSM)'), ('osm_restaurants_per_100k', 'Restaurants per 100k (OSM)'), ('osm_cafes_per_100k', 'Cafes per 100k (OSM)'),
    ('osm_fuel_stations_per_100k', 'Fuel stations per 100k (OSM)'), ('osm_fitness_centres_per_100k', 'Fitness centres per 100k (OSM)'),
    ('osm_cinemas_per_100k', 'Cinemas per 100k (OSM)'), ('osm_pharmacies_per_100k', 'Pharmacies per 100k (OSM)'),
]


def main():
    with open(CANON / 'panel_curated.csv', encoding='utf-8-sig', newline='') as f: P = list(csv.DictReader(f))
    cands = list(CANDIDATES)
    for col, lab in OSM_COLS:
        if sum(1 for r in P if r.get(col) not in (None, '') and r.get('dm_prev_age_std_pct')) >= 70: cands.append((col, lab, 'log1p'))
    regions = sorted({r['region'] for r in P}); base = 'central'
    def num(r, c): return float(r[c]) if r.get(c) not in (None, '') else None
    ctrl_names = [f'region={g}' for g in regions if g != base] + ['log_density', 'share_age_60plus_pct']
    def controls(r):
        return [1.0 if r['region'] == g else 0.0 for g in regions if g != base] + [math.log(num(r, 'density_per_km2')), num(r, 'share_age_60plus_pct')]
    def fit(rows, xcols):
        y = [num(r, 'dm_prev_age_std_pct') for r in rows]
        X = [[1.0] + controls(r) + [x(r) for x in xcols] for r in rows]
        return ols(X, y), y
    base_rows = [r for r in P if num(r, 'dm_prev_age_std_pct') is not None and num(r, 'density_per_km2') and num(r, 'share_age_60plus_pct') is not None]
    (b0, se0, r20, adj0, _), _ = fit(base_rows, [])
    res = []
    for col, lab, tr in cands:
        rows = [r for r in base_rows if num(r, col) is not None]
        if len(rows) < 60: continue
        raw = [num(r, col) for r in rows]
        val = [math.log(v) for v in raw] if tr == 'log' and min(raw) > 0 else [math.log1p(v) for v in raw] if tr == 'log1p' else raw
        sd = z(val); idx = {id(r): i for i, r in enumerate(rows)}
        getx = lambda r: sd[idx[id(r)]]
        (b, se, r2, adj, h), y = fit(rows, [getx]); k = len(b); t = b[-1] / se[-1]; p = p_two_sided(t, len(rows) - k)
        (bb, _, r2_ctrl, _, _), _ = fit(rows, [])
        # leverage check: drop the 5 provinces with the largest leverage and refit
        drop = {id(rows[i]) for i in sorted(range(len(rows)), key=lambda i: -h[i])[:5]}
        sub = [r for r in rows if id(r) not in drop]; ssd = z([val[idx[id(r)]] for r in sub]); sidx = {id(r): i for i, r in enumerate(sub)}
        (b2, se2, _, _, h2), _ = fit(sub, [lambda r: ssd[sidx[id(r)]]]); unreliable = max(h2) > 0.99
        res.append(dict(variable=col, label=lab, n=len(rows), transform=tr, unadjusted_spearman=round(spearman(val, [num(r, 'dm_prev_age_std_pct') for r in rows]), 3),
                        beta_per_sd=round(b[-1], 4), se_hc3=round(se[-1], 4), t=round(t, 2), p=p, delta_r2=round(r2 - r2_ctrl, 4),
                        beta_without_top5_leverage=round(b2[-1], 4), p_without_top5=(float('nan') if unreliable else p_two_sided(b2[-1] / se2[-1], len(sub) - k))))
    ps = [r['p'] for r in res]; hh, bq = holm(ps), bh(ps)
    for r, a, q in zip(res, hh, bq): r['p_holm'], r['q_bh'] = a, q
    res.sort(key=lambda r: r['p'])
    for r in res:
        for k in ('p', 'p_holm', 'q_bh', 'p_without_top5'): r[k] = round(r[k], 4)
        r['verdict'] = ('survives multiple-testing (Holm<0.05)' if r['p_holm'] < 0.05 else 'FDR<0.10 only' if r['q_bh'] < 0.10 else 'not distinguishable from noise')
        if r['p_holm'] < 0.10 and (r['beta_without_top5_leverage'] * r['beta_per_sd'] < 0 or not r['p_without_top5'] <= 0.10): r['verdict'] += ' | FRAGILE: changes when 5 high-leverage provinces are dropped'
    with open(CANON / 'model_results.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, list(res[0])); w.writeheader(); w.writerows(res)
    # joint model on the candidates available for (almost) all provinces
    joint = [(c, l, t) for c, l, t in cands if sum(1 for r in base_rows if num(r, c) is not None) == len(base_rows)]
    rows = base_rows; cols = []
    for c, l, t in joint:
        raw = [num(r, c) for r in rows]; v = [math.log(a) for a in raw] if t == 'log' and min(raw) > 0 else [math.log1p(a) for a in raw] if t == 'log1p' else raw
        cols.append((c, z(v)))
    X = [[1.0] + controls(r) + [cv[i] for _, cv in cols] for i, r in enumerate(rows)]; y = [num(r, 'dm_prev_age_std_pct') for r in rows]
    jb, jse, jr2, jadj, _ = ols(X, y); names = ['intercept'] + ctrl_names + [c for c, _ in cols]
    with open(CANON / 'model_joint.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['term', 'beta', 'se_hc3', 'p']); [w.writerow([n, round(b, 4), round(s, 4), round(p_two_sided(b / s, len(rows) - len(names)), 4)]) for n, b, s in zip(names, jb, jse)]
    L = ['# Province-level diabetes analysis (exploratory)', '',
         f'Outcome: age-standardised registered diabetes prevalence, adults 15+, HDC. n = {len(base_rows)} provinces (Bangkok has no HDC target).',
         f'Controls only (region FE, log density, share 60+): R² = {r20:.2f} (adj {adj0:.2f}).', '',
         '| variable | n | unadjusted ρ | β per +1 SD | p | Holm p | BH q | ΔR² | verdict |', '|---|---|---|---|---|---|---|---|---|']
    L += [f'| {r["label"]} | {r["n"]} | {r["unadjusted_spearman"]:+.2f} | {r["beta_per_sd"]:+.3f} | {r["p"]} | {r["p_holm"]} | {r["q_bh"]} | {r["delta_r2"]} | {r["verdict"]} |' for r in res]
    L += ['', f'Joint model (all candidates with full coverage + controls): R² = {jr2:.2f}, adj R² = {jadj:.2f}; see model_joint.csv.', '',
          '## How to read this', '- β is in percentage points of prevalence per +1 standard deviation of the variable, holding region, density and the share aged 60+ fixed.',
          '- "Unadjusted ρ" is the raw Spearman correlation; compare it with β to see how much the controls changed the picture.',
          '- Holm and BH adjust for the number of variables tried. Anything not surviving is consistent with chance.',
          '- Registered prevalence depends on screening and service use, so a variable can look protective simply because people there are tested less.',
          '- 76 provinces and ecological data: no individual-level causal claim, and neighbouring provinces are not independent (no spatial model fitted).']
    (CANON / 'analysis_report.md').write_text('\n'.join(L), encoding='utf-8')
    print('\n'.join(L[:7 + len(res)]))


if __name__ == '__main__':
    main()
