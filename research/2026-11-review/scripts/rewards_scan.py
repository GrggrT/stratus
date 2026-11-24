import json, requests, time, re, collections, statistics as st
S = requests.Session()
def fetch(q):
    out = []; cur = ""
    while True:
        p = {"q": q, "page_size": 500}
        if cur: p["next_cursor"] = cur
        r = S.get("https://clob.polymarket.com/rewards/markets/multi", params=p, timeout=60)
        r.raise_for_status(); j = r.json()
        out += j["data"]; cur = j.get("next_cursor")
        if not cur or cur == "LTE=" or not j["data"]: break
        time.sleep(0.2)
    return out
rows = fetch("temperature")
json.dump(rows, open("data/reward_markets_temperature.json","w"))
print("reward markets matching 'temperature':", len(rows))
def rate(r): return sum(c.get("rate_per_day",0) for c in r.get("rewards_config") or [])
act = [r for r in rows if rate(r) > 0 and not r["market_slug"].startswith("polyv2")]
print("with active rate:", len(act), "| total $/day:", round(sum(rate(r) for r in act)))
ev = collections.defaultdict(list)
for r in act: ev[r["event_slug"]].append(r)
print("events with rewards:", len(ev))
def parse(slug):
    m = re.match(r"(highest|lowest)-temperature-in-(.+)-on-([a-z]+)-(\d+)-(\d{4})", slug)
    return (m.group(1), m.group(2), f"{m.group(3)}-{m.group(4)}") if m else ("?", slug, "?")
byday = collections.defaultdict(float); bykind = collections.defaultdict(float)
for s, rs in ev.items():
    k, c, d = parse(s); byday[d] += sum(rate(r) for r in rs); bykind[(k, d)] += sum(rate(r) for r in rs)
print("by event date:", {k: round(v) for k, v in sorted(byday.items())})
print("by kind/date:", {k: round(v) for k, v in sorted(bykind.items())})
comp = [r["market_competitiveness"] for r in act]
zero = [r for r in act if r["market_competitiveness"] == 0]
print("competitiveness quantiles:", [round(x,1) for x in st.quantiles(comp, n=10)])
print("markets with zero competitiveness:", len(zero), "| $/day in them:", round(sum(rate(r) for r in zero)))
low = [r for r in act if 0 < r["market_competitiveness"] < 50]
print("markets with 0<comp<50:", len(low), "| $/day:", round(sum(rate(r) for r in low)))
# naive share estimate for a newcomer with Q=12 (20 shares both sides at 1c from mid)
for Q in (12, 60, 240):
    est = sum(rate(r) * Q / (r["market_competitiveness"] + Q) for r in act)
    print(f"naive newcomer share with Q={Q}: ${est:,.0f}/day across all (upper bound, assumes competitiveness == sum of others' Q)")
# per-event examples
tops = sorted(ev.items(), key=lambda kv: -sum(rate(r) for r in kv[1]))[:8]
for s, rs in tops:
    print(s, "| $/day", round(sum(rate(r) for r in rs)), "| comps", [round(r["market_competitiveness"]) for r in sorted(rs, key=lambda r: -rate(r))][:6], "| rates", [rate(r) for r in sorted(rs, key=lambda r: -rate(r))][:6], "| spreads", [r["spread"] for r in sorted(rs, key=lambda r: -rate(r))][:6])
for city in ("nyc","london","paris"):
    for s, rs in sorted(ev.items()):
        if f"-in-{city}-on-" in s:
            print(s, "| $/day", round(sum(rate(r) for r in rs)), "| comps", [round(r["market_competitiveness"]) for r in sorted(rs, key=lambda r: -rate(r))][:5], "| rates", [rate(r) for r in sorted(rs, key=lambda r: -rate(r))][:5], "| vol24", round(sum(r.get("volume_24hr") or 0 for r in rs)))
