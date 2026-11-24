import json, sys, collections
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))  # repo src/
from stratus.parsers.market_rules import (extract_station_candidates, parse_event_identity, parse_bucket_boundaries, parse_fee_schedule, parse_outcome_tokens, MarketRulesError)
evs = json.load(open(sys.argv[1]))
res = collections.Counter(); ex = {}
for e in evs:
    try:
        ident = parse_event_identity(e)
    except MarketRulesError as exc:
        res["identity_fail"] += 1; continue
    if ident.city not in ("New York City","London","Paris"): 
        continue
    cands = extract_station_candidates(str(e.get("description") or ""), e.get("resolutionSource"))
    res[f"{ident.city}|{ident.kind.value}|cands={sorted(cands)}"] += 1
    for m in e["markets"]:
        try:
            parse_bucket_boundaries(str(m.get("groupItemTitle") or "")); parse_outcome_tokens(m); parse_fee_schedule(m)
        except MarketRulesError as exc:
            res[f"market_fail:{exc}"[:80]] += 1
for k, v in sorted(res.items()): print(v, k)
