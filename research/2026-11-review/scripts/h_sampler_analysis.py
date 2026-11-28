import json, glob, re, math, collections
import pandas as pd, numpy as np
rows = []
files = sorted(glob.glob("data/sampler/*.json"))
for f in files:
    ts = f.split("/")[-1][:-5]
    for o in json.load(open(f)):
        o["ts"] = ts; rows.append(o)
D = pd.DataFrame(rows)
print("samples:", len(files), "from", files[0][-20:-5], "to", files[-1][-20:-5], "| market-samples:", len(D))
def parse(slug):
    m = re.match(r"(highest|lowest)-temperature-in-(.+)-on-([a-z]+)-(\d+)-(\d{4})", slug)
    return (m.group(1), m.group(2), int(m.group(4))) if m else ("?", "?", 0)
D[["kind", "city", "day"]] = [parse(s) for s in D["event"]]
N = 20.0
def share(r, d_c):
    v = r["v"]; S = ((v - d_c) / v) ** 2; qn = S * max(N, r["min_size"])
    if r["mid"] is None or (isinstance(r["mid"], float) and math.isnan(r["mid"])):
        return 1.0, 1.0, qn      # no qualifying two-sided book: by the documented formula a lone two-sided quoter takes the sample
    q1, q2 = r["q1"], r["q2"]
    if 0.10 <= r["mid"] <= 0.90:
        central = min(q1, q2) + abs(q1 - q2) / 3.0; upper = q1 + q2
    else:
        central = min(q1, q2); upper = min(q1, q2)
    return qn / (qn + central), qn / (qn + upper), qn
for d_c in (1.0, 2.0, 3.0):
    res = D.apply(lambda r: share(r, d_c), axis=1, result_type="expand"); D[f"sc{d_c}"] = res[0]; D[f"su{d_c}"] = res[1]
D["has_mid"] = D["mid"].notna()
per = D.groupby("ts").apply(lambda g: pd.Series(dict(markets=len(g), rate=g["rate"].sum(), no_mid=int((~g["has_mid"]).sum()), zeroQ=int(((g.q1 + g.q2) == 0).sum()),
        rate_noQ=g.loc[(g.q1 + g.q2) == 0, "rate"].sum(),
        est_c2=(g["rate"] * g["sc2.0"]).sum(), est_u2=(g["rate"] * g["su2.0"]).sum(),
        est_c2_mid_only=(g.loc[g.has_mid, "rate"] * g.loc[g.has_mid, "sc2.0"]).sum(), est_u2_mid_only=(g.loc[g.has_mid, "rate"] * g.loc[g.has_mid, "su2.0"]).sum())), include_groups=False)
pd.set_option("display.width", 220); pd.set_option("display.float_format", lambda x: f"{x:,.1f}")
print(per.to_string())
print("\nmean over samples: pool $/day", round(per.rate.mean()), "| markets", round(per.markets.mean()), "| with no qualifying order at all", round(per.zeroQ.mean()), f"({100 * per.zeroQ.mean() / per.markets.mean():.0f}%)", "| $/day sitting in those", round(per.rate_noQ.mean()))
print("hypothetical 20-share two-sided quoter at 2c from mid in EVERY rewarded market: central", round(per.est_c2.mean()), "$/day-rate; conservative", round(per.est_u2.mean()), "| restricted to markets that already have a two-sided mid: central", round(per.est_c2_mid_only.mean()), "conservative", round(per.est_u2_mid_only.mean()))
m = D.groupby("slug").agg(rate=("rate", "mean"), sc=("sc2.0", "mean"), su=("su2.0", "mean"), has_mid=("has_mid", "mean"), kind=("kind", "first"), city=("city", "first"), day=("day", "first"), price=("price", "mean"), vol24=("vol24", "mean"), min_size=("min_size", "first"))
m["exp_c"] = m.rate * m.sc; m["exp_u"] = m.rate * m.su; m["cap"] = np.maximum(N, m["min_size"]) * 0.96
m = m.sort_values("exp_u", ascending=False)
for k in (25, 50, 100):
    t = m.head(k)
    print(f"top {k} markets by conservative expected reward: capital ${t.cap.sum():,.0f} | conservative ${t.exp_u.sum():,.0f}/day | central ${t.exp_c.sum():,.0f}/day | of which in markets w/o existing mid: ${t.loc[t.has_mid < 0.5, 'exp_u'].sum():,.0f}")
mm = m[m.has_mid > 0.5].sort_values("exp_u", ascending=False)
for k in (25, 50, 100):
    t = mm.head(k)
    print(f"[only markets with an existing two-sided mid] top {k}: capital ${t.cap.sum():,.0f} | conservative ${t.exp_u.sum():,.0f}/day | central ${t.exp_c.sum():,.0f}/day")
print("\nby kind/day (mean per sample): pool, zero-Q share, conservative est for min-size quoter")
print(D.groupby(["kind", "day"]).apply(lambda g: pd.Series(dict(pool=g["rate"].sum() / len(files), markets=len(g) / len(files), zeroQ_share=((g.q1 + g.q2) == 0).mean(), est_u=(g["rate"] * g["su2.0"]).sum() / len(files), est_u_mid=(g.loc[g.has_mid, "rate"] * g.loc[g.has_mid, "su2.0"]).sum() / len(files))), include_groups=False).to_string())
ev = D.groupby(["event"]).apply(lambda g: pd.Series(dict(pool=g["rate"].sum() / len(files), est_u=(g["rate"] * g["su2.0"]).sum() / len(files), n=g["slug"].nunique())), include_groups=False)
print("\nper-event pool distribution ($/day):", ev.pool.describe(percentiles=[.1, .5, .9]).round(1).to_dict())
print("examples for the three STRATUS cities:"); print(ev[ev.index.str.contains("-in-(nyc|london|paris)-on-")].round(1).to_string())
