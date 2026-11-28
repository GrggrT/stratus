"""Live shadow-reward sampler: every few minutes snapshot reward configs + order books for all rewarded temperature markets
and compute the documented liquidity-reward score of existing quotes vs a hypothetical min-size two-sided quoter."""
import json, sys, time, requests, math, os, datetime as dt
S = requests.Session()
N_ROUNDS = int(sys.argv[1]); SLEEP = int(sys.argv[2])
def fetch_rewards():
    out = []; cur = ""
    while True:
        p = {"q": "temperature", "page_size": 500}
        if cur: p["next_cursor"] = cur
        j = S.get("https://clob.polymarket.com/rewards/markets/multi", params=p, timeout=60).json()
        out += j["data"]; cur = j.get("next_cursor")
        if not cur or cur == "LTE=" or not j["data"]: break
    return out
def books(tokens):
    res = {}
    for i in range(0, len(tokens), 50):
        for a in range(3):
            try:
                r = S.post("https://clob.polymarket.com/books", json=[{"token_id": t} for t in tokens[i:i + 50]], timeout=60)
                if r.status_code == 200:
                    for b in r.json(): res[b["asset_id"]] = b
                    break
            except Exception as ex:
                pass
            time.sleep(2)
        time.sleep(0.15)
    return res
def analyse(book, v_c, min_size):
    bids = sorted([(float(x["price"]), float(x["size"])) for x in book.get("bids", [])], reverse=True)
    asks = sorted([(float(x["price"]), float(x["size"])) for x in book.get("asks", [])])
    def adj(levels):
        cum = 0.0
        for p, s in levels:
            cum += s
            if cum >= min_size: return p
        return None
    bb, ba = adj(bids), adj(asks)
    raw_bb = bids[0][0] if bids else None; raw_ba = asks[0][0] if asks else None
    if bb is None or ba is None:
        return dict(mid=None, bb=raw_bb, ba=raw_ba, q1=0.0, q2=0.0, n_bid=len(bids), n_ask=len(asks))
    mid = (bb + ba) / 2; v = v_c / 100.0
    q1 = sum(((v - (mid - p)) / v) ** 2 * s for p, s in bids if 0 <= mid - p < v)
    q2 = sum(((v - (p - mid)) / v) ** 2 * s for p, s in asks if 0 <= p - mid < v)
    return dict(mid=mid, bb=raw_bb, ba=raw_ba, q1=q1, q2=q2, n_bid=len(bids), n_ask=len(asks))
for rnd in range(N_ROUNDS):
    t0 = time.time()
    try:
        rows = fetch_rewards()
        act = [r for r in rows if sum(c.get("rate_per_day", 0) for c in r.get("rewards_config") or []) > 0 and not r["market_slug"].startswith("polyv2")]
        bk = books([r["tokens"][0]["token_id"] for r in act])
        out = []
        for r in act:
            b = bk.get(r["tokens"][0]["token_id"])
            if b is None: continue
            a = analyse(b, r["rewards_max_spread"], r["rewards_min_size"])
            out.append(dict(slug=r["market_slug"], event=r["event_slug"], rate=sum(c.get("rate_per_day", 0) for c in r["rewards_config"]),
                            comp=r["market_competitiveness"], v=r["rewards_max_spread"], min_size=r["rewards_min_size"], vol24=r.get("volume_24hr"),
                            price=r["tokens"][0]["price"], **a))
        ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
        json.dump(out, open(f"data/sampler/{ts}.json", "w"))
        print(ts, "markets", len(out), "rate_total", round(sum(o["rate"] for o in out)), "no_mid", sum(o["mid"] is None for o in out), "zeroQ", sum(o["q1"] + o["q2"] == 0 for o in out), "took", round(time.time() - t0, 1), flush=True)
    except Exception as ex:
        print("round failed", ex, flush=True)
    time.sleep(max(5, SLEEP - (time.time() - t0)))
