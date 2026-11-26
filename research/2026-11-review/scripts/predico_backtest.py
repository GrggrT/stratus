"""Day-ahead Belgian solar forecast from public NWP against the grid operator's own forecast.

Usage: python scripts/predico_backtest.py

Question: can a newcomer using only public weather models come within 5% of Elia's own
day-ahead forecast (the reference a Predico entrant is ranked against)?

Protocol, fixed before looking at the result:
- Target: measured PV / monitored capacity (ods032), 15-minute resolution.
- Training: 2025-01-01 .. 2026-05-31, features from the Previous Runs archive.
- Test: 2026-06-02 .. 2026-10-25, features from the 00Z run of the day before delivery, the
  freshest run of every model that is published before the gate. Nothing from the test window
  is used for fitting or for choosing settings.
- Primary model: gradient boosting on six models (IFS, ICON-EU, ARPEGE, GFS, ICON-D2, AROME),
  trained on ~24 h lagged features. Reported next to it: a variant chosen from six pre-declared
  ones on the last four months of the training period, a four-model variant, lag brackets and a
  one-coefficient linear baseline.
- Metric: RMSE in % of monitored capacity over all quarter-hours, ours and Elia's 11:00
  day-ahead forecast on identical timestamps; ratio with a day-block bootstrap interval.
- Added after the first test run, declared before its data was fetched: the same two models
  with the regional models taken from their 03Z run (the Predico gate is 10:00 Brussels time,
  08:00 UTC in summer, so that run is published in time). No other variant was tried on the
  test window.
"""
import glob, json, os
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NWP = os.path.join(BASE, "data/predico/nwp")
POINTS = ["antwerp", "ghent", "bruges", "kortrijk", "brussels", "hasselt", "liege", "charleroi"]
REGIONS = {"west": ["ghent", "bruges", "kortrijk"], "east": ["antwerp", "brussels", "hasselt"], "south": ["liege", "charleroi"]}
GLOBAL = ["ifs", "iconeu", "arpege", "gfs"]
REGIONAL = ["icond2", "arome"]
ALIAS = {"ecmwf_ifs025": "ifs", "icon_eu": "iconeu", "meteofrance_arpege_europe": "arpege", "gfs_seamless": "gfs",
         "gfs_global": "gfs", "icon_d2": "icond2", "meteofrance_arome_france": "arome"}
TRAIN = ("2025-01-01", "2026-05-31")
TEST = ("2026-06-02", "2026-10-25")
VAL_SPLIT = "2026-02-01"  # variant choice: fit before this date, validate from it to the end of training
LAT, LON = 50.8, 4.4


def clear_sky(ts):
    """Haurwitz clear-sky GHI (W/m2) and cos(zenith) at the centre of Belgium for UTC timestamps."""
    doy = ts.day_of_year.values
    hour = ts.hour.values + ts.minute.values / 60 + ts.second.values / 3600
    g = 2 * np.pi / 365 * (doy - 1 + (hour - 12) / 24)
    eqt = 229.18 * (0.000075 + 0.001868 * np.cos(g) - 0.032077 * np.sin(g) - 0.014615 * np.cos(2 * g) - 0.040849 * np.sin(2 * g))
    decl = (0.006918 - 0.399912 * np.cos(g) + 0.070257 * np.sin(g) - 0.006758 * np.cos(2 * g) + 0.000907 * np.sin(2 * g)
            - 0.002697 * np.cos(3 * g) + 0.00148 * np.sin(3 * g))
    solar_minutes = hour * 60 + eqt + 4 * LON
    ha = np.radians(solar_minutes / 4 - 180)
    lat = np.radians(LAT)
    cosz = np.sin(lat) * np.sin(decl) + np.cos(lat) * np.cos(decl) * np.cos(ha)
    cs = np.where(cosz > 0, 1098 * cosz * np.exp(-0.057 / np.clip(cosz, 1e-3, None)), 0.0)
    return cs, cosz, solar_minutes - 720


def load_elia():
    d = pd.read_csv(os.path.join(BASE, "data/predico/ods032_be.csv"), parse_dates=["datetime"]).sort_values("datetime")
    d = d[(d.monitoredcapacity > 0)].drop_duplicates("datetime").set_index("datetime")
    d["lf"] = d.measured / d.monitoredcapacity
    d["elia"] = d.dayahead11hforecast / d.monitoredcapacity
    d["elia10"] = d.dayahead11hconfidence10 / d.monitoredcapacity
    d["elia90"] = d.dayahead11hconfidence90 / d.monitoredcapacity
    return d


def load_lagged():
    """Hourly frames per lag: columns (variable, model, point)."""
    out = {1: {}, 2: {}}
    for p in POINTS:
        h = pd.read_csv(f"{NWP}/lagged_{p}.csv", parse_dates=["time"]).set_index("time")
        h.index = h.index.tz_localize("UTC")
        for col in h.columns:
            for lag in (1, 2):
                tag = f"_previous_day{lag}_"
                if tag in col:
                    var, model = col.split(tag)
                    out[lag][(var, ALIAS[model], p)] = h[col]
    return {lag: pd.DataFrame(cols) for lag, cols in out.items()}


def load_runs(prefix="runs"):
    """Hourly frame for the test window from one run of the day before each delivery day (00Z, or 03Z for "runs03")."""
    parts = []
    for path in sorted(glob.glob(f"{NWP}/{prefix}_*.json")):
        day = pd.Timestamp(os.path.basename(path)[len(prefix) + 1:len(prefix) + 11], tz="UTC")
        d = json.load(open(path))
        cols = {}
        for p, loc in zip(d["points"], d["data"]):
            t = pd.to_datetime(loc["hourly"]["time"]).tz_localize("UTC")
            keep = (t > day) & (t <= day + pd.Timedelta(days=1))
            for k, v in loc["hourly"].items():
                if k == "time":
                    continue
                for var in ("shortwave_radiation", "cloud_cover", "temperature_2m"):
                    if k.startswith(var + "_"):
                        cols[(var, ALIAS[k[len(var) + 1:]], p)] = pd.Series(np.array(v, dtype=float)[keep], index=t[keep])
        parts.append(pd.DataFrame(cols))
    return pd.concat(parts).sort_index()


def features(hourly, models):
    """15-minute feature table from an hourly NWP frame (values are means over the preceding hour)."""
    q = pd.date_range(hourly.index.min() - pd.Timedelta(hours=1), hourly.index.max() - pd.Timedelta(minutes=15), freq="15min")
    cs, cosz, from_noon = clear_sky(q + pd.Timedelta(minutes=7.5))
    f = pd.DataFrame({"cs": cs, "cosz": cosz, "from_noon": from_noon,
                      "doy_sin": np.sin(2 * np.pi * q.day_of_year / 365.25), "doy_cos": np.cos(2 * np.pi * q.day_of_year / 365.25)}, index=q)
    hour_end = q.floor("h") + pd.Timedelta(hours=1)
    cs_hour = pd.Series(cs, index=q).groupby(hour_end).mean()
    sun = cs_hour > 10
    kcs = []
    for m in models:
        ghi = hourly["shortwave_radiation"][m]
        lit = sun.reindex(ghi.index).fillna(False).values[:, None]
        kc_pts = ghi.div(cs_hour.reindex(ghi.index), axis=0).where(np.broadcast_to(lit, ghi.shape)).clip(0, 1.5)
        kc = kc_pts.mean(axis=1)
        f[f"kc_{m}"] = kc.reindex(hour_end).values
        f[f"ghi_{m}"] = f[f"kc_{m}"] * f.cs
        f[f"kcstd_{m}"] = kc_pts.std(axis=1).reindex(hour_end).values
        f[f"cc_{m}"] = hourly["cloud_cover"][m].mean(axis=1).reindex(hour_end).values
        for r, pts in REGIONS.items():
            f[f"kc_{m}_{r}"] = kc_pts[pts].mean(axis=1).reindex(hour_end).values
        kcs.append(f[f"kc_{m}"])
    stack = pd.concat(kcs, axis=1)
    f["kc_mean"] = stack.mean(axis=1)
    f["kc_spread"] = stack.std(axis=1)
    f["ghi_mean"] = f.kc_mean * f.cs
    f["temp"] = hourly["temperature_2m"]["ifs"].mean(axis=1).reindex(hour_end).values
    return f


def make_model(loss="squared_error", quantile=None, light=False):
    """The one model definition used by the backtest and by the live shadow run."""
    return HistGradientBoostingRegressor(loss=loss, quantile=quantile, learning_rate=0.05, random_state=0, l2_regularization=1.0,
                                         max_iter=300 if light else 600, max_leaf_nodes=15 if light else 31,
                                         min_samples_leaf=100 if light else 40)


def fit_predict(train, test_x, cs_test, target="lf", loss="squared_error", quantile=None, light=False):
    """Fit on daylight training rows and predict the load factor for the given test rows."""
    tr = train[(train.cs > 5) & train.lf.notna()]
    x = tr[feature_cols(train)]
    model = make_model(loss, quantile, light)
    if target == "lf":
        model.fit(x, tr.lf)
        pred = model.predict(test_x)
    else:  # performance ratio against clear sky, weighted so that the loss is still in load-factor units
        scale = tr.cs / 1000
        model.fit(x, (tr.lf / scale).clip(0, 1.5), sample_weight=scale ** 2)
        pred = model.predict(test_x) * cs_test / 1000
    return np.clip(pred, 0, 1)


def feature_cols(t):
    return [c for c in t.columns if c not in ("lf", "elia", "elia10", "elia90", "monitoredcapacity")]


def trailing_bias(pred, actual, days, window=8, lag=2, clip=(0.9, 1.1)):
    """Scale each day's forecast by actual / forecast energy over days D-9 .. D-2, all known at gate closure."""
    d = pd.DataFrame({"p": pred, "a": actual, "d": days}).groupby("d").sum()
    d = d.reindex(pd.date_range(d.index.min(), d.index.max(), freq="D").strftime("%Y-%m-%d"))
    num = d.a.shift(lag).rolling(window, min_periods=4).sum()
    den = d.p.shift(lag).rolling(window, min_periods=4).sum()
    factor = (num / den).clip(*clip).fillna(1.0)
    return pred * factor.reindex(days).values


def rmse(a, b):
    return float(np.sqrt(np.mean((a - b) ** 2)))


def ratio_ci(err_ours, err_ref, days, n=2000, seed=0):
    """Day-block bootstrap of RMSE(ours) / RMSE(reference)."""
    g = pd.DataFrame({"a": err_ours ** 2, "b": err_ref ** 2, "d": days}).groupby("d").sum()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(g), size=(n, len(g)))
    r = np.sqrt(g.a.values[idx].sum(axis=1) / g.b.values[idx].sum(axis=1))
    return float(np.quantile(r, 0.05)), float(np.quantile(r, 0.95)), float((r <= 1.05).mean()), float((r <= 1.0).mean())


def daily_ratio_ci(ours, ref, n=2000, seed=0):
    """Bootstrap over days of mean(ours) / mean(ref) for two aligned series of daily scores."""
    a, b = np.asarray(ours), np.asarray(ref)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), size=(n, len(a)))
    r = a[idx].mean(axis=1) / b[idx].mean(axis=1)
    return float(np.quantile(r, 0.05)), float(np.quantile(r, 0.95)), float((r <= 1.0).mean())


VARIANTS = {  # name -> (targets averaged, trailing bias correction)
    "A load factor": (("lf",), False),
    "B clear-sky ratio": (("ratio",), False),
    "C = A + trailing bias": (("lf",), True),
    "D = B + trailing bias": (("ratio",), True),
    "E = mean of A and B": (("lf", "ratio"), False),
    "F = E + trailing bias": (("lf", "ratio"), True),
}


def predict_variant(train, test, idx, name):
    """Forecast for timestamps idx (zero at night) with one of the pre-declared variants."""
    targets, correct = VARIANTS[name]
    sun = (test.loc[idx, "cs"] > 5).values
    x = test.loc[idx, feature_cols(train)][sun]
    cs = test.loc[idx, "cs"].values[sun]
    pred = np.zeros(len(idx))
    pred[sun] = np.mean([fit_predict(train, x, cs, target=t) for t in targets], axis=0)
    if correct:
        pred = np.clip(trailing_bias(pred, test.loc[idx, "lf"].values, idx.strftime("%Y-%m-%d").values), 0, 1)
    return pred


def main():
    elia = load_elia()
    lagged = load_lagged()
    runs = load_runs()
    all_models = GLOBAL + REGIONAL
    print(f"Elia rows {len(elia)}, lagged hours {len(lagged[1])}, exact-run hours {len(runs)} "
          f"({runs.index.min():%Y-%m-%d} .. {runs.index.max():%Y-%m-%d})")

    def table(hourly, models, lo, hi):
        f = features(hourly, models)
        f = f[(f.index >= pd.Timestamp(lo, tz="UTC")) & (f.index < pd.Timestamp(hi, tz="UTC") + pd.Timedelta(days=1))]
        return f.join(elia[["lf", "elia", "elia10", "elia90", "monitoredcapacity"]], how="inner")

    tr1_six = table(lagged[1], all_models, *TRAIN)
    tr1 = table(lagged[1], GLOBAL, *TRAIN)
    tr2 = table(lagged[2], GLOBAL, *TRAIN)
    te_run_six = table(runs, all_models, *TEST)
    te_run = table(runs, GLOBAL, *TEST)
    te1 = table(lagged[1], GLOBAL, *TEST)
    te2 = table(lagged[2], GLOBAL, *TEST)
    te1_six = table(lagged[1], all_models, *TEST)
    # added after the first test run, declared before fetching: regional models from their 03Z run
    runs03 = load_runs("runs03") if glob.glob(f"{NWP}/runs03_*.json") else None
    te_mix_six = None
    if runs03 is not None:
        mixed = runs.copy()
        for col in runs03.columns:
            if col[1] in REGIONAL:
                mixed[col] = runs03[col].reindex(mixed.index).combine_first(mixed[col])
        te_mix_six = table(mixed, all_models, *TEST)
        print(f"03Z regional runs loaded for {len(set((runs03.index - pd.Timedelta(minutes=1)).floor('D')))} days")

    # ---- step 1: choose the variant inside the training period only (fit <= VAL_SPLIT, validate after it)
    split = pd.Timestamp(VAL_SPLIT, tz="UTC")
    early, late = tr1_six[tr1_six.index < split], tr1_six[tr1_six.index >= split]
    vidx = late.index[late[["lf", "elia"]].notna().all(axis=1).values]
    vy = late.loc[vidx, "lf"].values
    vday = vidx.strftime("%Y-%m-%d").values
    v_elia = rmse(late.loc[vidx, "elia"].values, vy)
    print(f"\nStep 1. Variant choice on {VAL_SPLIT} .. {TRAIN[1]} (inside the training period, ~24 h lagged features, "
          f"{len(set(vday))} days). Elia RMSE there: {100 * v_elia:.2f}%")
    scores = {}
    for name in VARIANTS:
        scores[name] = rmse(predict_variant(early, late, vidx, name), vy)
        print(f"   {name:24s} RMSE {100 * scores[name]:.3f}%   ratio to Elia {scores[name] / v_elia:.3f} (features fresher than in real time)")
    tuned_name = min(scores, key=scores.get)
    print(f"   chosen: {tuned_name}")

    # ---- step 2: one evaluation on the test window
    idx = te_run_six.index
    for t in (te_run, te1, te2, te1_six):
        idx = idx.intersection(t.index)
    ok = te_run_six.loc[idx, ["lf", "elia"]].notna().all(axis=1)
    have_run = pd.Series((idx.floor("D")).isin(sorted(set((runs.index - pd.Timedelta(minutes=1)).floor("D")))), index=idx)
    idx = idx[ok.values & have_run.values]
    ref = te_run_six.loc[idx]
    day = ref.index.strftime("%Y-%m-%d").values
    sun = (ref.cs > 5).values
    y = ref.lf.values
    elia_err = ref.elia.values - y
    print(f"\nStep 2. Test: {len(idx)} quarter-hours on {len(set(day))} days ({idx.min():%Y-%m-%d} .. {idx.max():%Y-%m-%d}), "
          f"mean capacity {ref.monitoredcapacity.mean():,.0f} MW, mean load factor {100 * ref.lf.mean():.1f}%")
    rows = []

    def report(label, pred):
        err = pred - y
        lo, hi, p105, p100 = ratio_ci(err, elia_err, day)
        rows.append(dict(model=label, rmse_pct=100 * rmse(pred, y), daylight_rmse_pct=100 * rmse(pred[sun], y[sun]),
                         mae_pct=100 * float(np.mean(np.abs(err))), bias_pct=100 * float(err.mean()),
                         ratio=rmse(pred, y) / rmse(ref.elia.values, y), ci90=f"{lo:.2f}-{hi:.2f}", p_within_5pct=p105, p_better=p100))

    stacked = pd.concat([tr1, tr2])
    primary = predict_variant(tr1_six, te_run_six, idx, "A load factor")
    report("PRIMARY six models, 00Z run of D-1", primary)
    tuned = predict_variant(tr1_six, te_run_six, idx, tuned_name)
    report(f"TUNED ({tuned_name})", tuned)
    extra = []
    if te_mix_six is not None:
        primary03 = predict_variant(tr1_six, te_mix_six, idx, "A load factor")
        tuned03 = predict_variant(tr1_six, te_mix_six, idx, tuned_name)
        report("PRIMARY, regional models from 03Z run", primary03)
        report("TUNED, regional models from 03Z run", tuned03)
        extra = [("PRIMARY 03Z", primary03), ("TUNED 03Z", tuned03)]
    report("four global models, 00Z run of D-1", predict_variant(stacked, te_run, idx, "A load factor"))
    report("four global, lag ~24h (too fresh)", predict_variant(stacked, te1, idx, "A load factor"))
    report("four global, lag ~48h (too stale)", predict_variant(stacked, te2, idx, "A load factor"))
    report("six models, lag ~24h (too fresh)", predict_variant(tr1_six, te1_six, idx, "A load factor"))
    tr = tr1_six[(tr1_six.cs > 5) & tr1_six.lf.notna() & tr1_six.ghi_mean.notna()]
    slope = float((tr.ghi_mean * tr.lf).sum() / (tr.ghi_mean ** 2).sum())
    report("one-coefficient linear, 00Z run of D-1", np.clip(np.nan_to_num(ref.ghi_mean.values * slope), 0, 1))
    report("50/50 blend of TUNED and Elia", 0.5 * tuned + 0.5 * ref.elia.values)
    rows.append(dict(model="Elia day-ahead 11:00 (reference)", rmse_pct=100 * rmse(ref.elia.values, y),
                     daylight_rmse_pct=100 * rmse(ref.elia.values[sun], y[sun]), mae_pct=100 * float(np.mean(np.abs(elia_err))),
                     bias_pct=100 * float(elia_err.mean()), ratio=1.0, ci90="", p_within_5pct=np.nan, p_better=np.nan))
    pers = elia.lf.shift(96).reindex(idx).values
    good = ~np.isnan(pers)
    rows.append(dict(model="persistence (same time yesterday)", rmse_pct=100 * rmse(pers[good], y[good]), daylight_rmse_pct=np.nan,
                     mae_pct=100 * float(np.mean(np.abs(pers[good] - y[good]))), bias_pct=100 * float(np.mean(pers[good] - y[good])),
                     ratio=rmse(pers[good], y[good]) / rmse(ref.elia.values[good], y[good]), ci90="", p_within_5pct=np.nan, p_better=np.nan))
    print("RMSE in % of monitored capacity, all quarter-hours; ratio = ours / Elia on identical timestamps")
    print("p_within_5pct = bootstrap share of resamples with ratio <= 1.05; p_better = share with ratio <= 1.00")
    print(pd.DataFrame(rows).round(3).to_string(index=False))

    # Predico ranks by the monthly mean of daily scores, in MW, over every quarter-hour of the day
    cap_mw = ref.monitoredcapacity.values

    def daily(values):
        return pd.Series(values, index=day).groupby(level=0).mean()

    d_elia = np.sqrt(daily((elia_err * cap_mw) ** 2))
    print("\nPredico's own deterministic metric: mean of daily RMSE in MW, and days won against Elia")
    for label, pred in [("PRIMARY", primary), ("TUNED", tuned)] + extra:
        d = np.sqrt(daily(((pred - y) * cap_mw) ** 2))
        lo_r, hi_r, p_b = daily_ratio_ci(d.values, d_elia.values)
        print(f"   {label:11s} mean daily RMSE {d.mean():.0f} MW, Elia {d_elia.mean():.0f} MW, ratio {d.mean() / d_elia.mean():.3f} "
              f"(90% interval {lo_r:.2f}-{hi_r:.2f}, P(better) {p_b:.2f}); better than Elia on {int((d < d_elia).sum())} of {len(d)} days")

    month = ref.index.strftime("%Y-%m").values
    m = pd.DataFrame({"month": month, "primary": (primary - y) ** 2, "tuned": (tuned - y) ** 2, "elia": elia_err ** 2}).groupby("month").mean() ** 0.5 * 100
    m["tuned_ratio"] = m.tuned / m.elia
    print("\nBy month (RMSE % of capacity):")
    print(m.round(2).to_string())
    print(f"\ncorrelation of TUNED and Elia errors in daylight: {float(np.corrcoef((tuned - y)[sun], elia_err[sun])[0, 1]):.2f}")

    # ---- P10-P90 band, daylight only, Winkler score with alpha = 0.2; widened by a conformal step fitted on the validation part
    def quantile_pair(train, test, ix):
        lit = (test.loc[ix, "cs"] > 5).values
        x = test.loc[ix, feature_cols(train)][lit]
        cs = test.loc[ix, "cs"].values[lit]
        lo, hi = np.zeros(len(ix)), np.zeros(len(ix))
        lo[lit] = fit_predict(train, x, cs, loss="quantile", quantile=0.1, light=True)
        hi[lit] = fit_predict(train, x, cs, loss="quantile", quantile=0.9, light=True)
        return lo, np.maximum(hi, lo), lit

    vlo, vhi, vlit = quantile_pair(early, late, vidx)
    vcs = late.loc[vidx, "cs"].values
    miss = np.maximum(vlo - vy, vy - vhi)[vlit] / (vcs[vlit] / 1000)
    widen = float(np.quantile(miss, 0.8))
    lo, hi, _ = quantile_pair(tr1_six, te_run_six, idx)
    lo_c = np.clip(lo - widen * ref.cs.values / 1000, 0, 1)
    hi_c = np.clip(hi + widen * ref.cs.values / 1000, 0, 1)

    def band(lo, hi, label):
        ok = sun & ~np.isnan(lo) & ~np.isnan(hi)
        inside = float(((y[ok] >= lo[ok]) & (y[ok] <= hi[ok])).mean())
        w = (hi[ok] - lo[ok]) + 10 * np.clip(lo[ok] - y[ok], 0, None) + 10 * np.clip(y[ok] - hi[ok], 0, None)
        print(f"   {label:34s} coverage {100 * inside:.1f}% (nominal 80%), mean width {100 * float((hi[ok] - lo[ok]).mean()):.1f}%, "
              f"Winkler {100 * float(w.mean()):.2f}% of capacity")
    print("\nP10-P90 band, daylight quarter-hours:")
    band(lo, hi, "ours, raw quantile models")
    band(lo_c, hi_c, "ours, conformally widened")
    band(ref.elia10.values, ref.elia90.values, "Elia day-ahead 11:00")

    def winkler_mw(lo, hi):
        w = (hi - lo) + 10 * np.clip(lo - y, 0, None) + 10 * np.clip(y - hi, 0, None)
        return daily(w * cap_mw)

    have = ~np.isnan(ref.elia10.values) & ~np.isnan(ref.elia90.values)
    print("\nPredico's own probabilistic metric: mean of daily mean Winkler score in MW (all quarter-hours)")
    w_elia = winkler_mw(np.nan_to_num(ref.elia10.values), np.nan_to_num(ref.elia90.values))
    bands = [("raw", lo, hi), ("widened", lo_c, hi_c)]
    if te_mix_six is not None:
        lo3, hi3, _ = quantile_pair(tr1_six, te_mix_six, idx)
        bands.append(("widened, 03Z", np.clip(lo3 - widen * ref.cs.values / 1000, 0, 1), np.clip(hi3 + widen * ref.cs.values / 1000, 0, 1)))
    for label, a, b in bands:
        w = winkler_mw(a, b)
        lo_r, hi_r, p_b = daily_ratio_ci(w.values, w_elia.values)
        print(f"   ours {label:13s} {w.mean():.0f} MW, Elia {w_elia.mean():.0f} MW, ratio {w.mean() / w_elia.mean():.3f} "
              f"(90% interval {lo_r:.2f}-{hi_r:.2f}, P(better) {p_b:.2f}); better than Elia on {int((w < w_elia).sum())} of {len(w)} days")
    print(f"   Elia band missing on {int((~have).sum())} quarter-hours")


if __name__ == "__main__":
    main()
