import json, re, requests, time, datetime as dt
S = requests.Session()
def by_slug(slug):
    for a in range(3):
        try:
            r = S.get("https://gamma-api.polymarket.com/events", params={"slug": slug}, timeout=40)
            if r.status_code == 200: return r.json()
        except Exception as ex: pass
        time.sleep(2)
    return []
months = ["january","february","march","april","may","june","july","august","september","october","november","december"]
def slug(city, kind, d, year=True):
    s = f"{kind}-temperature-in-{city}-on-{months[d.month-1]}-{d.day}"
    return s + (f"-{d.year}" if year else "")
dates = [dt.date(2025,3,15), dt.date(2025,6,15), dt.date(2025,9,15), dt.date(2025,12,15), dt.date(2026,2,15), dt.date(2026,4,15), dt.date(2026,6,15), dt.date(2026,7,10), dt.date(2026,8,15), dt.date(2026,9,15), dt.date(2026,10,15), dt.date(2026,11,10)]
for d in dates:
    for city in ["nyc","london","paris"]:
        res = by_slug(slug(city,"highest",d)) or by_slug(slug(city,"highest",d,False))
        if not res:
            print(d, city, "NOT FOUND"); continue
        e = res[0]
        desc = e.get("description","")
        src = "METAR-rule" if "hourly temperature remark" in desc else ("WU" if "wunderground" in desc.lower() or "Weather Underground" in desc else "other")
        m0 = e["markets"][0]
        st = re.findall(r"\(([A-Z]{4})\)", desc)
        print(d, city, "| n", len(e["markets"]), "| end", e.get("endDate"), "| closed", e.get("closedTime"), "| vol", round(float(e.get("volume") or 0)), "|", src, st[:1], "| fee", m0.get("feesEnabled"), m0.get("feeSchedule"), "| mech", m0.get("resolutionMechanism"), "| rsrc", (e.get("resolutionSource") or "")[:70])
        if d in (dt.date(2026,7,10), dt.date(2025,6,15)) and city=="nyc":
            print("   DESC:", desc[:1200].replace("\n"," "))
