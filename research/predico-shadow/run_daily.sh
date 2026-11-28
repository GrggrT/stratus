#!/usr/bin/env bash
# One daily step of the shadow run: forecast tomorrow, score the days that have measurements,
# commit and push. Meant to run once a day before 10:00 Brussels time.
set -euo pipefail
cd "$(dirname "$0")"
BRANCH=claude/awesome-maxwell-ag66x2

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

.venv/bin/python shadow.py forecast
.venv/bin/python shadow.py score || echo "scoring failed, forecast is kept"

git add forecasts
[ -f scores.csv ] && git add scores.csv
if git diff --cached --quiet; then
  echo "nothing new to commit"
  exit 0
fi
DAY=$(ls forecasts/*.json | sort | tail -1 | xargs basename | sed 's/\.json$//')
git commit -q -m "Shadow forecast for delivery day ${DAY}

Daily step of the Predico shadow run: Q10/Q50/Q90 for Belgian solar,
made before the 10:00 Brussels gate, plus scores for past days.

Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013Cw5bVzwwLN3BPg1EVKxqL"
for i in 1 2 3 4; do
  if git push -q -u origin "$BRANCH"; then
    echo "pushed forecast for ${DAY}"
    exit 0
  fi
  sleep $((2 ** i))
done
echo "push failed" >&2
exit 1
