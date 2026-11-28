"""Live shadow run of a day-ahead Belgian solar forecast (Predico rules, nothing is submitted).

Usage:
  python shadow.py build-train            rebuild the training table from the backtest data
  python shadow.py forecast [--day D]     forecast delivery day D (default: tomorrow, Brussels time)
  python shadow.py hindcast START END     forecasts for past delivery days from archived runs
  python shadow.py score                  score every stored forecast that has measurements

Each delivery day gets forecasts/<day>.csv (15-minute Q10/Q50/Q90) and forecasts/<day>.json
(when it was made, which model runs it used, whether it beat the 10:00 Brussels gate).
The model is the one from research/2026-11-review/scripts/predico_backtest.py: gradient
boosting on six public weather models, mean of a load-factor and a clear-sky-ratio model, a
trailing bias correction and a conformally widened P10-P90 band.
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "2026-11-review", "scripts"))
import om_source as om  # noqa: E402
import predico_backtest as pb  # noqa: E402

POINTS = {
    "antwerp": (51.22, 4.40), "ghent": (51.05, 3.72), "bruges": (51.21, 3.22), "kortrijk": (50.83, 3.26),
    "brussels": (50.85, 4.35), "hasselt": (50.93, 5.34), "liege": (50.63, 5.57), "charleroi": (50.41, 4.44),
}
ALL_MODELS = pb.GLOBAL + pb.REGIONAL
# Meteo-France radiation is archived with a historically wrong unit factor; readers must undo it
# (open-meteo: MeteoFranceSurfaceVariable.shortwaveRadiationArchiveCorrectionFactor).
METEOFRANCE_FIX = 10_000_000 / 3600 ** 2
TZ = "Europe/Brussels"
GATE_HOUR = 10
TRAIN_FILE = os.path.join(HERE, "train_features.csv.gz")
CONFIG_FILE = os.path.join(HERE, "model_config.json")
FORECASTS = os.path.join(HERE, "forecasts")
SCORES = os.path.join(HERE, "scores.csv")
ELIA = "https://opendata.elia.be/api/explore/v2.1/catalog/datasets"
# Elia moved its solar data to new datasets on 2026-10-26: ods209 is the daily-updated history.
SOURCE = {"dataset": "ods209", "filter": "region='Belgium' and gridconnectiontype='Total'", "measured": "measured_upscaled",
          "dayahead": "dayahead11hforecast", "p10": "dayahead11hconfidence10", "p90": "dayahead11hconfidence90",
          "capacity": "monitoredcapacity"}


# ---------------------------------------------------------------- weather inputs

def clear_sky_hourly(labels):
    """Mean clear-sky irradiance over the hour ending at each label (same definition as the backtest)."""
    q = pd.date_range(labels.min() - pd.Timedelta(hours=1), labels.max() - pd.Timedelta(minutes=15), freq="15min")
    cs, _, _ = pb.clear_sky(q + pd.Timedelta(minutes=7.5))
    return pd.Series(cs, index=q).groupby(q.floor("h") + pd.Timedelta(hours=1)).mean()


def assemble(read, labels):
    """Hourly frame with columns (variable, model, point) for hour-ending labels.

    read(model, variable) returns a frame of valid times x points on the model's own time step,
    or raises FileNotFoundError when the model has nothing for the period.
    """
    wide = pd.date_range(labels.min() - pd.Timedelta(hours=3), labels.max() + pd.Timedelta(hours=3), freq="h")
    cs_hour = clear_sky_hourly(wide)
    cols, used = {}, []
    for alias in ALL_MODELS:
        model, radiation = om.MODELS[alias]
        try:
            ghi = sum(read(model, v) for v in radiation).astype(float)
            cloud = read(model, "cloud_cover").astype(float)
            temp = read(model, "temperature_2m").astype(float)
        except (FileNotFoundError, RuntimeError) as ex:  # one model missing must not stop the forecast
            print(f"model {alias} skipped: {str(ex)[:100]}", flush=True)
            continue
        if alias in ("arpege", "arome"):
            ghi = ghi * METEOFRANCE_FIX
        step = om.meta(model)["temporal_resolution_seconds"]
        if step == 3600:
            ghi_h, cloud_h, temp_h = ghi.reindex(labels), cloud.reindex(labels), temp.reindex(labels)
        else:
            # coarser steps: keep the clear-sky index of each block constant over its hours
            hours = step // 3600
            block_cs = cs_hour.rolling(hours).mean().reindex(ghi.index)
            kc = ghi.div(block_cs, axis=0).where(block_cs > 10).clip(0, 1.5)
            flat = kc.reindex(kc.index.union(labels)).bfill(limit=hours - 1).reindex(labels)
            # linear between block centres, which is closer to what the API serves than a flat block
            centre = kc.copy()
            centre.index = centre.index - pd.Timedelta(hours=hours / 2)
            mid = labels - pd.Timedelta(minutes=30)
            kc_h = centre.reindex(centre.index.union(mid)).interpolate("time", limit_area="inside").reindex(mid)
            kc_h.index = labels
            kc_h = kc_h.where(kc_h.notna(), flat)
            ghi_h = kc_h.mul(cs_hour.reindex(labels), axis=0)
            ghi_h = ghi_h.where(cs_hour.reindex(labels) > 10, 0.0)
            cloud_h = cloud.reindex(cloud.index.union(labels)).interpolate("time", limit_area="inside").reindex(labels)
            temp_h = temp.reindex(temp.index.union(labels)).interpolate("time", limit_area="inside").reindex(labels)
        if ghi_h.notna().sum().sum() == 0:
            continue
        used.append(alias)
        for p in POINTS:
            cols[("shortwave_radiation", alias, p)] = ghi_h[p]
            cols[("cloud_cover", alias, p)] = cloud_h[p]
            cols[("temperature_2m", alias, p)] = temp_h[p]
    frame = pd.DataFrame(cols, index=labels)
    for alias in ALL_MODELS:  # a missing model stays in the table as empty columns
        for var in ("shortwave_radiation", "cloud_cover", "temperature_2m"):
            for p in POINTS:
                if (var, alias, p) not in frame.columns:
                    frame[(var, alias, p)] = np.nan
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)
    return frame, used


def day_labels(day):
    """Hour-ending UTC labels covering the delivery day 00:00-24:00 Brussels time."""
    start = pd.Timestamp(day, tz=TZ)
    end = (start + pd.Timedelta(days=1, hours=3)).normalize()  # next local midnight, DST-safe
    return pd.date_range(start.tz_convert("UTC") + pd.Timedelta(hours=1), end.tz_convert("UTC"), freq="h")


def latest_inputs(day):
    labels = day_labels(day)
    read = lambda model, variable: om.read_latest(model, variable, POINTS, labels.min() - pd.Timedelta(hours=3),  # noqa: E731
                                                  labels.max() + pd.Timedelta(hours=3))
    frame, used = assemble(read, labels)
    if len(used) < 3:
        raise RuntimeError(f"only {len(used)} weather models available, refusing to forecast")
    runs = {a: pd.Timestamp(om.meta(om.MODELS[a][0])["last_run_initialisation_time"], unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%MZ")
            for a in used}
    return frame, runs


def archived_inputs(day, run_hours):
    """Inputs as they stood before the gate of the day before: the given run hour of each model."""
    labels = day_labels(day)
    runs = {}

    def read(model, variable):
        alias = next(a for a, (m, _) in om.MODELS.items() if m == model)
        run = (pd.Timestamp(day) - pd.Timedelta(days=1)).replace(hour=run_hours[alias])
        runs[alias] = run.strftime("%Y-%m-%dT%H:%MZ")
        return om.read_run(model, run, variable, POINTS)

    frame, used = assemble(read, labels)
    return frame, {a: runs[a] for a in used}


# ---------------------------------------------------------------- model

def build_train():
    """Training table from the backtest data: ~24 h lagged features and measured load factor."""
    elia = pb.load_elia()
    lagged = pb.load_lagged()
    f = pb.features(lagged[1], ALL_MODELS).join(elia[["lf"]], how="inner")
    f = f[(f.cs > 5) & f.lf.notna()]
    f.round(4).to_csv(TRAIN_FILE, index_label="datetime", compression="gzip")

    # band width: quantile models fitted up to the end of May, widened on the June-October runs
    def table(hourly, lo, hi):
        t = pb.features(hourly, ALL_MODELS)
        t = t[(t.index >= pd.Timestamp(lo, tz="UTC")) & (t.index < pd.Timestamp(hi, tz="UTC") + pd.Timedelta(days=1))]
        return t.join(elia[["lf"]], how="inner")

    early = f[f.index < pd.Timestamp("2026-06-01", tz="UTC")]
    runs, runs03 = pb.load_runs(), pb.load_runs("runs03")
    for col in runs03.columns:
        if col[1] in pb.REGIONAL:
            runs[col] = runs03[col].reindex(runs.index).combine_first(runs[col])
    cal = table(runs, *pb.TEST)
    cal = cal[(cal.cs > 5) & cal.lf.notna()]
    x, cs = cal[pb.feature_cols(early)], cal.cs.values
    lo = pb.fit_predict(early, x, cs, loss="quantile", quantile=0.1, light=True)
    hi = np.maximum(pb.fit_predict(early, x, cs, loss="quantile", quantile=0.9, light=True), lo)
    miss = np.maximum(lo - cal.lf.values, cal.lf.values - hi) / (cs / 1000)
    config = {
        "trained_on": [str(f.index.min()), str(f.index.max())], "rows": int(len(f)), "models": ALL_MODELS,
        "point_forecast": "mean of load-factor and clear-sky-ratio gradient boosting, trailing bias correction",
        "band_widening_per_kw_clear_sky": round(float(np.quantile(miss, 0.8)), 5),
        "band_calibrated_on": list(pb.TEST), "last_known_capacity_mw": float(elia.monitoredcapacity.iloc[-1]),
    }
    json.dump(config, open(CONFIG_FILE, "w"), indent=2)
    print("training rows", len(f), "columns", f.shape[1], "file MB", round(os.path.getsize(TRAIN_FILE) / 1e6, 2))
    print(json.dumps(config, indent=2))


_FITTED = {}


def fitted():
    """Fit the four models once per process: point (two targets) and the 10% / 90% quantiles."""
    if not _FITTED:
        train = pd.read_csv(TRAIN_FILE, index_col="datetime", parse_dates=["datetime"])
        cols = pb.feature_cols(train)
        x, scale = train[cols], train.cs / 1000
        _FITTED["cols"] = cols
        _FITTED["lf"] = pb.make_model().fit(x, train.lf)
        _FITTED["ratio"] = pb.make_model().fit(x, (train.lf / scale).clip(0, 1.5), sample_weight=scale ** 2)
        _FITTED["q10"] = pb.make_model("quantile", 0.1, True).fit(x, train.lf)
        _FITTED["q90"] = pb.make_model("quantile", 0.9, True).fit(x, train.lf)
    return _FITTED


def predict(frame):
    """Raw Q10/Q50/Q90 load factors on the 15-minute grid of an hourly input frame."""
    config = json.load(open(CONFIG_FILE))
    m = fitted()
    f = pb.features(frame, ALL_MODELS)
    sun = (f.cs > 5).values
    out = pd.DataFrame(0.0, index=f.index, columns=["q10", "q50", "q90"])
    if sun.any():
        x, cs = f.loc[sun, m["cols"]], f.cs.values[sun]
        q50 = np.clip((np.clip(m["lf"].predict(x), 0, 1) + np.clip(m["ratio"].predict(x) * cs / 1000, 0, 1)) / 2, 0, 1)
        widen = config["band_widening_per_kw_clear_sky"] * cs / 1000
        out.loc[sun, "q50"] = q50
        out.loc[sun, "q10"] = np.clip(np.minimum(np.clip(m["q10"].predict(x), 0, 1), q50) - widen, 0, 1)
        out.loc[sun, "q90"] = np.clip(np.maximum(np.clip(m["q90"].predict(x), 0, 1), q50) + widen, 0, 1)
    return out, config


# ---------------------------------------------------------------- stored forecasts

def stored_days():
    if not os.path.isdir(FORECASTS):
        return []
    return sorted(n[:-4] for n in os.listdir(FORECASTS) if n.endswith(".csv"))


def bias_factor(day, actual_by_day):
    """Actual / forecast energy over delivery days D-9 .. D-2 (needs at least four of them)."""
    num = den = 0.0
    n = 0
    for back in range(2, 10):
        d = (pd.Timestamp(day) - pd.Timedelta(days=back)).strftime("%Y-%m-%d")
        path = os.path.join(FORECASTS, d + ".csv")
        if d in actual_by_day and os.path.exists(path):
            num += actual_by_day[d]
            den += float(pd.read_csv(path).q50_raw_lf.sum())
            n += 1
    if n < 4 or den <= 0:
        return 1.0, n
    return float(np.clip(num / den, 0.9, 1.1)), n


def save_forecast(day, frame, runs, kind, created_at, actual_by_day, capacity=None):
    raw, config = predict(frame)
    factor, n_days = bias_factor(day, actual_by_day)
    cap = capacity if capacity else config["last_known_capacity_mw"]
    out = pd.DataFrame({
        "q10_mw": (raw.q10 * factor * cap).round(1), "q50_mw": (raw.q50 * factor * cap).round(1),
        "q90_mw": (raw.q90 * factor * cap).round(1), "q50_raw_lf": raw.q50.round(5),
    }, index=raw.index)
    out["q10_mw"] = np.minimum(out.q10_mw, out.q50_mw)
    out["q90_mw"] = np.maximum(out.q90_mw, out.q50_mw)
    os.makedirs(FORECASTS, exist_ok=True)
    out.to_csv(os.path.join(FORECASTS, f"{day}.csv"), index_label="datetime_utc")
    gate = (pd.Timestamp(day, tz=TZ) - pd.Timedelta(days=1)).replace(hour=GATE_HOUR)
    info = {
        "delivery_day": day, "kind": kind, "created_at_utc": created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gate_utc": gate.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
        "before_gate": bool(created_at < gate) if kind == "live" else None,
        "model_runs": runs, "models_missing": [a for a in ALL_MODELS if a not in runs],
        "bias_factor": round(factor, 4), "bias_days": n_days, "capacity_mw": cap,
        "energy_mwh": round(float(out.q50_mw.sum() / 4), 0), "peak_mw": float(out.q50_mw.max()),
    }
    json.dump(info, open(os.path.join(FORECASTS, f"{day}.json"), "w"), indent=2)
    return info


# ---------------------------------------------------------------- measurements and scores

def elia_get(path, params):
    import requests
    for attempt in range(6):
        try:
            r = requests.get(f"{ELIA}/{SOURCE['dataset']}/{path}", params=params, timeout=90)
            if r.status_code == 200:
                return r
        except requests.RequestException:
            pass
        time.sleep(2 + 2 * attempt)
    raise RuntimeError("Elia open data did not answer")


def measurements(first_day, last_day):
    """Measured PV, Elia's own 11:00 day-ahead forecast and capacity per quarter-hour (UTC index, MW)."""
    import io
    a = pd.Timestamp(first_day, tz=TZ).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
    b = (pd.Timestamp(last_day, tz=TZ) + pd.Timedelta(days=1, hours=3)).normalize().tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
    fields = ["datetime", SOURCE["measured"], SOURCE["dayahead"], SOURCE["p10"], SOURCE["p90"], SOURCE["capacity"]]
    r = elia_get("exports/csv", {"where": f"{SOURCE['filter']} and datetime >= '{a}' and datetime < '{b}'", "select": ",".join(fields),
                                 "order_by": "datetime", "delimiter": ","})
    d = pd.read_csv(io.StringIO(r.text))
    if d.empty:
        return d
    d["datetime"] = pd.to_datetime(d["datetime"], utc=True)
    d = d.set_index("datetime").sort_index()
    return d.rename(columns={SOURCE["measured"]: "measured", SOURCE["dayahead"]: "elia", SOURCE["p10"]: "elia10",
                             SOURCE["p90"]: "elia90", SOURCE["capacity"]: "capacity"}).astype(float)


def current_capacity(default):
    """Latest monitored capacity Elia publishes (MW); the load-factor forecast is scaled with it."""
    try:
        r = elia_get("records", {"where": f"{SOURCE['filter']} and {SOURCE['capacity']} is not null", "order_by": "datetime desc",
                                 "limit": 1, "select": f"datetime,{SOURCE['capacity']}"})
        return float(r.json()["results"][0][SOURCE["capacity"]])
    except Exception as ex:
        print("capacity lookup failed, using the stored value:", str(ex)[:100])
        return default


def actual_energy_by_day(days):
    """Measured load-factor sum per delivery day, for the bias correction."""
    if not days:
        return {}
    m = measurements(min(days), max(days))
    out = {}
    if m.empty:
        return out
    local = m.index.tz_convert(TZ).strftime("%Y-%m-%d")
    for d, g in m.groupby(local):
        if d in days and g.measured.notna().sum() >= 90:
            out[d] = float((g.measured / g.capacity).sum())
    return out


def winkler(lo, hi, y):
    return (hi - lo) + 10 * np.clip(lo - y, 0, None) + 10 * np.clip(y - hi, 0, None)


def score():
    days = [d for d in stored_days() if pd.Timestamp(d, tz=TZ) + pd.Timedelta(days=1) < pd.Timestamp.now(tz=TZ)]
    if not days:
        print("nothing to score yet")
        return
    m = measurements(min(days), max(days))
    rows = []
    for d in days:
        f = pd.read_csv(os.path.join(FORECASTS, d + ".csv"), parse_dates=["datetime_utc"]).set_index("datetime_utc")
        info = json.load(open(os.path.join(FORECASTS, d + ".json")))
        j = f.join(m, how="inner") if not m.empty else pd.DataFrame()
        if len(j) < 90 or j.measured.isna().any():
            continue
        row = {"delivery_day": d, "kind": info["kind"], "before_gate": info["before_gate"], "quarter_hours": len(j),
               "measured_mwh": round(float(j.measured.sum() / 4)), "rmse_ours_mw": float(np.sqrt(((j.q50_mw - j.measured) ** 2).mean())),
               "rmse_elia_mw": float(np.sqrt(((j.elia - j.measured) ** 2).mean())) if j.elia.notna().all() else np.nan,
               "winkler_ours_mw": float(winkler(j.q10_mw, j.q90_mw, j.measured).mean()),
               "winkler_elia_mw": float(winkler(j.elia10, j.elia90, j.measured).mean()) if j.elia10.notna().all() else np.nan,
               "inside_band": float(((j.measured >= j.q10_mw) & (j.measured <= j.q90_mw))[j.measured > 1].mean()) if (j.measured > 1).any() else np.nan}
        rows.append(row)
    s = pd.DataFrame(rows)
    if s.empty:
        print("no delivery day has complete measurements yet")
        return
    s.round(1).to_csv(SCORES, index=False)
    def line(label, ours, ref, unit):
        lo, hi, p_better = pb.daily_ratio_ci(ours.values, ref.values) if len(ours) >= 5 else (np.nan, np.nan, np.nan)
        print(f"   {label} {ours.mean():.0f} MW, Elia {ref.mean():.0f} MW, ratio {ours.mean() / ref.mean():.3f} "
              f"(90% interval {lo:.2f}-{hi:.2f}, P(better) {p_better:.2f}), better on {int((ours < ref).sum())} of {len(ours)} days")

    for kind, g in s.groupby("kind"):
        g = g.dropna(subset=["rmse_elia_mw"])
        if g.empty:
            continue
        print(f"{kind}: {len(g)} days {g.delivery_day.min()} .. {g.delivery_day.max()}")
        line("point:    mean daily RMSE", g.rmse_ours_mw, g.rmse_elia_mw, "MW")
        w = g.dropna(subset=["winkler_elia_mw"])
        if len(w):
            line("interval: mean daily Winkler", w.winkler_ours_mw, w.winkler_elia_mw, "MW")
        if kind == "live":
            print(f"   gate: {int(g.before_gate.fillna(False).sum())} of {len(g)} forecasts made before 10:00 Brussels")


# ---------------------------------------------------------------- commands

def recent_actuals(day):
    try:
        days = [(pd.Timestamp(day) - pd.Timedelta(days=b)).strftime("%Y-%m-%d") for b in range(2, 10)]
        return actual_energy_by_day([d for d in days if os.path.exists(os.path.join(FORECASTS, d + ".csv"))])
    except Exception as ex:  # measurements are optional: without them the correction is simply off
        print("measurements unavailable, bias correction off:", str(ex)[:120])
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["build-train", "forecast", "hindcast", "score"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--day")
    a = ap.parse_args()
    if a.command == "build-train":
        build_train()
    elif a.command == "forecast":
        now = pd.Timestamp.now(tz="UTC")
        day = a.day or (now.tz_convert(TZ) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        frame, runs = latest_inputs(day)
        capacity = current_capacity(json.load(open(CONFIG_FILE))["last_known_capacity_mw"])
        info = save_forecast(day, frame, runs, "live", pd.Timestamp.now(tz="UTC"), recent_actuals(day), capacity)
        print(json.dumps(info, indent=2))
    elif a.command == "hindcast":
        run_hours = {"ifs": 0, "iconeu": 0, "arpege": 0, "gfs": 0, "icond2": 3, "arome": 3}
        capacities = measurements(a.args[0], a.args[1]).capacity.dropna()
        capacities = capacities.groupby(capacities.index.tz_convert(TZ).strftime("%Y-%m-%d")).last()
        for day in pd.date_range(a.args[0], a.args[1], freq="D").strftime("%Y-%m-%d"):
            if os.path.exists(os.path.join(FORECASTS, day + ".csv")):
                continue
            frame, runs = archived_inputs(day, run_hours)
            info = save_forecast(day, frame, runs, "hindcast", pd.Timestamp.now(tz="UTC"), recent_actuals(day),
                                 float(capacities.get(day, 0)) or None)
            print(day, "runs", len(runs), "missing", info["models_missing"], "bias", info["bias_factor"], "energy MWh", info["energy_mwh"], flush=True)
    else:
        score()


if __name__ == "__main__":
    main()
