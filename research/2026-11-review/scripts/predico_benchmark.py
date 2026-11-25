"""How good is the benchmark a Predico entrant must match? Elia's own Belgian PV forecasts versus measurements (ods032)."""
import pandas as pd, numpy as np
D = pd.read_csv("data/predico/ods032_be.csv", parse_dates=["datetime"]).dropna(subset=["measured", "monitoredcapacity"]).sort_values("datetime")
D = D[D.monitoredcapacity > 0].copy()
last = D[D.datetime >= D.datetime.max() - pd.Timedelta(days=365)].copy()
print("rows:", len(D), "| span:", D.datetime.min(), "→", D.datetime.max(), "| capacity MW first/last:", round(D.monitoredcapacity.iloc[0]), round(D.monitoredcapacity.iloc[-1]))
def rmse(a, b): return float(np.sqrt(np.nanmean((a - b) ** 2)))
def mae(a, b): return float(np.nanmean(np.abs(a - b)))
def block(x, label):
    cap = x.monitoredcapacity.mean(); day = x[x.measured > 0.005 * x.monitoredcapacity]
    print(f"\n== {label}: {len(x)} quarter-hours, mean capacity {cap:,.0f} MW, mean generation {x.measured.mean():,.0f} MW")
    rows = []
    for name, col in [("week-ahead", "weekaheadforecast"), ("day-ahead 11:00", "dayahead11hforecast"), ("day-ahead (later run)", "dayaheadforecast"), ("most recent (intraday)", "mostrecentforecast")]:
        if col not in x or x[col].notna().sum() == 0: continue
        r = rmse(x[col], x.measured); rd = rmse(day[col], day.measured)
        rows.append(dict(forecast=name, rmse_mw=r, rmse_pct_cap=100 * r / cap, rmse_daylight_pct_cap=100 * rd / cap, mae_pct_cap=100 * mae(x[col], x.measured) / cap, bias_mw=float(np.nanmean(x[col] - x.measured))))
    pers = x.set_index("datetime").measured.shift(96, freq=None)   # same quarter-hour, previous day
    xp = x.copy(); xp["pers"] = x.measured.shift(96).values
    r = rmse(xp.pers, xp.measured); rows.append(dict(forecast="persistence (yesterday)", rmse_mw=r, rmse_pct_cap=100 * r / cap, rmse_daylight_pct_cap=np.nan, mae_pct_cap=100 * mae(xp.pers, xp.measured) / cap, bias_mw=float(np.nanmean(xp.pers - xp.measured))))
    print(pd.DataFrame(rows).round(2).to_string(index=False))
    for tag, lo, hi in [("day-ahead 11:00", "dayahead11hconfidence10", "dayahead11hconfidence90"), ("day-ahead (later run)", "dayaheadconfidence10", "dayaheadconfidence90")]:
        if lo in day and day[lo].notna().sum():
            inside = ((day.measured >= day[lo]) & (day.measured <= day[hi])).mean(); width = (day[hi] - day[lo]).mean()
            a = 0.2; w = (day[hi] - day[lo]) + (2 / a) * np.clip(day[lo] - day.measured, 0, None) + (2 / a) * np.clip(day.measured - day[hi], 0, None)
            print(f"   {tag} P10–P90 band, daylight only: coverage {100 * inside:.1f}% (nominal 80%), mean width {100 * width / cap:.1f}% of capacity, mean Winkler score {100 * w.mean() / cap:.1f}% of capacity")
block(last, "last 12 months")
last["month"] = last.datetime.dt.to_period("M")
m = last.groupby("month").apply(lambda g: pd.Series(dict(da11_rmse_pct=100 * rmse(g.dayahead11hforecast, g.measured) / g.monitoredcapacity.mean(), recent_rmse_pct=100 * rmse(g.mostrecentforecast, g.measured) / g.monitoredcapacity.mean(), pers_rmse_pct=100 * rmse(g.measured.shift(96), g.measured) / g.monitoredcapacity.mean())), include_groups=False)
print("\nmonthly RMSE as % of capacity (day-ahead 11:00 / most recent / persistence):"); print(m.round(2).to_string())
