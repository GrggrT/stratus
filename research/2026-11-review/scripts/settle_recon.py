"""Reconstruct settlement from the exact resolution-source URL and compare to Polymarket outcome."""
import json, re, requests, time, datetime as dt, math, sys
from zoneinfo import ZoneInfo
from decimal import Decimal, ROUND_HALF_UP
S = requests.Session(); S.headers["User-Agent"] = "research-script/0.1"
evs = json.load(open("data/events_3city.json"))
TZ = {"nyc": "America/New_York", "london": "Europe/London", "paris": "Europe/Paris"}
def parse_line(line):
    line = line.strip()
    if not line: return None
    special = line.startswith("SPECI")
    m = re.search(r"\b([A-Z0-9]{4})\s+(\d{2})(\d{2})(\d{2})Z\b", line)
    if not m: return None
    day, hh, mm = int(m.group(2)), int(m.group(3)), int(m.group(4))
    t = re.search(r"\bT([01])(\d{3})([01])(\d{3})\b", line)
    if t:
        val = Decimal(t.group(2)) / 10 * (-1 if t.group(1) == "1" else 1); src = "T"
    else:
        b = re.search(r"\s(M?\d{2})/(M?\d{2})?\s", line + " ")
        if not b: return dict(special=special, day=day, hh=hh, mm=mm, temp=None, src=None, raw=line)
        s = b.group(1); val = Decimal(-int(s[1:]) if s.startswith("M") else int(s)); src = "body"
    return dict(special=special, day=day, hh=hh, mm=mm, temp=val, src=src, raw=line)
def to_f(c):
    return int((c * Decimal(9) / Decimal(5) + 32).quantize(Decimal(1), rounding=ROUND_HALF_UP)) if (c * Decimal(9) / Decimal(5) + 32) >= 0 else int(math.floor(float(c * Decimal(9) / Decimal(5) + 32) + 0.5))
def bucket_match(title, value, unit):
    t = title.replace("°F","").replace("°C","").strip()
    m = re.fullmatch(r"(-?\d+) or (below|lower)", t)
    if m: return value <= int(m.group(1))
    m = re.fullmatch(r"(-?\d+) or (higher|above)", t)
    if m: return value >= int(m.group(1))
    m = re.fullmatch(r"(-?\d+)-(-?\d+)", t)
    if m: return int(m.group(1)) <= value <= int(m.group(2))
    m = re.fullmatch(r"(-?\d+)", t)
    if m: return value == int(m.group(1))
    raise ValueError(title)
start = sys.argv[1]
res = []
for slug, e in sorted(evs.items(), key=lambda kv: kv[1]["date"]):
    if e["date"] < start or "aviationweather" not in (e.get("resolutionSource") or ""): continue
    url = e["resolutionSource"]
    r = S.get(url, timeout=60)
    if r.status_code != 200: print("fail", slug, r.status_code); continue
    obs = [parse_line(l) for l in r.text.splitlines()]
    obs = [o for o in obs if o]
    d = dt.date.fromisoformat(e["date"]); tz = ZoneInfo(TZ[e["city"]])
    q = re.search(r"date=(\d{4})-(\d{2})-(\d{2})T", url); end = dt.datetime(int(q.group(1)), int(q.group(2)), int(q.group(3)), tzinfo=dt.timezone.utc)
    sel_r, sel_all = [], []
    for o in obs:
        # resolve full timestamp: day-of-month near the url end date
        cand = None
        for back in range(0, 4):
            dd = end.date() - dt.timedelta(days=back)
            if dd.day == o["day"]:
                cand = dt.datetime(dd.year, dd.month, dd.day, o["hh"], o["mm"], tzinfo=dt.timezone.utc); break
        if cand is None or o["temp"] is None: continue
        if cand.astimezone(tz).date() != d: continue
        sel_all.append(o)
        if not o["special"]: sel_r.append(o)
    if not sel_r: print("no obs", slug); continue
    f = max if e["kind"] == "highest" else min
    unit = "F" if "°F" in e["markets"][0]["title"] else "C"
    def val(sel):
        c = f(o["temp"] for o in sel)
        return (to_f(c), c) if unit == "F" else (int(c.quantize(Decimal(1), rounding=ROUND_HALF_UP)), c)
    v_r, c_r = val(sel_r); v_all, c_all = val(sel_all)
    winner = [m["title"] for m in e["markets"] if m["outcomePrices"] and json.loads(m["outcomePrices"])[0] == "1"]
    mine = [m["title"] for m in e["markets"] if bucket_match(m["title"], v_r, unit)]
    mine_all = [m["title"] for m in e["markets"] if bucket_match(m["title"], v_all, unit)]
    res.append(dict(slug=slug, n_routine=len(sel_r), n_all=len(sel_all), v=v_r, c=str(c_r), winner=winner, mine=mine, ok=(winner == mine), speci_changes=(mine_all != mine), tsrc=sorted({o["src"] for o in sel_r})))
    time.sleep(0.3)
ok = sum(r["ok"] for r in res)
print("events:", len(res), "rule reconstruction matches Polymarket:", ok, "| mismatches:", len(res) - ok)
print("events where including SPECI would change the winning bucket:", sum(r["speci_changes"] for r in res))
for r in res:
    if not r["ok"] or r["speci_changes"]: print("  ", r)
import collections
print("obs counts per day (routine/all):", collections.Counter((r["slug"].split("-in-")[1].split("-on-")[0], r["n_routine"], r["n_all"]) for r in res).most_common(12))
print("temp source by city:", collections.Counter((r["slug"].split("-in-")[1].split("-on-")[0], tuple(r["tsrc"])) for r in res))
json.dump(res, open("data/settle_recon.json", "w"))
