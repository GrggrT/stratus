"""Three one-shot facts about Kalshi's daily temperature markets, from primary sources.

Usage: python scripts/kalshi_facts.py

1. Fee type of the temperature series (do resting orders pay a fee?) and the settlement source.
2. Whether any active liquidity-incentive programme sits on a daily temperature series.
3. Whether Austria is among the restricted jurisdictions of the Member Agreement
   (needs the `pdftotext` binary; skipped when it is missing).
"""
import collections, re, shutil, subprocess, tempfile
import requests

API = "https://api.elections.kalshi.com/trade-api/v2"
AGREEMENT = "https://kalshi-public-docs.s3.amazonaws.com/regulatory/agreement/kalshi_member_agreement.pdf"
S = requests.Session()
S.headers.update({"Accept": "application/json", "User-Agent": "research-client/1.0"})

print("1. Series")
for ticker in ("KXHIGHNY", "KXHIGHCHI", "KXLOWTNYC"):
    s = S.get(f"{API}/series/{ticker}", timeout=60).json()["series"]
    src = ", ".join(x.get("name", "") for x in s.get("settlement_sources") or [])
    print(f"   {ticker:10s} fee_type={s.get('fee_type')} fee_multiplier={s.get('fee_multiplier')} settlement={src}")
print("   fee_type 'quadratic' = taker-only fee; maker fees exist only on 'quadratic_with_maker_fees' series")

print("2. Active incentive programmes")
d = S.get(f"{API}/incentive_programs", params={"status": "active", "limit": 10000}, timeout=120).json()
items = d.get("incentive_programs") or []
weather = [i for i in items if re.match(r"KX(HIGH|LOW)", i.get("series_ticker") or i.get("market_ticker") or "")]
# period_reward is in centi-cents ($0.0001)
total = sum(float(i.get("period_reward") or 0) for i in items) / 10000
print(f"   programmes {len(items)}, total period reward ${total:,.0f}, more pages: {bool(d.get('next_cursor'))}")
print(f"   on daily temperature series (KXHIGH*/KXLOW*): {len(weather)}")
print("   by type:", dict(collections.Counter(i.get("incentive_type") for i in items)))

print("3. Member Agreement, restricted jurisdictions")
if not shutil.which("pdftotext"):
    print("   skipped: pdftotext is not installed")
else:
    with tempfile.TemporaryDirectory() as tmp:
        pdf = f"{tmp}/agreement.pdf"
        open(pdf, "wb").write(S.get(AGREEMENT, timeout=120).content)
        text = subprocess.run(["pdftotext", "-layout", pdf, "-"], capture_output=True, text=True, check=True).stdout
    flat = re.sub(r"\s+", " ", text)
    m = re.search(r"any of the following jurisdictions \(collectively, the .Restricted Jurisdictions.\): (.*?), or any jurisdiction", flat)
    names = [n.strip() for n in re.split(r",\s*(?:and\s+)?", m.group(1)) if n.strip()] if m else []
    print(f"   listed: {len(names)}")
    eu = ["Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czech Republic", "Czechia", "Denmark", "Estonia",
          "Finland", "France", "Germany", "Greece", "Hungary", "Ireland", "Italy", "Latvia", "Lithuania", "Luxembourg",
          "Malta", "Netherlands", "Poland", "Portugal", "Romania", "Slovakia", "Slovenia", "Spain", "Sweden"]
    print("   EU member states listed:", ", ".join(n for n in eu if n in names) or "none")
    print("   Austria listed:", "Austria" in names)
