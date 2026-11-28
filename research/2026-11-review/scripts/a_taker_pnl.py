import sys, os; sys.path.insert(0, os.path.dirname(__file__))
from lib import *
ev = load_events()
ev = ev[ev["resolved"]]
tok = {}
for r in ev.itertuples():
    tok[r.yes] = (r.slug, r.title, True, r.won, r.city, r.kind, r.date)
    tok[r.no] = (r.slug, r.title, False, r.won, r.city, r.kind, r.date)
fr = []
for slug in ev["slug"].unique():
    t = load_trades(slug)
    if t is None or t.empty: continue
    e = ev[ev["slug"] == slug].iloc[0]
    a, b = day_bounds_utc(e.city, e.date)
    t["slug"] = slug; t["city"] = e.city; t["kind"] = e.kind; t["date"] = e.date
    t["h_rel"] = (t["ts"] - pd.Timestamp(a)).dt.total_seconds() / 3600.0   # hours since local midnight of event day
    info = t["token"].map(tok)
    t = t[info.notna()].copy(); info = info[info.notna()]
    t["is_yes"] = [i[2] for i in info]; t["won"] = [i[3] for i in info]; t["title"] = [i[1] for i in info]
    t["payoff"] = np.where(t["is_yes"], t["won"].astype(float), 1 - t["won"].astype(float))
    fr.append(t)
T = pd.concat(fr, ignore_index=True)
T["sgn"] = np.where(T["side"] == "BUY", 1.0, -1.0)
T["notional"] = T["price"] * T["size"]
evs_raw = json.load(open(os.path.join(BASE, "data/events_3city.json")))
fee_rate = {slug: (e["markets"][0].get("fees") or {}).get("rate", 0.0) for slug, e in evs_raw.items() if e}
T["fee"] = T["slug"].map(fee_rate).fillna(0.01) * T["size"] * T["price"] * (1 - T["price"])  # per-market stored feeSchedule (0 during the Oct 19-31 fee holiday)
T["taker_pnl"] = T["sgn"] * T["size"] * (T["payoff"] - T["price"]) - T["fee"]
T["maker_pnl_gross"] = -T["sgn"] * T["size"] * (T["payoff"] - T["price"])
T.to_pickle(os.path.join(BASE, "data/trades_all.pkl"))
print("events with trades:", T["slug"].nunique(), "| trades:", len(T), "| notional $:", round(T["notional"].sum()), "| unique taker wallets:", T["wallet"].nunique())
print("date range:", T["date"].min(), T["date"].max())
def summ(g):
    return pd.Series(dict(trades=len(g), notional=g["notional"].sum(), taker_pnl=g["taker_pnl"].sum(), fees=g["fee"].sum(),
                          maker_gross=g["maker_pnl_gross"].sum(), taker_ret_bps=1e4 * g["taker_pnl"].sum() / g["notional"].sum()))
pd.set_option("display.width", 200); pd.set_option("display.float_format", lambda x: f"{x:,.1f}")
print("\n== overall =="); print(summ(T).to_string())
print("\n== by city/kind =="); print(T.groupby(["city", "kind"]).apply(summ, include_groups=False).to_string())
T["phase"] = pd.cut(T["h_rel"], [-1e9, -24, -12, 0, 6, 9, 12, 15, 18, 21, 24, 1e9], labels=["<D-1 00h", "D-1 00-12", "D-1 12-24", "D 00-06", "D 06-09", "D 09-12", "D 12-15", "D 15-18", "D 18-21", "D 21-24", "after day"])
print("\n== HIGH markets by local-time phase =="); print(T[T.kind == "highest"].groupby("phase", observed=True).apply(summ, include_groups=False).to_string())
print("\n== LOW markets by local-time phase =="); print(T[T.kind == "lowest"].groupby("phase", observed=True).apply(summ, include_groups=False).to_string())
T["pband"] = pd.cut(T["price"], [0, 0.02, 0.05, 0.1, 0.2, 0.4, 0.6, 0.8, 0.9, 0.95, 0.98, 1.0])
print("\n== by taker side & price band (token price) =="); print(T.groupby(["side", "pband"], observed=True).apply(summ, include_groups=False).to_string())
# wallets
w = T.groupby("wallet").agg(trades=("size", "size"), notional=("notional", "sum"), pnl=("taker_pnl", "sum"), events=("slug", "nunique")).sort_values("pnl")
print("\n== taker wallets: count", len(w), "| profitable:", (w.pnl > 0).sum(), "| total +pnl", round(w.pnl[w.pnl > 0].sum()), "| total -pnl", round(w.pnl[w.pnl < 0].sum()))
print("top 12 taker winners:"); print(w.tail(12)[::-1].to_string())
print("top 8 taker losers:"); print(w.head(8).to_string())
big = w[w.notional > 5000]
print("\nwallets with >$5k taker notional:", len(big), "| share profitable:", round((big.pnl > 0).mean(), 3), "| median ret bps:", round(1e4 * (big.pnl / big.notional).median(), 1))
