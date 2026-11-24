"""Fetch ALL closed daily-temperature events in an endDate range (every city); store compact JSON keyed by slug.
Usage: fetch_events_all.py 2026-11-10 2026-11-23 data/events_all.json"""
import json, re, sys, time, requests
S = requests.Session()
def get(params):
    for a in range(5):
        try:
            r = S.get("https://gamma-api.polymarket.com/events", params=params, timeout=120)
            if r.status_code == 200: return r.json()
            print("status", r.status_code, flush=True)
        except Exception as ex:
            print("err", ex, flush=True)
        time.sleep(3 + 4 * a)
    raise SystemExit("gamma failed")
start, end, out = sys.argv[1], sys.argv[2], sys.argv[3]
res = {}; off = 0
while True:
    pg = get({"tag_id": 84, "closed": "true", "limit": 100, "offset": off, "order": "endDate", "ascending": "true",
              "end_date_min": start + "T00:00:00Z", "end_date_max": end + "T23:59:59Z"})
    for e in pg:
        m = re.match(r"^(highest|lowest)-temperature-in-(.+)-on-[a-z]+-\d+-\d{4}$", e.get("slug", ""))
        if not m: continue
        res[e["slug"]] = {
            "id": e["id"], "title": e["title"], "slug": e["slug"], "city": m.group(2), "kind": m.group(1), "date": (e.get("eventDate") or e["endDate"][:10]),
            "startDate": e.get("startDate"), "endDate": e.get("endDate"), "closedTime": e.get("closedTime"),
            "volume": e.get("volume"), "resolutionSource": e.get("resolutionSource"),
            "markets": [{
                "id": mk["id"], "title": mk.get("groupItemTitle"), "tokens": json.loads(mk["clobTokenIds"]) if mk.get("clobTokenIds") else None,
                "outcomePrices": mk.get("outcomePrices"), "volume": mk.get("volume"), "fees": mk.get("feeSchedule"), "version": mk.get("version"),
            } for mk in e["markets"]],
        }
    print(off, len(pg), len(res), pg[0].get("endDate") if pg else None, flush=True)
    if len(pg) < 100: break
    off += 100; time.sleep(0.2)
json.dump(res, open(out, "w")); print("saved", len(res))
