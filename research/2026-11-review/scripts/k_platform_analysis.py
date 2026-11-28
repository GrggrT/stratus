import json, re, collections, math, datetime as dt
import numpy as np, pandas as pd
act = json.load(open("data/platform/rewards_all.json")); tags = json.load(open("data/platform/tags.json"))
def rate(r): return sum(c.get("rate_per_day", 0) for c in r.get("rewards_config") or [])
rows = []
now = dt.datetime.now(dt.timezone.utc)
PRIO = ["daily-temperature", "weather", "sports", "esports", "crypto", "elections", "global-elections", "politics", "geopolitics", "middle-east", "world", "economy", "fed-rates", "ai", "big-tech", "tech", "stocks", "finance", "business", "pop-culture", "movies", "music", "celebrities", "science", "games"]
for r in act:
    tg = tags.get(r["condition_id"], [])
    prim = next((t for t in PRIO if t in tg), "untagged")
    try:
        end = dt.datetime.fromisoformat(r["end_date"].replace(" ", "T").replace("+00", "+00:00")) if r.get("end_date") else None
        days = (end - now).total_seconds() / 86400 if end else np.nan
    except Exception:
        days = np.nan
    px = r["tokens"][0]["price"] if r.get("tokens") else np.nan
    rows.append(dict(slug=r["market_slug"], event=r.get("event_slug"), q=r.get("question"), rate=rate(r), comp=r.get("market_competitiveness") or 0.0, spread=r.get("spread"), vol24=r.get("volume_24hr") or 0.0,
                     min_size=r.get("rewards_min_size"), max_spread=r.get("rewards_max_spread"), price=px, days=days, cat=prim, tags=",".join(tg)))
D = pd.DataFrame(rows)
D.to_pickle("data/platform/rewards_df.pkl")
pd.set_option("display.width", 250); pd.set_option("display.float_format", lambda x: f"{x:,.1f}"); pd.set_option("display.max_colwidth", 70)
print("snapshot UTC:", now.strftime("%Y-%m-%d %H:%M"), "| markets:", len(D), "| events:", D.event.nunique(), "| pool $/day:", round(D.rate.sum()), "| vol24 $:", round(D.vol24.sum()))
def summ(g):
    return pd.Series(dict(markets=len(g), events=g.event.nunique(), pool=g.rate.sum(), pool_pct=100 * g.rate.sum() / D.rate.sum(), vol24=g.vol24.sum(),
        pool_per_1k_vol=1000 * g.rate.sum() / max(g.vol24.sum(), 1), med_rate=g.rate.median(), comp0_mkts=100 * (g.comp == 0).mean(), comp0_pool=g.loc[g.comp == 0, "rate"].sum(),
        complt1_pool=g.loc[g.comp < 1, "rate"].sum(), med_comp=g.comp.median(), med_spread=g.spread.median(), med_days=g.days.median(), med_min_size=g.min_size.median(), med_maxspr=g.max_spread.median()))
print("\n== by primary category (priority-ordered tag) ==")
print(D.groupby("cat").apply(summ, include_groups=False).sort_values("pool", ascending=False).to_string())
print("\n== by time to end_date ==")
D["horizon"] = pd.cut(D.days, [-1e9, 0, 1, 3, 7, 30, 90, 365, 1e9], labels=["past end", "<1d", "1-3d", "3-7d", "7-30d", "30-90d", "90-365d", ">1y"])
print(D.groupby("horizon", observed=True).apply(summ, include_groups=False).to_string())
print("\n== by rate per market ==")
D["rb"] = pd.cut(D.rate, [0, 1, 5, 20, 50, 200, 1000, 1e9])
print(D.groupby("rb", observed=True).apply(summ, include_groups=False)[["markets", "pool", "pool_pct", "vol24", "comp0_mkts", "comp0_pool", "complt1_pool", "med_comp", "med_spread", "med_days", "med_min_size", "med_maxspr"]].to_string())
print("\n== top 25 events by pool ==")
ev = D.groupby("event").agg(pool=("rate", "sum"), markets=("rate", "size"), vol24=("vol24", "sum"), med_comp=("comp", "median"), comp0=("comp", lambda x: (x == 0).mean()), days=("days", "median"), cat=("cat", "first"), min_size=("min_size", "median"), maxspr=("max_spread", "median")).sort_values("pool", ascending=False)
print(ev.head(25).to_string())
print("\nevents:", len(ev), "| top-25 events hold", round(100 * ev.pool.head(25).sum() / ev.pool.sum(), 1), "% of pool | top-100:", round(100 * ev.pool.head(100).sum() / ev.pool.sum(), 1), "%")
# temperature now vs yesterday
T = D[D.cat == "daily-temperature"].copy()
T["day"] = T.event.str.extract(r"-on-[a-z]+-(\d+)-\d{4}$")[0]
print("\n== daily temperature by event day (now) ==")
print(T.groupby("day").agg(markets=("rate", "size"), events=("event", "nunique"), pool=("rate", "sum"), comp0=("comp", lambda x: (x == 0).mean()), med_comp=("comp", "median")).to_string())
