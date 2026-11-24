import json, sys, requests
S = requests.Session()
def page(offset, closed="false", active="true", extra=None):
    p = {"tag_id": 84, "limit": 100, "offset": offset, "closed": closed}
    if active is not None: p["active"] = active
    if extra: p.update(extra)
    r = S.get("https://gamma-api.polymarket.com/events", params=p, timeout=40)
    r.raise_for_status()
    return r.json()
evs = []
off = 0
while True:
    pg = page(off)
    evs += pg
    if len(pg) < 100: break
    off += 100
print("active weather events:", len(evs))
import re, collections
temp = [e for e in evs if re.match(r"^(highest|lowest)\s+temperature\s+in\s+", e.get("title",""), re.I)]
print("daily temperature events:", len(temp))
cities = collections.Counter()
for e in temp:
    m = re.match(r"^(highest|lowest)\s+temperature\s+in\s+(.+?)\s+on\s+", e["title"], re.I)
    cities[(m.group(1).lower(), m.group(2))] += 1
for k, v in sorted(cities.items(), key=lambda kv: (-kv[1], kv[0])):
    print(v, k)
json.dump(temp, open("data/active_temp_events.json", "w"))
other = collections.Counter(re.sub(r"\d+", "#", e.get("title",""))[:60] for e in evs if e not in temp)
print("--- other weather titles ---")
for k, v in other.most_common(40): print(v, k)
