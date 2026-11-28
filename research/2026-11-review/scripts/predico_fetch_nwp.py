"""Archived NWP forecasts over Belgium for the Predico day-ahead backtest (Open-Meteo, research use).

Usage:
  python scripts/predico_fetch_nwp.py lagged 2025-01-01 2026-10-25
  python scripts/predico_fetch_nwp.py runs   2026-06-02 2026-10-25
  python scripts/predico_fetch_nwp.py runs03 2026-06-02 2026-10-25

`lagged` reads the Previous Runs API: for every valid hour the value forecast about 24 h
("previous_day1") and about 48 h ("previous_day2") earlier. It goes back to 2024 and is the
training set. Regional models (ICON-D2, AROME) only reach 48 h, so they have day1 only.

`runs` reads the Single Runs API: for each delivery day D the 00Z run of day D-1, the freshest
run that is certainly published before a 09Z gate closure. That archive only goes back about
six months, so it is the test set. One request per delivery day. `runs03` adds the 03Z run of
the two regional models, three hours fresher and still published before the gate.

Output: data/predico/nwp/lagged_<point>.csv and data/predico/nwp/runs_<date>.json (resumable).
The free Open-Meteo quota is shared per address and non-commercial; the script paces itself
and stops at the daily limit instead of hammering it.
"""
import json, os, sys, time
import pandas as pd
import requests

POINTS = {
    "antwerp": (51.22, 4.40), "ghent": (51.05, 3.72), "bruges": (51.21, 3.22), "kortrijk": (50.83, 3.26),
    "brussels": (50.85, 4.35), "hasselt": (50.93, 5.34), "liege": (50.63, 5.57), "charleroi": (50.41, 4.44),
}
GLOBAL = ["ecmwf_ifs025", "icon_eu", "meteofrance_arpege_europe", "gfs_seamless"]
REGIONAL = ["icon_d2", "meteofrance_arome_france"]
RUN_MODELS = ["ecmwf_ifs025", "icon_eu", "meteofrance_arpege_europe", "gfs_global", "icon_d2", "meteofrance_arome_france"]
MISSING_KEYS = {  # how each model is named in the API's "run is not available" message
    "ecmwf_ifs025": ["ecmwf_ifs"], "icon_eu": ["icon_eu"], "meteofrance_arpege_europe": ["arpege"],
    "gfs_global": ["gfs"], "icon_d2": ["icon_d2"], "meteofrance_arome_france": ["arome"],
}
OUT = "data/predico/nwp"
S = requests.Session()


class DailyLimit(RuntimeError):
    pass


def get(url, params):
    for attempt in range(6):
        try:
            r = S.get(url, params=params, timeout=180)
        except requests.RequestException as ex:
            print("  network error, retrying:", str(ex)[:80], flush=True)
            time.sleep(5 + 5 * attempt)
            continue
        if r.status_code == 200:
            return r.json()
        reason = r.text[:200]
        if r.status_code == 429 and "Daily" in reason:
            raise DailyLimit(reason)
        if r.status_code == 429:
            wait = 3700 if "Hourly" in reason else 65
            print(f"  rate limited ({reason[:60]}), sleeping {wait}s", flush=True)
            time.sleep(wait)
            continue
        if r.status_code == 400:
            return {"error": reason}
        print("  http", r.status_code, reason[:100], flush=True)
        time.sleep(5 + 5 * attempt)
    raise RuntimeError("gave up: " + json.dumps(params)[:200])


def year_chunks(start, end):
    a = pd.Timestamp(start)
    stop = pd.Timestamp(end)
    while a <= stop:
        b = min(pd.Timestamp(year=a.year, month=12, day=31), stop)
        yield a.strftime("%Y-%m-%d"), b.strftime("%Y-%m-%d")
        a = b + pd.Timedelta(days=1)


def lagged(start, end):
    url = "https://previous-runs-api.open-meteo.com/v1/forecast"
    jobs = [
        (GLOBAL, ["shortwave_radiation_previous_day1", "shortwave_radiation_previous_day2",
                  "cloud_cover_previous_day1", "cloud_cover_previous_day2"]),
        (REGIONAL, ["shortwave_radiation_previous_day1", "cloud_cover_previous_day1"]),
        (["ecmwf_ifs025"], ["temperature_2m_previous_day1", "temperature_2m_previous_day2"]),
    ]
    for name, (lat, lon) in POINTS.items():
        out = f"{OUT}/lagged_{name}.csv"
        if os.path.exists(out):
            continue
        parts = []
        for a, b in year_chunks(start, end):
            frame = None
            for models, variables in jobs:
                d = get(url, {"latitude": lat, "longitude": lon, "hourly": ",".join(variables), "models": ",".join(models),
                              "start_date": a, "end_date": b, "timezone": "UTC"})
                if "error" in d:
                    raise RuntimeError(d["error"])
                h = pd.DataFrame(d["hourly"]).set_index("time")
                if len(models) == 1:  # a single model comes back without the model suffix
                    h.columns = [f"{c}_{models[0]}" for c in h.columns]
                frame = h if frame is None else frame.join(h)
                time.sleep(6)
            parts.append(frame)
        full = pd.concat(parts)
        full.to_csv(out)
        print(name, "hours", len(full), "columns", full.shape[1], "non-null share", round(float(full.notna().mean().mean()), 3), flush=True)


def runs(start, end, hour="00", prefix="runs", wanted=RUN_MODELS):
    url = "https://single-runs-api.open-meteo.com/v1/forecast"
    lat = ",".join(str(v[0]) for v in POINTS.values())
    lon = ",".join(str(v[1]) for v in POINTS.values())
    for day in pd.date_range(start, end, freq="D"):
        out = f"{OUT}/{prefix}_{day.strftime('%Y-%m-%d')}.json"
        if os.path.exists(out):
            continue
        run = (day - pd.Timedelta(days=1)).strftime(f"%Y-%m-%dT{hour}:00")
        models = list(wanted)
        d = None
        while models:
            d = get(url, {"latitude": lat, "longitude": lon, "hourly": "shortwave_radiation,cloud_cover,temperature_2m",
                          "models": ",".join(models), "run": run, "forecast_days": 3, "timezone": "UTC"})
            if not (isinstance(d, dict) and "error" in d):
                break
            # one model without this run fails the whole request: drop that model and ask again
            missing = [m for m in models if any(key in d["error"] for key in MISSING_KEYS[m])]
            print(day.date(), "run", run, "dropping", missing or "?", "-", d["error"][:90], flush=True)
            if not missing:
                models = []
                break
            models = [m for m in models if m not in missing]
            time.sleep(1.5)
        if not models:
            continue
        json.dump({"run": run, "points": list(POINTS), "models": models, "data": d}, open(out, "w"))
        if day.day in (1, 11, 21):
            print(day.date(), "ok", flush=True)
        time.sleep(2.5)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    mode, start, end = sys.argv[1], sys.argv[2], sys.argv[3]
    try:
        if mode == "lagged":
            lagged(start, end)
        elif mode == "runs":
            runs(start, end)
        else:  # runs03: the regional models from the 03Z run, also published before the gate
            runs(start, end, hour="03", prefix="runs03", wanted=REGIONAL)
        print("done", mode)
    except DailyLimit as ex:
        print("STOPPED at the daily quota:", ex)
        sys.exit(2)
