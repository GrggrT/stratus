# Austria live-readiness memo

Status date: 2026-07-06. This is a technical compliance review, not legal or tax advice.

> **Operator decision, 2026-07-07.** The operator chose to proceed with the
> live executor implementation at small size without obtaining the written
> legal and tax opinions below, accepting the personal risk. Note that the
> legal uncertainty described here concerns participation as such, not a
> turnover threshold. The `LIVE_LEGAL_APPROVED` / `LIVE_TAX_APPROVED` flags
> remain in the configuration and are set exclusively by the operator in
> `.env`; the runtime, the quantitative paper gate, the geoblock check
> before every order, the double confirmation, and the kill switch stay
> mandatory and unchanged.

## Decision

Live trading is **blocked**. Technical access from Austria is currently available, but that
does not establish Austrian legality. Before live, STRATUS requires both:

1. a written Austrian legal opinion classifying peer-to-peer weather prediction contracts;
2. a written Austrian tax opinion covering automated, repeated trading and pUSD/USDC flows.

The runtime defaults `LIVE_LEGAL_APPROVED=false` and `LIVE_TAX_APPROVED=false`. Both flags,
the quantitative paper gate, second-message confirmation, and a deployed live executor are
independent requirements.

## Platform access and KYC

- Polymarket's current restricted-country list does not contain Austria. The official
  geoblock endpoint returned `blocked=false`, `country=AT`, `region=3` for this deployment IP
  on 2026-07-06. This result can change and must be checked immediately before every live
  order. [Geographic restrictions](https://help.polymarket.com/en/articles/13364163-geographic-restrictions),
  [geoblock API](https://docs.polymarket.com/api-reference/geoblock).
- Physical location, not residence, controls Polymarket's geoblock. VPN/proxy circumvention is
  prohibited. The production host and the operator must therefore both remain in an eligible
  location.
- Public signup documentation describes email, Google, and wallet signup without a universal
  identity-verification step. API trading uses wallet signatures plus L2 API credentials.
  Polymarket explicitly mentions KYC/KYB for direct server co-location, and may still request
  verification for compliance. Absence of a documented signup KYC step is not a guarantee
  that KYC will never be required. [Signup](https://help.polymarket.com/en/articles/13369877-how-to-sign-up),
  [API authentication](https://docs.polymarket.com/api-reference/authentication),
  [trading overview](https://docs.polymarket.com/trading/overview).

## Austrian legal risk

- Austrian GSpG §1 defines gambling by an outcome depending exclusively or predominantly on
  chance; §3 reserves gambling to the federal monopoly. Foreign unauthorized gambling can be
  prohibited. [Current GSpG](https://www.ris.bka.gv.at/GeltendeFassung.wxe?Abfrage=Bundesnormen&Gesetzesnummer=10004611).
- The Austrian Finance Ministry states that offering foreign/illegal online gambling and
  participating from Austria in foreign electronic lotteries is not permitted. The ministry
  also says its FAQ is a non-binding legal view and cannot replace individual advice.
  [BMF gambling FAQ](https://www.bmf.gv.at/themen/gluecksspiel-spielerschutz/gesetzliche-grundlagen-gluecksspiel/faq-gluecksspielmonopol.html).
- Polymarket is peer-to-peer and a weather outcome is determined by an external observation,
  not by a central random-number generator. It is therefore unresolved whether an Austrian
  authority would classify this product as an electronic lottery, another gambling product,
  an event bet, or a financial instrument.
- The NÖ Wettgesetz definitions cover bets occasioned by sporting events and do not clearly
  cover weather outcomes. This does not make weather contracts lawful; it leaves the applicable
  regime unclear. [NÖ WettG §3](https://ris.bka.gv.at/NormDokument.wxe?Abfrage=LrNO&Gesetzesnummer=20001287&Paragraf=3),
  [licensed Austrian operators](https://www.bmf.gv.at/themen/gluecksspiel-spielerschutz/gesetzliche-grundlagen-gluecksspiel/konzessionaere-ausspielbewilligte.html).

Required legal opinion: classification of Polymarket weather contracts for a participant
physically located in Lower Austria; legality of repeated API trading; effect of Polymarket's
foreign operator status; and whether any player-side administrative or criminal exposure exists.

## Austrian tax risk

- BMF states that asset increases outside the seven income categories, including gambling and
  lottery winnings, are not subject to income tax. The tax guidelines also list gambling and
  betting winnings as non-taxable in principle. [BMF income concept](https://www.bmf.gv.at/themen/steuern/fuer-unternehmen/einkommensteuer/einkommensbegriff.html),
  [EStR paragraph 101](https://findok.bmf.gv.at/findok/volltext%28suche%3AStandardsuche%29?segmentId=6ad0fb3a-f59c-4923-93c4-3ba4a4b3c247).
- STRATUS is automated, sustainable, organized, and explicitly profit-seeking. EStG §23 treats
  self-employed, sustainable, profit-oriented participation in general economic activity as
  business income. Whether own-account prediction-market trading crosses that threshold is a
  fact-specific question and cannot be resolved from the general gambling-winnings rule.
  [EStG §23](https://www.ris.bka.gv.at/NormDokument.wxe?Abfrage=Bundesnormen&Gesetzesnummer=10004570&Paragraf=23).
- pUSD is backed by USDC, and Austrian BMF notes that stablecoins can fall under the crypto tax
  regime. Realized crypto appreciation is generally taxed at 27.5%. Prediction-contract P&L and
  any separate pUSD/USDC/EUR value change must be tracked independently.
  [BMF crypto taxation](https://www.bmf.gv.at/themen/steuern/sparen-veranlagen/steuerliche-behandlung-von-kryptowaehrungen.html).

Required tax opinion: private windfall versus business income; treatment of contract shares;
pUSD/USDC acquisition cost and disposal; deductibility of fees, infrastructure, and losses;
recordkeeping and declaration forms.

## Evidence to retain

- Every order, fill, fee, position, resolution, and CLV record.
- Wallet transaction hashes and daily pUSD/USDC/EUR reference values.
- Deposits, withdrawals, bridge/swaps, gas, and third-party fees.
- Daily geoblock response and the then-current Polymarket terms/restriction page.
- Written legal and tax opinions supporting both approval flags.
