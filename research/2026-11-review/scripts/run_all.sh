#!/usr/bin/env bash
# Re-run every analysis and capture its text output into results/. Run from the research/2026-11-review directory.
set -u
PY=${PY:-python3}
mask() { sed -E 's/(0x[0-9a-f]{8})[0-9a-f]{32}/\1…/g' | grep -v -i -E "warning|tarr = np.full|print\(" ; }
mkdir -p results
$PY scripts/a_taker_pnl.py      2>&1 | mask > results/a_taker_pnl.txt
$PY scripts/b_calibration.py    2>&1 | mask > results/b_calibration.txt
$PY scripts/c_dead_buckets.py   2>&1 | mask > results/c_dead_buckets.txt
$PY scripts/d_nowcast.py        2>&1 | mask > results/d_nowcast.txt
$PY scripts/e_styles.py         2>&1 | mask > results/e_styles.txt
$PY scripts/f_makers.py         2>&1 | mask > results/f_makers.txt
$PY scripts/g_mm_replay.py      2>&1 | mask > results/g_mm_replay.txt
$PY scripts/h_sampler_analysis.py 2>&1 | mask > results/h_sampler_analysis.txt
echo done
