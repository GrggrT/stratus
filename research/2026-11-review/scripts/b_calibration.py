import sys, os; sys.path.insert(0, os.path.dirname(__file__))
from lib import *
T = pd.read_pickle(os.path.join(BASE, "data/trades_all.pkl"))
# Express every trade as a YES-equivalent price for the bucket: YES token price p; NO token price q -> yes price 1-q
T["yes_price"] = np.where(T["is_yes"], T["price"], 1 - T["price"])
T["yes_out"] = T["won"].astype(float)
pd.set_option("display.width", 220); pd.set_option("display.float_format", lambda x: f"{x:,.4f}")
bands = [0, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.98, 1.0]
def calib(df, label, cluster="slug"):
    df = df.copy(); df["band"] = pd.cut(df["yes_price"], bands)
    rows = []
    for b, g in df.groupby("band", observed=True):
        # size-weighted and equal-weighted; cluster-robust SE by event (bucket outcomes inside an event are dependent)
        ev = g.groupby(cluster).apply(lambda x: pd.Series(dict(w=x["size"].sum(), d=((x["yes_out"] - x["yes_price"]) * x["size"]).sum(), n=len(x))), include_groups=False)
        mean_d = ev["d"].sum() / ev["w"].sum()
        # cluster-robust se of ratio estimator
        k = len(ev); res = ev["d"] - mean_d * ev["w"]
        se = math.sqrt(k / max(k - 1, 1) * (res ** 2).sum()) / ev["w"].sum() if k > 1 else float("nan")
        rows.append(dict(band=str(b), trades=len(g), events=k, shares=g["size"].sum(), avg_price=np.average(g["yes_price"], weights=g["size"]),
                         win_rate=np.average(g["yes_out"], weights=g["size"]), diff=mean_d, se=se, z=mean_d / se if se and se > 0 else float("nan")))
    print(f"\n== YES-equivalent price calibration: {label} (size-weighted; SE clustered by event) ==")
    print(pd.DataFrame(rows).to_string(index=False))
calib(T, "all trades")
calib(T[T.kind == "highest"], "highest")
calib(T[T.kind == "lowest"], "lowest")
calib(T[(T.h_rel < 0)], "before event day (h_rel<0)")
calib(T[(T.h_rel >= 0) & (T.h_rel < 24)], "during event day")
# time-resolved market log-loss of the winner: last trade yes price per bucket per hour
