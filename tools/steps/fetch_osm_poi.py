"""Count OpenStreetMap points of interest per Thai province (consistent across all 77 provinces) -> data/external/osm_poi_by_province.csv

  python3 -I tools/steps/fetch_osm_poi.py            # resumable: provinces already in the csv are skipped
  python3 -I tools/steps/fetch_osm_poi.py --pause 45 # seconds between requests (default 30)

Uses the public Overpass API *politely*: one request at a time, a pause between requests, exponential back-off on 429/502/504, and it gives up
(keeping what it has) after repeated failures. The public server says it is not meant for heavy automated use, so if this keeps failing use a
Geofabrik extract + local processing instead of retrying harder. OSM data is (c) OpenStreetMap contributors, ODbL.
Counts reflect how completely volunteers mapped each province: a measurement-quality caveat, not a census.
"""
import argparse, csv, json, re, sys, time, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent   # repo root (this file lives in tools/steps/)
OUT = ROOT / 'data' / 'external' / 'osm_poi_by_province.csv'
UA = 'DiaGlance-research/1.0 (+https://github.com/Moshika-May/DiaGlance)'
URLS = ['https://overpass-api.de/api/interpreter', 'https://lz4.overpass-api.de/api/interpreter', 'https://z.overpass-api.de/api/interpreter']   # the project's own public servers, tried in turn
PROV_CODES = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49,
              50, 51, 52, 53, 54, 55, 56, 57, 58, 60, 61, 62, 63, 64, 65, 66, 67, 70, 71, 72, 73, 74, 75, 76, 77, 80, 81, 82, 83, 84, 85, 86, 90, 91, 92, 93, 94, 95, 96]
# name -> Overpass selector. Order matters: results come back as one count element per statement, in this order.
FEATURES = [
    ('seven_eleven_wikidata', 'nwr["brand:wikidata"="Q259340"]'),
    ('seven_eleven_name', 'nwr["name"~"7[- ]?eleven|เซเว่น",i]'),
    ('convenience_stores', 'nwr["shop"="convenience"]'),
    ('supermarkets', 'nwr["shop"="supermarket"]'),
    ('fast_food', 'nwr["amenity"="fast_food"]'),
    ('restaurants', 'nwr["amenity"="restaurant"]'),
    ('cafes', 'nwr["amenity"="cafe"]'),
    ('fuel_stations', 'nwr["amenity"="fuel"]'),
    ('fitness_centres', 'nwr["leisure"="fitness_centre"]'),
    ('sports_centres', 'nwr["leisure"="sports_centre"]'),
    ('cinemas', 'nwr["amenity"="cinema"]'),
    ('buddhist_places_of_worship', 'nwr["amenity"="place_of_worship"]["religion"="buddhist"]'),
    ('pharmacies', 'nwr["amenity"="pharmacy"]'),
    ('hospitals', 'nwr["amenity"="hospital"]'),
]


def query(code):
    body = ''.join(f'({sel}(area.a);)->.x{i}; .x{i} out count;\n' for i, (_, sel) in enumerate(FEATURES))
    return f'[out:json][timeout:600];\narea["ISO3166-2"="TH-{code}"]->.a;\n{body}'


def post(q, n=0):
    req = urllib.request.Request(URLS[n % len(URLS)], data=urllib.parse.urlencode({'data': q}).encode(), headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=660) as r: return json.load(r)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--pause', type=float, default=30); ap.add_argument('--max-failures', type=int, default=8)
    a = ap.parse_args()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if OUT.exists():
        with open(OUT, encoding='utf-8-sig') as f: done = {int(r['province_code']) for r in csv.DictReader(f)}
    cols = ['province_code'] + [n for n, _ in FEATURES] + ['osm_timestamp']
    new = not OUT.exists()
    fails = 0
    with open(OUT, 'a', newline='', encoding='utf-8-sig' if new else 'utf-8') as f:
        w = csv.DictWriter(f, cols)
        if new: w.writeheader()
        for code in PROV_CODES:
            if code in done: continue
            wait = 60
            while True:
                try:
                    d = post(query(code), fails); counts = [int(e['tags']['total']) for e in d['elements'] if e.get('type') == 'count']
                    if len(counts) != len(FEATURES): raise ValueError(f'expected {len(FEATURES)} counts, got {len(counts)}')
                    w.writerow(dict(province_code=code, osm_timestamp=d.get('osm3s', {}).get('timestamp_osm_base', ''), **{n: c for (n, _), c in zip(FEATURES, counts)}))
                    f.flush(); fails = 0
                    print(f'TH-{code}: 7-Eleven(wikidata)={counts[0]} convenience={counts[2]} fuel={counts[7]}', flush=True); break
                except Exception as e:
                    fails += 1; print(f'TH-{code}: {type(e).__name__} {str(e)[:80]} (failure {fails}, waiting {wait}s)', file=sys.stderr, flush=True)
                    if fails >= a.max_failures: print('too many failures: stopping, rerun later to resume', file=sys.stderr); return
                    time.sleep(wait); wait = min(wait * 2, 900)
            time.sleep(a.pause)
    print('done')


if __name__ == '__main__':
    main()
