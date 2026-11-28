"""Obs-only analog nowcast of the settlement value vs the market (trade tape), out of sample on 2026-09..11."""
import sys, os; sys.path.insert(0, os.path.dirname(__file__))
from lib import *
ev = load_events(); ev = ev[ev["resolved"] & (ev["date"] >= "2026-09-01")]
T = pd.read_pickle(os.path.join(BASE, "data/trades_all.pkl"))
T["yes_price"] = np.where(T["is_yes"], T["price"], 1 - T["price"])
T["taker_long_yes"] = ((T["side"] == "BUY") & T["is_yes"]) | ((T["side"] == "SELL") & ~T["is_yes"])
UNIT = {"nyc": "F", "london": "C", "paris": "C"}; SLOTS = {"nyc": 24, "london": 48, "paris": 48}

def build_days(city):
    m = load_metar(ST[city]); m = m[m.routine].copy()
    tz = TZ[city]; loc = m["ts"].dt.tz_convert(tz)
    m["ldate"] = loc.dt.date
    m["slot"] = (loc.dt.hour * (SLOTS[city] // 24) + (loc.dt.minute // 30 if SLOTS[city] == 48 else 0)).astype(int)
    m["val"] = [settle_units(x, UNIT[city]) for x in m["tenths"]]
    days = {}
    for d, g in m.groupby("ldate"):
        arr = np.full(SLOTS[city], np.nan); arr[g["slot"].values] = g["val"].values
        tarr = np.full(SLOTS[city], np.datetime64("NaT"), dtype="datetime64[ns]"); tarr[g["slot"].values] = g["ts"].dt.tz_convert(None).values
        if np.isnan(arr).sum() <= SLOTS[city] * 0.1: days[d] = (arr, tarr)
    return days

def nowcast(days_train, doy, k, run, cur, slope, kind, city, lag):
    """Weighted empirical distribution of D = final - running extreme (>=0 for high; <=0 for low)."""
    sg = 2.0 if UNIT[city] == "F" else 1.0
    w = {}; tot = 0.0
    for d, (arr, _) in days_train.items():
        dd = abs(d.timetuple().tm_yday - doy); dd = min(dd, 366 - dd)
        if dd > 21: continue
        a = arr[: k + 1]
        if np.isnan(a[k]) or (k - lag >= 0 and np.isnan(a[k - lag])): continue
        if kind == "highest":
            r = np.nanmax(a); fin = np.nanmax(arr)
        else:
            r = np.nanmin(a); fin = np.nanmin(arr)
        g = abs(r - a[k]); s = a[k] - a[k - lag] if k - lag >= 0 else 0.0
        wt = math.exp(-0.5 * ((g - abs(run - cur)) / sg) ** 2 - 0.5 * ((s - slope) / sg) ** 2 - 0.5 * (dd / 14.0) ** 2)
        if wt < 1e-4: continue
        D = int(fin - r); w[D] = w.get(D, 0.0) + wt; tot += wt
    return {D: x / tot for D, x in w.items()}, tot

res = []; sig = []
for city in TZ:
    days = build_days(city)
    train = {d: v for d, v in days.items() if d.year <= 2025}
    lag = 2 if SLOTS[city] == 24 else 4
    for slug, g in ev[ev.city == city].groupby("slug"):
        e = g.iloc[0]; d = dt.date.fromisoformat(e.date)
        if d not in days: continue
        arr, tarr = days[d]; t = T[T.slug == slug]
        if t.empty: continue
        hi = e.kind == "highest"
        fin = np.nanmax(arr) if hi else np.nanmin(arr)
        win = g[g.won].iloc[0]
        if not (win.lo <= fin <= win.hi): continue   # only days where METAR reconstruction equals the settlement
        tk = {r.title: t[t.title == r.title] for r in g.itertuples()}
        for k in range(SLOTS[city]):
            if np.isnan(arr[k]): continue
            a = arr[: k + 1]; run = np.nanmax(a) if hi else np.nanmin(a); cur = arr[k]
            slope = cur - arr[k - lag] if k - lag >= 0 and not np.isnan(arr[k - lag]) else 0.0
            dist, tot = nowcast(train, d.timetuple().tm_yday, k, run, cur, slope, e.kind, city, lag)
            if tot < 3: continue
            t0 = pd.Timestamp(tarr[k], tz="UTC") + pd.Timedelta(minutes=7)   # obs time + publication/reaction allowance
            pm, pn = {}, {}
            for r in g.itertuples():
                p_model = sum(p for D, p in dist.items() if r.lo <= run + D <= r.hi)
                pm[r.title] = p_model
                prev = tk[r.title]; prev = prev[(prev.ts <= t0) & (prev.ts > t0 - pd.Timedelta(hours=4))]
                pn[r.title] = prev["yes_price"].iloc[-1] if len(prev) else np.nan
            if np.isnan(list(pn.values())).mean() > 0.5: continue
            pmk = {k_: (v if not np.isnan(v) else 0.002) for k_, v in pn.items()}
            z = sum(pmk.values()); pmk = {k_: v / z for k_, v in pmk.items()}
            eps = 0.01
            ll_model = -math.log((1 - eps) * pm[win.title] + eps / len(pm)); ll_mkt = -math.log(max(pmk[win.title], 1e-4))
            res.append(dict(city=city, kind=e.kind, slug=slug, slot_h=k / (SLOTS[city] / 24), ll_model=ll_model, ll_mkt=ll_mkt, p_model_win=pm[win.title], p_mkt_win=pmk[win.title], overround=z))
            # executable test: next opposite-side trade within 25 minutes
            for r in g.itertuples():
                nxt = tk[r.title]; nxt = nxt[(nxt.ts > t0) & (nxt.ts <= t0 + pd.Timedelta(minutes=25))]
                if nxt.empty: continue
                buys = nxt[nxt.taker_long_yes]; sells = nxt[~nxt.taker_long_yes]
                out = float(r.won)
                if len(buys):
                    p = buys["yes_price"].iloc[0]; edge = pm[r.title] - p
                    sig.append(dict(city=city, kind=e.kind, slug=slug, slot_h=k / (SLOTS[city] / 24), dir="buy_yes", price=p, edge=edge, pnl=out - p - 0.01 * p * (1 - p)))
                if len(sells):
                    p = sells["yes_price"].iloc[0]; edge = p - pm[r.title]
                    sig.append(dict(city=city, kind=e.kind, slug=slug, slot_h=k / (SLOTS[city] / 24), dir="sell_yes", price=p, edge=edge, pnl=p - out - 0.01 * p * (1 - p)))
R = pd.DataFrame(res); Sg = pd.DataFrame(sig)
R.to_pickle(os.path.join(BASE, "data/nowcast_res.pkl")); Sg.to_pickle(os.path.join(BASE, "data/nowcast_sig.pkl"))
pd.set_option("display.width", 220); pd.set_option("display.float_format", lambda x: f"{x:,.3f}")
R["hb"] = pd.cut(R["slot_h"], [-0.1, 3, 6, 9, 11, 12, 13, 14, 15, 16, 17, 18, 20, 24], right=False)
for kind in ("highest", "lowest"):
    x = R[R.kind == kind]
    print(f"\n== {kind}: mean log-loss of the true bucket by local hour (model=obs-only analog nowcast; market=last trade prices normalised) ==")
    print(x.groupby("hb", observed=True).agg(n=("ll_model", "size"), events=("slug", "nunique"), ll_model=("ll_model", "mean"), ll_mkt=("ll_mkt", "mean"), p_model=("p_model_win", "mean"), p_mkt=("p_mkt_win", "mean"), overround=("overround", "mean")).to_string())
def clustered(df):
    g = df.groupby("slug")["pnl"].agg(["sum", "size"]); k = len(g)
    if k < 2: return pd.Series(dict(n=len(df), events=k, mean_pnl=df.pnl.mean(), se=np.nan, t=np.nan, avg_price=df.price.mean()))
    m = g["sum"].sum() / g["size"].sum(); r = g["sum"] - m * g["size"]
    se = math.sqrt(k / (k - 1) * (r ** 2).sum()) / g["size"].sum()
    return pd.Series(dict(n=len(df), events=k, mean_pnl=m, se=se, t=m / se, avg_price=df.price.mean()))
Sg["eb"] = pd.cut(Sg["edge"], [-1, -0.05, 0.0, 0.03, 0.06, 0.10, 0.2, 1])
print("\n== executable taker test: P&L per share (after fee) by model edge bucket, at the next real trade price on the needed side ==")
for kind in ("highest", "lowest"):
    for dr in ("buy_yes", "sell_yes"):
        x = Sg[(Sg.kind == kind) & (Sg.dir == dr)]
        print(f"\n-- {kind} / {dr} --"); print(x.groupby("eb", observed=True).apply(clustered, include_groups=False).to_string())
x = Sg[(Sg.edge > 0.06)]
x = x.copy(); x["hb"] = pd.cut(x["slot_h"], [-0.1, 6, 9, 12, 14, 16, 18, 24], right=False)
print("\n== signals with model edge > 6c: by kind and local hour =="); print(x.groupby(["kind", "hb"], observed=True).apply(clustered, include_groups=False).to_string())
print("\n== signals with model edge > 6c: by kind, city =="); print(x.groupby(["kind", "city"], observed=True).apply(clustered, include_groups=False).to_string())
print("\n== all trades baseline (any edge) =="); print(Sg.groupby(["kind", "dir"]).apply(clustered, include_groups=False).to_string())
