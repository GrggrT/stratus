import sys, os; sys.path.insert(0, os.path.dirname(__file__))
from lib import *
from collections import Counter
ev = load_events(); ev = ev[ev["resolved"] & (ev["date"] >= "2026-10-20")]
tok = {}
for r in ev.itertuples():
    tok[r.yes] = (True, r.won, r.title); tok[r.no] = (False, r.won, r.title)
fr = []
for slug in ev["slug"].unique():
    a = load_trades(slug, "_all"); t = load_trades(slug)
    if a is None or t is None or a.empty: continue
    key = lambda d: list(zip(d["ts"].astype("int64"), d["tx"], d["token"], d["wallet"], d["side"], d["price"], d["size"]))
    c = Counter(key(t)); is_maker = []
    for k in key(a):
        if c.get(k, 0) > 0: c[k] -= 1; is_maker.append(False)
        else: is_maker.append(True)
    a["maker"] = is_maker; a["slug"] = slug
    e = ev[ev.slug == slug].iloc[0]; a["kind"] = e.kind; a["city"] = e.city; a["date"] = e.date
    d0, _ = day_bounds_utc(e.city, e.date); a["h_rel"] = (a["ts"] - pd.Timestamp(d0)).dt.total_seconds() / 3600
    fr.append(a)
A = pd.concat(fr, ignore_index=True)
info = A["token"].map(tok); A = A[info.notna()].copy(); info = info[info.notna()]
A["is_yes"] = [i[0] for i in info]; A["won"] = [i[1] for i in info]
A["payoff"] = np.where(A["is_yes"], A["won"].astype(float), 1 - A["won"].astype(float))
A["sgn"] = np.where(A["side"] == "BUY", 1.0, -1.0); A["notional"] = A["price"] * A["size"]
A["pnl"] = A["sgn"] * A["size"] * (A["payoff"] - A["price"])
M = A[A.maker]; K = A[~A.maker]
pd.set_option("display.width", 230); pd.set_option("display.float_format", lambda x: f"{x:,.1f}")
print("events:", A.slug.nunique(), "| taker rows:", len(K), "| maker rows:", len(M), "| taker gross pnl:", round(K.pnl.sum()), "| maker gross pnl:", round(M.pnl.sum()), "| maker notional:", round(M.notional.sum()), "| taker notional:", round(K.notional.sum()))
w = M.groupby("wallet").agg(fills=("size", "size"), notional=("notional", "sum"), pnl=("pnl", "sum"), events=("slug", "nunique")).sort_values("notional", ascending=False)
print("maker wallets:", len(w), "| profitable (gross, pre-rewards):", int((w.pnl > 0).sum()), "| top-10 share of maker notional:", round(w.notional.head(10).sum() / w.notional.sum(), 3), "| top-50 share:", round(w.notional.head(50).sum() / w.notional.sum(), 3))
w["ret_bps"] = 1e4 * w.pnl / w.notional
print("\ntop 20 makers by notional (gross P&L before rewards/rebates):"); print(w.head(20).to_string())
big = w[w.notional > 20000]
print("\nmakers with >$20k maker notional:", len(big), "| profitable:", int((big.pnl > 0).sum()), "| pooled ret bps:", round(1e4 * big.pnl.sum() / big.notional.sum(), 1), "| median ret bps:", round(big.ret_bps.median(), 1))
# are top makers also takers?
tk = K.groupby("wallet").agg(t_notional=("notional", "sum"), t_pnl=("pnl", "sum"))
j = w.head(20).join(tk, how="left").fillna(0)
print("\ntop-20 makers: their taker activity:"); print(j[["notional", "pnl", "t_notional", "t_pnl", "events"]].to_string())
# maker pnl by phase / kind and by maker's side & price
M = M.copy(); M["phase"] = pd.cut(M["h_rel"], [-1e9, -24, 0, 6, 12, 18, 24, 1e9], labels=["<D-1", "D-1", "D 00-06", "D 06-12", "D 12-18", "D 18-24", "after"])
print("\nmaker gross P&L by kind/phase:"); print(M.groupby(["kind", "phase"], observed=True).agg(notional=("notional", "sum"), pnl=("pnl", "sum")).assign(bps=lambda d: 1e4 * d.pnl / d.notional).to_string())
M["yes_price"] = np.where(M["is_yes"], M["price"], 1 - M["price"])
M["maker_long_yes"] = ((M["side"] == "BUY") & M["is_yes"]) | ((M["side"] == "SELL") & ~M["is_yes"])
M["pb"] = pd.cut(M["yes_price"], [0, 0.02, 0.05, 0.1, 0.2, 0.4, 0.6, 0.8, 0.95, 1.0])
print("\nmaker gross P&L by maker direction (in YES terms) and YES price:"); print(M.groupby(["maker_long_yes", "pb"], observed=True).agg(fills=("size", "size"), shares=("size", "sum"), pnl=("pnl", "sum")).assign(c_per_share=lambda d: 100 * d.pnl / d.shares).to_string())
A.to_pickle(os.path.join(BASE, "data/trades_makers.pkl"))
