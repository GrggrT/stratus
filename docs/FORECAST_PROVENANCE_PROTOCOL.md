# Forecast Provider and Model-Input Provenance Protocol

Status: implemented, deployed, and release-verified in revision `20260715_0037`. Production
subsequently advanced to revision `20260715_0038` without changing the immutable 0037 boundary
or reclassifying pre-boundary forecast inputs.

## 1. Objective

STRATUS must be able to answer, for every new probability:

- which provider request produced each normalized forecast value;
- the exact URL and exact successful HTTP response bytes that were observed;
- when the request started, when the response completed, and when PostgreSQL first sealed it;
- which model/run identity was assigned and whether the upstream initialization time was actually
  verified or merely a collector snapshot convention;
- which parsed daily extremes and hourly trajectories were derived from those bytes;
- which exact immutable ingests were visible to the probability calculation.

This protocol creates that chain prospectively. It does not invent source responses, run times,
latency, model versions, or availability timestamps for legacy rows.

## 2. Frozen boundary and protocols

Migration `20260715_0037` creates `forecast_provenance_runtime` and opens the cohort with the
PostgreSQL `clock_timestamp()`. The migration performs zero backfill.

The production protocol is `prospective-http-v1`. `historical-backfill-v1` exists only as an
explicit non-prospective import label; it cannot be represented as newly observed HTTP evidence.
Normalized output uses `forecast-normalized-manifest-v1`, and the probability input union uses
`forecast-input-union-v1`.

Rows observed before the runtime boundary may remain without `ingest_id`. A post-boundary runtime
row without immutable provenance is rejected by PostgreSQL. Historical absence remains `unknown`,
not zero and not complete.

## 3. Evidence objects

### 3.1 `forecast_ingests`

One row represents one successful station/model collection request. It records:

- station and requested model/family;
- collector and collection protocol;
- exact probability-pipeline fingerprint;
- optional single `forecast_run` binding;
- upstream initialization time plus an explicit verified flag;
- availability semantics;
- deterministic normalized forecast/trajectory manifest and SHA-256;
- expected response count and ordered response-set SHA-256;
- database-authoritative `first_seen_at` and immutable record hash.

An ensemble request may yield 31 or 51 member runs. Such an ingest intentionally has no singular
`run_id`; every materialized member row retains its own run while sharing the request evidence.

### 3.2 `forecast_source_responses`

Each successful HTTP response stores:

- ordered sequence within the ingest;
- request method and exact final request URL;
- request start and response receive timestamps;
- HTTP `Date` and `ETag` when supplied;
- exact raw response bytes, byte length, and SHA-256;
- request, evidence, and record hashes.

Open-Meteo deterministic and ensemble requests have one response. NWS has an ordered two-response
chain: point discovery followed by the exact `forecastHourly` resource. Only the successful
attempt is sealed; failed retry attempts are operational logs, not forecast inputs.

PostgreSQL independently recomputes JSON, body, request, evidence, response-set, and record hashes.
The two evidence tables are append-only. A cited `forecast_run` also becomes immutable.

### 3.3 Materialized values

`forecasts` and `forecast_trajectories` gain nullable `ingest_id`. For prospective runtime writes,
the database requires:

- the same station as the ingest;
- a concrete run id;
- `available_at` exactly equal to the ingest's database `first_seen_at`;
- exact forecast temperature or exact ordered trajectory points present in the sealed manifest;
- the single-run identity when the parent ingest has one.

Retries may update the operational materialization for an existing identity, but every source
response and every normalized manifest remains immutable. A probability cites ingest hashes, not
only a mutable materialized-row pointer.

## 4. Time semantics

Three clocks are deliberately separate:

1. `request_started_at` and `response_received_at` measure the successful HTTP request.
2. `forecast_ingests.first_seen_at` is the PostgreSQL witness after successful parsing and before
   materialized values become eligible to the probability pipeline.
3. `upstream_init_time` is populated as verified only when the provider exposes a trustworthy
   upstream timestamp. NWS `properties.updateTime` qualifies. The current Open-Meteo forecast and
   ensemble APIs do not expose a verified upstream run for this integration, so their collector
   snapshot time stays explicitly unverified.

No report may present an unverified collector snapshot as provider publication latency.

## 5. Probability lineage and live readiness

Every new `ModelProb.components.forecast_provenance` records the exact union of daily and usable
trajectory inputs visible to the calculation, including row id, model, run id, ingest id,
run/availability times, normalized-manifest SHA-256, and ingest record hash.

`complete=true` requires every cited input to bind a run and a valid immutable ingest identity.
Legacy rows remain listed and force `complete=false`.

Health emits `forecast_input_provenance_missing` for a latest post-boundary active-market
probability with incomplete evidence. It is a warning during paper collection, not a global paper
kill switch. The formal live gate adds `forecast_input_provenance`: every latest eligible active
market probability must be complete and belong to the current pipeline cohort. The gate therefore
has eleven checks after revision 0037.

## 6. Operator surface

`GET /api/v1/forecast-provenance` exposes:

- cohort boundary and immutable definition identity;
- ingest/station/response/raw-byte counts;
- materialized forecast and trajectory counts;
- verified upstream-run coverage;
- average and p95 HTTP, database-seal, and verified upstream latency;
- per collector/request-model totals;
- complete lineage coverage of latest active-market probabilities.

Cockpit renders a dedicated **Forecast source evidence** card with ingest volume, materialized
inputs, active-probability coverage, collection latency, zero-backfill status, pipeline count, and
collector summaries. Endpoint failure degrades only this card and keeps live readiness fail-closed.

## 7. Known limitations

- Open-Meteo upstream model-run publication time and operational model version remain unverified
  unless the provider adds a stable, documented identifier. Raw response and first-seen evidence
  are exact; provider publication latency is not inferred.
- This protocol proves source lineage and timing, not forecast accuracy or official settlement
  truth.
- Raw response retention increases database and backup growth. PostgreSQL TOAST compression and
  the existing immutable-storage capacity policy apply; automatic evidence deletion remains
  prohibited.
- Provider terms and official-resolution storage rights are separate per-source legal questions.
  This ledger does not convert an otherwise impermissible source into a permissible one.
- Structural-break analysis becomes statistically possible only after multiple genuinely observed
  model cohorts and outcomes accumulate.

## 8. Verification and release criteria

Before production deployment:

1. Ruff and strict mypy pass.
2. Collector unit tests preserve exact URL/body/timestamps and NWS response ordering.
3. Clean PostgreSQL tests prove zero backfill, exact manifest materialization, raw-body/hash
   validation, append-only behavior, cited-run immutability, retry stability, legacy tolerance,
   guarded downgrade, and the reporting API.
4. Full migration chain has one head and upgrades a clean database.
5. WinUI x64 build succeeds with zero warnings/errors and the packaged app opens a real responsive
   `STRATUS Operator Console` window.
6. A pre-migration production backup and rollback image exist before applying 0037.
7. Runtime smoke confirms fresh ingests, non-empty exact source responses, materialized rows whose
   availability equals database first-seen, complete lineage for genuinely prospective inputs,
   explicit fail-closed treatment of legacy inputs that cannot be backfilled, and no unresolved
   error/critical/traceback logs.

## 9. Production acceptance — 2026-07-15

Revision `20260715_0037` is deployed in production on image
`sha256:db629090a014751148a3b68fe2dd800cda446e6c77201805bf51133754dfadec`.
The database boundary opened at `2026-07-15 04:21:32.550992Z`; runtime remained `PAPER`, the kill
switch remained off, `/healthz` and `/readyz` both returned `200`, and API and worker ran from the
same image.

The pre-migration rollback image is
`sha256:414a9fbd827c0b1e8d02143190657e6e527263ff1eed03d514a71eba29f40b5c`.
The custom-format backup
`backups/stratus_pre_0037_20260715T041244Z.dump` is 346,766,605 bytes with SHA-256
`8821A2B08048439A0C7613DD698C2B1A5A358A2F16F376EB135A5B0DFE3F4A37`. `pg_restore --list`
read all 586 catalog entries and a full isolated restore reproduced revision `20260714_0036` plus
the expected production table counts before the migration.

The first accepted collection retained 205 immutable ingests from 20 stations, 206 exact HTTP
responses, 5,416,554 raw bytes, 34,894 daily forecast rows, and 1,041 trajectory rows. All 206
response bodies passed independent byte-size and SHA-256 checks. NWS retained its ordered two-step
response chain and one upstream-verified initialization. Three optional shadow regional requests
were isolated because the provider returned no fully covered forecast day; they did not roll back
the other 125 deterministic requests.

Production smoke exposed and then closed two representation defects before acceptance: repeating
Fahrenheit-to-Celsius values were sealed before PostgreSQL `NUMERIC(6,2)` rounding, and
high-precision trajectory `Decimal` values could differ after the repository's `float -> JSONB`
serialization. Forecast and trajectory temperatures now use the same canonical representation for
both manifest sealing and materialization. The regressions are covered by unit and real-PostgreSQL
tests. Final verification passed Ruff, strict mypy over 148 source files, 489 non-PostgreSQL tests,
166 PostgreSQL integration tests, and a WinUI x64 build with zero warnings and zero errors.

The operator app was launched through its packaged development path and exposed a responsive
top-level `STRATUS Operator Console` window. Cockpit rendered the **Forecast source evidence** card
with the live ingest, response, materialization, latency, and lineage metrics.

At acceptance, 297/319 current active-market probabilities had complete immutable input lineage.
The remaining 22 are the still-open KLGA 2026-07-14 venue cohort: their forecast inputs predate the
zero-backfill boundary and therefore must not be fabricated. They remain explicitly
`complete=false`, visible in Cockpit, and keep the eleventh live-gate check red until the venue
closes those markets. This is intended fail-closed behavior, not an evidence gap in a new forecast
collection.

## 10. Next protocol

Revision 0038 must create a separate zero-backfill execution-cost ledger. It must not reinterpret
the older validation ledger. The first production slice will capture exact paper maker/taker
intents, immutable execution-book evidence, atomic fill legs, fees, liquidity role, fill midpoint,
and fixed 5m/30m post-fill markouts. Existing paper and live fills remain cost decomposition
`unknown`; live cost eligibility remains closed until venue trade ids, roles, fees, and timestamps
are verified from real provider payloads.
