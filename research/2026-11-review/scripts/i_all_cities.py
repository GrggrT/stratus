"""Generalisation check on ALL cities (two weeks): taker/maker P&L anatomy and naive-quoter adverse selection,
split into the three liquid cities (nyc, london, paris) versus every other city."""
import sys, os, re, json, gzip, math
import numpy as np, pandas as pd
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
evs = json.load(open(os.path.join(BASE, "data/events_all.json")))
TOP = {"nyc", "london", "paris"}
rows = []
for slug, e in evs.items():
    p = os.path.join(BASE, f"data/trades/{slug}.json.gz")
    if not os.path.exists(p): continue
    tr = json.load(gzip.open(p, "rt"))
    if not tr: continue
    tok = {}; ok = True
    for m in e["markets"]:
        if not m["tokens"] or not m["outcomePrices"]: ok = False; break
        op = json.loads(m["outcomePrices"])
        if op[0] not in ("0", "1"): ok = False; break
        tok[m["tokens"][0]] = (True, op[0] == "1", m["title"]); tok[m["tokens"][1]] = (False, op[0] == "1", m["title"])
    if not ok or sum(1 for v in tok.values() if v[0] and v[1]) != 1: continue
    t = pd.DataFrame(tr, columns=["ts", "token", "side", "price", "size", "wallet", "tx"])
    info = t["token"].map(tok); t = t[info.notna()].copy(); info = info[info.notna()]
    if t.empty: continue
    t["is_yes"] = [i[0] for i in info]; t["won"] = [i[1] for i in info]; t["title"] = [i[2] for i in info]
    q = re.search(r"date=(\d{4})-(\d{2})-(\d{2})T(\d{2})", e.get("resolutionSource") or "")
    closed = pd.Timestamp(e["closedTime"].replace(" ", "T").replace("+00", "+00:00")) if e.get("closedTime") else None
    if q: day0 = pd.Timestamp(f"{q.group(1)}-{q.group(2)}-{q.group(3)}T{q.group(4)}:00:00Z").timestamp() - 27 * 3600
    elif closed is not None: day0 = closed.timestamp() - 24.75 * 3600
    else: continue
    t["slug"] = slug; t["kind"] = e["kind"]; t["city"] = e["city"]; t["tier"] = "top3" if e["city"] in TOP else "other"
    t["h_rel"] = (t["ts"] - day0) / 3600.0
    t["fee_rate"] = (e["markets"][0].get("fees") or {}).get("rate", 0.0) or 0.0
    t["evol"] = float(e.get("volume") or 0)
    rows.append(t)
T = pd.concat(rows, ignore_index=True)
T["payoff"] = np.where(T["is_yes"], T["won"].astype(float), 1 - T["won"].astype(float))
T["sgn"] = np.where(T["side"] == "BUY", 1.0, -1.0); T["notional"] = T["price"] * T["size"]
T["gross"] = T["sgn"] * T["size"] * (T["payoff"] - T["price"]); T["fee"] = T["fee_rate"] * T["size"] * T["price"] * (1 - T["price"]); T["net"] = T["gross"] - T["fee"]
T["yes_price"] = np.where(T["is_yes"], T["price"], 1 - T["price"])
T["taker_long_yes"] = ((T["side"] == "BUY") & T["is_yes"]) | ((T["side"] == "SELL") & ~T["is_yes"])
T["phase"] = pd.cut(T["h_rel"], [-1e9, -24, 0, 24, 1e9], labels=["D-2", "D-1", "D", "after"])
pd.set_option("display.width", 230); pd.set_option("display.float_format", lambda x: f"{x:,.1f}")
print("events:", T.slug.nunique(), "| cities:", T.city.nunique(), "| trades:", len(T), "| notional $", round(T.notional.sum()), "| taker wallets:", T.wallet.nunique())
def clus(g):
    e = g.groupby("slug").agg(p=("net", "sum"), n=("notional", "sum")); k = len(e)
    r = e["p"].sum() / e["n"].sum(); res = e["p"] - r * e["n"]
    se = math.sqrt(k / max(k - 1, 1) * (res ** 2).sum()) / e["n"].sum() if k > 1 else float("nan")
    return pd.Series(dict(events=k, trades=len(g), notional=g["notional"].sum(), gross=g["gross"].sum(), fees=g["fee"].sum(), net=g["net"].sum(), net_bps=1e4 * r, se_bps=1e4 * se, t=r / se if se else float("nan"), gross_per_event=g["gross"].sum() / k))
print("\n== taker P&L by tier and kind (maker gross = -taker gross) =="); print(T.groupby(["tier", "kind"]).apply(clus, include_groups=False).to_string())
print("\n== by tier, kind, phase =="); print(T.groupby(["tier", "kind", "phase"], observed=True).apply(clus, include_groups=False).to_string())
ev = T.drop_duplicates("slug")
print("\nmedian event volume by tier/kind:"); print(ev.groupby(["tier", "kind"])["evol"].describe(percentiles=[.5]).to_string())
# naive follower replay, pre-event-day only
Q = 20.0; LAG = 120; WIN = 1800; COOL = 300
def replay(delta):
    out = []
    for (slug, title), g in T[T.h_rel < 0].groupby(["slug", "title"], sort=False):
        g = g.sort_values("ts"); ts = g["ts"].values.astype("int64"); px = g["yes_price"].values; sz = g["size"].values; lng = g["taker_long_yes"].values
        won = float(g["won"].iloc[0]); n = len(g); lo = hi = 0
        cpv = np.concatenate([[0.0], np.cumsum(px * sz)]); cv = np.concatenate([[0.0], np.cumsum(sz)]); last = {True: -10**12, False: -10**12}
        for i in range(n):
            t = ts[i]
            while hi < n and ts[hi] <= t - LAG: hi += 1
            while lo < hi and ts[lo] < t - WIN: lo += 1
            if hi > lo: f = (cpv[hi] - cpv[lo]) / (cv[hi] - cv[lo])
            elif hi > 0 and t - ts[hi - 1] <= 6 * 3600: f = px[hi - 1]
            else: continue
            if not (0.05 <= f <= 0.95): continue
            bid = math.floor((f - delta) * 100 + 1e-9) / 100; ask = math.ceil((f + delta) * 100 - 1e-9) / 100
            if lng[i] and px[i] >= ask and t - last[True] >= COOL and ask < 1:
                q = min(Q, sz[i]); last[True] = t; out.append((slug, q, q * (ask - won)))
            elif (not lng[i]) and px[i] <= bid and t - last[False] >= COOL and bid > 0:
                q = min(Q, sz[i]); last[False] = t; out.append((slug, q, q * (won - bid)))
    return pd.DataFrame(out, columns=["slug", "shares", "pnl"])
meta = ev.set_index("slug")[["tier", "kind"]]
pd.set_option("display.float_format", lambda x: f"{x:,.2f}")
for delta in (0.02, 0.03):
    F = replay(delta); per = F.groupby("slug").agg(pnl=("pnl", "sum"), fills=("pnl", "size"), shares=("shares", "sum")).reindex(meta.index).fillna(0.0).join(meta)
    print(f"\n== naive follower replay, pre-event-day only, delta={delta}, 20 shares: per-event adverse selection by tier/kind ==")
    print(per.groupby(["tier", "kind"]).agg(events=("pnl", "size"), pnl_per_event=("pnl", "mean"), se=("pnl", lambda x: x.std(ddof=1) / math.sqrt(len(x))), fills_per_event=("fills", "mean"), shares_per_event=("shares", "mean")).to_string())
