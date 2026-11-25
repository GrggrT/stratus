"""Maker-side P&L on a Kalshi series, from the public trade tape, held to settlement, gross of fees.

Usage: python scripts/kalshi_maker_pnl.py data/kalshi/trades_KXHIGHNY.jsonl.gz [switch-date]

Input comes from kalshi_fetch_trades.py. Per contract:
  taker buys YES at p     -> the maker is short YES at p -> p - y
  taker buys NO  at 1 - p -> the maker is long  YES at p -> y - p
Resting orders on the daily temperature series pay no fee, so gross equals net for the maker.
Standard errors are clustered by event (one event = one city-day). The switch date splits the
sample at the change of settlement source (2026-08-14, The Weather Company).
"""
import collections, gzip, json, math, sys

path = sys.argv[1]
switch = sys.argv[2] if len(sys.argv) > 2 else "2026-08-14"

rows = []  # (period, event, price the maker paid, contracts, maker P&L per contract)
markets = 0
try:
    with gzip.open(path, "rt") as f:
        for line in f:
            r = json.loads(line)
            markets += 1
            y = 1.0 if r["result"] == "yes" else 0.0
            period = "post" if r["close"][:10] >= switch else "pre"
            for t in r["trades"]:
                p, q = float(t[2]), float(t[3])
                if q <= 0 or p <= 0 or p >= 1:
                    continue
                if t[1] == "yes":
                    rows.append((period, r["event"], 1 - p, q, p - y))
                else:
                    rows.append((period, r["event"], p, q, y - p))
except EOFError:
    print("note: gzip stream is truncated, using the readable part")

print(f"{path.split('/')[-1]}: markets {markets}, trades {len(rows)}, events {len({x[1] for x in rows})}")


def agg(sel, label):
    if not sel:
        print(f"{label:34s} none")
        return
    q_sum = sum(x[3] for x in sel)
    pnl = sum(x[4] * x[3] for x in sel)
    ev = collections.defaultdict(lambda: [0.0, 0.0])
    for x in sel:
        ev[x[1]][0] += x[4] * x[3]
        ev[x[1]][1] += x[3]
    n, r = len(ev), pnl / q_sum
    lin = [v[0] - r * v[1] for v in ev.values()]
    se = math.sqrt(sum(v * v for v in lin) * n / (n - 1)) / q_sum if n > 1 else float("nan")
    print(f"{label:34s} contracts {q_sum:12.0f}  trades {len(sel):8d}  events {n:4d}  "
          f"maker P&L ${pnl:10.0f} = {r * 100:+.2f}c/contract (se {se * 100:.2f}, t {r / se:+.1f})")


for period in ("pre", "post"):
    sel = [x for x in rows if x[0] == period]
    agg(sel, f"{period} all")
    agg([x for x in sel if 0.10 < x[2] < 0.90], f"{period} maker pays 10-90c")
    agg([x for x in sel if x[2] >= 0.90], f"{period} maker pays >=90c")
    agg([x for x in sel if x[2] <= 0.10], f"{period} maker pays <=10c")
    agg([x for x in sel if x[3] < 10], f"{period} taker size <10")
    agg([x for x in sel if 10 <= x[3] < 100], f"{period} taker size 10-100")
    agg([x for x in sel if 100 <= x[3] < 1000], f"{period} taker size 100-1000")
    agg([x for x in sel if x[3] >= 1000], f"{period} taker size >=1000")

post = [x for x in rows if x[0] == "post"]
ev = collections.defaultdict(lambda: [0.0, 0.0])
for x in post:
    ev[x[1]][0] += x[4] * x[3]
    ev[x[1]][1] += x[3]
total = sum(v[0] for v in ev.values())
print(f"post: total maker P&L ${total:.0f}; events with positive maker P&L "
      f"{sum(1 for v in ev.values() if v[0] > 0)}/{len(ev)}; five largest single-event contributions:")
for k, v in sorted(ev.items(), key=lambda kv: -abs(kv[1][0]))[:5]:
    print(f"   {k}: ${v[0]:+.0f} on {v[1]:.0f} contracts")
