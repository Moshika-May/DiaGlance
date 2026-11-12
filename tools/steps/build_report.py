"""Build the bilingual (Thai + English) project report as ONE PDF.

  python3 -I tools/steps/build_report.py            # -> docs/report/DiaGlance_Report_TH_EN.pdf  (+ .html next to it)

Inputs  : data/canonical/*.csv (made by tools/pipeline.py + tools/steps/analyze.py), docs/../model.js (quiz likelihoods)
Method  : numbers and figures are computed here from those files (nothing typed in by hand except prose), HTML + inline SVG + MathML,
          printed to PDF with headless Chrome (needs /opt/google/chrome/chrome or google-chrome on PATH). Two passes so the table of
          contents carries real page numbers.
Layout  : Part I Thai, Part II English, Appendices bilingual.  No names or course details are printed.
"""
import collections, csv, html, json, math, re, runpy, shutil, statistics, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent   # repo root (this file lives in tools/steps/)
CANON, OUT = ROOT / 'data' / 'canonical', ROOT / 'docs' / 'report'
A = runpy.run_path(str(ROOT / 'tools' / 'steps' / 'analyze.py'))      # reuse the exact estimators used for the results (ols, p_two_sided, spearman, ...)
P_ = runpy.run_path(str(ROOT / 'tools' / 'pipeline.py'))    # province table / regions
DATE_CE, DATE_BE = '12 November 2026', '12 พฤศจิกายน 2569'

# ------------------------------------------------------------------ data
def rd(name): return list(csv.DictReader(open(CANON / name, encoding='utf-8-sig', newline='')))
panel, res_rows, joint_rows, tgt = rd('panel_curated.csv'), rd('model_results.csv'), rd('model_joint.csv'), rd('target_dm_province_year.csv')
manifest = list(csv.DictReader(open(ROOT / 'data/open/manifest.csv', encoding='utf-8-sig', newline='')))
profile = list(csv.DictReader(open(ROOT / 'data/pipeline/01_parsed/profile.csv', encoding='utf-8-sig', newline='')))
qsum = json.loads((ROOT / 'data/pipeline/03_validated/quality_summary.json').read_text(encoding='utf-8'))
qrep = (CANON / 'quality_report.md').read_text(encoding='utf-8')
arep = (CANON / 'analysis_report.md').read_text(encoding='utf-8')
MODEL = json.loads((ROOT / 'model.js').read_text(encoding='utf-8')[len('const MODEL='):].rstrip(';'))
num = lambda r, c: float(r[c]) if r.get(c) not in (None, '') else None
fmt = lambda x, d=2: f'{x:,.{d}f}'
n_facts = int(re.search(r'province-year facts: \*\*([\d,]+)\*\*', qrep).group(1).replace(',', ''))
R2_CTRL, ADJ_CTRL = [float(x) for x in re.search(r'R² = ([\d.]+) \(adj ([\d.]+)\)', arep).groups()]
R2_JOINT, ADJ_JOINT = [float(x) for x in re.search(r'Joint model.*?R² = ([\d.]+), adj R² = ([\d.]+)', arep).groups()]
TY = next(int(r['dm_year_be']) for r in panel if r.get('dm_year_be'))   # target year used in the panel (latest complete year)
REGION_TH = dict(north='ภาคเหนือ', northeast='ภาคตะวันออกเฉียงเหนือ', central='ภาคกลาง', east='ภาคตะวันออก', west='ภาคตะวันตก', south='ภาคใต้')
REGION_EN = dict(north='North', northeast='Northeast', central='Central', east='East', west='West', south='South')
base_rows = [r for r in panel if num(r, 'dm_prev_age_std_pct') is not None]

# the exact model design of tools/steps/analyze.py: controls = region FE (base central) + log density + share 60+
regions = sorted({r['region'] for r in panel}); BASE = 'central'
def controls(r): return [1.0 if r['region'] == g else 0.0 for g in regions if g != BASE] + [math.log(num(r, 'density_per_km2')), num(r, 'share_age_60plus_pct')]
CT_NAMES = [f'region={g}' for g in regions if g != BASE] + ['log_density', 'share_age_60plus_pct']
yv = [num(r, 'dm_prev_age_std_pct') for r in base_rows]
Xc = [[1.0] + controls(r) for r in base_rows]
b_c, se_c, r2_c, adj_c, _ = A['ols'](Xc, yv)
df_c = len(base_rows) - len(b_c)
ctrl_tbl = [(n, b, s, A['p_two_sided'](b / s, df_c)) for n, b, s in zip(['intercept'] + CT_NAMES, b_c, se_c)]

def resid(vec, X):
    b, *_ = A['ols'](X, vec); return [v - sum(bb * x for bb, x in zip(b, row)) for v, row in zip(vec, X)]

# ------------------------------------------------------------------ svg helpers (palette: validated reference palette, light surface)
INK, INK2, GRID, SURF = '#0b0b0b', '#52514e', '#dedcd7', '#ffffff'
BLUE, ORANGE, RED, AQUA, YEL = '#2a78d6', '#eb6834', '#e34948', '#1baf7a', '#eda100'
FONT = "font-family:'Noto Sans Thai','Noto Sans',sans-serif"
esc = html.escape

def svg_open(w, h, title): return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{esc(title)}" style="{FONT};font-size:11px">'
def T(x, y, s, size=11, anchor='start', fill=INK2, weight=400): return f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" fill="{fill}" font-weight="{weight}">{esc(str(s))}</text>'
def L(x1, y1, x2, y2, c=GRID, w=1): return f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{c}" stroke-width="{w}"/>'
def ticks(lo, hi, n=5):
    step = (hi - lo) / n; return [lo + i * step for i in range(n + 1)]

def col_path(x, y, w, y0, r=4):   # column: 4px rounded data end, square at the baseline
    r = min(r, w / 2, max(y0 - y, 0))
    return f'M{x:.1f},{y0:.1f} L{x:.1f},{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} L{x + w - r:.1f},{y:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} L{x + w:.1f},{y0:.1f} Z'

def fig_age(lang):
    labs = ['<15', '15–39', '40–49', '50–59', '60+']
    vals = []
    for g in range(1, 6):
        v = [num(r, f'dm_prev_pct_group{g}') for r in tgt if r['year_be'] == str(TY)]; vals.append(statistics.median([x for x in v if x is not None]))
    W, H, ml, mt, mb = 660, 250, 50, 16, 40; ymax = 25
    s = [svg_open(W, H, 'Prevalence by age group')]
    for t in ticks(0, ymax):
        y = H - mb - (H - mt - mb) * t / ymax; s += [L(ml, y, W - 16, y), T(ml - 8, y + 4, f'{t:.0f}%', anchor='end')]
    slot = (W - ml - 16) / 5
    for i, (lab, v) in enumerate(zip(labs, vals)):
        x = ml + slot * i + (slot - 24) / 2; y = H - mb - (H - mt - mb) * v / ymax
        s += [f'<path d="{col_path(x, y, 24, H - mb)}" fill="{BLUE}"/>', T(x + 12, y - 6, f'{v:.2f}%' if v < 1 else f'{v:.1f}%', 12, 'middle', INK, 600), T(x + 12, H - mb + 18, lab, 12, 'middle')]
    s += [L(ml, H - mb, W - 16, H - mb, INK2), '</svg>']
    return ''.join(s)

def fig_region(lang):
    nm = REGION_TH if lang == 'th' else REGION_EN
    by = collections.defaultdict(list)
    for r in base_rows: by[r['region']].append(num(r, 'dm_prev_age_std_pct'))
    order = sorted(by, key=lambda k: -statistics.fmean(by[k]))
    W, H, ml, mt = 660, 60 + 46 * len(order), 190, 14; lo, hi = 6, 14
    X = lambda v: ml + (W - ml - 20) * (v - lo) / (hi - lo)
    s = [svg_open(W, H, 'Prevalence by region')]
    for t in range(lo, hi + 1, 2): s += [L(X(t), mt, X(t), H - 34), T(X(t), H - 18, f'{t}%', anchor='middle')]
    for i, k in enumerate(order):
        cy = mt + 24 + 46 * i; vs = sorted(by[k])
        s += [T(ml - 12, cy + 4, f'{nm[k]} (n={len(vs)})', 12, 'end', INK)]
        for j, v in enumerate(vs): s.append(f'<circle cx="{X(v):.1f}" cy="{cy + ((j % 3) - 1) * 7:.1f}" r="4" fill="{BLUE}" fill-opacity=".75" stroke="{SURF}" stroke-width="2"/>')
        m = statistics.fmean(vs); s += [L(X(m), cy - 15, X(m), cy + 15, INK, 3), T(X(m), cy - 19, f'{m:.1f}', 11, 'middle', INK, 600)]
    s.append('</svg>')
    return ''.join(s)

def fig_scatter(lang, col='gas_station_traders_per_100k'):
    rows = [r for r in base_rows if num(r, col)]
    xs_raw = [math.log10(num(r, col)) for r in rows]; ys = [num(r, 'dm_prev_age_std_pct') for r in rows]
    rho = A['spearman'](xs_raw, ys)
    zs = A['z']([math.log(num(r, col)) for r in rows])
    Xcc = [[1.0] + controls(r) for r in rows]
    ry, rx = resid(ys, Xcc), resid(zs, Xcc)
    mx, my = statistics.fmean(rx), statistics.fmean(ry); sl = sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / sum((a - mx) ** 2 for a in rx)
    Xf = [[1.0] + controls(r) + [zz] for r, zz in zip(rows, zs)]; bf, sef, *_ = A['ols'](Xf, ys); pval = A['p_two_sided'](bf[-1] / sef[-1], len(rows) - len(bf))
    W, H = 660, 290; s = [svg_open(W, H, 'Raw vs adjusted association')]
    def panel_(x0, x1, xs_, ys_, xlo, xhi, ylo, yhi, xfmt, yfmt, xlab, ylab, line=None, note='', xt=None, yt=None):
        py0, py1 = H - 58, 22; X = lambda v: x0 + (x1 - x0) * (v - xlo) / (xhi - xlo); Y = lambda v: py0 - (py0 - py1) * (v - ylo) / (yhi - ylo)
        out = []
        for t in (yt or ticks(ylo, yhi, 4)): out += [L(x0, Y(t), x1, Y(t)), T(x0 - 6, Y(t) + 4, yfmt(t), 10, 'end')]
        for t in (xt or ticks(xlo, xhi, 4)): out += [T(X(t), py0 + 16, xfmt(t), 10, 'middle')]
        out += [L(x0, py0, x1, py0, INK2)]
        if line: out.append(L(X(xlo), Y(line[0] + line[1] * xlo), X(xhi), Y(line[0] + line[1] * xhi), INK, 2))
        for a, b in zip(xs_, ys_): out.append(f'<circle cx="{X(a):.1f}" cy="{Y(b):.1f}" r="4" fill="{BLUE}" fill-opacity=".7" stroke="{SURF}" stroke-width="2"/>')
        out += [T((x0 + x1) / 2, H - 22, xlab, 11, 'middle'), T(x0, 13, ylab, 11, 'start', INK), T(x0 + 8, 36, note, 12, 'start', INK, 600)]
        return out
    tk = lambda v: f'{10 ** v:,.0f}'
    lab = {'th': ('ผู้ค้าน้ำมันต่อ 100,000 คน (สเกลลอการิทึม)', 'ความชุกเบาหวานปรับอายุ (%)', 'ส่วนที่เหลือของตัวแปร X หลังคุมภาค/ความหนาแน่น/อายุ', 'ส่วนที่เหลือของความชุก (จุด%)'),
           'en': ('Fuel traders per 100,000 (log scale)', 'Age-standardised prevalence (%)', 'Residual of X after region/density/age', 'Residual prevalence (pct. points)')}[lang]
    s += panel_(52, 305, xs_raw, ys, min(xs_raw) - .05, max(xs_raw) + .05, 6, 14, tk, lambda v: f'{v:.0f}', lab[0], lab[1], note=f'Spearman ρ = {rho:+.2f}', xt=[1, math.log10(20), math.log10(50), 2], yt=[6, 8, 10, 12, 14])
    s += panel_(392, 645, rx, ry, -2.4, 1.6, -3.4, 3.4, lambda v: f'{v:+.0f}', lambda v: f'{v:+.0f}', lab[2], lab[3], line=(my - sl * mx, sl), note=f'β = {bf[-1]:+.2f} (p = {pval:.2f})', xt=[-2, -1, 0, 1], yt=[-3, -2, -1, 0, 1, 2, 3])
    s.append('</svg>')
    return ''.join(s), rho, bf[-1], pval

LABEL_TH = dict(gas_station_traders_per_100k='ผู้ค้าน้ำมันต่อ 100k', buddhist_temples_per_100k='ศาสนสถานพุทธต่อ 100k', sports_facilities_per_100k='สนามกีฬาต่อ 100k', household_income_baht_month='รายได้ครัวเรือน/เดือน',
                gpp_per_capita_baht='GPP ต่อหัว', divorce_rate_per_1000='อัตราหย่า ต่อ 1,000', marriage_rate_per_1000='อัตราสมรส ต่อ 1,000', internet_households_pct='ครัวเรือนใช้อินเทอร์เน็ต (%)',
                electricity_residential_kwh_per_capita='ไฟฟ้าที่อยู่อาศัย/คน', electricity_total_kwh_per_capita='ไฟฟ้ารวม/คน (รวมอุตสาหกรรม)', coastal='จังหวัดติดทะเล (0/1)')
LABEL_EN = dict(gas_station_traders_per_100k='Fuel traders per 100k', buddhist_temples_per_100k='Buddhist sites per 100k', sports_facilities_per_100k='Sports facilities per 100k', household_income_baht_month='Household income / month',
                gpp_per_capita_baht='GPP per capita', divorce_rate_per_1000='Divorce rate per 1,000', marriage_rate_per_1000='Marriage rate per 1,000', internet_households_pct='Households with internet (%)',
                electricity_residential_kwh_per_capita='Residential electricity / resident', electricity_total_kwh_per_capita='Total electricity / resident (incl. industry)', coastal='Coastal province (0/1)')

def fig_forest(lang):
    nm = LABEL_TH if lang == 'th' else LABEL_EN; rows = sorted(res_rows, key=lambda r: float(r['p']))
    k = len(b_c) + 1; tcrit = 1.996   # t(0.975, df≈67); df = n - k with k = 1 + 5 region + 2 + X = 9
    W, H, ml, mt = 660, 56 + 28 * len(rows), 232, 12; lo, hi = -1.0, 1.6
    X = lambda v: ml + (W - ml - 150) * (v - lo) / (hi - lo)
    s = [svg_open(W, H, 'Adjusted associations')]
    for t in ticks(lo, hi, 5): s += [L(X(t), mt, X(t), H - 36), T(X(t), H - 20, f'{t:+.1f}', 10, 'middle')]
    s += [L(X(0), mt, X(0), H - 36, INK2, 1.5)]
    for i, r in enumerate(rows):
        cy = mt + 20 + 28 * i; b, se = float(r['beta_per_sd']), float(r['se_hc3']); sig = float(r['q_bh']) < 0.10
        s += [T(ml - 12, cy + 4, nm.get(r['variable'], r['variable']), 11.5, 'end', INK), L(X(b - tcrit * se), cy, X(b + tcrit * se), cy, BLUE, 2),
              f'<circle cx="{X(b):.1f}" cy="{cy:.1f}" r="5" fill="{BLUE if sig else SURF}" stroke="{BLUE}" stroke-width="2"/>',
              T(W - 6, cy + 4, f'{b:+.2f}  [{b - tcrit * se:+.2f}, {b + tcrit * se:+.2f}]  q={float(r["q_bh"]):.2f}', 10.5, 'end', INK2)]
    s += [T(X(lo), H - 4, '← lower prevalence' if lang == 'en' else '← ความชุกต่ำกว่า', 10.5), T(X(hi), H - 4, 'higher prevalence →' if lang == 'en' else 'ความชุกสูงกว่า →', 10.5, 'end'), '</svg>']
    return ''.join(s)

def lr_items():
    out = []
    for q in MODEL['q']:
        if q['k'] == 'sugar': continue
        name_th, name_en = q.get('name') or q['t'], q.get('ne') or q.get('te') or q['k']
        for o in q['o']:
            lr = o['pD'] / o['pN']
            lab_th, lab_en = o['l'], o.get('e') or {'y': 'yes', 'n': 'no'}.get(o['v'], o['v'])
            if q.get('ui') or q.get('grp') or o['v'] in ('y', 'n'): lab_th, lab_en = {'y': 'ใช่', 'n': 'ไม่ใช่'}.get(o['v'], lab_th), {'y': 'yes', 'n': 'no'}.get(o['v'], lab_en)
            out.append(dict(k=q['k'], th=f'{name_th}: {lab_th}', en=f'{name_en}: {lab_en}', lr=lr, pD=o['pD'], pN=o['pN']))
    return out

def fig_lr(lang, n=14):
    items = sorted(lr_items(), key=lambda d: -abs(math.log(d['lr'])))[:n]; items.sort(key=lambda d: -d['lr'])
    W, H, ml, mt = 660, 40 + 24 * len(items), 262, 10; lo, hi = math.log2(.05), math.log2(10)
    X = lambda v: ml + (W - ml - 40) * (math.log2(v) - lo) / (hi - lo)
    s = [svg_open(W, H, 'Likelihood ratios')]
    for t in (.125, .25, .5, 1, 2, 4, 8):   # ticks on a log2 scale around 1
         s += [L(X(t), mt, X(t), H - 28), T(X(t), H - 12, f'×{t:g}' if t != 1 else '×1', 10, 'middle')]
    s.append(L(X(1), mt, X(1), H - 28, INK2, 1.5))
    for i, d in enumerate(items):
        cy = mt + 14 + 24 * i; c = RED if d['lr'] > 1 else BLUE; x0, x1 = sorted((X(1), X(d['lr'])))
        s += [T(ml - 10, cy + 4, d[lang], 11, 'end', INK), f'<rect x="{x0:.1f}" y="{cy - 7:.1f}" width="{max(x1 - x0, 1):.1f}" height="14" rx="3" fill="{c}"/>',
              T((x1 + 6) if d['lr'] > 1 else (x0 - 6), cy + 4, f'×{d["lr"]:.2f}', 10.5, 'start' if d['lr'] > 1 else 'end', INK, 600)]
    s.append('</svg>')
    return ''.join(s)

def seg_bar(lang):
    st = qsum['status']; tot = sum(st.values())
    names = {'pass': ('ผ่าน', 'pass'), 'warn': ('ผ่านพร้อมคำเตือน', 'pass with warning'), 'reject': ('ถูกปฏิเสธ', 'rejected')}
    cols = {'pass': '#0ca30c', 'warn': '#fab219', 'reject': '#d03b3b'}; txtc = {'pass': '#ffffff', 'warn': INK, 'reject': '#ffffff'}
    bar = ''.join(f'<div style="flex:{st[k]};background:{cols[k]};color:{txtc[k]};height:28px;line-height:28px;padding-left:8px;border-radius:4px;margin-right:2px;font-size:11px;font-weight:600;white-space:nowrap;overflow:hidden">{100 * st[k] / tot:.1f}%</div>' if st[k] / tot > .08 else f'<div style="flex:{st[k]};background:{cols[k]};height:28px;border-radius:4px;margin-right:2px;min-width:4px"></div>' for k in ('pass', 'warn', 'reject'))
    leg = ''.join(f'<span style="margin-right:14px"><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:{cols[k]};margin-right:5px"></span>{names[k][0 if lang == "th" else 1]} {st[k]:,}</span>' for k in ('pass', 'warn', 'reject'))
    return f'<div style="display:flex;margin:6px 0">{bar}</div><div style="font-size:10.5px;color:{INK2}">{leg}</div>'


# ------------------------------------------------------------------ html helpers
HEADS = []   # (id, level, th, en) collected while building, used for the table of contents
def mtext(s): return f'<mtext>{s}</mtext>'
def mspace(): return '<mspace width="1.5em"></mspace>'
def mi(s): return f'<mi>{s}</mi>'
def mo(s): return f'<mo>{s}</mo>'
def mn(s): return f'<mn>{s}</mn>'
def row(*a): return '<mrow>' + ''.join(a) + '</mrow>'
def sub(a, b): return f'<msub>{a}{b}</msub>'
def sup(a, b): return f'<msup>{a}{b}</msup>'
def frac(a, b): return f'<mfrac>{row(a)}{row(b)}</mfrac>'
def sumo(lo, hi=''): return f'<munderover><mo>∑</mo>{lo}{hi or "<mrow></mrow>"}</munderover>' if hi else f'<munder><mo>∑</mo>{lo}</munder>'
def prodo(lo): return f'<munder><mo>∏</mo>{lo}</munder>'
def hat(a): return f'<mover>{a}<mo>^</mo></mover>'
def bar(a): return f'<mover>{a}<mo>¯</mo></mover>'
def P(*a): return mi('P') + mo('(') + ''.join(a) + mo(')')
def cond(a, b): return a + mo('|') + b
EQN = [0]
def eq(m):
    EQN[0] += 1
    return f'<div class="eq"><div class="m"><math display="block">{m}</math></div><div class="n">({EQN[0]})</div></div>'
def table(head, rows_, cls='', cap=None, widths=None, right=()):
    ra = lambda i: ' style="text-align:right;font-variant-numeric:tabular-nums"' if i in right else ''
    th = ''.join(f'<th{ra(i)}>{h}</th>' for i, h in enumerate(head))
    body = ''.join('<tr>' + ''.join(f'<td{ra(i)}>{c}</td>' for i, c in enumerate(r)) + '</tr>' for r in rows_)
    colg = f'<colgroup>{"".join(f"<col style=width:{w}>" for w in widths)}</colgroup>' if widths else ''
    return f'<table class="t {cls}">{colg}<thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'
FIGN, TABN = [0], [0]
def figure(svg, cap, lang):
    FIGN[0] += 1; lab = 'รูปที่' if lang == 'th' else 'Figure'
    return f'<figure>{svg}<figcaption><b>{lab} {FIGN[0]}.</b> {cap}</figcaption></figure>'
def tcap(cap, lang):
    TABN[0] += 1; lab = 'ตารางที่' if lang == 'th' else 'Table'
    return f'<div class="tcap"><b>{lab} {TABN[0]}.</b> {cap}</div>'
def box(txt, kind=''): return f'<div class="box {kind}">{txt}</div>'
def pfmt(p): return '<0.001' if p < 0.001 else f'{p:.3f}'
def pearson(a, b):
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))

RR = {r['variable']: r for r in res_rows}
def rf(v, k, d=2): return f'{float(RR[v][k]):.{d}f}'
col = lambda c, f=lambda x: x: [f(num(r, c)) for r in base_rows]
CORR = dict(div_mar=pearson(col('divorce_rate_per_1000'), col('marriage_rate_per_1000')), div_gpp=pearson(col('divorce_rate_per_1000'), col('gpp_per_capita_baht', math.log)),
            elec_gpp=pearson(col('electricity_total_kwh_per_capita', math.log), col('gpp_per_capita_baht', math.log)), div_elec=pearson(col('divorce_rate_per_1000'), col('electricity_total_kwh_per_capita', math.log)))
n_res, n_dl = len(manifest), sum(1 for r in manifest if r['status'] in ('ok', 'cached'))
n_csv = sum(1 for r in profile if r['status'] == 'ok'); lay = collections.Counter(r['layout'] or r['status'] for r in profile)
try: n_osm = sum(1 for _ in open(ROOT / 'data/external/osm_poi_by_province.csv', encoding='utf-8-sig')) - 1
except OSError: n_osm = 0
st = qsum['status']; n_obs = sum(st.values())
sig_joint = [r for r in joint_rows if float(r['p']) < 0.05 and r['term'] != 'intercept']
fragile = [r['variable'] for r in res_rows if float(r['q_bh']) < 0.10 and 'FRAGILE' in r['verdict']]
age_med = [statistics.median([num(r, f'dm_prev_pct_group{g}') for r in tgt if r['year_be'] == str(TY) and num(r, f'dm_prev_pct_group{g}') is not None]) for g in range(1, 6)]
nat = [r for r in tgt if r['year_be'] == str(TY)]
ex_prev, ex_age, ex_walk, ex_bmi = 0.101, 'a5', 'y', 'b4'
def lr_of(k, v):
    q = next(q for q in MODEL['q'] if q['k'] == k); o = next(o for o in q['o'] if o['v'] == v); return o['pD'] / o['pN']
EX = [('Prior', ex_prev / (1 - ex_prev), None)]
odds = ex_prev / (1 - ex_prev); steps = [(odds, None, None)]
for lab_th, lab_en, k, v in (('อายุ 65 ปีขึ้นไป', 'Age 65+', 'age', ex_age), ('BMI 30–34.9', 'BMI 30–34.9', 'bmi', ex_bmi), ('เดิน/ขึ้นบันไดลำบากมาก', 'Serious difficulty walking', 'walk', ex_walk)):
    l = lr_of(k, v); odds *= l; steps.append((odds, l, (lab_th, lab_en)))

CSS = """
@page{size:A4;margin:17mm 16mm 19mm}@page{@bottom-center{content:counter(page);font:8.5pt 'Noto Sans Thai',sans-serif;color:#52514e}}@page:first{@bottom-center{content:none}}
*{box-sizing:border-box}body{font:10.2pt/1.62 'Noto Sans Thai','Noto Sans',sans-serif;color:#0b0b0b;margin:0}
h1{font-size:21pt;line-height:1.25;margin:0 0 8pt}h1.part{break-before:page;border-bottom:2px solid #2a78d6;padding-bottom:6pt;margin-bottom:14pt}
h2{font-size:14pt;margin:17pt 0 6pt;break-after:avoid}h3{font-size:11.2pt;margin:12pt 0 4pt;break-after:avoid}
p{margin:0 0 7pt}ul,ol{margin:0 0 7pt;padding-left:18pt}li{margin-bottom:2.5pt}
table.t{border-collapse:collapse;width:100%;font-size:8.5pt;margin:3pt 0 9pt}table.t th{border-bottom:1.3px solid #0b0b0b;text-align:left;padding:3px 5px;vertical-align:bottom;font-weight:600}
table.t td{border-bottom:.5px solid #dedcd7;padding:3px 5px;vertical-align:top}table.t tr{break-inside:avoid}p.small{break-after:avoid}table.num td:not(:first-child){text-align:right;font-variant-numeric:tabular-nums}
.tcap{font-size:8.9pt;color:#52514e;margin:8pt 0 2pt;break-after:avoid}figure{margin:8pt 0 10pt;break-inside:avoid}figcaption{font-size:8.9pt;color:#52514e;margin-top:3pt}
.eq{display:flex;justify-content:space-between;align-items:center;margin:4pt 0;break-inside:avoid}.eq .m{flex:1;text-align:center}.eq .n{font-size:9pt;color:#52514e;width:30pt;text-align:right}
math{font-family:'STIX Two Math','Noto Sans Math',serif;font-size:11.5pt}.box{border-left:3px solid #2a78d6;background:#f3f7fd;padding:6pt 10pt;margin:8pt 0;break-inside:avoid}.box.warn{border-left-color:#eda100;background:#fff8e8}
.small{font-size:8.6pt;color:#52514e}.title{min-height:245mm;display:flex;flex-direction:column;justify-content:center}.title .kick{color:#2a78d6;font-weight:600;letter-spacing:.04em;font-size:10pt;margin-bottom:10pt}
.kick{color:#2a78d6;font-weight:600;letter-spacing:.04em;font-size:9.5pt;margin-bottom:6pt}.en{font-size:14pt;font-weight:400;color:#52514e}.title h1{font-size:23pt}.title .en{font-size:15pt;color:#52514e;margin:6pt 0 22pt;line-height:1.3}.meta td{padding:4px 10px 4px 0;font-size:10pt;vertical-align:top}.meta td:first-child{color:#52514e;white-space:nowrap}
.toc{width:100%;font-size:10pt;border-collapse:collapse}.toc td{padding:.8pt 0;border-bottom:.5px dotted #b9b8b2}.toc{font-size:9.4pt}.toc td.pg{text-align:right;width:30pt}.toc tr.l2 td:first-child{padding-left:14pt;color:#52514e}
.cols2{column-count:2;column-gap:14mm;font-size:8.3pt}.cols2 table{break-inside:auto}code{font-family:'Noto Sans Mono','DejaVu Sans Mono',monospace;font-size:8.6pt;background:#f1f0ec;padding:0 3px;border-radius:3px}
"""

def heading(lvl, ident, th, en, lang):
    HEADS.append((ident, lvl, th, en)); return f'<h{lvl} id="{ident}">{th if lang == "th" else en}</h{lvl}>'


# ------------------------------------------------------------------ content
def content_a(lang):
    t = (lambda th, en: th if lang == 'th' else en)
    H = lambda lvl, ident, th, en: heading(lvl, ident, th, en, lang)
    EQN[0], FIGN[0], TABN[0] = 0, 0, 0
    RN = REGION_TH if lang == 'th' else REGION_EN
    sc_svg, rho_f, beta_f, p_f = fig_scatter(lang)
    nm = LABEL_TH if lang == 'th' else LABEL_EN
    o = []
    o.append(f'<h1 class="part" id="part-{lang}">{t("ภาษาไทย", "English")}</h1>')

    # ---------- 1 key findings
    sd_med = statistics.median(num(r, "dm_prev_age_std_pct") for r in base_rows); sd_lo = min(num(r, "dm_prev_age_std_pct") for r in base_rows); sd_hi = max(num(r, "dm_prev_age_std_pct") for r in base_rows)
    o.append(H(2, f'{lang}-abs', '1. สรุปประเด็นหลัก', '1. Key findings'))
    o.append('<ul>' + ''.join(f'<li>{x}</li>' for x in (
        t(f'<b>ข้อมูลและตัวแปรเป้าหมาย:</b> รวมข้อมูลเปิดรายจังหวัด {n_res:,} รายการ ได้ข้อเท็จจริงจังหวัด–ปี {n_facts:,} รายการ ตัวแปรเป้าหมายคือความชุกเบาหวานที่ขึ้นทะเบียน ปรับอายุ อายุ 15+ ปี พ.ศ. {TY} ของ {len(base_rows)} จังหวัด (ไม่มีกรุงเทพฯ) มัธยฐาน {sd_med:.1f}% (ช่วง {sd_lo:.1f}–{sd_hi:.1f}%)',
          f'<b>Data and outcome:</b> {n_res:,} open province-level resources were combined into {n_facts:,} province-year facts. The outcome is the age-standardised registered diabetes prevalence, adults 15+, B.E. {TY} (C.E. {TY - 543}), for {len(base_rows)} provinces (Bangkok missing); median {sd_med:.1f}% (range {sd_lo:.1f}–{sd_hi:.1f}%).'),
        t(f'<b>อายุแรงที่สุด:</b> ความชุกมัธยฐานเพิ่มจาก {age_med[1]:.1f}% (อายุ 15–39) เป็น {age_med[4]:.1f}% (60+)', f'<b>Age is strongest:</b> the median prevalence rises from {age_med[1]:.1f}% (ages 15–39) to {age_med[4]:.1f}% (60+).'),
        t(f'<b>ภาคสำคัญรองลงมา:</b> ตัวควบคุมอย่างเดียว (ภาค ความหนาแน่น สัดส่วน 60+) อธิบายความต่างระหว่างจังหวัดได้ R² = {R2_CTRL:.2f}; ภาคเหนือต่ำกว่าภาคกลางราว {abs(ctrl_tbl[2][1]):.1f} จุด',
          f'<b>Region matters next:</b> the controls alone (region, density, share 60+) explain R² = {R2_CTRL:.2f} of between-province variation; the North is about {abs(ctrl_tbl[2][1]):.1f} points below Central.'),
        t(f'<b>ความสัมพันธ์ดิบหลอกตา:</b> ผู้ค้าน้ำมันต่อประชากรมี ρ = {rf("gas_station_traders_per_100k", "unadjusted_spearman")} แต่หลังคุมตัวกวนเหลือ β = {rf("gas_station_traders_per_100k", "beta_per_sd")} (p = {rf("gas_station_traders_per_100k", "p")}); ปั๊มสนามกีฬา และติดทะเลก็หายไปเช่นกัน',
          f'<b>Raw associations mislead:</b> fuel traders per capita has ρ = {rf("gas_station_traders_per_100k", "unadjusted_spearman")} but only β = {rf("gas_station_traders_per_100k", "beta_per_sd")} (p = {rf("gas_station_traders_per_100k", "p")}) after adjustment; sports facilities and coastal location vanish too.'),
        t(f'<b>กลุ่ม "กิจกรรมเศรษฐกิจ"</b> (อัตราหย่า อัตราสมรส ไฟฟ้ารวมต่อคน GPP) สัมพันธ์กันสูง (r = {CORR["div_mar"]:.2f}–{CORR["elec_gpp"]:.2f}) ให้สัญญาณบวกเล็กน้อยที่ผ่านเกณฑ์ FDR แต่ไม่ผ่าน Holm จึงยังไม่ยืนยัน',
          f'<b>The "economic activity" cluster</b> (divorce rate, marriage rate, total electricity per resident, GPP) is highly intercorrelated (r = {CORR["div_mar"]:.2f}–{CORR["elec_gpp"]:.2f}) and gives a small positive signal that passes FDR but not Holm — not confirmed.'),
        t('<b>ช่องว่างสำคัญ:</b> ตัวแปรที่งานวิจัยสนับสนุนมากสุด (ร้านอาหาร/สะดวกซื้อ เครื่องดื่มหวาน เวลาหน้าจอ/วิดีโอเกม) ไม่มีข้อมูลรายจังหวัดที่ใช้ได้', '<b>Key gap:</b> the variables best supported by research (food and convenience outlets, sugary drinks, screen time/video games) have no usable province-level data.'),
        t('<b>DiaGlance:</b> แบบสอบถามความเสี่ยงรายบุคคลใช้ Naive Bayes จาก BRFSS 757,445 คน (หัวข้อ 7)', '<b>DiaGlance:</b> the individual risk questionnaire uses Naive Bayes on 757,445 BRFSS respondents (Sec. 7).'),
        t('<b>ข้อควรจำ:</b> ทั้งหมดเป็นความสัมพันธ์ระดับจังหวัด ไม่ใช่สาเหตุ และไม่ใช่คำแนะนำทางการแพทย์', '<b>Remember:</b> everything is a province-level association, not a cause, and not medical advice.'))) + '</ul>')

    # ---------- 2 background + hypotheses
    o.append(H(2, f'{lang}-intro', '2. ที่มาและสมมติฐาน', '2. Background and hypotheses'))
    o.append('<p>' + t('ผลสำรวจสุขภาพประชาชนไทยโดยการตรวจร่างกาย (NHES) ระบุความชุกเบาหวานปรับอายุ-เพศเพิ่มจาก 7.5% (พ.ศ. 2547) เป็น 10.1% (พ.ศ. 2563) และผู้ป่วยราว 37% ยังไม่ทราบว่าตนเองเป็นโรค [10] ปัจจัยเสี่ยงที่รู้จักกันดีคืออายุ น้ำหนักเกิน การเคลื่อนไหวน้อย อาหารพลังงานสูงและเครื่องดื่มหวาน ซึ่งถูกหล่อหลอมด้วยสภาพแวดล้อม (ร้านอาหาร ความเป็นเมือง เศรษฐกิจ) โครงงานนี้จึงถามว่าลักษณะของจังหวัดเกี่ยวข้องกับความชุกเบาหวานเพียงใด',
                      'The National Health Examination Survey (NHES) reports an age- and sex-standardised diabetes prevalence rising from 7.5% (2004) to 10.1% (2020), with about 37% of cases undiagnosed [10]. Known risk factors are age, overweight, inactivity, energy-dense food and sugary drinks, all shaped by the local environment (food outlets, urbanisation, the economy). This project asks how provincial characteristics relate to diabetes prevalence.') + '</p>')
    o.append('<p><b>' + t('สมมติฐานที่ตั้งก่อนดูผล', 'Pre-specified hypotheses') + '</b> ' + t('(เพื่อแยกจากการค้นหาแบบสุ่ม): H1 ร้านอาหาร/สะดวกซื้อ/ร้านเหล้าหนาแน่น → ความชุกสูง; H2 จังหวัดร่ำรวย/เป็นเมือง → ความชุกต่ำ; H3 สัดส่วนผู้สูงอายุมาก → ความชุกสูง; H4 พื้นที่ออกกำลังกายมาก → ความชุกต่ำ ตัวแปรอื่น (วัด การหย่า ไฟฟ้า ปั๊มน้ำมัน ฯลฯ) เป็นการสำรวจ จึงนับจำนวนที่ทดสอบและปรับค่า p',
                                                                           '(to separate them from fishing): H1 dense restaurants/convenience stores/alcohol outlets → higher prevalence; H2 richer/more urban provinces → lower; H3 more older people → higher; H4 more exercise space → lower. The other variables (temples, divorce, electricity, fuel traders, …) are exploratory, so we count how many are tested and adjust p-values.') + '</p>')

    # ---------- 3 literature
    o.append(H(2, f'{lang}-lit', '3. ทบทวนวรรณกรรมและกรอบแนวคิด', '3. Literature review and conceptual framework'))
    o.append('<p>' + t('ตารางที่ 1 สรุปตัวแปรที่รวบรวมได้ งานวิจัยที่เกี่ยวข้อง กลไกที่เป็นไปได้ และระดับหลักฐาน (A = หลายงานระดับบุคคลสอดคล้องกัน; B = งานระดับจังหวัดในไทย; C = ผลอ่อนหรือปนกัน; D = ไม่พบงานที่เชื่อมโดยตรง) "กลไก" คือสมมติฐานที่เป็นไปได้ ข้อมูลของโครงงานนี้ไม่ได้พิสูจน์กลไกเหล่านั้น',
                      'Table 1 lists the variables, relevant studies, plausible mechanisms and the strength of evidence (A = several consistent individual-level studies; B = province-level studies in Thailand; C = weak or mixed; D = no directly relevant study found). "Mechanism" is a hypothesis; this project\'s data do not prove it.') + '</p>')
    lit = [
        (t('อายุ / สัดส่วนผู้สูงอายุ', 'Age / share aged 60+'), t('Tappo และคณะ: ความหนาแน่นผู้สูงอายุสัมพันธ์ทางบวก (SLM β = 0.156) [2]', 'Tappo et al.: elderly density positively associated (SLM β = 0.156) [2]'), t('ดื้อต่ออินซูลินเพิ่มตามอายุ', 'Insulin resistance rises with age'), 'A–B'),
        (t('ร้านอาหาร สะดวกซื้อ ร้านเหล้า (เซเว่น)', 'Food shops, convenience and alcohol outlets (7-Eleven)'), t('Tappo และคณะ: ร้านอาหาร β = 9.63 (p<0.001), ร้านเหล้า β = 1.94 (p<0.05) [2]; ทบทวน built environment [7]', 'Tappo et al.: food shops β = 9.63 (p<0.001), alcohol stores β = 1.94 (p<0.05) [2]; built-environment review [7]'), t('หาอาหารพลังงานสูง/เครื่องดื่มหวานง่าย', 'Easy access to energy-dense food and sugary drinks'), 'B'),
        (t('รายได้ / ความเป็นเมือง', 'Income / urbanisation'), t('Polnakoo และคณะ: รายได้และแสงไฟกลางคืนสัมพันธ์ทางลบ (R² = 0.47) [1]; Tappo: แสงไฟกลางคืน β = −0.44 [2]', 'Polnakoo et al.: income and night-time light negatively associated (R² = 0.47) [1]; Tappo: night-time light β = −0.44 [2]'), t('เข้าถึงอาหารคุณภาพ ออกกำลังกาย และบริการสุขภาพ', 'Better access to healthy food, exercise and care'), 'B'),
        (t('เครื่องดื่มหวาน', 'Sugar-sweetened beverages'), t('Papier และคณะ (Thai Cohort, n = 39,175): ดื่มวันละครั้งขึ้นไป OR = 2.4 (1.5–3.9) ในผู้หญิง; ไม่พบในผู้ชาย [4]', 'Papier et al. (Thai Cohort, n = 39,175): daily intake OR = 2.4 (1.5–3.9) in women; none in men [4]'), t('น้ำตาลเหลว ดูดซึมเร็ว น้ำหนักเพิ่ม', 'Rapidly absorbed liquid sugar; weight gain'), 'A'),
        (t('เวลาหน้าจอ / นั่งนาน', 'Screen time / sedentary time'), t('Grøntved & Hu: RR = 1.20 (1.14–1.27) ต่อดูทีวีทุก 2 ชม./วัน [6]; Guo และคณะ: +8% ต่อชั่วโมง [8]', 'Grøntved & Hu: RR = 1.20 (1.14–1.27) per 2 h/day of TV [6]; Guo et al.: +8% per hour [8]'), t('เผาผลาญน้อย กินขณะดู', 'Less energy expenditure; snacking while viewing'), 'A'),
        (t('วิดีโอเกม', 'Video games'), t('ไม่พบงานติดตามระยะยาวในผู้ใหญ่; มีเพียงงานตัดขวาง [9]', 'No long-term adult study found; cross-sectional only [9]'), t('เวลานั่งเพิ่ม', 'More sitting time'), 'C–D'),
        (t('สถานภาพสมรส (สมรส/หย่า)', 'Marital status (marriage/divorce)'), t('Karimi และคณะ: ผู้หย่า OR = 0.84 (0.77–0.91); "ไม่สมรส" OR = 1.47 (0.88–2.45) ไม่มีนัยสำคัญ [3]', 'Karimi et al.: divorced OR = 0.84 (0.77–0.91); "unmarried" OR = 1.47 (0.88–2.45) n.s. [3]'), t('ความเครียด/วิถีชีวิตเปลี่ยน (ผลปนกัน)', 'Stress / lifestyle change (mixed)'), 'C'),
        (t('PM2.5 / อุณหภูมิ', 'PM2.5 / temperature'), t('Mazidi & Speakman (สหรัฐฯ 3,106 เทศมณฑล): PM2.5 อธิบายความแปรปรวนของเบาหวาน 17.9% [5]', 'Mazidi & Speakman (3,106 US counties): PM2.5 explained 17.9% of spatial variation [5]'), t('การอักเสบ/ความผิดปกติของอินซูลิน', 'Inflammation / impaired insulin signalling'), 'B–C'),
        (t('วัดและพระสงฆ์', 'Temples and monks'), t('รายงานข่าวและแคมเปญ: พระอ้วน/เบาหวานสูง [11]', 'News and campaign reports: high obesity/diabetes among monks [11]'), t('อาหารจากการบิณฑบาต ออกกำลังกายจำกัด (เฉพาะกลุ่มพระ)', 'Alms food, restricted exercise (monks only)'), 'C'),
        (t('ปั๊มน้ำมัน ไฟฟ้า สนามกีฬา อินเทอร์เน็ต ติดทะเล', 'Fuel traders, electricity, sports facilities, internet, coast'), t('ไม่พบงานที่เชื่อมโดยตรง', 'No directly relevant study found'), t('เป็นตัวแทน (proxy) ของชนบท/การพึ่งรถยนต์/กิจกรรมเศรษฐกิจ/พื้นที่ออกกำลังกาย — เป็นการเดา', 'Proxies for rurality / car dependence / economic activity / exercise space — conjecture'), 'D')]
    o.append(tcap(t('ตัวแปร หลักฐาน และกลไกที่เป็นไปได้', 'Variables, evidence and plausible mechanisms'), lang))
    o.append(table([t('ตัวแปร', 'Variable'), t('งานวิจัย', 'Research'), t('กลไกที่เป็นไปได้', 'Plausible mechanism'), t('ระดับ', 'Level')], lit, widths=['21%', '44%', '28%', '7%']))
    o.append(box('<b>' + t('บทเรียนจากวรรณกรรม', 'Lesson from the literature') + '</b> ' + t('ทิศทางของความสัมพันธ์ขึ้นกับหน่วยวิเคราะห์ ตัวอย่างเช่น งานระดับบุคคลพบว่าผู้หย่ามีความเสี่ยงต่ำกว่าผู้สมรสเล็กน้อย [3] แต่ในข้อมูลระดับจังหวัดของเรา จังหวัดที่มีอัตราหย่าสูงกลับมีความชุกสูงกว่า การอ่านผลข้ามระดับ (ecological fallacy [21,22]) จึงต้องระวัง',
                                                                              'The direction of an association depends on the unit of analysis. For example, individual-level studies find divorced people at slightly lower risk than married people [3], whereas in our province data, provinces with higher divorce rates have higher prevalence. Reading across levels (the ecological fallacy [21,22]) needs care.')))

    # ---------- 4 data
    o.append(H(2, f'{lang}-data', '4. ข้อมูลและการเตรียมข้อมูล', '4. Data and preparation'))
    o.append(H(3, f'{lang}-data1', '4.1 แหล่งข้อมูล', '4.1 Data sources'))
    src = [
        (t('ความชุกเบาหวาน (ตัวแปรเป้าหมาย)', 'Diabetes prevalence (outcome)'), t('ศูนย์ข้อมูลสุขภาพ (HDC) กระทรวงสาธารณสุข ตาราง s_dm_pop_age ผ่าน data.go.th', 'Health Data Center (HDC), Ministry of Public Health, table s_dm_pop_age via data.go.th'), t('โรงพยาบาล → จังหวัด', 'hospital → province'), f'2547–2569', '76'),
        (t('ประชากรทะเบียน พื้นที่ ความหนาแน่น บ้าน', 'Registry population, area, density, houses'), t('สำนักงานสถิติแห่งชาติ (NSO) ตาราง i7 (กรมการปกครอง)', 'National Statistical Office (NSO) table i7 (Dept. of Provincial Administration)'), t('จังหวัด', 'province'), '2555–2568', '77'),
        (t('การสมรส/หย่า', 'Marriage / divorce'), t('NSO ตาราง ya, ib', 'NSO tables ya, ib'), t('จังหวัด', 'province'), '2554–2568', '77'),
        (t('รายได้ครัวเรือน', 'Household income'), t('NSO สำรวจเศรษฐกิจและสังคมครัวเรือน (ชุด 0705_08_0031)', 'NSO household socio-economic survey (dataset 0705_08_0031)'), t('จังหวัด', 'province'), '2568', '77'),
        (t('อินเทอร์เน็ตในครัวเรือน', 'Households with internet'), t('NSO ตาราง AQ', 'NSO table AQ'), t('จังหวัด รายไตรมาส', 'province, quarterly'), '2564–2567', '77'),
        (t('ไฟฟ้า (ปริมาณ/ผู้ใช้)', 'Electricity (use / users)'), t('กระทรวงพลังงาน dataset_41_075', 'Ministry of Energy dataset_41_075'), t('จังหวัด', 'province'), '2561–2566', '77'),
        (t('ผู้ค้าน้ำมันตามมาตรา 11', 'Fuel traders (section 11)'), t('กรมธุรกิจพลังงาน doeb04062_11', 'Dept. of Energy Business doeb04062_11'), t('ทะเบียนรายราย → จังหวัด', 'licence list → province'), '2568', '77'),
        (t('ศาสนสถานพุทธ', 'Buddhist religious sites'), t('กรมการศาสนา mr_30309_0331', 'Dept. of Religious Affairs mr_30309_0331'), t('จังหวัด', 'province'), '2566', '76'),
        (t('สนามกีฬา', 'Sports facilities'), t('กระทรวงการท่องเที่ยวและกีฬา mots_stadiums', 'Ministry of Tourism and Sports mots_stadiums'), t('ทะเบียน → จังหวัด', 'registry → province'), '2567', '77'),
        ('GPP ต่อหัว / GPP per capita', t('สศช. (เผยแพร่ซ้ำโดยสำนักงานจังหวัด) dataset_66_84', 'NESDC (re-published by a provincial office) dataset_66_84'), t('จังหวัด', 'province'), '2555–2565', '77'),
        (t('แบบสอบถาม DiaGlance (ระดับบุคคล)', 'DiaGlance questionnaire (individual level)'), 'CDC BRFSS 2020 + 2022 [12]', t('ผู้ตอบแบบสำรวจ', 'respondents'), '2563, 2565', '757,445 persons'),
        (t('ตัวแปรที่ยังไม่ได้ข้อมูลครบ', 'Variables not yet complete'), t(f'OpenStreetMap (เซเว่น ร้านอาหาร ฯลฯ) ดึงได้ {n_osm} จังหวัด; PM2.5, แสงไฟกลางคืน, เวลาหน้าจอ, วิดีโอเกม ไม่มีข้อมูลรายจังหวัด', f'OpenStreetMap (7-Eleven, restaurants, …) retrieved for {n_osm} provinces so far; PM2.5, night-time light, screen time, video games have no province-level data'), '—', '—', '—')]
    o.append(tcap(t('แหล่งข้อมูลหลัก (ใบอนุญาตตามที่ระบุในแคตตาล็อก data.go.th เป็น "Open Data Common" เป็นส่วนใหญ่)', 'Main data sources (licence as listed in the data.go.th catalogue, mostly "Open Data Common")'), lang))
    o.append(table([t('ข้อมูล', 'Data'), t('ผู้เผยแพร่ / ตาราง', 'Publisher / table'), t('ระดับ', 'Level'), t('ปี พ.ศ.', 'Years (B.E.)'), 'n'], src, widths=['22%', '40%', '16%', '12%', '10%']))
    o.append(H(3, f'{lang}-data2', '4.2 ขั้นตอนประมวลผลข้อมูล (pipeline)', '4.2 Processing pipeline'))
    o.append('<p>' + t(f'ข้อมูลส่วนใหญ่ถูกเผยแพร่โดยสำนักงานจังหวัดแต่ละแห่งเองในรูปแบบและชื่อคอลัมน์ที่ต่างกัน จึงออกแบบขั้นตอนสี่ขั้นที่บันทึกผลเป็นไฟล์ CSV ทุกขั้น: <b>(1) Parse</b> อ่านไฟล์ {n_csv:,} ไฟล์ ตรวจรหัสอักษร (UTF-8/TIS-620) หาแถวหัวตาราง จัดประเภทโครงสร้างอัตโนมัติ (long, wide-year, snapshot, record list, HDC database export); '
                      f'<b>(2) Staging</b> แปลงเป็นรูปแบบยาวเดียวกัน (จังหวัด × ปี × ตัวชี้วัด × ค่า) โดยหาจังหวัดจากเนื้อหาไฟล์ก่อน แล้วจึงดูหน่วยงานผู้เผยแพร่; <b>(3) Validation</b> ตรวจกฎคุณภาพ (ตารางที่ 3); <b>(4) Canonical</b> รวมเป็นข้อเท็จจริงจังหวัด-ปี ตารางมิติจังหวัด และตารางวิเคราะห์ ขั้นตอนไม่ทิ้งข้อมูลอย่างเงียบๆ: แถวที่ถูกปฏิเสธเก็บไว้พร้อมเหตุผล และค่าที่คำนวณต่อยอด (เช่นรวมอำเภอเป็นจังหวัด) ระบุวิธีไว้ในคอลัมน์ method',
                      f'Most data are published by individual provincial offices with different formats and column names, so we built a four-stage pipeline that writes CSV at every stage: <b>(1) Parse</b> reads {n_csv:,} files, detects encoding (UTF-8/TIS-620), locates the header row and classifies the layout (long, wide-year, snapshot, record list, HDC database export); '
                      f'<b>(2) Staging</b> converts everything to one long format (province × year × indicator × value), resolving the province from file content first and then from the publishing office; <b>(3) Validation</b> applies quality rules (Table 3); <b>(4) Canonical</b> builds province-year facts, a province dimension table and the analysis table. Nothing is dropped silently: rejected rows are kept with a reason, and derived values (e.g. districts summed to a province) are labelled in a <i>method</i> column.') + '</p>')
    o.append(figure(seg_bar(lang), t(f'ผลการตรวจคุณภาพของข้อมูลที่นำเข้าสู่ staging ทั้งหมด {n_obs:,} รายการ', f'Validation outcome of all {n_obs:,} observations staged'), lang))
    rules = qsum['rules']
    rule_txt = {'warn:conflicting_duplicate': (t('คีย์เดียวกันแต่ค่าต่างกันในไฟล์เดียวกัน (ไม่ใช้ในข้อเท็จจริง)', 'same key, different values in one file (excluded from facts)')),
                'warn:year_from_update_date': t('ไม่มีปีในไฟล์ ใช้ปีที่อัปเดตข้อมูลแทน', 'no year in file; update-date year used'), 'reject:year_missing': t('ไม่ทราบปี', 'year unknown'),
                'warn:outlier': t('ค่าผิดปกติ (|z-ทนทาน| > 8) ตรวจเท่านั้น ไม่ตัดทิ้ง', 'outlier (robust |z| > 8), flagged only'), 'reject:province_unresolved': t('ระบุจังหวัดไม่ได้', 'province unresolved'),
                'reject:value_missing': t('ค่าว่าง/ไม่ใช่ตัวเลข', 'missing / non-numeric value'), 'warn:hdc_patients_exceed_population': t('ผู้ป่วยมากกว่าประชากรของกลุ่มอายุ (ระดับโรงพยาบาล)', 'patients exceed population of the age group (hospital level)'),
                'reject:year_out_of_range': t('ปีนอกช่วง 2540–2569', 'year outside 2540–2569'), 'warn:year_from_metadata': t('ปีมาจากเมทาดาทาของชุดข้อมูล', 'year taken from dataset metadata'), 'warn:negative_additive': t('ค่าติดลบของตัวชี้วัดที่ควรเป็นจำนวนนับ', 'negative value for a count-type indicator')}
    o.append(tcap(t('กฎการตรวจคุณภาพและจำนวนรายการที่ถูกทำเครื่องหมาย', 'Quality rules and number of flagged observations'), lang))
    o.append(table([t('กฎ', 'Rule'), t('ความหมาย', 'Meaning'), t('รายการ', 'Observations')], [(f'<code>{k}</code>', rule_txt.get(k, ''), f'{v:,}') for k, v in sorted(rules.items(), key=lambda x: -x[1])], right=(2,), widths=['34%', '48%', '18%']))
    o.append('<p>' + t(f'สรุปจำนวน: ทะเบียนทรัพยากร {n_res:,} รายการ ดาวน์โหลดสำเร็จ {n_dl:,} รายการ; ไฟล์ที่อ่านโครงสร้างไม่ออก {lay.get("unknown", 0):,} ไฟล์ (ไม่ถูกนำเข้า ไม่เดา); ข้อเท็จจริงจังหวัด-ปี {n_facts:,} รายการ; ตัวแปรเป้าหมาย {len(tgt):,} แถวจังหวัด-ปี ข้อมูลของ NSO ต้องใช้ User-Agent แบบเบราว์เซอร์เพราะไฟร์วอลล์ปฏิเสธโปรแกรมอื่น (ใช้อัตราคำขอต่ำ)',
                      f'In numbers: {n_res:,} resources registered, {n_dl:,} downloaded; {lay.get("unknown", 0):,} files with an unrecognised layout (not imported, not guessed); {n_facts:,} province-year facts; {len(tgt):,} province-year rows of the outcome. NSO files required a browser-style User-Agent because its firewall rejects other clients (low request rate).') + '</p>')
    o.append(H(3, f'{lang}-data3', '4.3 ตัวแปรเป้าหมายและข้อจำกัด', '4.3 Outcome variable and its limits'))
    o.append('<p>' + t(f'ตัวแปรเป้าหมายคือจำนวนผู้ป่วยเบาหวานที่ขึ้นทะเบียนจากบริการสุขภาพหารด้วยประชากร แยกตามกลุ่มอายุ (<15, 15–39, 40–49, 50–59, 60+) รวมจากระดับโรงพยาบาลเป็นจังหวัดด้วยรหัสพื้นที่ 2 หลักแรก ใช้ปี พ.ศ. {TY} ซึ่งเป็นปีล่าสุดที่ครบ ({TY + 1} ยังไม่ครบปี) '
                      'ข้อควรระวัง: นี่คือ <b>ผู้ป่วยที่ขึ้นทะเบียน</b> ไม่ใช่ความชุกจากการตรวจเลือดจริง จังหวัดที่ตรวจคัดกรองมากจะพบผู้ป่วยมากกว่า และกรุงเทพมหานครไม่มีข้อมูลครบจึงไม่อยู่ในตัวแปรเป้าหมาย',
                      f'The outcome is the number of diabetes patients registered through health services divided by population, by age group (<15, 15–39, 40–49, 50–59, 60+), summed from hospital to province using the first two digits of the area code. We use B.E. {TY}, the latest complete year ({TY + 1} is incomplete). '
                      'Caveat: this is <b>registered patients</b>, not prevalence from blood tests; provinces that screen more find more patients; and Bangkok lacks complete data and is not in the outcome.') + '</p>')

    # ---------- 5 methods
    o.append(H(2, f'{lang}-meth', '5. ระเบียบวิธีและสูตรที่ใช้', '5. Methods and formulas'))
    meth = [
        (t('ปรับอายุแบบตรง', 'Direct age standardisation'), t('เทียบจังหวัดที่โครงสร้างอายุต่างกัน', 'Compare provinces with different age structures'), t('ความชุกรายกลุ่มอายุเทียบกันได้; ใช้น้ำหนักมาตรฐานชุดเดียว', 'Age-specific rates are comparable; one set of standard weights'), t('ตัดอิทธิพลของอายุซึ่งเป็นตัวกำหนดที่แรงที่สุด', 'Removes the effect of age, the strongest determinant')),
        (t('อัตราต่อประชากร + ln + z-score', 'Per-capita rate + ln + z-score'), t('ทำให้ตัวแปรอยู่ในสเกลที่เทียบกันได้', 'Put variables on comparable scales'), t('ความเบ้ขวาลดลงหลังลอการิทึม', 'Right skew is reduced by the log'), t('β ตีความเป็น "จุดร้อยละต่อ +1 SD" เทียบข้ามตัวแปรได้', 'β reads as "points per +1 SD", comparable across variables')),
        (t('สหสัมพันธ์สเปียร์แมน', 'Spearman correlation'), t('วัดความสัมพันธ์ดิบ', 'Raw association'), t('ความสัมพันธ์แบบทางเดียว (monotonic) ไม่ต้องเชิงเส้น', 'Monotonic relation; linearity not required'), t('ทนต่อค่าสุดโต่ง', 'Resistant to outliers')),
        (t('OLS + ตัวควบคุม (ภาค, ln ความหนาแน่น, สัดส่วน 60+)', 'OLS + controls (region, ln density, share 60+)'), t('ความสัมพันธ์สุทธิหลังตัดตัวกวน', 'Association net of confounders'), t('เชิงเส้น ไม่มีตัวกวนที่ขาดไป ความคลาดเคลื่อนอิสระ (ข้อสุดท้ายถูกละเมิดเชิงพื้นที่)', 'Linear, no omitted confounders, independent errors (the last is violated spatially)'), t('เป็นวิธีมาตรฐานของงานนิเวศวิทยาและตีความง่าย', 'Standard in ecological studies and easy to interpret')),
        (t('SE แบบ HC3', 'HC3 standard errors'), t('อนุมานเมื่อความแปรปรวนไม่คงที่', 'Inference under heteroskedasticity'), t('เลเวอเรจ h < 1 ทุกจุด', 'Leverage h < 1 for every point'), t('เหมาะกับตัวอย่างขนาดเล็ก (n = 76) [16,17]', 'Suited to small samples (n = 76) [16,17]')),
        (t('Holm และ Benjamini–Hochberg', 'Holm and Benjamini–Hochberg'), t('คุมการทดสอบหลายครั้ง', 'Control multiple testing'), t('Holm: ไม่ต้องสมมติความเป็นอิสระ; BH: อิสระหรือสัมพันธ์เชิงบวก', 'Holm: no independence assumption; BH: independence or positive dependence'), t('รายงานทั้งสองเพื่อดูความไวของข้อสรุป', 'Both reported to show how sensitive conclusions are')),
        (t('ตรวจเลเวอเรจ (ตัด 5 จังหวัด)', 'Leverage check (drop 5 provinces)'), t('ดูว่าผลพึ่งจังหวัดไม่กี่แห่งหรือไม่', 'Does the result hinge on a few provinces?'), '—', t('ตรวจความทนทาน', 'Robustness')),
        (t('เบย์แบบไร้เดียงสา + odds + ลาปลาซ', 'Naive Bayes + odds + Laplace'), t('ประเมินความเสี่ยงรายบุคคลจากหลายแหล่งที่ต่อกันตรงๆ ไม่ได้', 'Individual risk from several sources that cannot be merged directly'), t('ปัจจัยเป็นอิสระต่อกันเมื่อรู้สถานะเบาหวาน', 'Factors independent given diabetes status'), t('ตีความง่าย คำนวณในเบราว์เซอร์ได้ ใช้ข้อมูลแยกชุดได้ [13,14]', 'Interpretable, runs in the browser, can combine separate datasets [13,14]'))]
    o.append('<p>' + t('ตารางที่ 4 สรุปวิธีที่ใช้ เหตุผลที่เลือก และสมมติฐานของแต่ละวิธี ส่วนสูตรอยู่ในหัวข้อย่อยถัดไป', 'Table 4 summarises each method, why it was chosen and its assumptions; the formulas follow in the next subsections.') + '</p>')
    o.append(tcap(t('ภาพรวมของวิธี จุดประสงค์ และสมมติฐาน', 'Overview of methods, purpose and assumptions'), lang))
    o.append(table([t('วิธี', 'Method'), t('จุดประสงค์', 'Purpose'), t('สมมติฐาน', 'Assumptions'), t('เหตุผลที่เลือก', 'Why chosen')], meth, widths=['22%', '24%', '29%', '25%']))
    o.append(H(3, f'{lang}-m1', '5.1 ตัวแปรเป้าหมาย: ความชุกและการปรับอายุแบบตรง', '5.1 Outcome: prevalence and direct age standardisation'))
    o.append('<p>' + t('ความชุกของกลุ่มอายุ g ในจังหวัด j คือจำนวนผู้ป่วยต่อประชากร เนื่องจากโครงสร้างอายุต่างกันมากระหว่างจังหวัด จึงปรับอายุด้วยวิธีตรง (direct standardisation) โดยถ่วงน้ำหนักความชุกแต่ละกลุ่มด้วยสัดส่วนประชากรมาตรฐาน (รวมทุกจังหวัดปีเดียวกัน) ของกลุ่มอายุ 15 ปีขึ้นไป [20]',
                      'The prevalence of age group g in province j is patients per population. Because age structures differ a lot between provinces, we use direct standardisation: each group\'s prevalence is weighted by the standard-population share (all provinces pooled, same year) of the adult age groups [20].') + '</p>')
    o.append(eq(sub(mi('p'), row(mi('j'), mo(','), mi('g'))) + mo('=') + frac(sub(mi('d'), row(mi('j'), mo(','), mi('g'))), sub(mi('N'), row(mi('j'), mo(','), mi('g'))))))
    o.append(eq(sub(hat(mi('p')), row(mi('j'), mo(','), mtext('std'))) + mo('=') + sumo(row(mi('g'), mo('∈'), mo('{'), mn('15–39'), mo(','), mn('40–49'), mo(','), mn('50–59'), mo(','), mn('60+'), mo('}'))) + sub(mi('w'), mi('g')) + sub(mi('p'), row(mi('j'), mo(','), mi('g'))) + mo(',') + mspace() + sub(mi('w'), mi('g')) + mo('=') + frac(sub(mi('N'), row(mi('g'), mo(','), mtext('std'))), sumo(mi('h')) + sub(mi('N'), row(mi('h'), mo(','), mtext('std'))))))
    o.append('<p class="small">' + t('d = ผู้ป่วย, N = ประชากร, std = ประชากรมาตรฐาน; ตัวแปรเป้าหมายจึงเป็นค่าเฉลี่ยถ่วงน้ำหนักของความชุกรายกลุ่มอายุ', 'd = patients, N = population, std = standard population; the outcome is thus a weighted mean of age-specific prevalence.') + '</p>')
    o.append(H(3, f'{lang}-m2', '5.2 การแปลงตัวแปรอธิบาย', '5.2 Transforming the explanatory variables'))
    o.append('<p>' + t('จำนวนนับ (เช่นจำนวนผู้ค้าน้ำมัน) หารด้วยประชากรทะเบียนปีเดียวกันของ NSO เป็นอัตราต่อ 100,000 คน; ตัวแปรที่เบ้ขวา (อัตราต่อประชากร รายได้ GPP ไฟฟ้า) ใช้ลอการิทึมธรรมชาติ แล้วทำให้เป็นมาตรฐาน (z-score) เพื่อให้สัมประสิทธิ์ β หมายถึงการเปลี่ยนแปลงของความชุก (จุดร้อยละ) ต่อ +1 ส่วนเบี่ยงเบนมาตรฐาน และเทียบข้ามตัวแปรได้',
                      'Counts (e.g. fuel traders) are divided by the same-year NSO registry population to give a rate per 100,000; right-skewed variables (per-capita rates, income, GPP, electricity) are log-transformed and then standardised (z-score), so that the coefficient β is the change in prevalence (percentage points) per +1 standard deviation and comparable across variables.') + '</p>')
    o.append(eq(mi('r') + mo('=') + mn('100000') + mo('×') + frac(mi('c'), mi('Pop')) + mo(',') + mspace() + mi('z') + mo('=') + frac(mi('x') + mo('−') + bar(mi('x')), sub(mi('s'), mi('x'))) + mo(',') + mspace() + mi('x') + mo('=') + mi('ln') + mo('(') + mi('r') + mo(')')))
    o.append(H(3, f'{lang}-m3', '5.3 สหสัมพันธ์สเปียร์แมน', '5.3 Spearman correlation'))
    o.append('<p>' + t('ใช้เป็นการวัดความสัมพันธ์ดิบ เพราะไม่ขึ้นกับรูปแบบเชิงเส้นและทนต่อค่าสุดโต่ง คำนวณเป็นสหสัมพันธ์เพียร์สันของอันดับ (กรณีไม่มีค่าซ้ำเท่ากับสูตรด้านล่าง) [15]', 'Used as the raw association measure because it does not assume linearity and resists outliers. It is the Pearson correlation of ranks (equal to the formula below when there are no ties) [15].') + '</p>')
    o.append(eq(sub(mi('ρ'), mi('s')) + mo('=') + mi('Pearson') + mo('(') + mi('rank') + mo('(') + mi('x') + mo(')') + mo(',') + mi('rank') + mo('(') + mi('y') + mo(')') + mo(')') + mo('=') + mn('1') + mo('−') + frac(mn('6') + sumo(mi('i')) + sup(sub(mi('d'), mi('i')), mn('2')), mi('n') + mo('(') + sup(mi('n'), mn('2')) + mo('−') + mn('1') + mo(')'))))
    o.append(H(3, f'{lang}-m4', '5.4 การถดถอยเชิงเส้นพร้อมตัวควบคุม', '5.4 Linear regression with controls'))
    o.append('<p>' + t(f'สำหรับตัวแปรอธิบาย X แต่ละตัว ประมาณการถดถอย OLS บน {len(base_rows)} จังหวัด โดยใส่ตัวควบคุม C: ภาค (6 ภาค ใช้ตัวแปรหุ่น ฐาน = ภาคกลาง) ลอการิทึมความหนาแน่นประชากร และสัดส่วนประชากรอายุ 60+ (ตัวควบคุมเหล่านี้อธิบายตัวกวนหลัก: ภาค/ชนบท-เมือง/โครงสร้างประชากร) ค่าประมาณ β̂ คำนวณด้วยสมการปกติ และใช้ค่าคลาดเคลื่อนมาตรฐานแบบ HC3 ซึ่งแก้ปัญหาความแปรปรวนไม่คงที่และเหมาะกับตัวอย่างขนาดเล็ก [16,17] โดยใช้ค่าเลเวอเรจ h แต่ละจังหวัด',
                      f'For each explanatory variable X we fit OLS on {len(base_rows)} provinces with controls C: region (6 regions as dummies, base = Central), log population density and the share of the population aged 60+ (these address the main confounders: region, rural–urban, demographic structure). β̂ is computed from the normal equations and standard errors are HC3, which handles heteroskedasticity and suits small samples [16,17], using each province\'s leverage h.') + '</p>')
    o.append(eq(sub(mi('Y'), mi('j')) + mo('=') + sub(mi('β'), mn('0')) + mo('+') + sumo(mi('k')) + sub(mi('γ'), mi('k')) + sub(mi('C'), row(mi('j'), mi('k'))) + mo('+') + mi('β') + sub(mi('z'), mi('j')) + mo('+') + sub(mi('ε'), mi('j'))))
    o.append(eq(hat(mi('β')) + mo('=') + sup(row(mo('('), sup(mi('X'), mo('′')), mi('X'), mo(')')), mo('−1')) + sup(mi('X'), mo('′')) + mi('y') + mo(',') + mspace() + sub(mi('h'), mi('i')) + mo('=') + sub(mi('x'), mi('i')) + mo('′') + sup(row(mo('('), sup(mi('X'), mo('′')), mi('X'), mo(')')), mo('−1')) + sub(mi('x'), mi('i'))))
    o.append(eq(sub(hat(mi('V')), mtext('HC3')) + mo('=') + sup(row(mo('('), sup(mi('X'), mo('′')), mi('X'), mo(')')), mo('−1')) + row(sup(mi('X'), mo('′')), mtext('diag'), mo('('), frac(sup(sub(mi('e'), mi('i')), mn('2')), sup(row(mo('('), mn('1'), mo('−'), sub(mi('h'), mi('i')), mo(')')), mn('2'))), mo(')'), mi('X')) + sup(row(mo('('), sup(mi('X'), mo('′')), mi('X'), mo(')')), mo('−1'))))
    o.append(eq(mi('t') + mo('=') + frac(hat(mi('β')), mi('SE')) + mo(',') + mspace() + mi('p') + mo('=') + mn('2') + mo('[') + mn('1') + mo('−') + sub(mi('F'), row(mi('t'), mo(','), mi('n'), mo('−'), mi('k'))) + mo('(') + mo('|') + mi('t') + mo('|') + mo(')') + mo(']') + mo(',') + mspace() + sup(mi('R'), mn('2')) + mo('=') + mn('1') + mo('−') + frac(mi('SSE'), mi('SST')) + mo(',') + mspace() + mi('ΔR²') + mo('=') + sup(sub(mi('R'), mtext('full')), mn('2')) + mo('−') + sup(sub(mi('R'), mtext('controls')), mn('2'))))
    o.append('<p class="small">' + t('ผลลัพธ์ β ที่ได้ตรงกับการถดถอยส่วนที่เหลือ (ทฤษฎีบท Frisch–Waugh–Lovell [24]): นำความชุกและ X ไปถดถอยบนตัวควบคุมก่อน แล้วถดถอยส่วนที่เหลือซึ่งกันและกัน (รูปที่ 4 ขวา)', 'The β equals the slope of regressing residuals on residuals (Frisch–Waugh–Lovell theorem [24]): regress prevalence and X on the controls first, then regress the residuals on each other (Figure 4, right).') + '</p>')
    o.append(H(3, f'{lang}-m5', '5.5 การทดสอบหลายครั้ง', '5.5 Multiple testing'))
    o.append('<p>' + t(f'เมื่อทดสอบ m = {len(res_rows)} ตัวแปร โอกาสพบ "นัยสำคัญ" โดยบังเอิญสูงขึ้นมาก (ถ้า α = 0.05 และไม่มีความสัมพันธ์จริงเลย คาดว่าพบประมาณ {len(res_rows) * 0.05:.1f} ตัวแปร) จึงรายงาน p ที่ปรับด้วยวิธี Holm (ควบคุมโอกาสผิดพลาดเชิงครอบครัว [18]) และ q-value วิธี Benjamini–Hochberg (ควบคุมอัตราการค้นพบเท็จ [19]) เรียง p จากน้อยไปมาก p(1) ≤ … ≤ p(m)',
                      f'When m = {len(res_rows)} variables are tested, the chance of a spurious "significant" result rises sharply (with α = 0.05 and no true associations we would expect about {len(res_rows) * 0.05:.1f}). We therefore report Holm-adjusted p (family-wise error control [18]) and Benjamini–Hochberg q-values (false discovery rate control [19]). With p sorted ascending, p(1) ≤ … ≤ p(m):') + '</p>')
    o.append(eq(sub(mi('p'), row(mo('('), mi('i'), mo(')'), mtext('Holm'))) + mo('=') + mi('max') + sub(mo(''), row(mi('j'), mo('≤'), mi('i'))) + mi('min') + mo('(') + mn('1') + mo(',') + row(mo('('), mi('m'), mo('−'), mi('j'), mo('+'), mn('1'), mo(')')) + sub(mi('p'), row(mo('('), mi('j'), mo(')'))) + mo(')') + mo(',') + mspace() + sub(mi('q'), row(mo('('), mi('i'), mo(')'))) + mo('=') + mi('min') + sub(mo(''), row(mi('j'), mo('≥'), mi('i'))) + mo('(') + frac(mi('m'), mi('j')) + sub(mi('p'), row(mo('('), mi('j'), mo(')'))) + mo(')')))
    o.append('<p>' + t('การตรวจความทนทาน: ปรับแบบจำลองซ้ำโดยตัดจังหวัดที่มีเลเวอเรจสูงสุด 5 แห่ง แล้วดูว่าทิศทางและนัยสำคัญเปลี่ยนหรือไม่', 'Robustness check: refit after dropping the 5 provinces with the highest leverage and see whether the direction and significance change.') + '</p>')
    o.append(H(3, f'{lang}-m6', '5.6 แบบจำลองเบย์สำหรับแบบสอบถาม DiaGlance', '5.6 Bayesian model for the DiaGlance questionnaire'))
    o.append('<p>' + t('แบบสอบถามประเมินความเสี่ยงรายบุคคลใช้กฎของเบย์ร่วมกับสมมติฐานเบย์แบบไร้เดียงสา (Naive Bayes): ปัจจัยแต่ละข้อเป็นอิสระต่อกันเมื่อรู้สถานะเบาหวาน [13,14,25] กฎของเบย์: ความน่าจะเป็นหลังสังเกต (posterior) เท่ากับ likelihood × prior หารด้วย evidence',
                      'The individual-level questionnaire uses Bayes\' rule with the Naive Bayes assumption: each factor is independent of the others given diabetes status [13,14,25]. Bayes\' rule: the posterior equals likelihood × prior divided by evidence.') + '</p>')
    o.append(eq(P(cond(mi('D'), mi('x'))) + mo('=') + frac(P(cond(mi('x'), mi('D'))) + P(mi('D')), P(mi('x')))))
    o.append(eq(P(cond(mi('D'), sub(mi('x'), mn('1')) + mo(',') + mo('…') + mo(',') + sub(mi('x'), mi('n')))) + mo('=') + frac(P(mi('D')) + prodo(mi('i')) + P(cond(sub(mi('x'), mi('i')), mi('D'))), P(mi('D')) + prodo(mi('i')) + P(cond(sub(mi('x'), mi('i')), mi('D'))) + mo('+') + P(mi('¬D')) + prodo(mi('i')) + P(cond(sub(mi('x'), mi('i')), mi('¬D'))))))
    o.append('<p>' + t('สูตรในเอกสารสรุปของโครงงานหารด้วย P(S)·P(G) โดยตรง ซึ่งให้ผล 22.72% ในตัวอย่าง (0.45×0.60×0.101 ÷ (0.30×0.40)) แต่ตัวส่วนที่เป็นผลคูณของความน่าจะเป็นรายปัจจัยอาจทำให้ค่ามากกว่า 100% เมื่อมีหลายปัจจัยหรือปัจจัยไม่อิสระ ในระบบจึงทำให้เป็นมาตรฐาน (normalise) โดยเทียบกับกลุ่มไม่เป็นเบาหวานตามสมการข้างบน ซึ่งไม่เกิน 100% เสมอ รูปแบบอัตราต่อรอง (odds) ใช้อธิบายผลของแต่ละคำตอบได้ง่าย:',
                      'The formula in the project\'s summary note divides directly by P(S)·P(G), giving 22.72% in its example (0.45×0.60×0.101 ÷ (0.30×0.40)). However, a denominator made of a product of single-factor probabilities can exceed 100% when there are many or dependent factors. The system therefore normalises against the non-diabetic group as in the equation above, which never exceeds 100%. The odds form explains the effect of each answer easily:') + '</p>')
    o.append(eq(mi('odds') + mo('(') + mi('D') + mo('|') + mi('x') + mo(')') + mo('=') + mi('odds') + mo('(') + mi('D') + mo(')') + mo('×') + prodo(mi('i')) + sub(mi('LR'), mi('i')) + mo(',') + mspace() + sub(mi('LR'), mi('i')) + mo('=') + frac(P(cond(sub(mi('x'), mi('i')), mi('D'))), P(cond(sub(mi('x'), mi('i')), mi('¬D')))) + mo(',') + mspace() + mi('p') + mo('=') + frac(mi('odds'), mn('1') + mo('+') + mi('odds'))))
    o.append('<p>' + t(f'ค่า P(x|D) และ P(x|¬D) นับจาก BRFSS 2563 + 2565 (ผู้ตอบ 757,445 คน; ไม่รวมเบาหวานขณะตั้งครรภ์) ด้วยการปรับเรียบแบบลาปลาซ (+1 ต่อหมวด K หมวด) เพื่อกันค่าศูนย์ [23] และใช้ prior P(D) = 10.1% ตามเอกสารสรุป (ความชุกเบาหวานของไทย)',
                      f'P(x|D) and P(x|¬D) are counted from BRFSS 2020 + 2022 (757,445 respondents; gestational diabetes excluded) with Laplace smoothing (+1 per category, K categories) to avoid zero counts [23], and the prior P(D) = 10.1% from the summary note (Thai diabetes prevalence).') + '</p>')
    o.append(eq(mi('P') + mo('(') + cond(mi('x'), mi('D')) + mo(')') + mo('=') + frac(sub(mi('n'), row(mi('x'), mo(','), mi('D'))) + mo('+') + mn('1'), sub(mi('n'), mi('D')) + mo('+') + mi('K'))))
    return o, (sc_svg, rho_f, beta_f, p_f)


VERDICT = {'survives multiple-testing (Holm<0.05)': ('ผ่านการปรับ Holm (p<0.05)', 'survives Holm adjustment (p<0.05)'), 'FDR<0.10 only': ('ผ่าน FDR (q<0.10) เท่านั้น', 'passes FDR (q<0.10) only'),
           'not distinguishable from noise': ('แยกจากความบังเอิญไม่ได้', 'not distinguishable from chance')}

def content_b(lang, o, ctx):
    t = (lambda th, en: th if lang == 'th' else en)
    H = lambda lvl, ident, th, en: heading(lvl, ident, th, en, lang)
    sc_svg, rho_f, beta_f, p_f = ctx
    nm = LABEL_TH if lang == 'th' else LABEL_EN; RN = REGION_TH if lang == 'th' else REGION_EN
    # ---------- 6 results
    o.append(H(2, f'{lang}-res', '6. ผลการวิเคราะห์ระดับจังหวัด', '6. Province-level results'))
    o.append(H(3, f'{lang}-r1', '6.1 อายุเป็นตัวกำหนดที่แรงที่สุด', '6.1 Age is the strongest determinant'))
    o.append('<p>' + t(f'ความชุกเพิ่มขึ้นอย่างชัดเจนตามอายุ: มัธยฐานข้ามจังหวัดปี {TY} คือ {age_med[0]:.2f}% (<15 ปี), {age_med[1]:.1f}% (15–39), {age_med[2]:.1f}% (40–49), {age_med[3]:.1f}% (50–59) และ {age_med[4]:.1f}% (60+) จึงต้องปรับอายุก่อนเปรียบเทียบจังหวัด และใช้สัดส่วนผู้สูงอายุเป็นตัวควบคุม',
                      f'Prevalence rises steeply with age: the between-province medians in B.E. {TY} are {age_med[0]:.2f}% (<15), {age_med[1]:.1f}% (15–39), {age_med[2]:.1f}% (40–49), {age_med[3]:.1f}% (50–59) and {age_med[4]:.1f}% (60+). Provinces must therefore be compared after age adjustment, and the share of older people is used as a control.') + '</p>')
    o.append(figure(fig_age(lang), t('ความชุกเบาหวานที่ขึ้นทะเบียนตามกลุ่มอายุ (มัธยฐาน 76 จังหวัด)', 'Registered diabetes prevalence by age group (median of 76 provinces)'), lang))
    o.append(H(3, f'{lang}-r2', '6.2 ภาคเป็นตัวแปรที่แรงที่สุดหลังปรับอายุ', '6.2 Region is the strongest factor after age adjustment'))
    reg_mean = {k: statistics.fmean(num(r, 'dm_prev_age_std_pct') for r in base_rows if r['region'] == k) for k in regions}
    o.append('<p>' + t(f'ความชุกปรับอายุเฉลี่ยสูงสุดในภาคตะวันออกเฉียงเหนือ ({reg_mean["northeast"]:.1f}%) และภาคกลาง ({reg_mean["central"]:.1f}%) ต่ำสุดในภาคใต้ ({reg_mean["south"]:.1f}%) ตัวควบคุมสามตัว (ภาค ความหนาแน่น สัดส่วน 60+) อธิบายความแปรปรวนข้ามจังหวัดได้ R² = {R2_CTRL:.2f} (ปรับแล้ว {ADJ_CTRL:.2f}) โดยภาคเหนือต่ำกว่าภาคกลาง {abs(ctrl_tbl[2][1]):.2f} จุด (p {"< 0.001" if ctrl_tbl[2][3] < 0.001 else "= " + f"{ctrl_tbl[2][3]:.3f}"}) และสัดส่วน 60+ ที่เพิ่มขึ้น 1 จุดสัมพันธ์กับความชุกที่สูงขึ้น {ctrl_tbl[-1][1]:.2f} จุด (ตารางที่ 5)',
                      f'Mean age-standardised prevalence is highest in the Northeast ({reg_mean["northeast"]:.1f}%) and Central ({reg_mean["central"]:.1f}%) regions and lowest in the South ({reg_mean["south"]:.1f}%). The three controls (region, density, share 60+) explain R² = {R2_CTRL:.2f} (adjusted {ADJ_CTRL:.2f}) of the between-province variation: the North is {abs(ctrl_tbl[2][1]):.2f} points below Central (p {"< 0.001" if ctrl_tbl[2][3] < 0.001 else "= " + f"{ctrl_tbl[2][3]:.3f}"}), and each extra point in the share aged 60+ is associated with {ctrl_tbl[-1][1]:.2f} points higher prevalence (Table 5).') + '</p>')
    o.append(figure(fig_region(lang), t('ความชุกเบาหวานปรับอายุรายจังหวัดแยกตามภาค (จุด = จังหวัด, ขีดดำ = ค่าเฉลี่ยภาค)', 'Age-standardised prevalence by province, grouped by region (dot = province, black bar = regional mean)'), lang))
    cn = {'intercept': t('ค่าคงที่ (ภาคกลาง)', 'Intercept (Central)'), 'log_density': t('ln ความหนาแน่นประชากร', 'ln population density'), 'share_age_60plus_pct': t('สัดส่วนอายุ 60+ (จุด%)', 'Share aged 60+ (pct. points)')}
    rows_c = [((cn.get(n) or RN[n.split('=')[1]] + t(' (เทียบกับภาคกลาง)', ' region (vs Central)')), f'{b:+.2f}', f'{s:.2f}', pfmt(p)) for n, b, s, p in ctrl_tbl]
    o.append(tcap(t(f'แบบจำลองตัวควบคุมอย่างเดียว (n = {len(base_rows)}, R² = {R2_CTRL:.2f}, ปรับแล้ว {ADJ_CTRL:.2f}); ตัวแปรตาม = ความชุกปรับอายุ (จุด%); SE แบบ HC3', f'Controls-only model (n = {len(base_rows)}, R² = {R2_CTRL:.2f}, adjusted {ADJ_CTRL:.2f}); dependent variable = age-standardised prevalence (pct. points); HC3 SE'), lang))
    o.append(table([t('ตัวควบคุม', 'Control'), 'β', 'SE', 'p'], rows_c, right=(1, 2, 3), widths=['52%', '16%', '16%', '16%']))
    o.append(H(3, f'{lang}-r3', '6.3 ความสัมพันธ์ดิบเทียบกับหลังควบคุมตัวแปรกวน', '6.3 Raw versus adjusted associations'))
    o.append('<p>' + t(f'ตัวอย่างชัดเจนที่สุดคือผู้ค้าน้ำมันต่อประชากร: สหสัมพันธ์ดิบ ρ = {rho_f:+.2f} (รูปที่ 4 ซ้าย) ดูเหมือนแข็งแรง แต่เมื่อควบคุมภาค ความหนาแน่น และอายุ เหลือเพียง β = {beta_f:+.2f} จุดต่อ 1 SD (p = {p_f:.2f}; รูปที่ 4 ขวา) จังหวัดที่มีผู้ค้าน้ำมันมากต่อประชากรส่วนใหญ่อยู่ในภาคที่มีความชุกสูงอยู่แล้ว สัญญาณส่วนใหญ่จึงเป็นเรื่องของภาคและความเป็นชนบท ไม่ใช่ปั๊มน้ำมันเอง',
                      f'The clearest example is fuel traders per capita: the raw correlation ρ = {rho_f:+.2f} (Figure 4, left) looks strong, but after controlling for region, density and age only β = {beta_f:+.2f} points per 1 SD remains (p = {p_f:.2f}; Figure 4, right). Provinces with many fuel traders per capita are mostly in regions with already high prevalence, so most of the signal reflects region and rurality, not fuel traders themselves.') + '</p>')
    o.append(figure(sc_svg, t('ผู้ค้าน้ำมันต่อ 100,000 คนกับความชุกเบาหวานปรับอายุ: ซ้าย = ดิบ, ขวา = ส่วนที่เหลือหลังควบคุมภาค ความหนาแน่น และสัดส่วน 60+ (ส่วนที่เหลือของ X เป็นหน่วย SD)', 'Fuel traders per 100,000 and age-standardised prevalence: left = raw, right = residuals after controlling for region, density and share 60+ (residual of X in SD units)'), lang))
    rows_r = [(nm.get(r['variable'], r['variable']), r['n'], f'{float(r["unadjusted_spearman"]):+.2f}', f'{float(r["beta_per_sd"]):+.2f}', pfmt(float(r['p'])), f'{float(r["p_holm"]):.2f}', f'{float(r["q_bh"]):.2f}', f'{float(r["delta_r2"]):.3f}', VERDICT.get(r['verdict'].split(' | ')[0], (r['verdict'], r['verdict']))[0 if lang == 'th' else 1]) for r in res_rows]
    o.append(tcap(t(f'ผลการทดสอบทีละตัวแปรหลังควบคุมภาค ความหนาแน่น และสัดส่วน 60+ (β = จุดร้อยละของความชุกต่อ +1 SD; ΔR² = ความแปรปรวนที่ตัวแปรเพิ่มให้นอกเหนือตัวควบคุม)', f'One-at-a-time tests after controlling for region, density and share 60+ (β = percentage points of prevalence per +1 SD; ΔR² = variance explained beyond the controls)'), lang))
    o.append(table([t('ตัวแปร', 'Variable'), 'n', 'ρ ' + t('ดิบ', 'raw'), 'β', 'p', 'Holm p', 'BH q', 'ΔR²', t('สรุป', 'Verdict')], rows_r, right=(1, 2, 3, 4, 5, 6, 7), widths=['25%', '5%', '7%', '7%', '7%', '8%', '7%', '7%', '27%']))
    o.append(figure(fig_forest(lang), t('ค่า β หลังควบคุมพร้อมช่วงเชื่อมั่น 95% (HC3); จุดทึบ = ผ่านเกณฑ์ FDR (q < 0.10), จุดกลวง = ไม่ผ่าน', 'Adjusted β with 95% confidence intervals (HC3); filled dot = passes FDR (q < 0.10), open dot = does not'), lang))
    o.append(H(3, f'{lang}-r4', '6.4 กลุ่มตัวแปร "กิจกรรมเศรษฐกิจ" และโมเดลรวม', '6.4 The "economic activity" cluster and the joint model'))
    o.append('<p>' + t(f'สามตัวแปรที่ผ่านเกณฑ์ FDR ได้แก่ อัตราหย่า ไฟฟ้ารวมต่อคน และอัตราสมรส (q ≈ {float(RR["divorce_rate_per_1000"]["q_bh"]):.2f}) แต่ไม่ผ่านเกณฑ์ Holm (p ปรับ ≈ {float(RR["divorce_rate_per_1000"]["p_holm"]):.2f}) และตัวแปรเหล่านี้สัมพันธ์กันสูง: หย่า–สมรส r = {CORR["div_mar"]:.2f}; ไฟฟ้ารวม–GPP (ln) r = {CORR["elec_gpp"]:.2f}; หย่า–GPP (ln) r = {CORR["div_gpp"]:.2f} น่าจะวัดสิ่งเดียวกัน คือความคึกคักทางเศรษฐกิจ/อุตสาหกรรม (และการจดทะเบียนของผู้ย้ายถิ่น เพราะอัตราคำนวณต่อประชากรทะเบียน) '
                      f'เมื่อใส่ทุกตัวแปรที่มีข้อมูลครบพร้อมกัน (R² = {R2_JOINT:.2f}, ปรับแล้ว {ADJ_JOINT:.2f}) เหลือเพียงตัวแปรภาคที่มีนัยสำคัญ ({", ".join(RN.get(r["term"].replace("region=", ""), r["term"]) for r in sig_joint)}) ไม่มีตัวแปรเศรษฐกิจตัวใดมีนัยสำคัญ (ตารางภาคผนวก B) จึงไม่ควรเลือกตัวแปรเดียวมาเล่าเรื่อง ' + (f'ในการตรวจความทนทาน (ตัด 5 จังหวัดเลเวอเรจสูงสุด) ไม่มีตัวแปรที่ผ่าน FDR เปลี่ยนทิศทางหรือเสียนัยสำคัญ' if not fragile else f'ในการตรวจความทนทาน ตัวแปรต่อไปนี้ไม่คงที่: {", ".join(fragile)}'),
                      f'Three variables pass the FDR criterion — divorce rate, total electricity per resident and marriage rate (q ≈ {float(RR["divorce_rate_per_1000"]["q_bh"]):.2f}) — but not Holm (adjusted p ≈ {float(RR["divorce_rate_per_1000"]["p_holm"]):.2f}), and they are highly intercorrelated: divorce–marriage r = {CORR["div_mar"]:.2f}; total electricity–GPP (ln) r = {CORR["elec_gpp"]:.2f}; divorce–GPP (ln) r = {CORR["div_gpp"]:.2f}. They probably measure the same thing: economic/industrial activity (and registration by migrant workers, since the rates are per registry population). '
                      f'When all variables with complete coverage enter together (R² = {R2_JOINT:.2f}, adjusted {ADJ_JOINT:.2f}), only region terms remain significant ({", ".join(RN.get(r["term"].replace("region=", ""), r["term"]) for r in sig_joint)}); no economic variable is significant (Appendix B). A single variable should therefore not be singled out for a story. ' + ('In the robustness check (dropping the 5 highest-leverage provinces) no variable passing FDR changed direction or lost significance.' if not fragile else f'In the robustness check these variables were unstable: {", ".join(fragile)}.')) + '</p>')

    # ---------- 7 DiaGlance
    o.append(H(2, f'{lang}-dg', '7. การประยุกต์: แบบจำลอง DiaGlance (ระดับบุคคล)', '7. Application: the DiaGlance model (individual level)'))
    o.append('<p>' + t('เว็บ DiaGlance (https://moshika-may.github.io/DiaGlance/) เป็นแบบสอบถาม 14 ข้อ ทำงานในเบราว์เซอร์ ไม่ส่งคำตอบออกนอกเครื่อง แปลงคำตอบเป็นอัตราส่วนความน่าจะเป็น (LR) ตามสมการที่ 11–12 แล้วคูณกับอัตราต่อรองตั้งต้น ผลที่ได้คือความน่าจะเป็นหลังสังเกต พร้อมการ์ดสรุปและตารางว่าคำตอบใดดันความเสี่ยงขึ้นหรือลง',
                      'DiaGlance (https://moshika-may.github.io/DiaGlance/) is a 14-question questionnaire that runs in the browser and sends no answers off the device. It turns the answers into likelihood ratios (LR) as in Eqs. 11–12, multiplies them onto the prior odds, and reports the posterior probability with a summary card and a table of which answers push risk up or down.') + '</p>')
    o.append(figure(fig_lr(lang), t('อัตราส่วนความน่าจะเป็น (LR) ของตัวเลือกที่แรงที่สุด 14 ตัวจาก BRFSS: แดง = เพิ่มความเสี่ยง (LR > 1), น้ำเงิน = ลดความเสี่ยง (LR < 1); สเกลลอการิทึม', 'Likelihood ratios (LR) of the 14 strongest options from BRFSS: red = raises risk (LR > 1), blue = lowers risk (LR < 1); log scale'), lang))
    ex_rows = []
    cum = steps[0][0]; ex_rows.append((t('ค่าตั้งต้น (prior 10.1%)', 'Prior (10.1%)'), '—', f'{steps[0][0]:.3f}', f'{100 * steps[0][0] / (1 + steps[0][0]):.1f}%'))
    for od, l, lab in steps[1:]: ex_rows.append((lab[0 if lang == 'th' else 1], f'×{l:.2f}', f'{od:.3f}', f'{100 * od / (1 + od):.1f}%'))
    o.append(tcap(t('ตัวอย่างการคำนวณ: ผู้ตอบอายุ 65+, BMI 30–34.9, เดิน/ขึ้นบันไดลำบากมาก (ข้ออื่นข้าม)', 'Worked example: respondent aged 65+, BMI 30–34.9, serious difficulty walking (other questions skipped)'), lang))
    o.append(table([t('ขั้นตอน', 'Step'), 'LR', t('อัตราต่อรองสะสม', 'Cumulative odds'), t('ความน่าจะเป็น', 'Probability')], ex_rows, right=(1, 2, 3), widths=['52%', '14%', '19%', '15%']))
    o.append('<p>' + t(f'วิธีคิด: อัตราต่อรองตั้งต้น = 0.101/(1−0.101) = {steps[0][0]:.3f} คูณ LR ทีละข้อ ได้อัตราต่อรองสุดท้าย {odds:.3f} หรือความน่าจะเป็น {100 * odds / (1 + odds):.1f}% (เทียบกับ 10.1% ของคนทั่วไป) ผลนี้อาศัยสมมติฐานว่าปัจจัยอิสระต่อกัน ซึ่งไม่จริงเสมอ (เช่น BMI กับการเดินลำบากสัมพันธ์กัน) จึงอาจประเมินสูงเกินเมื่อตอบกลุ่มเสี่ยงหลายข้อพร้อมกัน [13,14]',
                      f'Reasoning: prior odds = 0.101/(1−0.101) = {steps[0][0]:.3f}; multiply by each LR to reach final odds {odds:.3f}, i.e. a probability of {100 * odds / (1 + odds):.1f}% (versus 10.1% for the general population). This relies on the independence assumption, which is not always true (e.g. BMI and difficulty walking are related), so the estimate can be too high when several risk answers occur together [13,14].') + '</p>')
    o.append(box('<b>' + t('บทเรียนเรื่อง "ความสัมพันธ์ ≠ สาเหตุ" จากแอลกอฮอล์', 'A lesson on "association ≠ causation": alcohol') + '</b><br>' + t('ในข้อมูล BRFSS ปี 2563 คนที่เป็นเบาหวานรายงานดื่มหนักเพียง 3.0% เทียบกับ 7.4% ในคนไม่เป็น (LR ≈ 0.40 และทิศทางเดียวกันในทุกช่วงอายุ) ซึ่งไม่ได้แปลว่าการดื่มหนักช่วยป้องกัน แต่น่าจะเพราะผู้ที่ได้รับการวินิจฉัยแล้วมักลดการดื่ม (เหตุและผลย้อนกลับ) ระบบจึงแสดงหมายเหตุกำกับ และไม่นำปัจจัยนี้ไปแสดงในฝั่ง "ช่วยลดความเสี่ยง" ของการ์ดที่แชร์', 'In the 2020 BRFSS data, people with diabetes report heavy drinking in only 3.0% versus 7.4% of people without (LR ≈ 0.40, the same direction in every age band). This does not mean heavy drinking protects; people who have been diagnosed tend to cut back (reverse causation). The app therefore shows a note and keeps this factor out of the "lowers risk" side of the shared card.'), 'warn'))
    o.append('<p class="small">' + t('ข้อจำกัดของ DiaGlance: ข้อมูลเป็นของสหรัฐฯ (ไม่ใช่ไทย) แต่ใช้ prior ของไทย; ข้อสูบบุหรี่/แอลกอฮอล์/ออกกำลังกาย/การเดิน ผู้ใช้ตอบได้หลายระดับแต่ข้อมูลมีแค่ ใช่-ไม่ใช่ จึงรวมเป็น 2 กลุ่ม; ปัจจัยการกินหวานเป็นค่าสมมติจากตัวอย่างในเอกสารสรุป เพราะ BRFSS ที่ใช้ไม่มีตัวแปรนี้; ไม่นับประวัติครอบครัวหรือผลเลือด', 'Limitations of DiaGlance: the data are American (not Thai) but the prior is Thai; for smoking/alcohol/exercise/walking users choose among several levels but the data hold only yes/no, so answers are merged into two groups; the sugar factor is an assumption from the summary note, since the BRFSS files used lack that variable; family history and blood tests are not counted.') + '</p>')

    # ---------- 8 interpretation + limitations (merged)
    o.append(H(2, f'{lang}-disc', '8. การตีความและข้อจำกัด', '8. Interpretation and limitations'))
    o.append('<p>' + t('ผลหลักคือ <b>อายุและภาคอธิบายความต่างระหว่างจังหวัดได้ดีกว่าสภาพแวดล้อมแต่ละตัว</b> สอดคล้องกับธรรมชาติของโรคและกับ Tappo และคณะที่พบความหนาแน่นผู้สูงอายุสัมพันธ์ทางบวก [2] ความต่างจากงานก่อนหน้ามีสามจุด: (ก) Polnakoo และคณะพบรายได้สัมพันธ์ทางลบกับ "ความเสี่ยงเบาหวาน" [1] แต่เราไม่พบ (β รายได้ครัวเรือน = ' + rf('household_income_baht_month', 'beta_per_sd') + ' ไม่มีนัยสำคัญ และ GPP เป็นบวกเล็กน้อย); (ข) Tappo และคณะพบความหนาแน่นร้านอาหารสัมพันธ์ทางบวกชัดเจน [2] แต่เรายังไม่มีข้อมูลร้านอาหารรายจังหวัดครบ จึงทดสอบ H1 ไม่ได้; (ค) งานระดับบุคคลพบว่าผู้หย่าเสี่ยงต่ำกว่าเล็กน้อย [3] แต่ระดับจังหวัดของเราให้ทิศทางตรงข้าม',
                      'The main result is that <b>age and region explain between-province differences better than any single environmental variable</b>, in line with the nature of the disease and with Tappo et al., who found elderly density positively associated [2]. Three differences from earlier work: (a) Polnakoo et al. found income negatively associated with "diabetes risk" [1], but we do not (household-income β = ' + rf('household_income_baht_month', 'beta_per_sd') + ', not significant, and GPP slightly positive); (b) Tappo et al. found food-shop density clearly positive [2], but we lack complete province-level food-outlet data and cannot test H1; (c) individual-level studies find divorced people at slightly lower risk [3], while our province-level result goes the other way.') + '</p>')
    o.append('<p>' + t('สาเหตุที่เป็นไปได้ของความต่าง (ยังไม่ได้ทดสอบ): ตัวแปรเป้าหมายต่างกัน ("ความเสี่ยงที่คัดกรอง" เทียบ "ผู้ป่วยขึ้นทะเบียน" ซึ่งสูงขึ้นในจังหวัดที่มีบริการมาก); เราควบคุมภาค ซึ่งดูดซับความต่างข้ามภาคส่วนใหญ่; อัตราสมรส/หย่าต่อประชากรทะเบียนได้รับอิทธิพลจากผู้ย้ายถิ่นที่จดทะเบียนในจังหวัดอุตสาหกรรม', 'Possible reasons (untested): different outcomes ("screened risk" versus "registered patients", higher where services are plentiful); we control for region, which absorbs most between-region differences; and marriage/divorce rates per registry population are influenced by migrants registering in industrial provinces.') + '</p>')
    o.append('<p><b>' + t('ข้อควรระวังและข้อจำกัด', 'Cautions and limitations') + '</b></p><ul>' + ''.join(f'<li>{x}</li>' for x in (
        t('<b>ความเข้าใจผิดเชิงนิเวศวิทยา</b> [21,22]: ความสัมพันธ์ระหว่างจังหวัดไม่ได้บอกว่าบุคคลที่ได้รับปัจจัยนั้นเสี่ยงมากขึ้น', '<b>Ecological fallacy</b> [21,22]: an association between provinces does not tell us that an individual exposed to the factor is at higher risk.'),
        t('<b>ตัวกวนและตัวแทน:</b> ตัวแปรหลายตัวเป็นตัวแทนของ "ชนบท–เมือง" และ "กิจกรรมเศรษฐกิจ" แยกผลเฉพาะตัวไม่ได้เมื่อสัมพันธ์กันสูง', '<b>Confounders and proxies:</b> many variables proxy "rural–urban" and "economic activity"; their separate effects cannot be isolated when highly correlated.'),
        t('<b>เหตุและผลย้อนกลับ:</b> บริการสุขภาพกระจายตามภาระโรค [1] และผู้ป่วยเปลี่ยนพฤติกรรมหลังวินิจฉัย (ตัวอย่างแอลกอฮอล์)', '<b>Reverse causation:</b> health services follow disease burden [1] and patients change behaviour after diagnosis (the alcohol example).'),
        t('<b>การวัดผลลัพธ์:</b> ผู้ป่วยขึ้นทะเบียน ≠ ความชุกจริง; จังหวัดที่ตรวจมากพบมาก; กรุงเทพฯ ไม่มีข้อมูล', '<b>Outcome measurement:</b> registered patients ≠ true prevalence; provinces that test more find more; Bangkok has no data.'),
        t(f'<b>ขนาดตัวอย่างและเวลา:</b> {len(base_rows)} จังหวัด กำลังทดสอบต่ำ ใส่ตัวควบคุมได้จำกัด; เป็นภาพตัดขวางปีเดียว (ข้อมูลปี พ.ศ. 2561–2568 ไม่ตรงกัน) ยังไม่ได้ใช้ข้อมูลหลายปีหรือแบบจำลองเชิงพื้นที่ [1,2]', f'<b>Sample size and time:</b> {len(base_rows)} provinces, low power, few controls; a single cross-section (data years B.E. 2561–2568 differ); no multi-year or spatial model yet [1,2].'),
        t('<b>ตัวแปรสำคัญขาด:</b> ร้านอาหาร/สะดวกซื้อ/ฟาสต์ฟู้ด เครื่องดื่มหวาน เวลาหน้าจอ/วิดีโอเกม PM2.5 แสงไฟกลางคืน', '<b>Key variables missing:</b> restaurants/convenience stores/fast food, sugary drinks, screen time/video games, PM2.5, night-time light.'),
        t('<b>คุณภาพตัวแปร:</b> ผู้ค้าน้ำมันตามใบอนุญาตและทะเบียนสนามกีฬามีความครบถ้วนไม่ทราบ; อัตราสมรส/หย่าคิดต่อประชากรทะเบียน; ภาค 6 ภาคมาจากความรู้ทั่วไป ไม่ใช่ไฟล์ข้อมูล', '<b>Variable quality:</b> licensed fuel traders and the stadium registry have unknown completeness; marriage/divorce rates use registry population; the six regions come from general knowledge, not a data file.'),
        t(f'<b>การอ่านไฟล์:</b> {lay.get("unknown", 0):,} ไฟล์อ่านโครงสร้างไม่ออกและไม่ถูกนำเข้า; การจับคู่ชื่อตัวชี้วัดอัตโนมัติไม่น่าเชื่อถือ (คำว่า "คดี" จับได้ชุดข้อมูลค่าทนายความ) จึงใช้รายการตัวแปรที่ตรวจด้วยมือ', f'<b>File parsing:</b> {lay.get("unknown", 0):,} files had an unrecognised layout and were not imported; automatic matching of indicator names was unreliable (the keyword "case" matched a lawyer-fee dataset), so a hand-reviewed variable list is used.'),
        t('<b>เอกสารอ้างอิง:</b> บางชิ้นอ่านจากบทคัดย่อ/ข้อความย่อ (เครื่องหมาย † ในรายการอ้างอิง) ควรตรวจกับต้นฉบับ', '<b>References:</b> some were read from abstracts or snippets only (marked † in the list) and should be checked against the originals.'))) + '</ul>')

    # ---------- 9 next steps
    o.append(H(2, f'{lang}-con', '9. สิ่งที่ควรทำต่อ', '9. Next steps'))
    o.append('<ol>' + ''.join(f'<li>{x}</li>' for x in (
        t('เก็บจำนวนร้านสะดวกซื้อ/เซเว่น/ร้านอาหารรายจังหวัดให้ครบ 77 จังหวัด (เช่น ประมวลผลไฟล์ OpenStreetMap ของ Geofabrik เอง) แล้วทดสอบ H1 ซึ่งมีหลักฐานสนับสนุนมากสุด', 'Complete province-level counts of convenience stores/7-Eleven/restaurants for all 77 provinces (e.g. process the Geofabrik OpenStreetMap extract locally) and test H1, the best-supported hypothesis.'),
        t('ใช้ข้อมูลหลายปีพร้อม fixed effects ของจังหวัด และแบบจำลองเชิงพื้นที่ (spatial lag/error)', 'Use multi-year panel data with province fixed effects and spatial (lag/error) models.'),
        t('ขอไมโครดาต้า NHES จาก สวรส. เพื่อวิเคราะห์ระดับบุคคลและเลี่ยงความเข้าใจผิดเชิงนิเวศวิทยา', 'Request NHES microdata from the Health Systems Research Institute for individual-level analysis.'),
        t('เพิ่ม PM2.5 รายจังหวัดและแสงไฟกลางคืน (VIIRS) เป็นตัวแทนความเป็นเมือง', 'Add province-level PM2.5 and night-time light (VIIRS) as an urbanisation proxy.'),
        t('DiaGlance: อัปเดต prior ด้วยข้อมูลไทยล่าสุด และหาข้อมูลการกินหวานระดับบุคคลของไทยแทนค่าสมมติ', 'DiaGlance: update the prior with current Thai data and replace the assumed sugar-intake values with individual-level Thai data.'))) + '</ol>')

    # ---------- 10 provenance
    o.append(H(2, f'{lang}-rep', '10. ที่มาของข้อมูลและโค้ด', '10. Data and code provenance'))
    o.append('<ul>' + ''.join(f'<li>{x}</li>' for x in (
        t('<b>ทำซ้ำได้:</b> โค้ดทั้งหมดอยู่ใน github.com/Moshika-May/DiaGlance (โฟลเดอร์ tools/) คำสั่งอยู่ในภาคผนวก D ตัวเลขและกราฟในรายงานคำนวณจากไฟล์ผลลัพธ์โดย tools/steps/build_report.py', '<b>Reproducible:</b> all code is in github.com/Moshika-May/DiaGlance (folder tools/); commands are in Appendix D. Numbers and charts in this report are computed from the output files by tools/steps/build_report.py.'),
        t('<b>ใบอนุญาตข้อมูล:</b> ส่วนใหญ่ "Open Data Common" ตามแคตตาล็อก data.go.th; ตรวจเงื่อนไขของ NSO และ BRFSS ก่อนเผยแพร่ซ้ำ; OpenStreetMap เป็น ODbL', '<b>Data licences:</b> mostly "Open Data Common" per data.go.th; check NSO and BRFSS terms before redistributing; OpenStreetMap is ODbL.'),
        t('<b>เว็บ NSO:</b> ไฟร์วอลล์ปฏิเสธโปรแกรมที่ไม่ใช่เบราว์เซอร์ จึงใช้ User-Agent แบบเบราว์เซอร์ด้วยอัตราคำขอต่ำ', '<b>NSO website:</b> its firewall rejects non-browser clients, so a browser-style User-Agent was used at a low request rate.'),
        t('<b>ความเป็นส่วนตัว:</b> ใช้เฉพาะข้อมูลรวมระดับพื้นที่; แบบสอบถาม DiaGlance ไม่เก็บหรือส่งคำตอบ', '<b>Privacy:</b> only area-level aggregates are used; the DiaGlance questionnaire stores and sends no answers.'),
        t('<b>การจัดทำ:</b> ภาพแมวสร้างด้วย AI (Gemini); โค้ดวิเคราะห์และร่างรายงานพัฒนาโดยใช้ผู้ช่วย AI (Claude Code)', '<b>Production:</b> the cat images were generated with AI (Gemini); the analysis code and the draft of this report were developed with an AI assistant (Claude Code).'))) + '</ul>')
    return o


REFS = [
    'Polnakoo W, Laohasiriwong W, Sornlorm K. Geospatial determinants of diabetes risk in Thailand: socioeconomic and health service factors. <i>Geospatial Health</i>. 2026;21(1). doi:10.4081/gh.2026.1440',
    'Tappo S, Laohasiriwong W, Puttanapong N. Spatial association of socio-demographic, environmental factors and prevalence of diabetes mellitus in middle-aged and elderly people in Thailand. <i>Geospatial Health</i>. 2022;17(2). doi:10.4081/gh.2022.1091',
    'Karimi MA, Binaei S, Hashemi SH, et al. Marital status and risk of type 2 diabetes among middle-aged and elderly population: a systematic review and meta-analysis. <i>Front Med</i>. 2025;11:1485490. doi:10.3389/fmed.2024.1485490',
    'Papier K, D\'Este C, Bain C, et al. Consumption of sugar-sweetened beverages and type 2 diabetes incidence in Thai adults: results from an 8-year prospective study. <i>Nutr Diabetes</i>. 2017;7(6):e283. doi:10.1038/nutd.2017.27',
    'Mazidi M, Speakman JR. Ambient particulate air pollution (PM2.5) is associated with the ratio of type 2 diabetes to obesity. <i>Sci Rep</i>. 2017;7:9144. doi:10.1038/s41598-017-08287-1',
    'Grøntved A, Hu FB. Television viewing and risk of type 2 diabetes, cardiovascular disease, and all-cause mortality: a meta-analysis. <i>JAMA</i>. 2011;305(23):2448–2455. doi:10.1001/jama.2011.812',
    'den Braver NR, et al. Built environmental characteristics and diabetes: a systematic review and meta-analysis. <i>BMC Med</i>. 2018;16:12. † (read from search snippets only; see https://pmc.ncbi.nlm.nih.gov/articles/PMC7937232)',
    'Guo C, et al. Television viewing and type 2 diabetes: dose-response meta-analysis. <i>Diabetes Obes Metab</i>. 2020. PMID 31468597. † (read from a search snippet only; title and authors not verified)',
    'Arnaez JM, et al. Physical wellness among gaming adults: cross-sectional study. <i>JMIR Serious Games</i>. 2018. https://games.jmir.org/2018/2/e12 † (title/authors from a search snippet)',
    'Diabetes trends and determinants among Thai adults from 2004 to 2020 (NHES). Mahidol University repository. https://repository.li.mahidol.ac.th/handle/123456789/111970 † (authors and journal not verified)',
    'Thai Buddhist monks and obesity/diabetes: Buddhistdoor Global (https://www.buddhistdoor.net/news/thailands-buddhist-monks-urged-to-watch-their-waistlines-amid-obesity-epidemic/); Khaosod English, 30 May 2018. † (news reports)',
    'Centers for Disease Control and Prevention. Behavioral Risk Factor Surveillance System (BRFSS), 2020 and 2022. https://www.cdc.gov/brfss/',
    'Hand DJ, Yu K. Idiot\'s Bayes — not so stupid after all? <i>Int Stat Rev</i>. 2001;69(3):385–398.',
    'Domingos P, Pazzani M. On the optimality of the simple Bayesian classifier under zero-one loss. <i>Mach Learn</i>. 1997;29:103–130.',
    'Spearman C. The proof and measurement of association between two things. <i>Am J Psychol</i>. 1904;15(1):72–101.',
    'MacKinnon JG, White H. Some heteroskedasticity-consistent covariance matrix estimators with improved finite sample properties. <i>J Econometrics</i>. 1985;29(3):305–325.',
    'Long JS, Ervin LH. Using heteroscedasticity consistent standard errors in the linear regression model. <i>Am Stat</i>. 2000;54(3):217–224.',
    'Holm S. A simple sequentially rejective multiple test procedure. <i>Scand J Stat</i>. 1979;6(2):65–70.',
    'Benjamini Y, Hochberg Y. Controlling the false discovery rate: a practical and powerful approach to multiple testing. <i>J R Stat Soc B</i>. 1995;57(1):289–300.',
    'Ahmad OB, Boschi-Pinto C, Lopez AD, et al. Age standardization of rates: a new WHO standard. GPE Discussion Paper Series No. 31. World Health Organization; 2001.',
    'Robinson WS. Ecological correlations and the behavior of individuals. <i>Am Sociol Rev</i>. 1950;15(3):351–357.',
    'Morgenstern H. Ecologic studies in epidemiology: concepts, principles, and methods. <i>Annu Rev Public Health</i>. 1995;16:61–81.',
    'Manning CD, Raghavan P, Schütze H. <i>Introduction to Information Retrieval</i>. Cambridge University Press; 2008 (add-one smoothing, ch. 13).',
    'Lovell MC. Seasonal adjustment of economic time series and multiple regression analysis. <i>J Am Stat Assoc</i>. 1963;58(304):993–1010.',
    'Course project note: "สรุปสูตรความน่าจะเป็นสำหรับการรวมชุดข้อมูล" (Probability formulas for integrating datasets), Data Science and Probability course project (unpublished).']


# ------------------------------------------------------------------ appendices, title, toc, assembly
def appendices():
    o = []
    bi = lambda th, en: f'{th} / {en}'
    o.append('<h1 class="part" id="ap">' + bi('ภาคผนวก', 'Appendices') + '</h1>')
    o.append(heading(2, 'ap-A', bi('ภาคผนวก A · พจนานุกรมตัวแปร', 'Appendix A · Variable dictionary'), bi('ภาคผนวก A · พจนานุกรมตัวแปร', 'Appendix A · Variable dictionary'), 'th'))
    spec = rd('panel_curated_spec.csv')
    own = [('dm_prev_age_std_pct', 'HDC s_dm_pop_age (age groups 15–39, 40–49, 50–59, 60+)', str(TY), str(len(base_rows)), 'age-standardised registered diabetes prevalence, adults 15+ (outcome)'),
           ('share_age_60plus_pct', 'HDC s_dm_pop_age (population by age group)', str(TY), str(len(base_rows)), 'share of residents aged 60+ (control)'),
           ('density_per_km2 / log', 'NSO i7 (registry population ÷ area)', '2568', '77', 'population density, natural log (control)'),
           ('region', 'general knowledge, 6-region scheme', '—', '77', 'North 9, Northeast 20, Central 22, East 7, West 5, South 14 (control)'),
           ('coastal', 'general knowledge (23 coastal provinces)', '—', '77', '0/1 flag')]
    rows_ = own + [(r['variable'], f'{r.get("dataset", "")}: {r.get("label", "").lstrip("^")[:70]}', r.get('year_be', ''), r.get('n_provinces', ''), r.get('note', '')) for r in spec if r.get('status') == 'ok' and not r['variable'].startswith('osm')]
    o.append('<p class="small">' + bi('ทุกตัวแปรอธิบายแปลงตามหัวข้อ 5.2 (หารประชากรทะเบียน, ln, z-score)', 'All explanatory variables are transformed as in Sec. 5.2 (per registry population, ln, z-score).') + '</p>')
    o.append(table([bi('ตัวแปร', 'Variable'), bi('ที่มา', 'Source'), bi('ปี พ.ศ.', 'Year (B.E.)'), 'n', bi('ความหมาย', 'Definition / note')], [(f'<code>{a}</code>', b, c, d, e) for a, b, c, d, e in rows_], widths=['22%', '33%', '8%', '6%', '31%']))
    o.append(heading(2, 'ap-B', bi('ภาคผนวก B · แบบจำลองรวม', 'Appendix B · Joint model'), bi('ภาคผนวก B · แบบจำลองรวม', 'Appendix B · Joint model'), 'th'))
    o.append('<p class="small">' + bi(f'ตัวควบคุม + ตัวแปรที่มีข้อมูลครบทุกจังหวัดพร้อมกัน (n = {len(base_rows)}, R² = {R2_JOINT:.2f}, ปรับแล้ว {ADJ_JOINT:.2f}); ตัวแปรอธิบายเป็น z-score', f'Controls plus all variables with complete coverage entered together (n = {len(base_rows)}, R² = {R2_JOINT:.2f}, adjusted {ADJ_JOINT:.2f}); explanatory variables are z-scores.') + '</p>')
    o.append(table([bi('พจน์', 'Term'), 'β', 'SE (HC3)', 'p'], [(f'<code>{r["term"]}</code>', f'{float(r["beta"]):+.3f}', f'{float(r["se_hc3"]):.3f}', pfmt(float(r['p']))) for r in joint_rows], right=(1, 2, 3), widths=['52%', '16%', '16%', '16%']))
    o.append(heading(2, 'ap-C', bi('ภาคผนวก C · ความชุกรายจังหวัด', f'Appendix C · Prevalence by province'), bi('ภาคผนวก C · ความชุกรายจังหวัด', 'Appendix C · Prevalence by province'), 'th'))
    o.append('<p class="small">' + bi(f'ความชุกเบาหวานที่ขึ้นทะเบียน อายุ 15+ ปี พ.ศ. {TY} เรียงจากมากไปน้อย (ปรับอายุ / ดิบ, %); กรุงเทพมหานครไม่มีข้อมูล', f'Registered diabetes prevalence, adults 15+, B.E. {TY}, sorted high to low (age-standardised / crude, %); Bangkok has no data.') + '</p>')
    SHORT = dict(north='เหนือ/N', northeast='อีสาน/NE', central='กลาง/C', east='ตะวันออก/E', west='ตะวันตก/W', south='ใต้/S')
    prow = sorted(nat, key=lambda r: -float(r['dm_prev_pct_15plus_age_standardised']))
    reg_of = {r['province_th']: r['region'] for r in panel}
    rr = [(i + 1, r['province_th'], SHORT[reg_of[r['province_th']]], f'{float(r["dm_prev_pct_15plus_age_standardised"]):.2f}', f'{float(r["dm_prev_pct_15plus_crude"]):.2f}') for i, r in enumerate(prow)]
    o.append('<div class="cols2">' + table(['#', bi('จังหวัด', 'Province'), bi('ภาค', 'Region'), bi('ปรับอายุ', 'Std'), bi('ดิบ', 'Crude')], rr, right=(0, 3, 4), widths=['9%', '33%', '24%', '17%', '17%']) + '</div>')
    o.append(heading(2, 'ap-D', bi('ภาคผนวก D · วิธีทำซ้ำ', 'Appendix D · How to reproduce'), bi('ภาคผนวก D · วิธีทำซ้ำ', 'Appendix D · How to reproduce'), 'th'))
    o.append('<pre style="font:8.4pt/1.5 \'Noto Sans Mono\',\'DejaVu Sans Mono\',monospace;background:#f1f0ec;padding:8pt;border-radius:4px;white-space:pre-wrap">' + esc(
        'git clone https://github.com/Moshika-May/DiaGlance.git && cd DiaGlance\n'
        'python3 -I tools/pipeline.py full                # download -> parse -> staging -> validation -> canonical -> analysis (resumable)\n'
        '                                                 # add --osm for OpenStreetMap counts; single stages: fetch | parse | stage | validate | external | canonical | curated | analyze\n'
        'python3 -I tools/steps/build_report.py           # this report (needs Google Chrome)\n'
        'python3 -I tools/build.py                       # DiaGlance likelihood table from BRFSS (data/heart_*.csv)') + '</pre>')
    return o


def title_page():
    return ('<div class="kick">รายงาน · REPORT · ' + DATE_BE + ' / ' + DATE_CE + '</div>'
            '<h1>เบาหวานระดับจังหวัดของไทย: ข้อมูล หลักฐาน และสิ่งที่ข้อมูลบอกได้<br><span class="en">Provincial diabetes in Thailand: data, evidence and what the data can say</span></h1>'
            '<p class="small">github.com/Moshika-May/DiaGlance · moshika-may.github.io/DiaGlance · ' + 'เพื่อการเรียนรู้ ไม่ใช่คำแนะนำทางการแพทย์ / for learning, not medical advice</p>')


def toc_html(pages):
    rows = ['<h2 style="margin-top:14pt">สารบัญ / Contents</h2><table class="toc">']
    for ident, lvl, th, en in HEADS:
        if lvl != 2: continue
        txt = th if ident.startswith('th-') else en if ident.startswith('en-') else th
        part = ''
        if ident.endswith('-abs') and ident.startswith('th-'): rows.append('<tr class="l1"><td colspan="2"><b>ภาษาไทย</b></td></tr>')
        if ident.endswith('-abs') and ident.startswith('en-'): rows.append('<tr class="l1"><td colspan="2"><b>English</b></td></tr>')
        rows.append(f'<tr class="l2"><td>{txt}</td><td class="pg">{pages.get(ident, "")}</td></tr>')
    rows.append('</table>')
    return ''.join(rows)


def assemble(pages):
    HEADS.clear()
    parts = []
    for lang in ('th', 'en'):
        o, ctx = content_a(lang); o = content_b(lang, o, ctx)
        if lang == 'en':
            o.append(heading(2, 'refs', 'เอกสารอ้างอิง / References', 'เอกสารอ้างอิง / References', 'th') if False else '')
        parts.append(''.join(o))
    refs = heading(2, 'refs', 'เอกสารอ้างอิง / References', 'เอกสารอ้างอิง / References', 'th') + '<p class="small">† = ' + 'อ่านจากบทคัดย่อ/ข้อความย่อเท่านั้น / read from abstract or snippet only' + '</p><ol class="small" style="padding-left:20pt">' + ''.join(f'<li>{r}</li>' for r in REFS) + '</ol>'
    ap = ''.join(appendices())   # build first: registers the appendix headings for the table of contents
    body = title_page() + toc_html(pages) + parts[0] + parts[1] + refs + ap
    return f'<!doctype html><html lang="th"><head><meta charset="utf-8"><title>DiaGlance Report (TH/EN)</title><style>{CSS}</style></head><body>{body}</body></html>'


def chrome():
    for c in ('/opt/google/chrome/chrome', shutil.which('google-chrome'), shutil.which('chromium')):
        if c and Path(c).exists(): return c
    sys.exit('Google Chrome / Chromium not found (needed to print the PDF)')


def render(html_text, name):
    OUT.mkdir(parents=True, exist_ok=True); h, pdf = OUT / f'{name}.html', OUT / f'{name}.pdf'
    h.write_text(html_text, encoding='utf-8')
    subprocess.run([chrome(), '--headless=new', '--no-sandbox', '--disable-gpu', '--no-pdf-header-footer', f'--print-to-pdf={pdf}', f'file://{h}'], capture_output=True, timeout=300)
    return pdf


def page_numbers(pdf):
    n = int(re.search(r'Pages:\s+(\d+)', subprocess.run(['pdfinfo', str(pdf)], capture_output=True, text=True).stdout).group(1))
    txt = [re.sub(r'\s+', '', subprocess.run(['pdftotext', '-f', str(i), '-l', str(i), str(pdf), '-'], capture_output=True, text=True).stdout) for i in range(1, n + 1)]
    toc_end = max(i for i, t_ in enumerate(txt) if 'สารบัญ/Contents' in t_)   # last page that is part of the contents
    pages, start = {}, toc_end + 1
    for ident, lvl, th, en in HEADS:
        if lvl != 2: continue
        key = re.sub(r'\s+', '', (th if ident.startswith('th-') else en if ident.startswith('en-') else th))
        for i in range(start, n):
            if key in txt[i]: pages[ident] = i + 1; start = i; break
    return pages, n


def main():
    pages = {}
    pdf = render(assemble(pages), 'DiaGlance_Report_TH_EN')       # pass 1: layout without page numbers
    pages, n = page_numbers(pdf)
    pdf = render(assemble(pages), 'DiaGlance_Report_TH_EN')       # pass 2: contents with real page numbers
    pages2, n2 = page_numbers(pdf)
    if pages2 != pages: pdf = render(assemble(pages2), 'DiaGlance_Report_TH_EN'); pages2, n2 = page_numbers(pdf)
    print(f'{pdf.relative_to(ROOT)}: {n2} pages; {len(pages2)} headings located')


if __name__ == '__main__':
    main()
