"""Fetch closed daily-temperature events for given cities by slug; store compact JSON."""
import json, sys, time, datetime as dt, requests, os
S = requests.Session()
months = ["january","february","march","april","may","june","july","august","september","october","november","december"]
def by_slug(slug):
    for a in range(4):
        try:
            r = S.get("https://gamma-api.polymarket.com/events", params={"slug": slug}, timeout=40)
            if r.status_code == 200: return r.json()
        except Exception: pass
        time.sleep(2 + 2*a)
    return None
cities = sys.argv[1].split(","); start = dt.date.fromisoformat(sys.argv[2]); end = dt.date.fromisoformat(sys.argv[3]); out = sys.argv[4]
res = json.load(open(out)) if os.path.exists(out) else {}
d = start
while d <= end:
    for city in cities:
        for kind in ("highest","lowest"):
            slug = f"{kind}-temperature-in-{city}-on-{months[d.month-1]}-{d.day}-{d.year}"
            if slug in res: continue
            r = by_slug(slug)
            if r is None: print("FAIL", slug, flush=True); continue
            if not r: res[slug] = None; continue
            e = r[0]
            res[slug] = {
                "id": e["id"], "title": e["title"], "slug": slug, "city": city, "kind": kind, "date": d.isoformat(),
                "startDate": e.get("startDate"), "endDate": e.get("endDate"), "closedTime": e.get("closedTime"),
                "volume": e.get("volume"), "resolutionSource": e.get("resolutionSource"),
                "desc_head": (e.get("description") or "")[:400],
                "markets": [{
                    "id": m["id"], "title": m.get("groupItemTitle"), "tokens": json.loads(m["clobTokenIds"]) if m.get("clobTokenIds") else None,
                    "outcomePrices": m.get("outcomePrices"), "volume": m.get("volume"), "closedTime": m.get("closedTime"),
                    "createdAt": m.get("createdAt"), "fees": m.get("feeSchedule"), "conditionId": m.get("conditionId"),
                } for m in e["markets"]],
            }
            time.sleep(0.12)
    if d.day % 10 == 0:
        json.dump(res, open(out, "w")); print("progress", d, len(res), flush=True)
    d += dt.timedelta(days=1)
json.dump(res, open(out, "w"))
print("done", len(res), "found", sum(1 for v in res.values() if v))
