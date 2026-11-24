"""Tape replay of a naive 'market-following' min-size quoter: fair = lagged VWAP of the public tape, quotes fair +- delta.
Fill rule: a real taker trade at or through our price fills us at OUR price (capped by quote size, cooldown per side).
Measures adverse selection (P&L to resolution and 30-min markout) per market-day. No model, no book data needed."""
import sys, os; sys.path.insert(0, os.path.dirname(__file__))
from lib import *
ev = load_events(); ev = ev[ev["resolved"]]
T = pd.read_pickle(os.path.join(BASE, "data/trades_all.pkl"))
T["yes_price"] = np.where(T["is_yes"], T["price"], 1 - T["price"])
T["taker_long_yes"] = ((T["side"] == "BUY") & T["is_yes"]) | ((T["side"] == "SELL") & ~T["is_yes"])
Q = 20.0; LAG = 120; WIN = 1800; COOL = 300
OBS_MIN = {"nyc": [51], "london": [20, 50], "paris": [0, 30]}
def replay(delta, pull_metar, band=(0.05, 0.95)):
    out = []
    for (slug, title), g in T.groupby(["slug", "title"], sort=False):
        g = g.sort_values("ts")
        ts = (g["ts"] - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds().values.astype("int64"); px = g["yes_price"].values; sz = g["size"].values; lng = g["taker_long_yes"].values
        won = float(g["won"].iloc[0]); city = g["city"].iloc[0]; kind = g["kind"].iloc[0]; h_rel = g["h_rel"].values
        n = len(g); lo = 0; hi = 0; cum_pv = np.concatenate([[0.0], np.cumsum(px * sz)]); cum_v = np.concatenate([[0.0], np.cumsum(sz)])
        last_fill = {True: -10**12, False: -10**12}
        for i in range(n):
            t = ts[i]
            while hi < n and ts[hi] <= t - LAG: hi += 1
            while lo < hi and ts[lo] < t - WIN: lo += 1
            if hi > lo: f = (cum_pv[hi] - cum_pv[lo]) / (cum_v[hi] - cum_v[lo])
            elif hi > 0 and t - ts[hi - 1] <= 6 * 3600: f = px[hi - 1]
            else: continue
            if not (band[0] <= f <= band[1]): continue
            if pull_metar:
                m = (t // 60) % 60
                if any(((m - om) % 60) <= 6 or ((om - m) % 60) <= 1 for om in OBS_MIN[city]) and 0 <= h_rel[i] < 24: continue
            bid = math.floor((f - delta) * 100 + 1e-9) / 100; ask = math.ceil((f + delta) * 100 - 1e-9) / 100
            if lng[i] and px[i] >= ask and t - last_fill[True] >= COOL and ask < 1:
                q = min(Q, sz[i]); last_fill[True] = t
                # future fair 30 minutes later
                j = np.searchsorted(ts, t + 1800); fut = px[j] if j < n else won
                out.append((slug, title, kind, city, h_rel[i], "sell", ask, q, q * (ask - won), q * (ask - fut)))
            elif (not lng[i]) and px[i] <= bid and t - last_fill[False] >= COOL and bid > 0:
                q = min(Q, sz[i]); last_fill[False] = t
                j = np.searchsorted(ts, t + 1800); fut = px[j] if j < n else won
                out.append((slug, title, kind, city, h_rel[i], "buy", bid, q, q * (won - bid), q * (fut - bid)))
    return pd.DataFrame(out, columns=["slug", "title", "kind", "city", "h_rel", "dir", "price", "shares", "pnl", "mo30"])
pd.set_option("display.width", 230); pd.set_option("display.float_format", lambda x: f"{x:,.2f}")
n_events = T.groupby("kind")["slug"].nunique()
mkts = T.groupby(["kind"]).apply(lambda d: d.groupby(["slug", "title"]).ngroups, include_groups=False)
def report(F, label):
    print(f"\n=== {label} ===")
    for kind in ("highest", "lowest"):
        x = F[F.kind == kind]; ne = n_events[kind]
        e = x.groupby("slug")["pnl"].sum().reindex(T[T.kind == kind]["slug"].unique()).fillna(0.0)
        se = e.std(ddof=1) / math.sqrt(len(e))
        print(f"{kind}: fills {len(x)} | shares {x.shares.sum():,.0f} | notional ${ (x.shares * x.price).sum():,.0f} | P&L to resolution ${x.pnl.sum():,.0f} "
              f"| per event ${e.mean():.2f} (se {se:.2f}, t {e.mean() / se:.2f}) | 30-min markout ${x.mo30.sum():,.0f} | c/share {100 * x.pnl.sum() / x.shares.sum():.2f} | fills/event {len(x) / ne:.1f}")
    F = F.copy(); F["phase"] = pd.cut(F["h_rel"], [-1e9, -24, 0, 6, 12, 18, 24, 1e9], labels=["<D-1", "D-1", "D 00-06", "D 06-12", "D 12-18", "D 18-24", "after"])
    print(F.groupby(["kind", "phase"], observed=True).agg(fills=("pnl", "size"), shares=("shares", "sum"), pnl=("pnl", "sum"), mo30=("mo30", "sum")).assign(c_per_share=lambda d: 100 * d.pnl / d.shares).to_string())
for delta, pull in [(0.02, False), (0.02, True), (0.01, True), (0.03, True)]:
    F = replay(delta, pull)
    report(F, f"naive follower, delta={delta}, pull around METAR minutes={pull}, quote size {Q:.0f} sh, lag {LAG}s")
    if delta == 0.02 and pull: F.to_pickle(os.path.join(BASE, "data/mm_replay.pkl"))
