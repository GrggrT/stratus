"""For a set of reward events, read live books and compute the documented reward score of existing quotes and the
formula-implied share of one extra two-sided quote of the minimum qualifying size, per dollar of collateral."""
import json, sys, requests, math, time, collections
import pandas as pd, numpy as np
S = requests.Session()
D = pd.read_pickle("data/platform/rewards_df.pkl")
raw = {r["market_slug"]: r for r in json.load(open("data/platform/rewards_all.json"))}
def books(tokens):
    res = {}
    for i in range(0, len(tokens), 50):
        for a in range(3):
            try:
                r = S.post("https://clob.polymarket.com/books", json=[{"token_id": t} for t in tokens[i:i + 50]], timeout=60)
                if r.status_code == 200:
                    for b in r.json(): res[b["asset_id"]] = b
                    break
            except Exception: pass
            time.sleep(2)
        time.sleep(0.1)
    return res
def analyse(book, v_c, min_size, d_c):
    bids = sorted([(float(x["price"]), float(x["size"])) for x in book.get("bids", [])], reverse=True)
    asks = sorted([(float(x["price"]), float(x["size"])) for x in book.get("asks", [])])
    def adj(levels):
        cum = 0.0
        for p, s in levels:
            cum += s
            if cum >= min_size: return p
        return None
    bb, ba = adj(bids), adj(asks)
    if bb is None or ba is None: return None
    mid = (bb + ba) / 2; v = v_c / 100.0
    def q(levels, sign, only_big):
        tot = 0.0; usd = 0.0
        for p, s in levels:
            dist = (mid - p) if sign > 0 else (p - mid)
            if 0 <= dist < v and (s >= min_size or not only_big):
                tot += ((v - dist) / v) ** 2 * s; usd += s * (p if sign > 0 else 1 - p)
        return tot, usd
    q1, u1 = q(bids, +1, False); q2, u2 = q(asks, -1, False)
    q1b, _ = q(bids, +1, True); q2b, _ = q(asks, -1, True)
    half = (ba - bb) / 2
    d = max(d_c / 100.0, 0.0)
    qn = ((v - d) / v) ** 2 * min_size
    two = 0.10 <= mid <= 0.90
    oth = (min(q1, q2) + abs(q1 - q2) / 3) if two else min(q1, q2)
    othb = (min(q1b, q2b) + abs(q1b - q2b) / 3) if two else min(q1b, q2b)
    return dict(mid=mid, half_spread=half, q1=q1, q2=q2, usd_in_band=u1 + u2, share=qn / (qn + oth), share_bigonly=qn / (qn + othb), share_cons=qn / (qn + q1 + q2))
def run(label, sel, d_c):
    sel = sel.copy(); toks = [raw[s]["tokens"][0]["token_id"] for s in sel.slug]
    bk = books(toks); out = []
    for s, t in zip(sel.slug, toks):
        r = raw[s]; b = bk.get(t)
        if b is None: continue
        a = analyse(b, r["rewards_max_spread"], r["rewards_min_size"], d_c)
        if a is None: continue
        rt = sum(c.get("rate_per_day", 0) for c in r["rewards_config"])
        out.append(dict(slug=s, rate=rt, min_size=r["rewards_min_size"], comp=r["market_competitiveness"], cap=r["rewards_min_size"] * (1 - 2 * d_c / 100), **a))
    o = pd.DataFrame(out)
    if o.empty: print(label, "no books"); return o
    o["exp"] = o.rate * o.share; o["exp_cons"] = o.rate * o.share_cons
    print(f"\n=== {label}: markets {len(o)} | pool ${o.rate.sum():,.0f}/day | resting $ within band ${o.usd_in_band.sum():,.0f} | pool per $1k resting ${1000 * o.rate.sum() / max(o.usd_in_band.sum(), 1):,.1f}/day")
    print(f"    median half-spread {100 * o.half_spread.median():.2f}c | median comp {o.comp.median():.1f} | median book Q (both sides) {(o.q1 + o.q2).median():,.0f} | median comp/Q {(o.comp / (o.q1 + o.q2).replace(0, np.nan)).median():.3f}")
    print(f"    one min-size two-sided quote at {d_c}c in EVERY market: collateral ${o.cap.sum():,.0f} | formula reward central ${o.exp.sum():,.0f}/day, conservative ${o.exp_cons.sum():,.0f}/day | per $1k collateral: central ${1000 * o.exp.sum() / o.cap.sum():,.0f}, conservative ${1000 * o.exp_cons.sum() / o.cap.sum():,.0f}")
    return o
gpu = D[D.event.str.contains("average-rental-price", na=False)]
o1 = run("GPU rental-price index markets (Ornn), quote at 1c", gpu, 1.0)
temp = D[D.cat == "daily-temperature"]
o2 = run("daily temperature, quote at 2c", temp, 2.0)
for cat in ["elections", "politics", "sports", "economy", "pop-culture", "crypto", "geopolitics", "stocks"]:
    sel = D[(D.cat == cat)].sort_values("rate", ascending=False).head(250)
    run(f"{cat} (top 250 by rate), quote at 1c", sel, 1.0)
pd.set_option("display.width", 220); pd.set_option("display.float_format", lambda x: f"{x:,.2f}")
print("\nGPU detail (top by rate):"); print(o1.sort_values("rate", ascending=False).head(12)[["slug", "rate", "min_size", "comp", "mid", "half_spread", "q1", "q2", "usd_in_band", "share", "share_cons", "exp_cons"]].to_string())
