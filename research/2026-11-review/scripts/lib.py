import json, re, gzip, os, glob, math, datetime as dt
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TZ = {"nyc": "America/New_York", "london": "Europe/London", "paris": "Europe/Paris"}
ST = {"nyc": "LGA", "london": "EGLC", "paris": "LFPB"}
ROUTINE_MIN = {"LGA": {51}, "EGLC": {20, 50}, "LFPB": {0, 30}}

def parse_bucket(title):
    t = title.replace("°F", "").replace("°C", "").strip()
    m = re.fullmatch(r"(-?\d+) or (below|lower)", t)
    if m: return (-999, int(m.group(1)))
    m = re.fullmatch(r"(-?\d+) or (higher|above)", t)
    if m: return (int(m.group(1)), 999)
    m = re.fullmatch(r"(-?\d+)-(-?\d+)", t)
    if m: return (int(m.group(1)), int(m.group(2)))
    m = re.fullmatch(r"(-?\d+)", t)
    if m: return (int(m.group(1)), int(m.group(1)))
    raise ValueError(title)

def load_events(path="data/events_3city.json"):
    evs = json.load(open(os.path.join(BASE, path)))
    rows = []
    for slug, e in evs.items():
        if not e: continue
        for m in e["markets"]:
            if not m["tokens"] or not m["outcomePrices"]: continue
            op = json.loads(m["outcomePrices"])
            lo, hi = parse_bucket(m["title"])
            rows.append(dict(slug=slug, event_id=e["id"], city=e["city"], kind=e["kind"], date=e["date"], title=m["title"],
                             yes=m["tokens"][0], no=m["tokens"][1], won=(op[0] == "1"), resolved=(op[0] in ("0", "1") and op[1] in ("0", "1")),
                             lo=lo, hi=hi, unit="F" if "°F" in m["title"] else "C", mvol=float(m["volume"] or 0),
                             start=e["startDate"], end=e["endDate"], closed=e["closedTime"], evol=float(e["volume"] or 0),
                             rsrc=("metar" if "aviationweather" in (e.get("resolutionSource") or "") else "nws_ts" if "weather.gov" in (e.get("resolutionSource") or "") else "wu")))
    df = pd.DataFrame(rows)
    df["closed_ts"] = pd.to_datetime(df["closed"].str.replace(" ", "T").str.replace(r"\+00$", "+00:00", regex=True), utc=True, format="ISO8601")
    df["start_ts"] = pd.to_datetime(df["start"], utc=True, format="ISO8601")
    return df

def day_bounds_utc(city, date):
    tz = ZoneInfo(TZ[city]); d = dt.date.fromisoformat(date) if isinstance(date, str) else date
    a = dt.datetime(d.year, d.month, d.day, tzinfo=tz); b = a + dt.timedelta(days=1)
    # handle DST by constructing next local midnight explicitly
    nd = d + dt.timedelta(days=1); b = dt.datetime(nd.year, nd.month, nd.day, tzinfo=tz)
    return a.astimezone(dt.timezone.utc), b.astimezone(dt.timezone.utc)

_T = re.compile(r"\bT([01])(\d{3})([01])(\d{3})\b")
_B = re.compile(r"\s(M?\d{2})/(M?\d{2})?(\s|$)")
def metar_temp_tenths(raw):
    """Return temperature in tenths of °C (int) per the Polymarket rule (T-group else body), or None."""
    t = _T.search(raw)
    if t: return (-1 if t.group(1) == "1" else 1) * int(t.group(2))
    b = _B.search(raw)
    if not b: return None
    s = b.group(1)
    return (-int(s[1:]) if s.startswith("M") else int(s)) * 10

def settle_units(tenths, unit):
    """Settlement integer from tenths °C: whole °C (as reported) or °F rounded half up."""
    if unit == "C":
        return int(math.floor(tenths / 10 + 0.5))
    f10 = tenths * 18 + 3200          # tenths*1.8*100 -> hundredths of °F
    return int(math.floor(f10 / 100 + 0.5))

def load_metar(station):
    fr = []
    for p in sorted(glob.glob(os.path.join(BASE, f"data/iem_{station}_*.csv"))):
        d = pd.read_csv(p, dtype=str)
        fr.append(d)
    d = pd.concat(fr, ignore_index=True)
    d = d[d["metar"].notna() & (d["metar"] != "null")]
    d["ts"] = pd.to_datetime(d["valid"], utc=True)
    d["tenths"] = d["metar"].map(metar_temp_tenths)
    d = d[d["tenths"].notna()].copy()
    d["tenths"] = d["tenths"].astype(int)
    d["routine"] = d["ts"].dt.minute.isin(ROUTINE_MIN[station])
    d = d.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    return d[["ts", "tenths", "routine", "metar"]]

def load_trades(slug, sfx=""):
    p = os.path.join(BASE, f"data/trades/{slug}{sfx}.json.gz")
    if not os.path.exists(p): return None
    rows = json.load(gzip.open(p, "rt"))
    if not rows: return pd.DataFrame(columns=["ts", "token", "side", "price", "size", "wallet", "tx"])
    t = pd.DataFrame(rows, columns=["ts", "token", "side", "price", "size", "wallet", "tx"])
    t["ts"] = pd.to_datetime(t["ts"], unit="s", utc=True)
    return t.sort_values("ts").reset_index(drop=True)
