"""Download raw METAR history (routine + special) from IEM for stations, yearly chunks."""
import sys, time, requests, os, datetime as dt
S = requests.Session(); S.headers["User-Agent"] = "research-script/0.1"
stations = {"LGA": "NY_ASOS", "EGLC": "GB__ASOS", "LFPB": "FR__ASOS"}
y0 = int(sys.argv[1]); y1 = int(sys.argv[2])
for st, net in stations.items():
    for y in range(y0, y1 + 1):
        out = f"data/iem_{st}_{y}.csv"
        if os.path.exists(out) and os.path.getsize(out) > 1000: continue
        params = [("network", net), ("station", st), ("data", "tmpf"), ("data", "tmpc"), ("data", "metar"),
                  ("year1", y), ("month1", 1), ("day1", 1), ("year2", y + 1), ("month2", 1), ("day2", 1),
                  ("tz", "Etc/UTC"), ("format", "onlycomma"), ("latlon", "no"), ("elev", "no"), ("missing", "null"),
                  ("trace", "null"), ("direct", "no"), ("report_type", "3"), ("report_type", "4")]
        for a in range(5):
            try:
                r = S.get("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py", params=params, timeout=300)
                if r.status_code == 200 and r.text.startswith("station"):
                    open(out, "w").write(r.text); print(st, y, len(r.text), flush=True); break
                print("retry", st, y, r.status_code, r.text[:120], flush=True)
            except Exception as ex:
                print("err", st, y, ex, flush=True)
            time.sleep(20 + 20 * a)
        time.sleep(8)
print("done")
