import json, requests, time, random
S = requests.Session()
def fetch_rewards():
    out = []; cur = ""
    while True:
        p = {"q": "temperature", "page_size": 500}
        if cur: p["next_cursor"] = cur
        j = S.get("https://clob.polymarket.com/rewards/markets/multi", params=p, timeout=60).json()
        out += j["data"]; cur = j.get("next_cursor")
        if not cur or cur == "LTE=" or not j["data"]: break
    return out
rows = [r for r in fetch_rewards() if sum(c.get("rate_per_day",0) for c in r.get("rewards_config") or []) > 0 and not r["market_slug"].startswith("polyv2")]
random.seed(7)
zero = [r for r in rows if r["market_competitiveness"] == 0]
pos = sorted([r for r in rows if r["market_competitiveness"] > 0], key=lambda r: r["market_competitiveness"])
sample = random.sample(zero, 10) + pos[::max(1, len(pos)//22)][:22]
toks = [r["tokens"][0]["token_id"] for r in sample]
books = {}
for i in range(0, len(toks), 40):
    b = S.post("https://clob.polymarket.com/books", json=[{"token_id": t} for t in toks[i:i+40]], timeout=60).json()
    for x in b: books[x["asset_id"]] = x
def score(book, v_c, min_size):
    bids = sorted([(float(x["price"]), float(x["size"])) for x in book.get("bids", [])], reverse=True)
    asks = sorted([(float(x["price"]), float(x["size"])) for x in book.get("asks", [])])
    # size-cutoff-adjusted midpoint: best bid/ask among orders with cumulative size >= min_size (approximation: ignore levels until cum size >= min_size)
    def adj(levels):
        cum = 0
        for p, s in levels:
            cum += s
            if cum >= min_size: return p
        return None
    bb, ba = adj(bids), adj(asks)
    raw_bb = bids[0][0] if bids else None; raw_ba = asks[0][0] if asks else None
    if bb is None or ba is None: return None, raw_bb, raw_ba, 0, 0
    mid = (bb + ba) / 2
    v = v_c / 100
    q1 = sum(((v - (mid - p)) / v) ** 2 * s for p, s in bids if 0 <= mid - p < v and s >= 0)
    q2 = sum(((v - (p - mid)) / v) ** 2 * s for p, s in asks if 0 <= p - mid < v and s >= 0)
    return mid, raw_bb, raw_ba, q1, q2
print(f"{'slug':58} {'rate':>5} {'comp':>8} {'mid':>6} {'bid':>6} {'ask':>6} {'Q1':>8} {'Q2':>8}  bounds[lo,hi]")
for r in sample:
    t = r["tokens"][0]["token_id"]; b = books.get(t)
    if not b: print(r["market_slug"][-58:], "no book"); continue
    mid, bb, ba, q1, q2 = score(b, r["rewards_max_spread"], r["rewards_min_size"])
    rate = sum(c.get("rate_per_day",0) for c in r["rewards_config"])
    if mid is None:
        print(f"{r['market_slug'][-58:]:58} {rate:5.0f} {r['market_competitiveness']:8.1f} {'-':>6} {str(bb):>6} {str(ba):>6}"); continue
    lo = max(q1, q2) / 3 if 0.1 <= mid <= 0.9 else 0; hi = (q1 + q2) if 0.1 <= mid <= 0.9 else min(q1, q2)
    print(f"{r['market_slug'][-58:]:58} {rate:5.0f} {r['market_competitiveness']:8.1f} {mid:6.3f} {bb:6.3f} {ba:6.3f} {q1:8.1f} {q2:8.1f}  [{lo:.1f}, {hi:.1f}]")
