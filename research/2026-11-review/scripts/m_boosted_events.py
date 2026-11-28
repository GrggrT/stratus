"""Who already sits in the temperature reward pools? Live book samples (reward_sampler2.py) grouped by event pool size."""
import json, glob, numpy as np, pandas as pd
files = sorted(glob.glob("data/sampler2/*.json")); rows = []
for f in files:
    for o in json.load(open(f)): o["ts"] = f[-20:-5]; rows.append(o)
D = pd.DataFrame(rows); N = 20.0; d = 2.0
def sh(r):
    v = r["v"]; qn = ((v - d) / v) ** 2 * max(N, r["min_size"])
    if pd.isna(r["mid"]): return np.nan
    return qn / (qn + r.q1 + r.q2)
D["share_cons"] = D.apply(sh, axis=1)
print("samples:", D.ts.nunique(), D.ts.min(), "→", D.ts.max(), "| pool mean $/day:", round(D.groupby("ts").rate.sum().mean()), "| markets:", round(D.groupby("ts").size().mean()),
      "| markets with no order inside the band:", round(D.groupby("ts").apply(lambda g: ((g.q1 + g.q2) == 0).sum(), include_groups=False).mean()))
ev = D.groupby(["event", "ts"]).rate.sum().groupby("event").mean()
m = D.groupby("slug").agg(event=("event", "first"), rate=("rate", "mean"), share_cons=("share_cons", "mean"), q1=("q1", "mean"), q2=("q2", "mean"), has_mid=("mid", lambda x: x.notna().mean()))
m["evpool"] = m.event.map(ev); m["exp_cons"] = m.rate * m.share_cons.fillna(0); m["qtot"] = m.q1 + m.q2
m["tier"] = pd.cut(m.evpool, [0, 60, 110, 200, 600], labels=["event pool ≤ $60", "$60–110", "$110–200", "$400–500"])
pd.set_option("display.width", 220); pd.set_option("display.float_format", lambda x: f"{x:,.1f}")
print("\nper-event pool:", ev.describe(percentiles=[.1, .5, .9]).round(0).to_dict())
print("\nby event-pool tier:")
print(m.groupby("tier", observed=True).apply(lambda g: pd.Series(dict(markets=len(g), events=g.event.nunique(), pool=g.rate.sum(), with_two_sided_book=(g.has_mid > 0.5).mean(), median_in_band_score=g.qtot.median(), p90_in_band_score=g.qtot.quantile(.9), markets_score_gt_1000=int((g.qtot > 1000).sum()), min_size_quote_formula_usd=g.exp_cons.sum(), share_pct=100 * g.exp_cons.sum() / g.rate.sum())), include_groups=False).to_string())
big = m[m.evpool >= 400].sort_values("rate", ascending=False)
big = big.assign(event=big.event.str.replace("highest-temperature-in-", "").str.replace("-2026", ""))
print("\nboosted events, per rewarded market (score = formula score of all resting orders inside the band):"); print(big[["event", "rate", "qtot", "share_cons", "exp_cons"]].head(30).to_string())
