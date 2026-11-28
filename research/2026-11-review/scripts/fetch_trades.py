"""Fetch full taker-side trade tape per event from Data API v2. Usage: fetch_trades.py events.json min_date [taker_only]"""
import json, sys, time, os, gzip, requests
S = requests.Session()
evs = json.load(open(sys.argv[1])); min_date = sys.argv[2]; max_date = sys.argv[3] if len(sys.argv) > 3 else "9999"
taker_only = (sys.argv[4] if len(sys.argv) > 4 else "true")
sfx = "" if taker_only == "true" else "_all"
todo = [e for e in evs.values() if e and min_date <= e["date"] <= max_date]
todo.sort(key=lambda e: e["date"], reverse=True)
n = 0
for e in todo:
    out = f"data/trades/{e['slug']}{sfx}.json.gz"
    if os.path.exists(out): continue
    rows = []; cursor = None; pages = 0
    while True:
        p = {"event_id": e["id"], "limit": 1000, "taker_only": taker_only}
        if cursor: p = {"event_id": e["id"], "cursor": cursor, "taker_only": taker_only}
        ok = False
        for a in range(5):
            try:
                r = S.get("https://data-api.polymarket.com/v2/trades", params=p, timeout=60)
                if r.status_code == 200:
                    j = r.json(); ok = True; break
                print("status", r.status_code, r.text[:150], flush=True)
            except Exception as ex:
                print("err", ex, flush=True)
            time.sleep(3 + 4 * a)
        if not ok: break
        for t in j["data"]:
            rows.append([t["timestamp"], t["token_id"], t["side"], t["price"], t["size"], t["proxy_wallet"], t.get("transaction_hash","")[:18]])
        pg = j.get("pagination") or {}
        pages += 1
        if not pg.get("has_more") or not pg.get("next_cursor"): break
        cursor = pg["next_cursor"]
        time.sleep(0.05)
    if ok:
        with gzip.open(out, "wt") as f: json.dump(rows, f)
        n += 1
        if n % 10 == 0: print(n, e["slug"], len(rows), pages, flush=True)
print("done", n)
