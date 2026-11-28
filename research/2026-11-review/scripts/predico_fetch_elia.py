"""Download Belgian PV measurements and Elia's own forecasts (Elia Open Data ods032) in monthly chunks."""
import requests, time, io, sys, os
import pandas as pd
S = requests.Session()
cols = "datetime,measured,mostrecentforecast,dayahead11hforecast,dayahead11hconfidence10,dayahead11hconfidence90,dayaheadforecast,dayaheadconfidence10,dayaheadconfidence90,weekaheadforecast,monitoredcapacity"
months = pd.period_range("2024-01", "2026-10", freq="M")
frames = []
for m in months:
    a = m.start_time.strftime("%Y-%m-%dT00:00:00Z"); b = (m + 1).start_time.strftime("%Y-%m-%dT00:00:00Z")
    ok = False
    for attempt in range(4):
        try:
            r = S.get("https://opendata.elia.be/api/explore/v2.1/catalog/datasets/ods032/exports/csv",
                      params={"where": f"region='Belgium' and datetime>='{a}' and datetime<'{b}'", "select": cols, "order_by": "datetime", "delimiter": ","}, timeout=120)
            if r.status_code == 200 and len(r.text) > 200:
                d = pd.read_csv(io.StringIO(r.text)); frames.append(d); ok = True; break
            print("status", m, r.status_code, len(r.text), flush=True)
        except Exception as ex:
            print("err", m, str(ex)[:100], flush=True)
        time.sleep(3 + 3 * attempt)
    print(m, "ok" if ok else "FAILED", len(frames[-1]) if ok else 0, flush=True)
    time.sleep(0.3)
D = pd.concat(frames, ignore_index=True); D.to_csv("data/predico/ods032_be.csv", index=False)
print("rows", len(D), D["datetime"].min(), D["datetime"].max())
