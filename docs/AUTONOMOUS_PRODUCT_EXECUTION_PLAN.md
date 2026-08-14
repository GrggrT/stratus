# STRATUS autonomous product execution plan

Status: active

Owner: Codex engineering/product owner

Started: 2026-07-29

Scope: STRATUS weather-market research, PAPER execution, production hardening,
evidence collection, operator experience, and quantitative promotion.

## 1. Product outcome

STRATUS is complete only when it is:

1. operationally safe and recoverable;
2. reproducible from a reviewed immutable image;
3. continuously observable from the operator console and audited data;
4. statistically demonstrated to add value after realistic execution costs;
5. legally and operationally eligible for the intended operator;
6. capable of a tightly capped LIVE canary without bypassing any gate.

Engineering can deliver a production-grade system. It cannot promise profit.
Profitability is an empirical release condition measured on prospective,
unaltered evidence; it is never inferred from green tests or one favorable
sample.

## 2. Non-negotiable safety invariants

- No automation, agent, console, Telegram path, or operator command may enable
  LIVE before every quantitative, operational, legal, and explicit-approval
  gate passes.
- Binance remains `enabled + backtest + kill_switch=true` until a separate
  Binance release protocol is approved.
- During the current release, only Polymarket PAPER may be resumed.
- Every PAPER resume is bound to one short PostgreSQL-clock safety lease and
  one exact audited request identity.
- The operator API, runtime database login, safety API, and safety database
  login use distinct credentials. Runtime cannot read or mutate lease rows;
  its only kill-off path is the DB-owned exact-resume function.
- Applying revision 0047 permanently latches that schema to Polymarket PAPER,
  including the interval before the first lease exists. Any future LIVE
  transition requires a separate audited
  fence-retirement migration after all release gates; a PAPER lease can never
  authorize LIVE.
- Collector death, lease expiry, API ambiguity, scheduler divergence, identity
  drift, unexpected operator activity, or evidence corruption fails closed.
- Emergency stop precedes diagnostic collection.
- No release reuses historical evidence across a schema, image, cohort, or
  model boundary without an explicit continuity decision.
- A successful canary ends fail-closed; it does not leave trading running.

## 3. Definition of the current production candidate

The current candidate is accepted only after all of the following are true:

- migration head, model, API, worker, safety-control, and console contracts
  agree;
- the independent safety-control process and DB watchdog are healthy;
- the emergency stop is atomic, idempotent, venue-isolated, and directly
  confirmed in PostgreSQL;
- every path that can clear a kill switch is lease-gated;
- the scheduler has one composite execution-evidence owner with no overlapping
  legacy jobs;
- global mark obligations have no post-grace gaps or finalizable unsealed rows;
- API and database snapshots are coherent and identity-bound;
- the exact immutable image passes unit, integration, migration, restore,
  static-analysis, and production-size load gates;
- a successor evidence epoch starts on that exact image;
- a 60-minute Polymarket PAPER canary produces immutable T+0/5/15/30/45/60
  evidence with no safety divergence;
- terminal stop/release evidence proves Polymarket PAPER is fail-closed and
  Binance is unchanged.

Natural fills, marks, seals, enrollments, and outcomes are reported separately.
Their absence does not falsify runtime stability, and runtime stability does
not falsely claim that execution-quality cases were observed.

## 4. Execution queue

| Order | Workstream | Exit condition |
|---:|---|---|
| 1 | Independent safety control | Separate API/DB credentials, runtime RLS isolation, atomic stop, DB-clock lease/watchdog, all kill-off paths gated, real-login and drift tests pass |
| 2 | Canary collector | Stop-first, bounded I/O, exact command and obligation identities, scheduler-contention detection, rolling resource gates, immutable evidence, adversarial contract passes |
| 3 | Candidate build | One content-addressed image for API, worker, and safety-control; revision and release digest match |
| 4 | Release verification | Full CI, PostgreSQL 16 migrations/integration, restore proof, supply-chain and production-load gates pass |
| 5 | Successor epoch and API-only rollback proof | New cohort/epoch bound to exact image, schema, model manifest, and continuity decision; with kills engaged and the worker still stopped, the retained predecessor API fails closed on the active successor identity, cannot relabel evidence, and is replaced by the exact matching candidate API |
| 6 | Cold-worker gate | Four clean composite scheduler cycles under kill switches; no LIVE, contention, restart, or evidence debt |
| 7 | PAPER canary | Exact audited resume, 60-minute bounded run, atomic terminal stop/release, sealed manifest |
| 8 | Evidence accumulation | Prospective PAPER outcomes grow without changing the promoted cohort |
| 9 | Accuracy program | Resolution-source fidelity, model ensembles, bias/regime/latency studies, and execution attribution promoted only by holdout evidence |
| 10 | Limited LIVE decision | Legal/KYC/tax clearance, all live-gate checks, independent review, explicit operator approval, tiny capped canary |

## 5. Quantitative promotion gates

LIVE remains blocked until the implemented gate passes in full. At minimum the
evidence must show:

- sufficient closed positions and independent station-day-kind samples;
- calibrated stations with adequate prospective sample sizes;
- positive CLV and a robust CLV beat rate;
- model Brier score better than the contemporaneous market benchmark;
- ROI confidence interval excluding zero after costs;
- statistically positive skill P&L rather than luck-only P&L;
- no unresolved resolution-source or structural-break concern;
- realistic maker/taker, fill, fee, spread, latency, and adverse-selection
  attribution.

Thresholds are changed only through a reviewed protocol and never to make an
existing sample pass.

## 6. Operating cadence

### Every automated guard interval

- verify runtime/venue modes, kill switches, lease, image identity, zero LIVE,
  unexpected commands, scheduler freshness, execution obligations, continuity,
  health/readiness, and bounded resources;
- fail closed immediately on a hard violation.

### Every day

- inspect provider freshness and official-resolution reconciliation;
- inspect newly resolved PAPER positions, Brier/CLV/skill/luck, markouts, fill
  quality, and evidence completeness;
- create an incident for any unexplained gap rather than silently repairing
  history.

### Every week

- publish the edge report and stable-slice diagnostics;
- review station qualification, source changes, structural breaks, and
  candidate experiments;
- promote no model or rule without prospective/holdout acceptance.

### Every release

- use `docs/RELEASE_CUTOVER_RUNBOOK.md`;
- update `docs/PRODUCTION_READINESS_CHECKLIST.md`;
- preserve exact manifests, hashes, migration evidence, CI results, backup
  restore proof, and canary artifacts;
- leave runtime fail-closed unless the next explicitly authorized PAPER phase
  begins.

## 7. Decision rights

Codex may autonomously:

- research, design, implement, test, document, review, and refactor within this
  repository;
- run read-only diagnostics and local/integration test infrastructure;
- build candidate images and prepare release artifacts;
- resume only Polymarket PAPER when every documented precondition is proven and
  the user has authorized that PAPER scope.

Codex may not autonomously:

- enable LIVE or weaken a live gate;
- change Binance out of killed backtest mode;
- spend real funds, bypass legal/KYC/tax requirements, or claim guaranteed
  returns;
- erase or rewrite audit/evidence history;
- deploy an unreviewed image or mutate production to make a failing gate pass.

## 8. Current disposition

As of the current code-only hardening phase, production PostgreSQL, API, worker, and
`safety-control` are deliberately stopped; Polymarket PAPER collection and execution are
paused. An already-running backup sidecar may remain running, but it is not authority to start
PostgreSQL and its status without a running production database is not backup-freshness
evidence. Apart from that sidecar, only disposable candidate/test infrastructure may run until
the immutable-image transition is recorded.

The production runtime remains deliberately fail-closed. The independent
safety lease/watchdog and hardened PAPER-canary collector are implemented in
the working tree and have focused/adversarial contract coverage; they are not
deployed or release-accepted. Exact-candidate PostgreSQL/PowerShell/full-CI
verification, immutable image build, 0046 backup plus 0047 migration,
production-size load gate, successor epoch plus post-activation/pre-worker API-only rollback
proof, cold-worker proof, and the
60-minute PAPER canary follow in that order. The detailed engineering evidence
and status remain authoritative in:

- `docs/QUALITY_ROADMAP.md`
- `docs/PRODUCTION_READINESS_CHECKLIST.md`
- `docs/RELEASE_CUTOVER_RUNBOOK.md`
- `docs/PAPER_CANARY_SAFETY_LEASE.md`
- `docs/incidents/2026-07-29_EXECUTION_MARKOUT_STARVATION.md`
