import sys, os; sys.path.insert(0, os.path.dirname(__file__))
from lib import *
ev = load_events(); ev = ev[ev["resolved"]]
evs_raw = json.load(open(os.path.join(BASE, "data/events_3city.json")))
fee_rate = {slug: (e["markets"][0].get("fees") or {}).get("rate", 0.0) for slug, e in evs_raw.items() if e}
T = pd.read_pickle(os.path.join(BASE, "data/trades_all.pkl"))
T["fee_rate"] = T["slug"].map(fee_rate).fillna(0.01)
T["fee"] = T["fee_rate"] * T["size"] * T["price"] * (1 - T["price"])
T["gross"] = T["sgn"] * T["size"] * (T["payoff"] - T["price"])
T["taker_pnl"] = T["gross"] - T["fee"]
start = ev.drop_duplicates("slug").set_index("slug")["start_ts"]; closed = ev.drop_duplicates("slug").set_index("slug")["closed_ts"]
T["age_h"] = (T["ts"] - T["slug"].map(start)).dt.total_seconds() / 3600
T["to_close_h"] = (T["slug"].map(closed) - T["ts"]).dt.total_seconds() / 3600
pd.set_option("display.width", 230); pd.set_option("display.float_format", lambda x: f"{x:,.1f}")
def clus(g):
    e = g.groupby("slug").agg(p=("taker_pnl", "sum"), n=("notional", "sum")); k = len(e)
    r = e["p"].sum() / e["n"].sum(); res = e["p"] - r * e["n"]
    se = math.sqrt(k / max(k - 1, 1) * (res ** 2).sum()) / e["n"].sum() if k > 1 else float("nan")
    return pd.Series(dict(trades=len(g), events=k, notional=g["notional"].sum(), gross=g["gross"].sum(), fees=g["fee"].sum(), net=g["taker_pnl"].sum(), net_bps=1e4 * r, se_bps=1e4 * se, t=r / se if se else float("nan")))
print("== TOTAL (fees from each market's stored feeSchedule) =="); print(clus(T).to_string())
print("\nsensitivity: taker net if weather coefficient had been 0.05 on fee-enabled markets:", round(T["gross"].sum() - 5 * T["fee"].sum()))
print("\n== by kind =="); print(T.groupby("kind").apply(clus, include_groups=False).to_string())
T["ageb"] = pd.cut(T["age_h"], [-1, 1, 2, 5, 12, 24, 36, 48, 60, 1e9], labels=["0-1h", "1-2h", "2-5h", "5-12h", "12-24h", "24-36h", "36-48h", "48-60h", ">60h"])
print("\n== taker P&L by MARKET AGE since listing, highest =="); print(T[T.kind == "highest"].groupby("ageb", observed=True).apply(clus, include_groups=False).to_string())
print("\n== taker P&L by MARKET AGE since listing, lowest =="); print(T[T.kind == "lowest"].groupby("ageb", observed=True).apply(clus, include_groups=False).to_string())
# STRATUS's own tradable window: market age >= 2h and 0 < endDate - now <= 48h
end = ev.drop_duplicates("slug").set_index("slug")["end"]; end = pd.to_datetime(end, utc=True, format="ISO8601")
T["to_end_h"] = (T["slug"].map(end) - T["ts"]).dt.total_seconds() / 3600
T["stratus_window"] = (T["age_h"] >= 2) & (T["to_end_h"] > 0) & (T["to_end_h"] <= 48)
print("\n== share of activity inside STRATUS's own allowed window (age>=2h, 0<endDate-now<=48h) ==")
print(T.groupby(["kind", "stratus_window"]).apply(clus, include_groups=False).to_string())
# wallet persistence: split events by date parity, correlate per-wallet returns
w = T.groupby("wallet").agg(n=("size", "size"), notional=("notional", "sum"), net=("taker_pnl", "sum"), events=("slug", "nunique"))
T["half"] = np.where(T["date"] < "2026-10-13", "H1", "H2")
ww = T.groupby(["wallet", "half"]).agg(net=("taker_pnl", "sum"), notional=("notional", "sum")).unstack()
ww.columns = [f"{a}_{b}" for a, b in ww.columns]; ww = ww.dropna()
act = ww[(ww.notional_H1 > 3000) & (ww.notional_H2 > 3000)].copy()
act["r1"] = act.net_H1 / act.notional_H1; act["r2"] = act.net_H2 / act.notional_H2
print("\n== persistence of taker skill: wallets with >$3k notional in each half (Sep1-Oct12 vs Oct13-Nov23) ==")
print("wallets:", len(act), "| Spearman corr of return on notional H1 vs H2:", round(act[["r1", "r2"]].corr(method="spearman").iloc[0, 1], 3))
top = act.sort_values("r1", ascending=False)
q = len(top) // 4
print("H1 top-quartile wallets -> H2 pooled return bps:", round(1e4 * top.head(q).net_H2.sum() / top.head(q).notional_H2.sum(), 1), "| H1 bottom-quartile -> H2 bps:", round(1e4 * top.tail(q).net_H2.sum() / top.tail(q).notional_H2.sum(), 1))
# style of the top-25 persistent winners
best = w[(w.events >= 40) & (w.notional > 10000)].sort_values("net", ascending=False).head(25).index
B = T[T.wallet.isin(best)]
print("\n== top-25 systematic taker winners (>=40 events, >$10k): pooled =="); print(clus(B).to_string())
B = B.copy(); B["phase"] = pd.cut(B["h_rel"], [-1e9, -24, 0, 6, 12, 18, 24, 1e9], labels=["<D-1", "D-1", "D 00-06", "D 06-12", "D 12-18", "D 18-24", "after"])
print(B.groupby(["kind", "phase"], observed=True).apply(clus, include_groups=False).to_string())
B["pb"] = pd.cut(B["price"], [0, 0.05, 0.2, 0.5, 0.8, 0.95, 1.0])
print(B.groupby(["side", "pb"], observed=True).apply(clus, include_groups=False).to_string())
print("\nper-wallet summary of top-12:")
for wl in list(best)[:12]:
    x = T[T.wallet == wl]
    ph = x.groupby(pd.cut(x["h_rel"], [-1e9, 0, 12, 24, 1e9], labels=["pre", "D am", "D pm", "after"]), observed=True)["notional"].sum()
    print(f"  {wl[:10]} net {x.taker_pnl.sum():8.0f} notional {x.notional.sum():9.0f} events {x.slug.nunique():3d} lows% {100 * x[x.kind == 'lowest'].notional.sum() / x.notional.sum():4.0f} buy% {100 * x[x.side == 'BUY'].notional.sum() / x.notional.sum():4.0f} avg_px {np.average(x.price, weights=x['size']):.2f} | notional share pre/am/pm/after: {[round(float(v) / x.notional.sum(), 2) for v in ph.values]}")
# CLV degeneracy: YES-equivalent last trade price before close-15min
T["yes_price"] = np.where(T["is_yes"], T["price"], 1 - T["price"])
rows = []
for slug, g in T.groupby("slug"):
    c = closed.get(slug)
    for title, gg in g.groupby("title"):
        won = bool(gg["won"].iloc[0])
        a = gg[gg.ts <= c - pd.Timedelta(minutes=15)]
        if a.empty: continue
        e0 = ev[(ev.slug == slug)].iloc[0]; d0, d1 = day_bounds_utc(e0.city, e0.date)
        b = gg[gg.ts <= pd.Timestamp(d0)]
        rows.append(dict(slug=slug, won=won, p_close15=a["yes_price"].iloc[-1], p_day_start=b["yes_price"].iloc[-1] if len(b) else np.nan, kind=e0.kind))
C = pd.DataFrame(rows)
pd.set_option("display.float_format", lambda x: f"{x:,.4f}")
print("\n== 'closing line' used by STRATUS (last price <= close-15min) vs outcome ==")
print(C.groupby("won")["p_close15"].describe(percentiles=[.05, .5, .95]).to_string())
print("share of winners priced >= 0.97 at close-15m:", round((C[C.won].p_close15 >= 0.97).mean(), 3), "| losers priced <= 0.03:", round((C[~C.won].p_close15 <= 0.03).mean(), 3))
print("Brier of that 'closing line':", round(((C.p_close15 - C.won.astype(float)) ** 2).mean(), 5), "| Brier of price at local midnight before the event day:", round(((C.p_day_start - C.won.astype(float)) ** 2).dropna().mean(), 5))
print("winner's price at local 00:00 of event day, by kind:"); print(C[C.won].groupby("kind")["p_day_start"].describe(percentiles=[.1, .5, .9]).to_string())

# --- taker P&L by local-time phase with event-clustered t (all takers) ---
pd.set_option("display.float_format", lambda x: f"{x:,.1f}")
T["phase"] = pd.cut(T["h_rel"], [-1e9, -24, -12, 0, 6, 12, 18, 24, 1e9], labels=["<D-1 00h", "D-1 00-12", "D-1 12-24", "D 00-06", "D 06-12", "D 12-18", "D 18-24", "after day"])
print("\n== taker P&L by local-time phase (all takers, event-clustered) ==")
print(T.groupby(["kind", "phase"], observed=True).apply(clus, include_groups=False).to_string())
