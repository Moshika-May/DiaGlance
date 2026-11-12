# tools/ — from raw data to the quiz

Everything is Python standard library (3.10+, developed on 3.14). **There is nothing to `pip install` to build the data or the quiz.**

```
survey files (CDC BRFSS) ──┐
Thai open data (HDC, NSO…) ─┼─► tools/pipeline.py ─► data/canonical/*.csv ─► tools/build.py ─► model.js ─► index.html (the quiz)
your own CSV / Excel links ─┘        (fetch → parse → staging → validation → canonical → analysis)
```

## 1. Set up (once)

```bash
git clone https://github.com/Moshika-May/DiaGlance.git && cd DiaGlance
python3 -m venv .venv && source .venv/bin/activate     # optional but tidy; Windows: .venv\Scripts\activate
pip install -r tools/requirements.txt                   # only Pillow, and only needed for tools/cutout.py
```

| Need | For | Install (Fedora / Debian-Ubuntu) |
|---|---|---|
| Python 3.10+ | everything | usually preinstalled |
| LibreOffice (`soffice`) | converting old `.xls` / NSO tables to csv | `sudo dnf install libreoffice-calc` / `sudo apt install libreoffice-calc` |
| Google Chrome + poppler (`pdftotext`, `pdfinfo`) | only `steps/build_report.py` (the PDF report) | Chrome from google.com/chrome; `sudo dnf install poppler-utils` / `sudo apt install poppler-utils` |
| ~12 GB free disk | the full open-data download (`data/` is git-ignored); cap it with `fetch_opendata.py --max-total-gb` | |

Run scripts as `python3 -I tools/...` (isolated mode: nothing in the current folder can shadow the standard library).

**You must put the two CDC survey files in `data/` yourself** (they are not in git): `data/heart_2020_cleaned.csv` and `data/heart_2022_with_nans.csv`
(CDC BRFSS 2020 / 2022 extracts; see the links in `tools/sources.csv`).

## 2. Run the quiz locally

```bash
python3 -m http.server 8000      # then open http://localhost:8000
```
or just open `index.html` in a browser (works from disk too; the cat pictures are embedded in `cats.js`).
After the quiz data changes: `python3 -I tools/pipeline.py model` (or `python3 -I tools/build.py`) rewrites `model.js`.

## 3. Get the data and rebuild everything

```bash
python3 -I tools/pipeline.py full        # fetch → parse → stage → validate → external → canonical → analyze → model
```
Hours for the first run (thousands of files), resumable: run it again and it continues. Single stages:

| Command | What it does | Output |
|---|---|---|
| `pipeline.py fetch` (`--osm` adds OpenStreetMap) | data.go.th crawl, NSO tables, your own sources | `data/open/`, `data/external/`, `data/sources/` |
| `pipeline.py parse` / `stage` / `validate` | detect layout → one long format → quality rules (warn/reject, nothing silently dropped) | `data/pipeline/` |
| `pipeline.py external` / `canonical` / `curated` | province-year facts, the diabetes target, the curated variable panel | `data/canonical/*.csv` |
| `pipeline.py analyze` | controlled models (region fixed effects, density, share 60+, HC3, Holm/BH) | `data/canonical/model_results.csv`, `analysis_report.md` |
| `pipeline.py model` | rebuild `model.js` for the quiz | `model.js`, `data/canonical/nb_validation.txt` |

Quick test on a subset: `pipeline.py parse --topics temple --limit 20` (do not follow it with `all`: that rewrites staging from the subset).
Optional: `python3 -I tools/steps/build_report.py` makes the bilingual PDF report in `docs/report/`.

## 4. Add data — no code needed

**A. A new province-level dataset (CSV or Excel link)**

1. Add one row to `tools/sources.csv` (open it in Excel/LibreOffice):

   | source_id | kind | title | url | format |
   |---|---|---|---|---|
   | `my_gyms` | `url` | Fitness gyms per province | `https://…/gyms.xlsx` | `xlsx` |

   `kind=url` downloads it, `kind=local` + `path=` reads a file you already have. Formats: `csv`, `xlsx` (standard library), `xls` (needs LibreOffice).
2. `python3 -I tools/steps/fetch_sources.py` → `data/sources/my_gyms.csv` (first sheet, UTF-8).
3. Add one row to `tools/province_variables.csv` to say which columns matter:

   | variable | source_id | province_col | value_col | year_col | agg | per | … |
   |---|---|---|---|---|---|---|---|
   | `gyms_per_100k` | `my_gyms` | `จังหวัด` | `จำนวน` | `ปี` | `sum` | `100k` | |

   `agg` = `value` / `sum` / `mean` / `count` (count = number of rows per province); `per` = `100k`, `1000` or empty; `filter_col`/`filter_value` keep only some rows;
   the newest year that covers ≥ 60 provinces is used. Province names/codes are matched automatically.
4. `python3 -I tools/pipeline.py curated` → the variable appears in `data/canonical/panel_curated.csv` (and in `panel_curated_spec.csv`, status ok/FAILED with the reason) and in the next `pipeline.py analyze`.

**B. Show a variable on the quiz result page** — add a row to `tools/app_province.csv` (`column` from the panel, Thai/English label, Thai/English unit, decimals), then `pipeline.py model`.

**C. New CDC/BRFSS year or another survey** — in `tools/model_spec.json`: add the file to `sources.csv` (`kind=builtin`, `path=data/…`), add an entry to `datasets`
(`id`, `source_id`, `outcome.col` / `positive`; `"validate": true` marks the file used for the hold-out check), and for each factor add `from.<dataset id>` = which column gives the answer.
Rule types: `yn` (Yes/No), `raw`, `map` (+`default`), `num` (`thresholds` + `codes`, optional `extract: first_int`), `anyyes` (several columns). No rule = that file is not counted for that factor.
**D. A new question** — add a factor to `model_spec.json` (Thai/English text, answer options, `from` rules). Factors without `from` are *assumed* (hand-set `pD`/`pN`, or `pN` + `or`) and are labelled "assumed" in the app.
Then `pipeline.py model`; the hold-out report (`data/canonical/nb_validation.txt`) tells you whether it helped.

## 5. What each file is

| File | Role |
|---|---|
| `pipeline.py` | the one command for data (stages above) |
| `build.py` | `model.js` from `model_spec.json` + canonical data (likelihoods, exponent τ, province table) |
| `model_spec.json` | recipe: datasets, questions, answer options, which column answers what |
| `sources.csv` | inventory of data sources (id, publisher, link, license, what it is used for) |
| `province_variables.csv` | province variables built from sources (section 4A) |
| `app_province.csv` | which panel columns the result page shows |
| `provinces.csv` | Thai/English names, region, coastal flag, neighbouring provinces (**neighbours were entered by hand from map knowledge — approximate, please check**) |
| `steps/fetch_*.py` | downloaders (data.go.th, NSO, OpenStreetMap, your sources) |
| `steps/analyze.py`, `steps/build_report.py` | statistical analysis, PDF report |
| `cutout.py`, `embed_cats.py` | cat pictures: background removal, embed into `cats.js` |

## 6. Troubleshooting

- **NSO answers HTTP 418** — its firewall rejects non-browser clients; the fetchers send a Firefox-style User-Agent at a low request rate. Keep it polite.
- **Overpass (OpenStreetMap) 502 / busy** — the public servers are shared; `steps/fetch_osm_poi.py` rotates servers, stops after repeated failures, and continues next time you run it.
- **`FAILED … column(s) not in the file`** in `panel_curated_spec.csv` — a column name in `province_variables.csv` does not match the header (the message lists the real ones).
- **A province is not recognised** — add the spelling to `ALIAS` in `pipeline.py`.
- **Quiz shows no province question** — `data/canonical/target_dm_province_year.csv` is missing; run `pipeline.py full` (or `canonical`) first, then `pipeline.py model`.
