"""Roadkill (deer-vehicle collision) index for Missouri.

Daily expected collisions = k * deer movement(d) * exposure(d) * weekday(d)
  deer movement(d) = 1 + A * rut(d)            rut = MO activity curve + 1/4 second rut (peak 1)
  exposure(d)      = sum over hours of traffic(h) * deer-on-road(h) * visibility(h)
     deer-on-road: night level + day level + crepuscular peaks at sunrise and sunset
     visibility: 1 in the dark, r in daylight, linear across civil twilight
Hour-of-day shape is fit to MSHP hour-of-day tables (2008-2011); A is fit to MSHP month tables (2004-2011).
"""
import json, math, datetime as dt
import numpy as np, ephem
from scipy.optimize import minimize

W = '/home/claude/work/'
REG = json.load(open(W + 'regional.json'))
B = REG['bio26']
RUT = np.array(B['act']) + 0.25 * np.array(B['act2']); RUT = RUT / RUT.max()   # Sep 1..Dec 31 (122 days)

# ---- MSHP data -------------------------------------------------------------
MONTHS = {  # Jan..Dec, MSHP annual deer reports
 2004: [237,167,189,190,259,255,186,139,251,615,962,377],
 2005: [289,220,219,192,260,239,153,129,145,608,781,271],
 2006: [264,185,202,170,309,243,178,134,204,508,822,311],
 2007: [256,183,220,163,262,238,173,153,212,503,779,277],
 2008: [222,168,206,203,289,262,182,158,167,637,849,260],
 2009: [230,208,195,210,324,231,142,134,198,533,973,298],
 2010: [214,156,187,153,221,284,150,117,195,671,807,265],
 2011: [193,159,197,159,284,276,141,115,216,560,890,373],
}
HOURS = {  # 0..23 (hour beginning), MSHP hour-of-day tables
 2008: [122,158,79,86,100,210,293,151,65,61,44,45,45,37,44,36,65,219,307,359,349,317,199,205],
 2009: [140,146,126,106,88,202,272,137,64,41,37,39,36,39,28,31,55,267,367,293,385,332,245,198],
 2010: [178,125,103,82,106,200,284,176,84,50,36,39,41,28,32,28,38,207,272,322,307,297,196,183],
 2011: [160,121,115,105,111,209,261,162,61,51,33,30,40,28,38,34,49,257,322,312,343,300,218,197],
}
DOW = [465,518,457,504,534,547,537]  # Sun..Sat, 2011
ANNUAL = {2004:3827,2005:3506,2006:3530,2007:3419,2008:3603,2009:3676,2010:3420,2011:3563,
          2014:3720,2015:3732,2016:4604,2017:4070,2018:4090,2019:4320,2020:3639,2021:3779,2022:3699,2023:3591,2024:2951}

# ---- traffic: typical rural weekday share of daily volume by hour (FHWA-style profile; assumption)
TRAF = np.array([0.9,0.6,0.5,0.5,0.8,1.7,3.6,5.6,5.4,5.1,5.3,5.6,5.8,5.9,6.3,7.0,7.6,7.6,6.2,4.8,3.8,3.1,2.4,1.6]); TRAF = TRAF / TRAF.sum()
WK = np.array(DOW, float); WK = WK / WK.mean()

# ---- sun ------------------------------------------------------------------
OBS = ephem.Observer(); OBS.lat, OBS.lon = '38.58', '-92.17'; OBS.pressure = 0
def dst_bounds(y):
    if y >= 2007:
        mar = [d for d in range(8, 15) if dt.date(y, 3, d).weekday() == 6][0]; nov = [d for d in range(1, 8) if dt.date(y, 11, d).weekday() == 6][0]
        return dt.date(y, 3, mar), dt.date(y, 11, nov)
    apr = [d for d in range(1, 8) if dt.date(y, 4, d).weekday() == 6][0]; octo = [d for d in range(25, 32) if dt.date(y, 10, d).weekday() == 6][0]
    return dt.date(y, 4, apr), dt.date(y, 10, octo)
_sun = {}
def sun_times(d):
    """local clock hours (CST or CDT) of sunrise, sunset, civil dawn, civil dusk"""
    if d in _sun: return _sun[d]
    a, b = dst_bounds(d.year); off = -5 if a <= d < b else -6
    OBS.date = ephem.Date(dt.datetime(d.year, d.month, d.day, 12) - dt.timedelta(hours=off))  # local noon in UTC
    s = ephem.Sun(); res = []
    for horizon, use_c in (('-0:50', False), ('-6', True)):
        OBS.horizon = horizon
        r = OBS.previous_rising(s, use_center=use_c).datetime(); st = OBS.next_setting(s, use_center=use_c).datetime()
        res += [((r.hour + r.minute / 60 + off) % 24), ((st.hour + st.minute / 60 + off) % 24)]
    out = (res[0], res[1], res[2], res[3], off); _sun[d] = out; return out

H = np.arange(24) + 0.5
def light_of(d, shift=0.0):
    sr, ss, cd, cu, _ = sun_times(d); sr, ss, cd, cu = sr + shift, ss + shift, cd + shift, cu + shift
    return np.clip(np.minimum((H - cd) / max(sr - cd, 0.1), (cu - H) / max(cu - ss, 0.1)), 0, 1)  # 0 dark .. 1 full daylight
def hourly(d, p, shift=0.0):
    """relative collision risk by hour: traffic x (1 in the dark, rho in daylight, linear through civil twilight)"""
    rho = p[0]
    return TRAF * (1 - (1 - rho) * light_of(d, shift))

def year_days(y):
    d0 = dt.date(y, 1, 1); return [d0 + dt.timedelta(i) for i in range((dt.date(y + 1, 1, 1) - d0).days)]

# ---- fit hour-of-day shape (annual tables, days weighted by that month's crash rate) ----
hshare = {y: np.array(v, float) / sum(v) for y, v in HOURS.items()}
def hour_loss(p):
    if p[0] <= 0 or p[0] > 1: return 1e9
    err = 0
    for y in HOURS:
        mw = np.array(MONTHS[y], float); acc = np.zeros(24)
        for d in year_days(y):
            n = (dt.date(y + (d.month == 12), d.month % 12 + 1, 1) - dt.date(y, d.month, 1)).days
            acc += hourly(d, p) * mw[d.month - 1] / n
        acc /= acc.sum(); err += ((acc - hshare[y]) ** 2).sum()
    return err
p0 = [0.1]
fit_h = minimize(hour_loss, p0, method='Nelder-Mead', options={'maxiter': 600, 'xatol': 1e-3, 'fatol': 1e-9})
PH = fit_h.x; print('hour fit', np.round(PH, 3), 'sse', fit_h.fun)

# ---- daily exposure and month fit for A (Sep-Dec, 2004-2011) ----
SEASON = lambda y: [dt.date(y, 9, 1) + dt.timedelta(i) for i in range(122)]
def exposure(d): return hourly(d, PH).sum()
EXP = {y: np.array([exposure(d) for d in SEASON(y)]) for y in list(MONTHS) + [2026]}
OPENER = {2004:13,2005:12,2006:11,2007:10,2008:15,2009:14,2010:13,2011:12,2026:14}
POP = 1.4e6   # MDC statewide estimate, roughly
def herd(y):
    """share of the pre-season herd still alive each day, from a simplified harvest calendar"""
    ds = SEASON(y); o = dt.date(y, 11, OPENER[y]); h = np.zeros(len(ds))
    for i, d in enumerate(ds):
        x = 0.0
        if d >= dt.date(y, 9, 15): x += 520                       # archery
        if (o - dt.timedelta(20)) <= d <= (o - dt.timedelta(19)): x += 6000  # early youth weekend
        if d == o or d == o + dt.timedelta(1): x += 42000          # opening weekend
        elif o + dt.timedelta(2) <= d <= o + dt.timedelta(10): x += 10800  # rest of November portion
        if o + dt.timedelta(13) <= d <= o + dt.timedelta(15): x += 1000   # late youth
        if o + dt.timedelta(21) <= d <= o + dt.timedelta(29): x += 1900   # late antlerless
        h[i] = x
    return 1 - np.cumsum(h) / POP
HERD = {y: herd(y) for y in list(MONTHS) + [2026]}
IDX = np.arange(122)
def move(q, shift=0):
    A, mu, s1, s2 = q; mu = mu + shift
    g = np.where(IDX < mu, np.exp(-0.5 * ((IDX - mu) / s1) ** 2), np.exp(-0.5 * ((IDX - mu) / s2) ** 2))
    return 1 + A * g
def daily(y, q, shift=0):
    ds = SEASON(y); wk = np.array([WK[(d.weekday() + 1) % 7] for d in ds])
    return move(q, shift) * HERD[y] * EXP[y] * wk
def month_sum(y, v):
    ds = SEASON(y); return np.array([v[[i for i, d in enumerate(ds) if d.month == m]].sum() for m in (9, 10, 11, 12)])
obs_sh = {y: np.array(MONTHS[y][8:], float) / sum(MONTHS[y][8:]) for y in MONTHS}
def month_loss(q):
    if q[0] < 0 or min(q[2:]) < 3 or not (20 < q[1] < 110): return 1e9
    A = q
    return sum(((month_sum(y, daily(y, A)) / month_sum(y, daily(y, A)).sum() - obs_sh[y]) ** 2).sum() for y in MONTHS)
MU = 70  # peak fixed at the rut model's visible peak (Nov 10); monthly counts can't place it within a month (fit is flat from Oct 26 to Nov 12)
PK26 = (dt.date.fromisoformat(REG['regions'][0]['fc']['peak_q']['50']) - dt.date(2026, 11, 10)).days
f3 = lambda q: month_loss([q[0], MU, q[1], q[2]])
fit_m = min((minimize(f3, x0, method='Nelder-Mead', options={'maxiter': 4000}) for x0 in ([3, 15, 12], [5, 25, 15], [2, 10, 25])), key=lambda r: r.fun)
A = np.array([fit_m.x[0], MU, fit_m.x[1], fit_m.x[2]])
# no-movement comparison
loss0 = sum(((month_sum(y, HERD[y]*EXP[y]) / month_sum(y, HERD[y]*EXP[y]).sum() - obs_sh[y]) ** 2).sum() for y in MONTHS)
print('peak date', dt.date(2026,9,1)+dt.timedelta(float(A[1])))
print('A', np.round(A, 3), 'sse', fit_m.fun, 'sse without rut', loss0)
pred = {y: (month_sum(y, daily(y, A)) / month_sum(y, daily(y, A)).sum()) for y in MONTHS}
resid = np.array([pred[y] - obs_sh[y] for y in MONTHS])
print('mean obs share', np.round(np.mean([obs_sh[y] for y in MONTHS], 0), 4), 'mean pred', np.round(np.mean([pred[y] for y in MONTHS], 0), 4), 'rmse pp', np.sqrt((resid ** 2).mean()) * 100)
# share of the year that falls Sep-Dec
sd_share = np.mean([sum(MONTHS[y][8:]) / sum(MONTHS[y]) for y in MONTHS]); print('Sep-Dec share of year', sd_share)
# hour fit check (Oct-Dec only for display)
def hour_dist(y, months):
    mw = np.array(MONTHS[y], float); acc = np.zeros(24)
    for d in year_days(y):
        if d.month not in months: continue
        n = (dt.date(y + (d.month == 12), d.month % 12 + 1, 1) - dt.date(y, d.month, 1)).days
        acc += hourly(d, PH) * mw[d.month - 1] / n
    return acc / acc.sum()
hfit = np.mean([hour_dist(y, range(1, 13)) for y in HOURS], 0); hobs = np.mean([hshare[y] for y in HOURS], 0)
print('hour r', np.corrcoef(hfit, hobs)[0, 1])

# ---- 2026 ----
recent = [ANNUAL[y] for y in (2021, 2022, 2023, 2024)]; ann26 = float(np.mean(recent))
band = (min(recent) / ann26, max(recent) / ann26)  # 2021-2024 range
print('band', band)
d26 = daily(2026, A, PK26); k = ann26 * sd_share / d26.sum(); exp26 = d26 * k
ds = SEASON(2026)
noDST = []  # counterfactual: daylight time kept through Dec (for the time-change effect)
for d in ds:
    sr, ss, cd, cu, off = sun_times(d)
    noDST.append(None)
# time-change effect: compare week after Nov 1 with exposure under CDT clock
def exposure_shift(d, shift): return hourly(d, PH, shift).sum()
i1 = ds.index(dt.date(2026, 11, 1))
cst = np.array([exposure_shift(ds[i], 0) for i in range(i1, i1 + 7)]); cdt = np.array([exposure_shift(ds[i], 1) for i in range(i1, i1 + 7)])
dst_eff = cst.sum() / cdt.sum() - 1
print('time-change exposure effect, week after Nov 1:', round(dst_eff * 100, 1), '%')
pk = int(np.argmax(exp26)); print('2026 annual basis', ann26, 'Sep-Dec expected', exp26.sum(), 'peak', ds[pk], exp26[pk])
for m in (9, 10, 11, 12): print(m, round(sum(exp26[i] for i, d in enumerate(ds) if d.month == m)))
# per-day components for the page
out = {
  'start': '2026-09-01', 'band': [round(band[0], 3), round(band[1], 3)], 'pk_shift': PK26, 'nodst': [round(float(v), 2) for v in (d26 / EXP[2026] * np.array([exposure_shift(d, 1 if d >= dt.date(2026, 11, 1) else 0) for d in ds]) * k)], 'exp': [round(float(v), 2) for v in exp26],
  'move': [round(float(v), 3) for v in move(A, PK26)], 'herd': [round(float(v), 4) for v in HERD[2026]], 'expo': [round(float(v / EXP[2026].mean()), 3) for v in EXP[2026]],
  'A': [round(float(v), 3) for v in A], 'hour_p': [round(float(v), 3) for v in PH], 'hour_r': round(float(np.corrcoef(hfit, hobs)[0, 1]), 3),
  'month_rmse_pp': round(float(np.sqrt((resid ** 2).mean()) * 100), 2), 'sse': float(fit_m.fun), 'sse0': float(loss0),
  'sd_share': round(float(sd_share), 4), 'annual_basis': round(ann26), 'dst_eff': round(float(dst_eff), 3), 'dst_date': '2026-11-01',
  'annual': ANNUAL, 'months': MONTHS, 'hours_obs': [round(float(v), 4) for v in hobs], 'hours_fit': [round(float(v), 4) for v in hfit],
  'hist_pred': {str(y): [round(float(v), 4) for v in pred[y]] for y in MONTHS},
  'hist_obs': {str(y): [round(float(v), 4) for v in obs_sh[y]] for y in MONTHS},
  'dow': DOW, 'traf': [round(float(v), 4) for v in TRAF], 'rho': round(float(PH[0]), 3),
  'sun': [[round(sun_times(d)[0], 2), round(sun_times(d)[1], 2)] for d in ds],
}
json.dump(out, open('/home/claude/standalone/roadkill.json', 'w'))
print('written')
