"""Platform-wide scan of Polymarket liquidity-reward markets (all categories).
Sorted by rate so pagination can stop at the first page with no active allocation."""
import json, sys, time, requests, collections
S = requests.Session()
def rate(r): return sum(c.get("rate_per_day", 0) for c in r.get("rewards_config") or [])
def fetch(params, max_pages=400):
    out = []; cur = ""; pages = 0
    while pages < max_pages:
        p = dict(params); p.update({"page_size": 500, "order_by": "rate_per_day", "position": "DESC"})
        if cur: p["next_cursor"] = cur
        j = None
        for a in range(4):
            try:
                r = S.get("https://clob.polymarket.com/rewards/markets/multi", params=p, timeout=60)
                if r.status_code == 200: j = r.json(); break
                print("status", r.status_code, r.text[:120], flush=True)
            except Exception as ex:
                print("err", ex, flush=True)
            time.sleep(2 + 3 * a)
        if j is None: raise SystemExit("rewards api failed")
        pages += 1; data = j["data"]; out += data; cur = j.get("next_cursor")
        if not data or all(rate(r) == 0 for r in data) or not cur or cur == "LTE=": break
        time.sleep(0.1)
    return [r for r in out if rate(r) > 0], pages
act, pages = fetch({})
json.dump(act, open("data/platform/rewards_all.json", "w"))
print("pages:", pages, "| active reward markets:", len(act), "| total posted rate $/day:", round(sum(rate(r) for r in act)), flush=True)
print("top rates:", [(round(rate(r)), r["market_slug"][:50]) for r in act[:8]], flush=True)
TAGS = ["sports", "crypto", "politics", "weather", "economy", "finance", "tech", "culture", "pop-culture", "geopolitics", "mentions", "elections", "science", "business", "world", "daily-temperature", "nfl", "nba", "soccer", "esports", "ai", "earnings", "stocks", "games", "tennis", "mlb", "nhl", "ufc", "f1", "crypto-prices", "bitcoin", "movies", "music", "celebrities", "global-elections", "us-politics", "middle-east", "trade-war", "economic-policy", "fed-rates", "inflation", "commodities", "ipo", "big-tech"]
tag_of = collections.defaultdict(list)
for t in TAGS:
    try:
        rows, _ = fetch({"tag_slug": t}, max_pages=60)
    except SystemExit:
        continue
    for r in rows: tag_of[r["condition_id"]].append(t)
    print(f"tag {t:18} active reward markets {len(rows):5d}  rate ${sum(rate(r) for r in rows):>10,.0f}/day", flush=True)
json.dump(tag_of, open("data/platform/tags.json", "w"))
print("labelled:", len(tag_of), "of", len(act))
