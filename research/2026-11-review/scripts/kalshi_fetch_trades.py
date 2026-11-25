"""Download the public trade tape of one Kalshi series (settled markets only).

Usage: python scripts/kalshi_fetch_trades.py KXHIGHNY 2026-06-15 2026-11-24

Writes data/kalshi/trades_<SERIES>.jsonl.gz, one line per market:
  {"ticker", "event", "result", "open", "close", "trades": [[created_time, taker_side, yes_price_dollars, count], ...]}
The run is resumable: markets already present in the output file are skipped.
No account and no credentials are needed; the endpoints are public.
"""
import gzip, json, os, sys, time
import requests

BASE = "https://api.elections.kalshi.com/trade-api/v2"
S = requests.Session()
S.headers.update({"Accept": "application/json", "User-Agent": "research-client/1.0"})
_last = [0.0]


def get(path, params=None, pace=0.35):
    err = None
    for attempt in range(8):
        wait = pace - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            r = S.get(BASE + path, params=params, timeout=60)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                err = RuntimeError(f"http {r.status_code}")
                time.sleep(2.0 * (attempt + 1))
                continue
            if r.status_code == 404:
                return {}
            r.raise_for_status()
        except requests.RequestException as ex:
            err = ex
            time.sleep(2.0 * (attempt + 1))
    raise err


def paged(path, params, key, maxpages=200):
    out, cursor = [], None
    for _ in range(maxpages):
        p = dict(params)
        if cursor:
            p["cursor"] = cursor
        d = get(path, p)
        out += d.get(key) or []
        cursor = d.get("cursor")
        if not cursor:
            break
    return out


def main():
    series, start, end = sys.argv[1], sys.argv[2], sys.argv[3]
    os.makedirs("data/kalshi", exist_ok=True)
    out = f"data/kalshi/trades_{series}.jsonl.gz"

    # Recent markets live under /markets, older ones under /historical/markets.
    markets = {}
    for m in paged("/historical/markets", {"series_ticker": series, "limit": 1000}, "markets", maxpages=40):
        markets[m["ticker"]] = m
    for m in paged("/markets", {"series_ticker": series, "status": "settled", "limit": 1000}, "markets"):
        markets[m["ticker"]] = m
    sel = [m for m in markets.values() if start <= m["close_time"][:10] <= end and m.get("result") in ("yes", "no")]
    sel.sort(key=lambda m: m["close_time"])

    done = set()
    if os.path.exists(out):
        try:
            with gzip.open(out, "rt") as f:
                for line in f:
                    done.add(json.loads(line)["ticker"])
        except (EOFError, json.JSONDecodeError):
            pass
    print(series, "settled markets in window", len(sel), "already downloaded", len(done), flush=True)

    n, t0 = 0, time.time()
    with gzip.open(out, "at") as f:
        for i, m in enumerate(sel):
            if m["ticker"] in done:
                continue
            trades = []
            for path in ("/markets/trades", "/historical/trades"):
                got = paged(path, {"ticker": m["ticker"], "limit": 1000}, "trades")
                if got:
                    trades = got
                    break
            rec = {
                "ticker": m["ticker"], "event": m["event_ticker"], "result": m["result"],
                "open": m["open_time"], "close": m["close_time"],
                "trades": [[x["created_time"], x["taker_side"], x["yes_price_dollars"], x["count_fp"]] for x in trades],
            }
            f.write(json.dumps(rec) + "\n")
            n += len(trades)
            if i % 30 == 0:
                print(i, len(sel), m["ticker"], len(trades), "total", n, round(time.time() - t0), flush=True)
    print("done", series, "new trades", n, "seconds", round(time.time() - t0))


if __name__ == "__main__":
    main()
