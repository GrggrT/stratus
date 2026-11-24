import sys, os; sys.path.insert(0, os.path.dirname(__file__))
from lib import *
ev = load_events(); ev = ev[ev["resolved"]]
T = pd.read_pickle(os.path.join(BASE, "data/trades_all.pkl"))
print("resolution regimes by first/last date:"); print(ev.drop_duplicates("slug").groupby("rsrc")["date"].agg(["min", "max", "count"]))
met = {c: load_metar(ST[c]) for c in TZ}
rows = []; per_event = []
for slug, g in ev.groupby("slug"):
    e = g.iloc[0]
    if e.date < "2026-09-01": continue
    t = T[T["slug"] == slug]
    if t.empty: continue
    a, b = day_bounds_utc(e.city, e.date)
    m = met[e.city]; m = m[(m.ts >= a) & (m.ts < b) & m.routine].copy()
    if m.empty: continue
    m["val"] = m["tenths"].map(lambda x: settle_units(x, e.unit))
    hi = (e.kind == "highest")
    m["run"] = m["val"].cummax() if hi else m["val"].cummin()
    final = m["run"].iloc[-1]
    winner = g[g.won].iloc[0]
    ok = winner.lo <= final <= winner.hi
    # time at which final extreme was first reported
    t_final = m[m["run"] == final]["ts"].iloc[0]
    per_event.append(dict(slug=slug, city=e.city, kind=e.kind, date=e.date, rsrc=e.rsrc, metar_matches=ok, final=final,
                          h_final=(t_final - pd.Timestamp(a)).total_seconds() / 3600))
    # for each bucket: time when it became impossible
    for r in g.itertuples():
        if hi:
            dead = m[m["run"] > r.hi]
        else:
            dead = m[m["run"] < r.lo]
        if dead.empty: continue
        t_dead = dead["ts"].iloc[0]
        tt = t[(t["title"] == r.title) & (t["ts"] > t_dead)]
        if tt.empty: continue
        tt = tt.copy(); tt["lag_min"] = (tt["ts"] - t_dead).dt.total_seconds() / 60
        # value transferred from the stale side: selling dead YES / buying NO below 1
        tt["yes_price"] = np.where(tt["is_yes"], tt["price"], 1 - tt["price"])
        # taker direction in YES terms: BUY yes or SELL no => long YES (loses); SELL yes or BUY no => short YES (wins)
        tt["taker_short_yes"] = ((tt["side"] == "SELL") & tt["is_yes"]) | ((tt["side"] == "BUY") & ~tt["is_yes"])
        tt["transfer"] = tt["size"] * tt["yes_price"]
        tt["slug"] = slug; tt["city"] = e.city; tt["kind"] = e.kind; tt["rsrc"] = e.rsrc; tt["metar_ok"] = ok
        rows.append(tt[["slug", "city", "kind", "rsrc", "metar_ok", "ts", "lag_min", "yes_price", "size", "taker_short_yes", "transfer", "wallet"]])
PE = pd.DataFrame(per_event); D = pd.concat(rows, ignore_index=True)
pd.set_option("display.width", 220); pd.set_option("display.float_format", lambda x: f"{x:,.2f}")
print("\nMETAR-max reconstruction vs Polymarket winner, by regime:"); print(PE.groupby(["rsrc", "city", "kind"])["metar_matches"].agg(["mean", "count"]).to_string())
print("\nlocal hour when the final extreme was first reported (quantiles):")
print(PE.groupby(["city", "kind"])["h_final"].describe(percentiles=[.1, .25, .5, .75, .9]).to_string())
D.to_pickle(os.path.join(BASE, "data/dead_trades.pkl")); PE.to_pickle(os.path.join(BASE, "data/per_event.pkl"))
Dm = D[D.metar_ok]
print("\n== trades in already-impossible buckets after the killing METAR obs time (events where METAR reconstruction matches) ==")
print("trades:", len(Dm), "| events:", Dm.slug.nunique(), "| value transferred to the informed side ($):", round(Dm["transfer"].sum()))
Dm = Dm.copy(); Dm["lagb"] = pd.cut(Dm["lag_min"], [0, 2, 4, 6, 10, 15, 30, 60, 180, 1e6])
print(Dm.groupby(["taker_short_yes", "lagb"], observed=True).agg(trades=("size", "size"), shares=("size", "sum"), transfer=("transfer", "sum"), avg_yes_price=("yes_price", "mean")).to_string())
big = Dm[Dm.yes_price >= 0.03]
print("\nonly trades with dead-bucket YES price >= 3c: trades", len(big), "| transfer $", round(big.transfer.sum()), "| by informed takers $", round(big[big.taker_short_yes].transfer.sum()), "| by uninformed takers buying dead YES $", round(big[~big.taker_short_yes].transfer.sum()))
print(big.groupby(["taker_short_yes", "lagb"], observed=True).agg(trades=("size", "size"), transfer=("transfer", "sum"), avg_yes_price=("yes_price", "mean")).to_string())
print("\nper-kind/city transfer for yes>=3c:"); print(big.groupby(["kind", "city"]).agg(transfer=("transfer", "sum"), trades=("size", "size"), events=("slug", "nunique")).to_string())
w = big[big.taker_short_yes].groupby("wallet").agg(transfer=("transfer", "sum"), trades=("size", "size"), med_lag=("lag_min", "median"), events=("slug", "nunique")).sort_values("transfer", ascending=False)
print("\ntop informed takers of dead buckets:"); print(w.head(10).to_string())
