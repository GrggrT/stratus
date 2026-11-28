# Predico shadow run — Belgian solar, day-ahead

A live rehearsal of taking part in the [Predico](https://predico-elia.inesctec.pt/docs/) forecast
market, started on 2026-11-26. **Nothing is submitted anywhere and there is no account**: each
day a forecast for the next day is produced before the market's gate, stored here with a
timestamp, and later scored against the measurements and against the grid operator's own
forecast.

The question it answers: does the result of the backtest
(`../2026-11-review/results/predico_backtest.txt`) hold up day after day, in winter, when the
forecast really has to be ready before 10:00 Brussels time?

## Rules followed

- Target: total solar generation in Belgium, 15-minute mean power in MW, for the next
  calendar day 00:00–24:00 Brussels time. Three values per interval: Q10, Q50, Q90.
- Gate: 10:00 Brussels time on the day before. A forecast made later is marked
  `"before_gate": false` and counts as a missed day.
- Truth: Elia Open Data, dataset `ods209`, field `measured_upscaled`, region Belgium, all grid
  connection types. Elia moved its solar data there on 2026-10-26; the older `ods032` stops on
  2026-10-25.
- Reference: Elia's own day-ahead forecast published at 11:00 (`dayahead11hforecast` with its
  P10/P90 band) on the same timestamps.
- Scores, as on the platform: daily RMSE of Q50 and daily mean Winkler score of the Q10–Q90
  band (α = 0.2), both in MW over all 96 intervals, averaged over days.

## Model

The model of the backtest, unchanged: gradient boosting on six public weather models (IFS,
ICON-EU, ARPEGE, GFS, ICON-D2, AROME) at eight points; the point forecast is the mean of a
load-factor model and a clear-sky-ratio model, scaled by a trailing bias factor (measured over
forecast energy on delivery days D-9 … D-2, clipped to ±10%); the band comes from two quantile
models, widened by a constant calibrated on June–October 2026.

`train_features.csv.gz` is the training table (2025-01-01 … 2026-10-25, daylight intervals
only, about 32,000 rows), kept in the repository so that a fresh machine can refit the model
in under a minute without downloading two years of forecasts again.

## Weather inputs

Forecasts are read from the Open-Meteo open-data bucket on AWS (`s3://openmeteo`, licence
CC-BY-4.0) with HTTP range requests, a few kilobytes per point. This is the same processed
data the Open-Meteo API serves, without its request quota, which the free API on a shared
address could not guarantee every morning.

`om_source.py` was checked against API responses for the same model run: ICON-EU and GFS match
exactly, AROME and ARPEGE after undoing a unit factor the archive keeps for historical reasons,
ICON-D2 within about 1%. IFS is stored three-hourly and is interpolated here; its clear-sky
index differs from the API's hourly value by 0.05 on average. Forecasts made from bucket inputs
and from API inputs differ by 0.4% of capacity (RMSE over nine days) against a forecast error
of about 1.6%.

The daily run starts at 09:17 Brussels time and takes about two minutes. It uses whatever run
of each model is in the bucket at that moment, and `forecasts/<day>.json` records which. In
practice that is the 06Z run of ICON-D2, the 03Z run of ICON-EU and AROME, the 00Z run of
ARPEGE and GFS. The 00Z run of IFS reaches the bucket between about 07:50 and 08:30 UTC or
later: on 2026-11-26 it had not arrived by 08:27 UTC, so that day's forecast used the IFS run
of 18Z the evening before. The backtest and the hindcast assumed the 00Z run of IFS every day,
which a live entry cannot always have; the final comparison should split the live days by the
IFS run they used.

## Files

- `forecasts/<day>.csv` — `datetime_utc` (interval start), `q10_mw`, `q50_mw`, `q90_mw`, and
  `q50_raw_lf`, the point forecast as a load factor before the bias factor.
- `forecasts/<day>.json` — when it was made, the gate, the model runs used, the bias factor,
  the capacity used. `kind` is `live` or `hindcast`.
- `scores.csv` — one row per delivery day with complete measurements.

Days marked `hindcast` (2026-10-26 … 2026-11-26) were produced after the fact from archived
runs that had been published before each day's gate: the 00Z run of the global models and the
03Z run of the regional ones. They show how the model does in late autumn, and they give the
bias factor its history. They are not counted as live days.

## Running it

```bash
./run_daily.sh
```

That creates `.venv` from `requirements.txt` if it is missing, forecasts tomorrow, scores every
stored day that has measurements, commits and pushes. Single steps:

```bash
.venv/bin/python shadow.py forecast
```

```bash
.venv/bin/python shadow.py score
```

```bash
.venv/bin/python shadow.py hindcast 2026-10-26 2026-11-26
```

```bash
.venv/bin/python shadow.py build-train
```

`build-train` needs the downloads of the backtest (`../2026-11-review/data/predico/`).

## What this run can and cannot show

- It shows whether the pipeline makes the gate every day, and it catches gross failures in
  winter conditions (snow, fog, low sun) that the June–October backtest could not contain.
- It cannot settle a difference of a few percent on its own: three weeks of December days are
  too few and too dark. The judgement at the end pools the backtest (146 days), the hindcast
  and the live days.
- The scores use the measurements as published the next morning. Elia revises them for about
  a month, so early scores move slightly.

Planned end: delivery day 2026-12-17, 21 live days.

Weather data by [Open-Meteo.com](https://open-meteo.com) (CC-BY-4.0), from DWD, ECMWF,
Météo-France and NOAA. Generation data by Elia Open Data.
