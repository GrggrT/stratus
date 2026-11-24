# Review evidence pack — 2026-11-24

Reproducible measurements behind `docs/STRATEGY_REVIEW_2026-11.md`. Everything here reads
public endpoints only (Polymarket Gamma / CLOB / Data API v2, aviationweather.gov, IEM); no
wallet, no credentials, no orders.

This directory is research tooling, not part of the `stratus` package: it is outside the
`ruff` / `mypy` / `pytest` scope on purpose and must never be imported by `src/`.

## Layout

- `scripts/` — fetchers and analyses (plain scripts, run from this directory).
- `results/` — captured text output of each analysis as run on 2026-11-24. Wallet addresses
  are truncated to their first ten characters.
- `data/` — raw downloads (git-ignored, ~120 MB). Recreate with the fetchers below.

## Reproduce

```bash
cd research/2026-11-review
python3 -m venv .venv && .venv/bin/pip install numpy pandas requests
```

```bash
.venv/bin/python scripts/fetch_events.py nyc,london,paris 2026-07-01 2026-11-23 data/events_3city.json
```

```bash
.venv/bin/python scripts/fetch_iem.py 2019 2026
```

```bash
.venv/bin/python scripts/fetch_trades.py data/events_3city.json 2026-09-01 9999 true
```

```bash
.venv/bin/python scripts/fetch_trades.py data/events_3city.json 2026-10-20 9999 false
```

```bash
.venv/bin/python scripts/reward_sampler.py 60 180
```

```bash
.venv/bin/python scripts/fetch_events_all.py 2026-11-10 2026-11-23 data/events_all.json
```

```bash
.venv/bin/python scripts/fetch_trades.py data/events_all.json 2026-11-10 9999 true
```

```bash
PY=.venv/bin/python scripts/run_all.sh
```

`run_all.sh` covers analyses `a`–`h`; run `i_all_cities.py` separately after the all-city
download.

`parser_check.py` takes the live snapshot written by `gamma_probe.py`:

```bash
.venv/bin/python scripts/gamma_probe.py
```

```bash
uv run python research/2026-11-review/scripts/parser_check.py research/2026-11-review/data/active_temp_events.json
```

`fetch_iem.py` is rate-limited by IEM (expect 429s; the script backs off). The settlement
check needs the official resolution URLs, which aviationweather.gov serves for about 15 days,
so `settle_recon.py <start-date>` only works for recent events. The two parser checks import
the repository's own parser and need the project environment:

```bash
uv run python research/2026-11-review/scripts/parser_history.py
```

## What each script measures

| Script | Question | Result file |
|---|---|---|
| `gamma_probe.py` | Which daily temperature events are live right now? Writes `data/active_temp_events.json` | — |
| `rule_history.py` | How did the rule text, station, bucket count, fees and volume change over 2025–2026? | — |
| `settle_recon.py` | Can the published rule be replicated exactly from the official METAR feed? | `settle_recon.txt` |
| `parser_check.py`, `parser_history.py` | Does STRATUS's own parser still find the station? Since when not? | `parser_check_live_2026-11-24.txt`, `parser_history.txt` |
| `a_taker_pnl.py` | Who makes money: takers or makers? By phase, price band, wallet | `a_taker_pnl.txt` |
| `b_calibration.py` | Are prices calibrated (favourite–longshot bias), event-clustered | `b_calibration.txt` |
| `c_dead_buckets.py` | How fast does the market kill buckets that a METAR made impossible? | `c_dead_buckets.txt` |
| `d_nowcast.py` | Does an observation-only analog nowcast beat the market intraday? | `d_nowcast.txt` |
| `e_styles.py` | Taker P&L by market age, skill persistence, winner styles, CLV degeneracy | `e_styles.txt` |
| `f_makers.py` | Maker-side P&L before rewards, concentration, where makers bleed | `f_makers.txt` |
| `g_mm_replay.py` | Adverse selection of a naive min-size market-following quoter (tape replay) | `g_mm_replay.txt` |
| `rewards_scan.py`, `comp_check.py`, `reward_sampler.py`, `h_sampler_analysis.py` | Size of the liquidity-reward pool and how contested it is | `h_sampler_analysis.txt` |
| `fetch_events_all.py`, `i_all_cities.py` | Do the taker/maker anatomy and the naive-quoter cost carry over to all 49 cities? | `i_all_cities.txt` |

## Known limitations

- Main sample: three cities (NYC, London, Paris), 2026-09-01 … 2026-11-23, 504 events.
  `i_all_cities.py` repeats the two headline measurements on all 49 cities for 2026-11-10 …
  2026-11-23; sniping, maker-side and persistence analyses cover the three cities only.
- Fees use each market's stored `feeSchedule` (0.01, and 0 during the Oct 19–31 fee holiday).
  Gross numbers do not depend on this.
- There is no public historical order book. The maker replay and the reward-share estimate
  therefore rest on the trade tape and on live book samples; both are stated as bounds in the
  review, not as forecasts of income.
- `d_nowcast.py` is a deliberately simple baseline (analog days, no NWP). It answers "is a
  trivial model enough?", not "what is the best possible nowcast?".
