import json, sys, time
import httpx
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))  # repo src/
from stratus.parsers.market_rules import extract_station_candidates, parse_event_identity, parse_bucket_boundaries, parse_outcome_tokens, parse_fee_schedule, MarketRulesError
S = httpx.Client()
for slug in ["highest-temperature-in-nyc-on-july-10-2026", "highest-temperature-in-nyc-on-august-20-2026", "highest-temperature-in-nyc-on-august-25-2026", "highest-temperature-in-london-on-september-15-2026", "highest-temperature-in-paris-on-october-15-2026", "highest-temperature-in-nyc-on-october-25-2026", "lowest-temperature-in-paris-on-november-10-2026"]:
    e = S.get("https://gamma-api.polymarket.com/events", params={"slug": slug}, timeout=40).json()[0]
    ident = parse_event_identity(e)
    c = extract_station_candidates(str(e.get("description") or ""), e.get("resolutionSource"))
    bad = 0
    for m in e["markets"]:
        try:
            parse_bucket_boundaries(str(m.get("groupItemTitle") or "")); parse_outcome_tokens(m); parse_fee_schedule(m)
        except MarketRulesError as exc:
            bad += 1
    mc = [extract_station_candidates(str(m.get("description") or ""), m.get("resolutionSource")) for m in e["markets"]]
    print(f"{slug[-40:]:40} city={ident.city!r:16} event candidates={sorted(c)} market-level candidates(first)={sorted(mc[0])} unparseable_markets={bad} | rsrc={e.get('resolutionSource')!r:.80}")
    time.sleep(0.3)
