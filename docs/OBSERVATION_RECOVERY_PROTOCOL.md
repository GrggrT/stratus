# STRATUS observation-recovery protocol

Status: production-accepted for revision `20260717_0043`; the original cutover evidence is in
section 11. The automatic missed-hour correction was accepted prospectively as epoch 15 on
2026-07-20 and is recorded in section 12. Epoch 16 adds the downstream canonical-truth quarantine
recorded in section 13; it does not change archive collection or recovery completeness rules.

## 1. Purpose

STRATUS normally collects current METAR reports from Aviation Weather and derives a station-local
daily high/low only when the day is demonstrably complete. A worker outage longer than the live
collector lookback can leave an otherwise recoverable local day permanently incomplete. The
recovery protocol fills that evidence gap without weakening completeness, rewriting prospective
history, refreshing station liveness, or pretending that a METAR-derived extreme is the market's
official settlement value.

The protocol is fail-closed. Missing data remains missing unless one immutable archive response,
by itself, passes the same completeness rules as the live path.

## 2. Source authority and limitations

The bounded recovery source is the Iowa Environmental Mesonet ASOS archive. IEM documents its
ASOS download service and states that materials on its website are public domain and may be used
freely for lawful purposes, with attribution appreciated:

- <https://mesonet.agron.iastate.edu/request/download.phtml>
- <https://mesonet.agron.iastate.edu/disclaimer.php>
- <https://mesonet.agron.iastate.edu/api/>

This is a reviewed engineering basis for retaining the fetched IEM artifact, not legal advice and
not a claim that the archive has an SLA or is correction-free. IEM explicitly supplies material
as-is. Each response is therefore captured as immutable content-addressed evidence. A later remote
revision never silently replaces the first accepted station-day.

The reviewed policy statement is identified by SHA-256
`bdd514125aeb91bbde8a4728063b8862010c821d05427572954b239085cc7535`. Changing the source,
retention conclusion, parser contract, or this policy hash requires a new predictive release.
The exact configured IEM ASOS endpoint is independently identified by SHA-256
`7d7cff72adb53f6a374175396a1f86eeab9863c5c26d38bf1aa49adf0bdc5891`. Revision 0043 stores only
that digest in release/runtime evidence; a configured endpoint whose digest differs is rejected
before release-gate evaluation can reach an HTTP request.

## 3. Non-negotiable separations

1. Recovery points never enter `observations`. That table remains the current/live feature source;
   an archive fetch must not acquire a fictitious historical first-seen time or make a stale station
   look healthy.
2. Recovery is label evidence for METAR-derived calibration and QA only. It cannot create or modify
   `settlement_actuals`, official-resolution responses, exact-source coverage, Gamma outcomes, or
   market winners.
3. One accepted station-day is computed from one IEM artifact. Live and archive points are never
   blended to make an incomplete day appear complete.
4. Existing `ModelProb`, `AccuracyPrediction`, signal, order, fill, evaluation, and sealed evidence
   rows are never rewritten after a recovered label arrives.

## 4. Time semantics

- `obs_time` is weather-event time from the normalized report.
- `utc_start` and `utc_end` are the frozen station-timezone bounds for the local target date.
- `request_started_at` and `response_received_at` are HTTP acquisition clocks.
- `first_seen_at` is the database clock at which STRATUS first retained the artifact/label.
- `DailyActual.first_seen_at` is availability for future calculations. It is never backdated to the
  observation, target date, source publication, or requested range.
- PostgreSQL overwrites caller-supplied or null `DailyActual.first_seen_at` on every prospective
  ordinary insert. Such rows and recovered rows are append-only after that database attestation.
- PostgreSQL likewise assigns `HistoricalForecast.first_seen_at`. Historical previous-run
  forecasts are immutable insert-or-compare evidence: an exact retry is idempotent, while a
  different value, source, or fetch time under the same natural identity is a typed conflict.

Every training calculation captures one database-clock cutoff before reading truth. A recovered
actual is eligible only when `first_seen_at <= cutoff`. Legacy rows that pre-date the 0043 boundary
remain nullable and are treated only as pre-boundary historical inputs; no per-row provenance is
invented for them. Previous-run forecast inputs require both `fetched_at <= cutoff` and
`first_seen_at <= cutoff`. A recovery or historical-forecast import concurrent with a fit cannot
leak into that already-open fit.

## 5. Completeness policy

For the exact half-open local-day interval `[utc_start, utc_end)`:

- at least 20 valid temperature observations;
- first observation no more than 90 minutes after local midnight;
- last observation no more than 90 minutes before the next local midnight; and
- no internal observation gap greater than 120 minutes.

Timezone bounds are computed with IANA zone rules, so 23-hour and 25-hour DST days remain valid.
Rows outside the frozen interval are retained in the raw artifact but cannot become selected
recovery points or influence its extrema. Configuration changes to these thresholds are part of
the predictive identity.

## 6. Revision 0043 evidence

`observation_recovery_runtime` opens a database-clock prospective boundary and freezes the
definition/protocol hash plus the exact IEM endpoint hash. Migration performs zero evidence or
daily-actual backfill. The same migration adds a nullable, zero-backfill
`historical_forecasts.first_seen_at`; only post-boundary inserts receive the database clock.

`observation_recovery_artifacts` retains, append-only:

- station, local date, frozen timezone and UTC bounds;
- IEM network/source station identity;
- canonical request manifest and SHA-256;
- request/response timestamps and selected non-secret HTTP metadata;
- exact response bytes, size, and SHA-256;
- source/parser/completeness/terms policy identities;
- validation outcome and deterministic record hash.

`observation_recovery_points` retains the ordered normalized points cited by an artifact. It is
append-only and never projects into live observations.

Accepted recovery rows in `daily_actuals` cite the artifact and carry source protocol,
`first_seen_at`, sample count, first/last observation, maximum gap, and record hash. HIGH and LOW are
inserted atomically. Generic finalization must skip an already attested recovery pair.

Resolution reconciliation preserves whether its METAR-side value came from an ordinary
database-attested daily actual or a recovery artifact. Its canonical hash/provenance cites the
recovery artifact and recovered daily-actual record hash when present, rather than collapsing the
recovered value into indistinguishable legacy METAR truth. This provenance feeds station
reliability and downstream selection claims without changing official-resolution truth.

## 7. Revision and conflict policy

- Identical response retry is idempotent.
- A changed remote body is stored as a distinct revision.
- An incomplete or ambiguous response produces evidence but zero actual rows.
- If a station-day already has an attested pair, a later revision is quarantined. Equal extrema do
  not reseal the old pair; changed extrema require explicit review and a new predictive cohort.
- A partial existing HIGH/LOW pair is a conflict, not an invitation to synthesize the missing half.
- Evidence rows reject update/delete at the database boundary. Downgrade is refused after evidence
  exists.
- Historical forecasts reject update/delete at the database boundary. The repository performs
  insert-or-compare: exact retries succeed without mutation and conflicting retries fail with a
  typed integrity error instead of revising training history.

## 8. Bounded operation

Automatic recovery considers only already-closed local days in the configured lookback, has a hard
per-run HTTP budget shared by initial attempts and all retries, uses serial requests and the existing
IEM delay, and retries an unresolved day only after the configured cooldown. HTTP responses are read
as a stream and aborted as soon as the configured byte cap is exceeded; the implementation does not
first buffer an unbounded body. The per-run station/date candidate cap is separate from, and cannot
multiply beyond, the global HTTP budget. A failure at one station is committed/audited independently
and does not roll back another complete station. If every due station fails, the job raises a typed
error so job health cannot call the run successful.

The CLI `stratus-recover-observations` provides the same bounded path for an operator-requested
station/date. The three-year `stratus-bootstrap-iem` command is not an operational recovery tool.

## 9. Continuity contract

Revision 0043 changes label availability and predictive training inputs, and recovered provenance
changes the reconciliation input used by station reliability and selection claims. The reviewed
candidate is therefore expected to fork `predictive_validation`, `selection_policy`, and
`station_lifecycle`. The changed reconciliation record/value-hash semantics are also part of the
`operational_legacy` dependency closure, so that lane is expected to fork as well.
`official_resolution_truth` and `execution_evidence` may continue only if the generated
database-bound manifest proves exact parent identity. Operators must review and activate the
generated plan; no lane action is forced in code.

The predictive fingerprint and lane manifest include the IEM collector, exact endpoint digest,
recovery job/service/repository, daily-actual finalizer/repository/completeness rules, immutable
historical-forecast collector/repository, official-resolution availability semantics, all three
truth consumers, material recovery settings, station mapping, and migration 0043. Selection and
station-lifecycle identities include the changed reconciliation/provenance dependency closure.

## 10. Acceptance gates

Before PAPER resumes on the candidate:

1. one migration head; 0042 -> 0043 changes no legacy evidence/value counts;
2. empty 0043 downgrade/upgrade succeeds, while downgrade after evidence is refused;
3. raw bytes/hash/size and normalized membership are reproducible;
4. append-only and cross-station/date/source constraints reject tampering;
5. DST, boundary, minimum-sample, gap, adjacent-row, duplicate, and correction tests pass;
6. incomplete/conflicting artifacts create zero daily actuals;
7. accepted artifacts create exactly one atomic HIGH/LOW pair and never touch `observations`;
8. an as-of cutoff immediately before database first-seen excludes the label/previous-run forecast
   and one after includes it in EMOS, weighted-ensemble, and recompute inputs;
9. pre-release probability/evaluation/order evidence hashes remain byte-for-byte unchanged;
10. official-resolution row counts remain unchanged;
11. generated continuity plan contains the reviewed predictive, selection, station-lifecycle, and
    operational-legacy forks, continues execution and official truth, and has no unexplained lane
    action;
12. health has zero critical/blocking findings before the audited PAPER resume.

## 11. Observed production acceptance — 2026-07-17

Revision `20260717_0043` was accepted as an evidence-integrity release, not as a claim of model
skill or an authorization for LIVE. Audited operator command 11 stopped PAPER at
`2026-07-17T12:11:24.463534Z`. The observation-recovery runtime opened its prospective database
boundary at `2026-07-17T12:12:22.739436Z`; no recovery artifact was collected before release
activation.

The production schema reached the single Alembic head `20260717_0043`. A compose dependency
started the migration before the planned pre-migration dump, so the retained archive is accurately
named and classified as the **post-migration, pre-activation** backup
`stratus_post_0043_pre_activation_20260717T121343Z.dump` (1,132,357,356 bytes, SHA-256
`1FB73D945E0825763765176E40B09A93EC5ACE3F1CF74C752C52FDCA1A6A4151`). The archive catalog and a
full isolated restore both passed. This sequencing exception is not rewritten as a pre-migration
backup; instead, zero-backfill was separately proven by unchanged legacy evidence/value counts.

Image `sha256:bd5545a5db0d965bc18871b934b70f09a725ca565a6574bad9e68b46bb3b9b78` was admitted by
reviewed plan 13, hash
`479f0438ed4a0932df649155b8be99b8dfdd2cce1fae4dec115af717d1e6e19a`. Its release-manifest,
lane-manifest, and activation-manifest hashes were respectively
`ae61538a05eb3056f874cb2dbe63ca92305ec28fe60ab19511bdc1f52ea8c106`,
`c6c55b7364d2cd6b7ade0eff17807998e50741a4fd0280f5d702ec608d1344ac`, and
`c75ad808b3d6655cf944d43c0be1c20366e208d9389c6e63b318ea892d099290`. Epoch 9 opened from epoch 8
at `2026-07-17T12:43:49.447950Z`, forking `predictive_validation`, `selection_policy`,
`station_lifecycle`, and `operational_legacy`, while continuing `execution_evidence` and
`official_resolution_truth` with exact parent identity.

Recovery began only after that boundary: the first artifact was retained at approximately
`12:44:22Z`. The accepted run produced 21/21 complete immutable artifacts — all 20 active stations
for local date `2026-07-16` plus NZWN for `2026-07-17` — containing 878 normalized points and 42
atomic recovered `daily_actuals` rows (one HIGH/LOW pair per artifact). Audits 658 and 659 were
`warning`, with respectively 24 and 14 findings and zero critical or blocking findings. No LIVE
command, order, position, or authority was introduced. Command 11 remained the only cutover
command; this acceptance does not invent a separate PAPER resume that did not occur.

## 12. Missed-hour scheduling correction — epoch 15, 2026-07-20

### Defect and correction

The automatic path originally admitted recovery only while the station-local clock was in the
single configured hour. A worker that was stopped or busy throughout that hour would skip the
closed station-day until the next calendar run, even though the data was already eligible. This
was a scheduler admission defect, not a reason to weaken the completeness rules.

The admission rule is now monotonic within the local day: an automatic run is too early only while
`local_now.hour < recovery_local_hour`. At the configured hour and every later hour it may process
the bounded backlog. The existing local-date cutoff, lookback, request cap, serial HTTP pacing,
retry cooldown, immutable artifact, atomic HIGH/LOW pair, and fail-closed completeness contracts
remain unchanged.

Regression coverage proves all three time cases (before, at, and after the local threshold), an
idempotent repeat after a missed hour, deterministic cooldown expiry, and backlog drainage across
multiple request-capped passes. The recovery job is now explicitly bound into the
`predictive_validation`, `selection_policy`, `station_lifecycle`, and `operational_legacy` source
closures. It remains excluded from `execution_evidence` and `official_resolution_truth` because it
neither changes order evidence nor creates an official settlement value.

### Reviewed release and continuity result

Candidate image
`sha256:c8813973ad897e9b7e0cc12de77eca4a58a636e901ae8e391f53843ce6b38a2c`
was admitted by reviewed plan 22, plan hash
`552954843fce5ff3e4db4b09fd350043e00d4b08360e26940316df4dd88137b8`.
Epoch 15 opened from epoch 14 at `2026-07-20T11:22:49.768643Z`. The reviewed plan continued
`execution_evidence` and `official_resolution_truth` with exact parent identity and forked
`operational_legacy`, `predictive_validation`, `selection_policy`, and `station_lifecycle`.
The final continuity state is `evidence_continuity_match`, with image and cohort match and zero
mismatched or frozen lanes.

Audited command 15 enabled the all-venue kill switch before the cutover. The accepted epoch-14
worker quarantined 50/50 nonterminal PAPER execution records before it was replaced; no LIVE state
existed. The new worker was first started under the kill switch.

### Bounded catch-up and observed acceptance

Three bounded recovery passes ran only after the epoch-15 boundary. The first two materialized 20
and 18 station-days respectively; the third was an idempotent zero-work pass. Accepted recovery
artifacts increased from 42 to 80 and recovered `daily_actuals` rows from 84 to 160. Every accepted
artifact has exactly one HIGH/LOW pair, no non-accepted artifact has a derived actual, and the
active 20-station three-day lookback ended with zero missing station-days.

Static and regression verification passed: Ruff was clean, strict mypy checked 170 source files,
the non-PostgreSQL suite reported 847 passed and 3 skipped, and the focused clean-PostgreSQL
recovery suite reported 8 passed. No schema migration was required.

Audit 721 at `2026-07-20T11:59:13.936335Z` was `warning` with 16 findings and zero critical or
blocking findings. Audited command 16 then resumed only Polymarket PAPER; Binance remained
`backtest + kill`. `/healthz` and `/readyz` returned 200. The first natural post-resume cycle
created only PAPER orders (one natural maker fill and 21 resting PAPER orders at the acceptance
snapshot). LIVE positions, LIVE orders, LIVE intents, and LIVE authority remained zero.

This release repairs future label availability; it does not rewrite earlier evaluations or prove
predictive skill. The current accuracy denominator and Brier comparison therefore remain an
honest prospective collection problem, and LIVE remains prohibited.

## 13. Downstream canonical-truth quarantine - epoch 16, 2026-07-20

Observation recovery remains a METAR-derived label source, not official settlement truth. Epoch 16
makes that distinction executable in every downstream truth consumer. A recovered or ordinary
`daily_actuals` cell is now ineligible when current resolution reconciliation identifies an exact
source mismatch, clear mismatch, exact-truth gap, or unavailable truth. A resolved or attested
market without reconciliation also fails closed. When agreed exact settlement evidence exists, it
is canonical; identical duplicates collapse to one unit and conflicting exact values block the
entire station/date/kind cell rather than allowing fallback to the recovered METAR extreme.

This is a **current-remediated** training view. It is not a historical point-in-time reconstruction
of reconciliation state, because reconciliation does not yet expose an append-only as-of revision
history. Sealed prospective model predictions and the formal Gamma outcome ledger are not changed,
relabelled, or re-evaluated by this correction. Recovery artifacts, their normalized points, and
their append-only HIGH/LOW pairs are likewise retained unchanged; quarantine affects eligibility,
not custody of the evidence.

Health verifies reconciliation record hashes, blocks on conflicting exact cells, and reports a
current clear mismatch as `resolution_clear_mismatch_contained` only while a valid station
lifecycle state keeps that station out of selection. Repeated reconciliation polling cannot erase
that containment decision. Invalid or missing lifecycle evidence cannot be used to downgrade a
critical mismatch.

Image `sha256:d081e2e640eb5ad713954dbd87d0a3b3d8b1d05b1581017e2e48ff9941531843`
was reviewed as plan 25 with exact hash
`05bede4926514ba9248cbba549681c42b8c8e864eb2fad89f103f1edff4814ec`.
It continued `execution_evidence`, `official_resolution_truth`, `selection_policy`, and
`station_lifecycle`, while forking `operational_legacy` and `predictive_validation`. Preview plan
24 exposed a stale image binding and was deliberately never activated. Audited command 17
(`truth-quarantine-stop-20260720T161940Z`) stopped execution before candidate activation; its
execution-state quarantine was `paper=1`, `sealed=1`, `legacy=0`. Epoch 16 was activated from the
exact plan-25 identity at `2026-07-20T16:27:06.935419Z` under the kill switch. The active continuity
state is `collecting`, with cohort and image match and the evidence execution gate allowed.

Verification passed with 860 non-PostgreSQL tests passed and 3 skipped, all 280 unique PostgreSQL
integration tests, and the separate production-HBA boundary check. The correction preserves the
0043 source, endpoint, artifact, completeness, availability-clock, and append-only recovery
contracts. It authorizes no LIVE execution and makes no claim of predictive skill.
