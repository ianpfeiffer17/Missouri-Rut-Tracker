#!/usr/bin/env python3
"""Missouri Rut Tracker data-feed updater.

Run by the scheduled "Rut Tracker refresh" task. It downloads:
  * GFS and ECMWF IFS deterministic runs (Open-Meteo forecast API) for the tracker's 81-point Missouri grid,
  * GFS (GEFS) and ECMWF ensemble runs (Open-Meteo ensemble API) at the six climate-division centroids,
  * the latest CPC outlook GIS files (monthly, week 3-4, 8-14 day, 6-10 day),
and writes JSON documents for the artifact's shared database plus manifest.json describing the writes.

Usage: python3 -I updater.py OUT_DIR [PREV_FEED_DIR]
  PREV_FEED_DIR: folder holding the current feed docs (from an ArtifactData list with out_dir), used to
  carry the run history forward. Nothing here touches the artifact; the calling session does the writes.
Standard library only, plus pyshp (pip install pyshp) for the CPC shapefiles.
"""
import datetime as dt
import io
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
import zipfile

OUT = sys.argv[1] if len(sys.argv) > 1 else 'feedout'
PREV = sys.argv[2] if len(sys.argv) > 2 else None
FIX = os.environ.get('RUT_FIXTURES')  # offline testing: folder of saved responses
os.makedirs(OUT, exist_ok=True)

# 81 half-degree grid points inside Missouri, in the page's order: [lon, lat, climate division]
PTS = [[-90.25, 36, "2306"], [-94.25, 36.5, "2304"], [-93.75, 36.5, "2304"], [-93.25, 36.5, "2304"], [-92.75, 36.5, "2304"], [-92.25, 36.5, "2304"], [-91.75, 36.5, "2305"], [-91.25, 36.5, "2305"], [-90.75, 36.5, "2305"], [-90.25, 36.5, "2306"], [-89.75, 36.5, "2306"], [-94.25, 37, "2304"], [-93.75, 37, "2304"], [-93.25, 37, "2304"], [-92.75, 37, "2304"], [-92.25, 37, "2304"], [-91.75, 37, "2305"], [-91.25, 37, "2305"], [-90.75, 37, "2305"], [-90.25, 37, "2305"], [-89.75, 37, "2306"], [-89.25, 37, "2306"], [-94.25, 37.5, "2304"], [-93.75, 37.5, "2304"], [-93.25, 37.5, "2304"], [-92.75, 37.5, "2304"], [-92.25, 37.5, "2305"], [-91.75, 37.5, "2305"], [-91.25, 37.5, "2305"], [-90.75, 37.5, "2305"], [-90.25, 37.5, "2305"], [-89.75, 37.5, "2305"], [-94.25, 38, "2303"], [-93.75, 38, "2303"], [-93.25, 38, "2303"], [-92.75, 38, "2303"], [-92.25, 38, "2305"], [-91.75, 38, "2305"], [-91.25, 38, "2305"], [-90.75, 38, "2305"], [-90.25, 38, "2305"], [-94.25, 38.5, "2303"], [-93.75, 38.5, "2303"], [-93.25, 38.5, "2303"], [-92.75, 38.5, "2303"], [-92.25, 38.5, "2303"], [-91.75, 38.5, "2302"], [-91.25, 38.5, "2302"], [-90.75, 38.5, "2302"], [-94.25, 39, "2301"], [-93.75, 39, "2301"], [-93.25, 39, "2301"], [-92.75, 39, "2301"], [-92.25, 39, "2302"], [-91.75, 39, "2302"], [-91.25, 39, "2302"], [-90.75, 39, "2302"], [-94.75, 39.5, "2301"], [-94.25, 39.5, "2301"], [-93.75, 39.5, "2301"], [-93.25, 39.5, "2301"], [-92.75, 39.5, "2301"], [-92.25, 39.5, "2302"], [-91.75, 39.5, "2302"], [-91.25, 39.5, "2302"], [-95.25, 40, "2301"], [-94.75, 40, "2301"], [-94.25, 40, "2301"], [-93.75, 40, "2301"], [-93.25, 40, "2301"], [-92.75, 40, "2302"], [-92.25, 40, "2302"], [-91.75, 40, "2302"], [-95.25, 40.5, "2301"], [-94.75, 40.5, "2301"], [-94.25, 40.5, "2301"], [-93.75, 40.5, "2301"], [-93.25, 40.5, "2301"], [-92.75, 40.5, "2301"], [-92.25, 40.5, "2302"], [-91.75, 40.5, "2302"]]
# climate-division centroids [division, lon, lat]
CENT = [["2301", -93.956, 39.799], ["2302", -91.708, 39.321], ["2303", -93.469, 38.33], ["2304", -93.358, 37.117], ["2305", -91.066, 37.452], ["2306", -89.866, 36.649]]
SAT_UTC = int(dt.datetime(2026, 11, 14, 12, tzinfo=dt.timezone.utc).timestamp())  # 6 AM CST, opening Saturday
UA = {'User-Agent': 'MissouriRutTracker/1.0 (scheduled refresh)'}
errors, status = [], {}


OM_KEY = os.environ.get('OPEN_METEO_KEY', '').strip()


def keyed(url):
    if not OM_KEY or 'open-meteo.com' not in url or '/v1/' not in url:
        return url
    url = url.replace('://api.open-meteo.com/', '://customer-api.open-meteo.com/').replace('://ensemble-api.open-meteo.com/', '://customer-ensemble-api.open-meteo.com/')
    return url + ('&' if '?' in url else '?') + 'apikey=' + OM_KEY


def get(url, timeout=120, binary=False):
    url = keyed(url)
    if FIX:
        import hashlib
        name = hashlib.sha1(url.encode()).hexdigest()
        path = os.path.join(FIX, name)
        data = open(path, 'rb').read()
    else:
        import time
        for attempt in range(3):  # brief network hiccups are common; try a few times
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                    data = r.read()
                break
            except urllib.error.HTTPError as e:
                if e.code < 500 and e.code != 429 or attempt == 2:
                    try:
                        why = json.loads(e.read().decode('utf-8', 'replace')).get('reason')
                    except Exception:  # noqa: BLE001
                        why = None
                    if why:
                        raise RuntimeError(f'HTTP {e.code}: {why}') from None
                    raise
                time.sleep(10 * (attempt + 1))
            except Exception:  # noqa: BLE001
                if attempt == 2:
                    raise
                time.sleep(10 * (attempt + 1))
    return data if binary else data.decode('utf-8', 'replace')


def r1(x, nd=1):
    return None if x is None else round(float(x), nd)


# ---------------------------------------------------------------- deterministic grid
VARS = ['temperature_2m', 'dew_point_2m', 'wind_speed_10m', 'wind_direction_10m', 'wind_gusts_10m', 'precipitation']
KEY = {'temperature_2m': 't', 'dew_point_2m': 'td', 'wind_speed_10m': 'ws', 'wind_direction_10m': 'wd', 'wind_gusts_10m': 'wg', 'precipitation': 'p'}
DMODELS = {'gfs': (['gfs_seamless', 'gfs_global', 'gfs025'], 16, ['ncep_gfs013', 'ncep_gfs025']), 'ecmwf': (['ecmwf_ifs025', 'ecmwf_ifs'], 15, ['ecmwf_ifs025', 'ecmwf_ifs'])}
# run metadata (static files, not API calls): which run each source holds and when it finished arriving
SOURCES = {'gfs': ('api', ['ncep_gfs013', 'ncep_gfs025']), 'ecmwf': ('api', ['ecmwf_ifs025', 'ecmwf_ifs']),
           'ens_gfs': ('ensemble-api', ['ncep_gefs025', 'ncep_gefs05']), 'ens_ecmwf': ('ensemble-api', ['ecmwf_ifs025_ensemble'])}


def run_meta(host, name):
    try:
        mj = json.loads(get(f'https://{host}.open-meteo.com/data/{name}/static/meta.json', 30))
        return mj.get('last_run_initialisation_time'), mj.get('last_run_availability_time')
    except Exception:  # noqa: BLE001
        return None, None


def signature(src):
    """[init, availability] for every file behind a source; changes whenever a new run (or the rest of one) lands"""
    host, names = SOURCES[src]
    return [list(run_meta(host, n)) for n in names]

LATS = ','.join(f'{p[1]:.4f}' for p in PTS)
LONS = ','.join(f'{p[0]:.4f}' for p in PTS)


def pick(h, v, mid):
    if v in h:
        return h[v]
    if f'{v}_{mid}' in h:
        return h[f'{v}_{mid}']
    for k, val in h.items():
        if k.startswith(v + '_'):
            return val
    return []


def deterministic(key):
    ids, days, meta = DMODELS[key]
    last = ''
    for mid in ids:
        url = (f'https://api.open-meteo.com/v1/forecast?latitude={LATS}&longitude={LONS}&hourly={",".join(VARS)}'
               f'&models={mid}&forecast_days={days}&temperature_unit=fahrenheit&wind_speed_unit=mph'
               f'&precipitation_unit=inch&timeformat=unixtime&timezone=GMT')
        try:
            j = json.loads(get(url, 180))
        except Exception as e:  # noqa: BLE001
            last = str(e)
            continue
        if isinstance(j, dict) and j.get('error'):
            last = str(j.get('reason'))
            continue
        arr = j if isinstance(j, list) else [j]
        tm = arr[0]['hourly']['time']
        raw = [{KEY[v]: pick(o['hourly'], v, mid) for v in VARS} for o in arr]
        n = len(tm)
        while n > 0 and all((p['t'][n - 1] if n - 1 < len(p['t']) else None) is None for p in raw):
            n -= 1
        idx = [i for i in range(n) if tm[i] % 10800 == 0]
        if len(idx) < 8:  # a run that is still arriving (or a refused request) can come back empty
            last = f'{mid} returned no values'
            continue

        def agg(p, i, k):
            arr_k = p[k]
            if k in ('p', 'wg'):
                vals = [arr_k[j] for j in range(max(0, i - 2), i + 1) if j < len(arr_k) and arr_k[j] is not None]
                if not vals:
                    return None
                return round(sum(vals), 2) if k == 'p' else round(max(vals), 1)
            x = arr_k[i] if i < len(arr_k) else None
            return None if x is None else (int(round(x)) if k == 'wd' else round(x, 1))

        pts = [{k: [agg(p, i, k) for i in idx] for k in KEY.values()} for p in raw]
        init = None
        for name in meta:  # first file that answers; for GFS the 0.13° file covers the first days
            init = run_meta('api', name)[0]
            if init:
                break
        return {'id': mid, 'time': [tm[i] for i in idx], 'pts': pts, 'init': init}
    raise RuntimeError(f'{key}: {last}')


# ---------------------------------------------------------------- ensembles
EMODELS = {'gfs': ['gfs_seamless', 'gfs025', 'gfs05'], 'ecmwf': ['ecmwf_ifs025', 'ecmwf_ifs04']}
EVARS = ['temperature_2m', 'wind_speed_10m', 'wind_direction_10m', 'wind_gusts_10m']
EK = {'temperature_2m': 't', 'wind_speed_10m': 'ws', 'wind_direction_10m': 'wd', 'wind_gusts_10m': 'wg'}


def members(h, v):
    out = {}
    rx = re.compile('^' + re.escape(v) + r'(?:_member(\d+))?(?:_[a-z0-9_]+)?$')
    for k, val in h.items():
        m = rx.match(k)
        if m and not (m.group(1) is None and '_member' in k):
            out[int(m.group(1) or 0)] = val
    return out


def ensemble(key):
    last = ''
    lats = ','.join(f'{c[2]:.3f}' for c in CENT)
    lons = ','.join(f'{c[1]:.3f}' for c in CENT)
    for mid in EMODELS[key]:
        url = (f'https://ensemble-api.open-meteo.com/v1/ensemble?latitude={lats}&longitude={lons}&hourly={",".join(EVARS)}'
               f'&models={mid}&forecast_days=16&temperature_unit=fahrenheit&wind_speed_unit=mph&timeformat=unixtime&timezone=GMT')
        try:
            j = json.loads(get(url, 180))
        except Exception as e:  # noqa: BLE001
            last = str(e)
            continue
        if isinstance(j, dict) and j.get('error'):
            last = str(j.get('reason'))
            continue
        arr = j if isinstance(j, list) else [j]
        tm = arr[0]['hourly']['time']
        per = [{EK[v]: members(o['hourly'], v) for v in EVARS} for o in arr]
        mids = sorted(set.intersection(*[set(p['t'].keys()) for p in per])) if per else []
        if not mids or not any(v is not None for p in per for m in mids for v in (p['t'].get(m) or [])):
            last = f'{mid} ensemble returned no values'
            continue
        series = []
        for m in mids:
            s = {}
            for k in ('t', 'ws', 'wd', 'wg'):
                s[k] = [[(p[k].get(m) or [None] * len(tm))[i] if i < len(p[k].get(m) or []) else None for i in range(len(tm))] for p in per]
            series.append(s)
        return {'id': mid, 'members': [summarize(tm, s) for s in series]}
    raise RuntimeError(f'{key} ensemble: {last}')


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def vdir(ws, wd):
    u = v = 0.0
    n = 0
    for s, d in zip(ws, wd):
        if s is None or d is None:
            continue
        u += -s * math.sin(math.radians(d))
        v += -s * math.cos(math.radians(d))
        n += 1
    if not n:
        return None
    return int(round((math.degrees(math.atan2(-u, -v)) + 360) % 360))


def summarize(tm, s):
    """Statewide (mean of points) opening-weekend summary for one member. Mirrors wxSummarize() in the page."""
    npt = len(s['t'])
    st = {k: [mean([s[k][p][i] for p in range(npt)]) for i in range(len(tm))] for k in ('t', 'ws', 'wg')}
    wdv = [vdir([s['ws'][p][i] for p in range(npt)], [s['wd'][p][i] for p in range(npt)]) for i in range(len(tm))]
    out = {}
    for di, tag in ((0, 's'), (1, 'u')):
        t0 = SAT_UTC + di * 86400
        win = [i for i, t in enumerate(tm) if t0 <= t <= t0 + 12 * 3600 and st['t'][i] is not None]
        if len(win) < 3:
            out[tag] = None
            continue
        am = [i for i in win if tm[i] <= t0 + 4 * 3600]
        pm = [i for i in win if tm[i] >= t0 + 8 * 3600]
        def part(ix):
            if not ix:
                return None
            return [r1(mean([st['ws'][i] for i in ix])), vdir([st['ws'][i] for i in ix], [wdv[i] for i in ix])]
        out[tag] = {'hi': r1(max(st['t'][i] for i in win)), 'ws': r1(mean([st['ws'][i] for i in win])),
                    'wg': r1(max([st['wg'][i] for i in win if st['wg'][i] is not None] or [0])),
                    'wd': vdir([st['ws'][i] for i in win], [wdv[i] for i in win]), 'am': part(am), 'pm': part(pm)}
    # front: a 24-hour fall of 8F or more in the statewide temperature ending Sat 6 AM - Sun 6 PM
    front = False
    tix = {t: i for i, t in enumerate(tm)}
    for i, t in enumerate(tm):
        if SAT_UTC <= t <= SAT_UTC + 36 * 3600 and (t - 86400) in tix:
            a, b = st['t'][tix[t - 86400]], st['t'][i]
            if a is not None and b is not None and a - b >= 8:
                front = True
                break
    out['f'] = front
    return out


def probs(mem):
    def share(fn):
        ok = [m for m in mem if fn(m) is not None]
        return round(sum(1 for m in ok if fn(m)) / len(ok), 3) if ok else None
    return {
        's': share(lambda m: None if not m.get('s') else m['s']['hi'] >= 65),
        'u': share(lambda m: None if not m.get('u') else m['u']['hi'] >= 65),
        'f': share(lambda m: None if not (m.get('s') or m.get('u')) else bool(m.get('f'))),
        'w': share(lambda m: None if not (m.get('s') or m.get('u')) else any(d and (d['ws'] >= 15 or d['wg'] >= 30) for d in (m.get('s'), m.get('u')))),
        'n': len(mem),
    }


# ---------------------------------------------------------------- CPC outlooks
CPC_GIS = 'https://ftp.cpc.ncep.noaa.gov/GIS/us_tempprcpfcst/'
CAT = {'above': 'A', 'below': 'B', 'normal': 'N', 'near': 'N', 'ec': 'EC', 'equal': 'EC'}


def pip(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def in_shape(x, y, rings):
    n = sum(1 for r in rings if pip(x, y, r))
    return n % 2 == 1


def cat_of(text):
    t = str(text).strip().lower()
    for k, v in CAT.items():
        if t.startswith(k):
            return v
    return None


def dates_in(text):
    out = []
    for m in re.finditer(r'(\d{1,2})/(\d{1,2})/(\d{4})', str(text)):
        out.append(dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2))))
    for m in re.finditer(r'(\d{4})-(\d{2})-(\d{2})', str(text)):
        out.append(dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in re.finditer(r'\b(20\d{2})(\d{2})(\d{2})\b', str(text)):
        try:
            out.append(dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    return out


MONTHS = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}


def month_span(text):
    m = re.search(r'\b([A-Za-z]{3})[A-Za-z]*\.?\s+(\d{4})\b', str(text))
    if not m or m.group(1).lower() not in MONTHS:
        return None
    y, mo = int(m.group(2)), MONTHS[m.group(1).lower()]
    end = (dt.date(y + (mo == 12), mo % 12 + 1, 1) - dt.timedelta(days=1))
    return dt.date(y, mo, 1), end


def read_zip(blob):
    import shapefile  # pyshp
    z = zipfile.ZipFile(io.BytesIO(blob))
    names = z.namelist()
    base = next(n[:-4] for n in names if n.lower().endswith('.shp'))
    rd = shapefile.Reader(shp=io.BytesIO(z.read(base + '.shp')), dbf=io.BytesIO(z.read(base + '.dbf')), shx=io.BytesIO(z.read(base + '.shx')) if base + '.shx' in names else None)
    fields = [f[0] for f in rd.fields[1:]]
    feats = []
    for sr in rd.iterShapeRecords():
        rec = dict(zip(fields, sr.record))
        pts = sr.shape.points
        parts = list(sr.shape.parts) + [len(pts)]
        rings = [pts[parts[i]:parts[i + 1]] for i in range(len(parts) - 1)]
        feats.append((rec, rings))
    return feats


def classify(feats):
    """Category and probability at each grid point, plus product dates from the attributes."""
    attrs = feats[0][0] if feats else {}
    res = []
    for lon, lat, div in PTS:
        hit = None
        for rec, rings in feats:
            if in_shape(lon, lat, rings):
                c = None
                p = None
                for k, v in rec.items():
                    kl = k.lower()
                    if c is None and 'cat' in kl:
                        c = cat_of(v)
                    if p is None and 'prob' in kl:
                        try:
                            p = float(v)
                        except (TypeError, ValueError):
                            pass
                if c is None:
                    c = next((cat_of(v) for v in rec.values() if cat_of(v)), None)
                if c:
                    if p is not None and p > 1:
                        p = p / 100
                    if hit is None or (p or 0) > (hit[1] or 0):
                        hit = (c, p)
        res.append((div, hit or ('EC', 1 / 3)))
    return res, attrs


def summarize_cpc(res):
    divs = {}
    for div, (c, p) in res:
        divs.setdefault(div, []).append((c, p))
    def vote(lst):
        counts = {}
        for c, p in lst:
            counts.setdefault(c, []).append(p if p is not None else (1 / 3 if c == 'EC' else 0.4))
        c = max(counts, key=lambda k: (len(counts[k]), k != 'EC'))
        return [c, round(sum(counts[c]) / len(counts[c]), 3)]
    return vote([x for v in divs.values() for x in v]), {d: vote(v) for d, v in sorted(divs.items())}


def listing(url):
    try:
        html = get(url, 60)
    except Exception as e:  # noqa: BLE001
        errors.append(f'CPC listing {url}: {e}')
        return []
    return sorted(set(re.findall(r'href="([^"?/][^"]*?)"', html)))


def cpc_product(pid, files_dir, temp_pat, prcp_pat):
    names = listing(files_dir)
    out = {'id': pid}
    for var, pat in (('temp', temp_pat), ('prcp', prcp_pat)):
        cand = [n for n in names if re.search(pat, n, re.I) and n.lower().endswith('.zip')]
        cand.sort(key=lambda n: ('latest' not in n.lower(), n))
        if not cand:
            errors.append(f'CPC {pid} {var}: no file matching {pat} in {files_dir}')
            return None
        url = files_dir + cand[0] if not cand[0].startswith('http') else cand[0]
        try:
            feats = read_zip(get(url, 120, binary=True))
        except Exception as e:  # noqa: BLE001
            errors.append(f'CPC {pid} {var}: {e}')
            return None
        res, attrs = classify(feats)
        state_v, divs = summarize_cpc(res)
        out[var] = {'cat': state_v[0], 'p': state_v[1], 'assumed': False}
        if var == 'temp':
            out['div'] = divs
            txt = ' '.join(f'{k}={v}' for k, v in attrs.items())
            ds = dates_in(txt)
            span = month_span(' '.join(str(v) for k, v in attrs.items() if 'valid' in k.lower() or 'seas' in k.lower()))
            issued = next((d for k, v in attrs.items() if 'fcst' in k.lower() or 'issu' in k.lower() for d in dates_in(v)), None)
            if span:
                out['start'], out['end'] = span[0].isoformat(), span[1].isoformat()
            elif len(ds) >= 2:
                vs = sorted(d for d in ds if d != issued)
                if len(vs) >= 2:
                    out['start'], out['end'] = vs[0].isoformat(), vs[-1].isoformat()
            if issued:
                out['issued'] = issued.isoformat()
            elif ds:
                out['issued'] = min(ds).isoformat()
            out['src'] = url
    return out


def cpc_all():
    defs = [
        ('mon', CPC_GIS + 'monthlyupdate/', r'monthupd_temp', r'monthupd_prcp', 'November monthly outlook', 'Monthly', 'Updated the last day of the month'),
        ('wk34', CPC_GIS, r'wk34temp', r'wk34prcp', 'Week 3-4 outlook', 'Week 3-4', 'Issued Fridays'),
        ('d814', CPC_GIS, r'814temp', r'814prcp', '8-14 day outlook', '8-14 day', 'Issued daily'),
        ('d610', CPC_GIS, r'610temp', r'610prcp', '6-10 day outlook', '6-10 day', 'Issued daily'),
    ]
    prods = []
    for pid, d, tp, pp, name, short, nxt in defs:
        try:
            p = cpc_product(pid, d, tp, pp)
        except Exception as e:  # noqa: BLE001
            errors.append(f'CPC {pid}: {e}')
            p = None
        if p and p.get('start') and p.get('end'):
            if pid == 'mon':
                name = dt.date.fromisoformat(p['start']).strftime('%B') + ' monthly outlook'
            p.update({'name': name, 'short': short, 'next': nxt})
            prods.append(p)
            status['cpc_' + pid] = f"{p.get('issued')} {p['start']}..{p['end']} temp {p['temp']['cat']} {p['temp']['p']}"
    return prods


# ---------------------------------------------------------------- run
def load_prev(name):
    try:
        return json.load(open(os.path.join(PREV, name + '.json'))) if PREV else None
    except Exception:  # noqa: BLE001
        return None


def main():
    now = dt.datetime.now(dt.timezone.utc)
    docs = {}
    meta = {'at': now.isoformat(timespec='seconds'), 'pts': [[p[0], p[1]] for p in PTS], 'models': {}, 'ens': {}, 'step': 3, 'v': 1}
    # SMART=1: check each source's run metadata first and download only what changed since the live feed
    smart = os.environ.get('RUT_SMART') == '1'
    pmeta, pens, pcpc = load_prev('meta') or {}, load_prev('ens') or {}, load_prev('cpc')
    psig = pmeta.get('sig') or {}
    sig, todo = {}, {'gfs', 'ecmwf', 'ens_gfs', 'ens_ecmwf', 'cpc'}
    if smart and pmeta:
        for src in SOURCES:
            sig[src] = signature(src)
            have = (src.startswith('ens') and (pens.get('models') or {}).get(src[4:])) or (not src.startswith('ens') and (pmeta.get('models') or {}).get(src))
            if have and sig[src] == psig.get(src) and any(s[0] for s in sig[src]):
                todo.discard(src)
        try:
            age = now - dt.datetime.fromisoformat(pcpc['at'])
            if pcpc and pcpc.get('products') and age < dt.timedelta(hours=3):
                todo.discard('cpc')
        except Exception:  # noqa: BLE001
            pass
        if not todo:
            print(json.dumps({'changed': False, 'note': 'no new model runs or outlooks since ' + str(pmeta.get('at'))}))
            open(os.path.join(OUT, 'unchanged'), 'w').write(str(pmeta.get('at')))
            return
        # carry over what is not being refreshed
        for k in ('gfs', 'ecmwf'):
            if k not in todo and (pmeta.get('models') or {}).get(k):
                meta['models'][k] = pmeta['models'][k]
                status[k] = (pmeta.get('status') or {}).get(k, '') + ' (unchanged)'
        for k in ('gfs', 'ecmwf'):
            if 'ens_' + k not in todo and (pmeta.get('ens') or {}).get(k):
                meta['ens'][k] = pmeta['ens'][k]
                status['ens_' + k] = (pmeta.get('status') or {}).get('ens_' + k, '') + ' (unchanged)'
        if 'cpc' not in todo:
            for k, v in (pmeta.get('status') or {}).items():
                if k.startswith('cpc_'):
                    status[k] = v
        print('refreshing:', sorted(todo))
    else:
        sig = {src: signature(src) for src in SOURCES}
    meta['sig'] = {k: v for k, v in sig.items()}
    failed = set()
    for key in ('gfs', 'ecmwf'):
        if key not in todo:
            continue
        try:
            m = deterministic(key)
            meta['models'][key] = {'id': m['id'], 'time': m['time'], 'init': m['init']}
            for k in KEY.values():
                docs[f'g_{key}_{k}'] = {'v': [p[k] for p in m['pts']]}
            status[key] = f"{m['id']} init {m['init']} {len(m['time'])} steps"
        except Exception as e:  # noqa: BLE001
            failed.add(key)
            errors.append(str(e))
    # a refresh that failed keeps the previous run instead of dropping that model from the page
    for key in ('gfs', 'ecmwf'):
        if key not in meta['models'] and (pmeta.get('models') or {}).get(key):
            meta['models'][key] = pmeta['models'][key]
            status[key] = 'kept previous run (refresh failed)'
    ens = {'at': meta['at'], 'models': {}}
    for key in ('gfs', 'ecmwf'):
        if 'ens_' + key not in todo:
            if (pens.get('models') or {}).get(key):
                ens['models'][key] = pens['models'][key]
            continue
        try:
            e = ensemble(key)
            ens['models'][key] = {'id': e['id'], 'members': e['members'], 'p': probs(e['members'])}
            meta['ens'][key] = {'id': e['id'], 'n': len(e['members'])}
            status['ens_' + key] = f"{e['id']} {len(e['members'])} members p={ens['models'][key]['p']}"
        except Exception as e:  # noqa: BLE001
            failed.add('ens_' + key)
            errors.append(str(e))
    for key in ('gfs', 'ecmwf'):
        if key not in ens['models'] and (pens.get('models') or {}).get(key):
            ens['models'][key] = pens['models'][key]
            meta['ens'][key] = (pmeta.get('ens') or {}).get(key) or {'id': pens['models'][key].get('id'), 'n': len(pens['models'][key].get('members', []))}
            status['ens_' + key] = 'kept previous run (refresh failed)'
    if ens['models']:
        docs['ens'] = ens
    prods = cpc_all() if 'cpc' in todo else None
    if prods:
        docs['cpc'] = {'checked': now.date().isoformat(), 'at': meta['at'], 'products': prods}
    # history of warm-weekend chances by run
    hist = {'items': []}
    if PREV and os.path.exists(os.path.join(PREV, 'hist.json')):
        try:
            hist = json.load(open(os.path.join(PREV, 'hist.json')))
            hist = {'items': list(hist.get('items', []))}
        except Exception:  # noqa: BLE001
            hist = {'items': []}
    if ens['models'] or meta['models']:
        item = {'at': meta['at'], 'init': {k: v.get('init') for k, v in meta['models'].items()},
                'lead': round((SAT_UTC - now.timestamp()) / 86400, 2), 'm': {k: v['p'] for k, v in ens['models'].items()}}
        key = json.dumps(item['init'], sort_keys=True) + json.dumps({k: (v['id'], len(v['members'])) for k, v in ens['models'].items()}, sort_keys=True)
        if not hist['items'] or hist['items'][-1].get('_k') != key:
            item['_k'] = key
            hist['items'].append(item)
        hist['items'] = hist['items'][-90:]
        docs['hist'] = hist
    for src in failed:  # not marked as current, so the next check tries again
        meta['sig'].pop(src, None)
    meta['status'] = status
    meta['errors'] = errors
    docs['meta'] = meta
    files = []
    for doc_id, body in docs.items():
        path = os.path.join(OUT, f'{doc_id}.json')
        with open(path, 'w') as f:
            json.dump(body, f, separators=(',', ':'))
        files.append((doc_id, path, os.path.getsize(path)))
    # meta last so the page only switches once everything else is written
    files.sort(key=lambda r: (r[0] == 'meta', r[0]))
    batches, cur, size = [], [], 0
    for doc_id, path, sz in files:
        if cur and (size + sz > 420_000 or len(cur) >= 40 or doc_id == 'meta'):
            batches.append(cur)
            cur, size = [], 0
        cur.append({'collection': 'feed', 'doc_id': doc_id, 'file': path, 'bytes': sz})
        size += sz
    if cur:
        batches.append(cur)
    manifest = {'generated': meta['at'], 'errors': errors, 'status': status, 'batches': batches}
    json.dump(manifest, open(os.path.join(OUT, 'manifest.json'), 'w'), indent=1)
    print(json.dumps({'docs': len(files), 'batches': [[d['doc_id'] for d in b] for b in batches], 'errors': errors, 'status': status}, indent=1))


if __name__ == '__main__':
    main()
