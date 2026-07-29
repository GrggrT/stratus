# STRATUS official resolution-source protocol

Status: **revision `20260715_0039` is implemented and production-deployed; official-source
acquisition remains externally blocked and disabled**. The pre-0039 snapshot quoted below is
historical. Nothing in this document grants a licence, proves source equivalence, starts
collection, changes model weights, or authorizes live trading.

Related protocols:

- [Accuracy research](ACCURACY_RESEARCH.md)
- [Forecast provider and model-input provenance](FORECAST_PROVENANCE_PROTOCOL.md)
- [Prospective validation](PROSPECTIVE_VALIDATION_PROTOCOL.md)
- [Accuracy promotion](ACCURACY_PROMOTION_PROTOCOL.md)
- [Operator Console](OPERATOR_APP_SPEC.md)

## 1. Purpose and authority

STRATUS needs a point-in-time, independently auditable copy of the exact published value named by
the market's resolution rules. A number typed by an operator, a METAR-derived daily extreme, the
venue's winning bucket, and an API value from a merely similar product are not equivalent evidence.

Revision 0039 implements this prospective chain:

```text
resolution contract
  -> contract-scoped legal and semantic attestation
  -> immutable raw JSON response/import
  -> deterministic exact-value extraction
  -> immutable settlement_actual projection
```

The chain records the exact contract/rules identity, reviewed parser configuration, permission
hashes, raw bytes and their server-verified SHA-256, source publication/finality evidence,
database first-seen time, extracted decimal token/value, and the final projection used by existing
reconciliation code.

Authorities remain separate:

1. Gamma's outcome vector controls operational venue settlement and realised P&L.
2. The 0039 official-source chain controls exact-source comparison and future settlement-aligned
   evaluation.
3. METAR `daily_actuals` remain operational observations and QA comparators only.
4. Forecasts and market prices are predictions, never truth sources.

No bucket outcome is inverted into a temperature. No missing official value is replaced with
METAR, SYNOP, a nearby station, a screenshot, a model analysis, or an operator-entered number.

### Historical pre-0039 snapshot, 2026-07-15

The then-current production database contained 155 active resolution contracts, all classified
as `wunderground / restricted_unlicensed / storage_permitted=false`; zero
`settlement_actuals`; and 1,078 reconciliation projections without a `settlement_actual_id` or
attested exact-source hash. These numbers are a diagnostic snapshot only. They are not migration
fixtures and will not be backfilled.

## 2. Prospective boundary and zero backfill

Migration `20260715_0039` creates one row in
`official_resolution_evidence_runtime` using PostgreSQL `clock_timestamp()`:

- `id = 1`;
- `definition_version = 'official-resolution-evidence-v1'`;
- `definition_hash =
  'c078c2c72a0547907f7fc7cbe3f09bf656c6c560043c347f925528743c2340e4'`;
- `license_protocol = 'official-resolution-license-v1'`;
- `response_protocol = 'official-resolution-source-v1'`;
- `extraction_protocol = 'official-resolution-extraction-v1'`.

The definition hash is SHA-256 of:

```text
official-resolution-evidence-v1|official-resolution-license-v1|
official-resolution-source-v1|official-resolution-extraction-v1|
contract-scoped-policy|licensed-http-v1|authorized-artifact-v1|
strict-settlement-projection
```

`opened_at` is the evidence cohort boundary. The migration creates empty evidence tables and adds
a nullable link to `settlement_actuals`; it does not fetch, infer, copy, or manufacture evidence
from old events, `daily_actuals`, Gamma outcomes, reconciliations, screenshots, caches, or logs.
Existing rows stay exactly as they were. Unknown legacy evidence remains unknown.

The database requires every new source response to receive a database `first_seen_at` on or after
`opened_at`. Revision 0039 does **not** pretend that merely observing an old artifact after the
boundary proves its historical first availability; operational admission of historical artifacts
therefore remains prohibited even though the database cannot reconstruct that external fact.

## 3. Implemented immutable schema

Revision 0039 has exactly four new tables. There are no separate collection-attempt, artifact,
authorization-document, or extraction tables in this revision.

### 3.1 `official_resolution_evidence_runtime`

The singleton stores the boundary and four protocol identities above. PostgreSQL rejects all
`UPDATE` and `DELETE` operations.

### 3.2 `official_resolution_license_attestations`

Each append-only row is scoped to one active `resolution_contracts.id` and one immutable
`attestation_version`. It records:

- grantee, source family, exact settlement-source URL, licensed endpoint URL;
- approved status and required commercial-use, value-retention, and raw-response-retention flags;
- terms URL and SHA-256, reviewer, reviewed time, rationale, validity interval;
- adapter name/version, canonical parser configuration and its hash;
- maximum allowed response age, canonical policy hash, database attestation time, and record hash.

The API first previews the canonical policy and hash. A separate attestation request must echo the
exact `approved_policy_sha256`; changing any policy field changes the hash and causes rejection.
PostgreSQL independently recomputes the parser hash, policy hash, contract binding, attestation
time, and record hash. A row is accepted only when all three permission flags are true, the
contract is active, the validity interval is current, and parser literals match the contract's
station, unit, precision, and finalization rule.

The attestation records a reviewed decision; it is not a legal opinion generated by STRATUS.
Contract-specific terms must actually grant the rights represented by those booleans. A paid
account, API key, reachable endpoint, or `storage_permitted` field elsewhere is not sufficient.

### 3.3 `official_resolution_source_responses`

Each append-only row seals one raw JSON source response and its acquisition envelope. It stores:

- contract and licence-attestation foreign keys;
- `licensed-http-v1`/`GET`/HTTP 200 or
  `authorized-artifact-v1`/`IMPORT`/no fabricated HTTP status;
- source, endpoint, adapter/parser/policy identities;
- canonical request manifest and SHA-256;
- request, receipt, retrieval, publication, and database first-seen timestamps;
- redacted structured HTTP metadata when the acquisition was HTTP;
- exact raw `BYTEA`, byte length, raw SHA-256, and database record hash.

PostgreSQL overwrites `first_seen_at` with its own clock, verifies timestamp ordering, recomputes
manifest/body/record hashes and byte length, checks the active licence at acquisition time, and
validates every contract/adapter/policy binding. It never trusts a client-supplied body hash as
evidence authority.

Uniqueness is enforced for `(contract_id, request_sha256)`,
`(contract_id, response_sha256)`, and `record_hash`. An identical retry resolves to the original
row and preserves its timestamps; a conflicting replay fails.

### 3.4 `official_resolution_exact_actuals`

This append-only table combines deterministic extraction evidence and the exact official value.
One row is allowed per contract and per source response. It records:

- response, contract, station, event, exact `rules_hash`, source and endpoint identities;
- station code, market date, `high`/`low`, exact decimal source value, unit, Celsius conversion,
  and published decimal places;
- finalization rule, `is_final=true`, source publication time;
- adapter/parser identities, extraction locator, canonical extraction manifest/hash;
- source-response SHA-256, extraction protocol, database extraction time, and record hash.

The parser preserves the published numeric token and uses `Decimal`, never binary floating point.
PostgreSQL verifies the response/contract/station/market scope, exact precision, deterministic
Celsius conversion, finality, extraction-manifest hash, source-response hash, and all immutable
identity fields. A second different truth for the same contract is rejected.

### 3.5 Strict `settlement_actuals` projection

Migration 0039 adds nullable unique foreign key
`settlement_actuals.official_exact_actual_id`. The repository writes response, exact actual, and
settlement projection in one transaction/savepoint. The projection must exactly equal its source
row for contract, station, date, kind, source value/unit, Celsius value, finality, publication
time, response hash, retrieval time, and canonical provenance JSON.

After 0039:

- every **new** `settlement_actuals` insert requires an `official_exact_actual_id`;
- all `settlement_actuals` updates and deletes are rejected, including mutation of legacy rows;
- direct manual values cannot enter through SQL or the operator API;
- identical repository retries return the existing chain; conflicts fail closed.

The migration itself does not modify legacy settlement rows. The stronger mutation trigger takes
effect only after the migration is installed.

### 3.6 Immutability and downgrade guard

The runtime, attestation, response, and exact-actual tables reject `UPDATE` and `DELETE` in
PostgreSQL. Hashes and timestamps are independently recomputed by database triggers. Contract-
scoped advisory locks plus unique identities make concurrent identical ingest idempotent.

Downgrade is allowed only while all three evidence ledgers are empty and no settlement projection
references an official exact actual. Once an attestation or evidence row exists, downgrade raises
`refusing 0039 downgrade: official resolution evidence exists`; rollback must retain the schema
and use a forward fix or compatible prior application image.

## 4. Permission and semantic-equivalence gates

An attestation is valid only when both independent gates have been reviewed for that exact
contract and product.

### 4.1 Permission gate

Written terms must cover commercial automated use, processing, exact value retention, and exact
raw-response retention for the required audit/backup lifetime. Any attribution, deletion,
geographical, access-control, or redistribution restrictions remain operator/legal obligations.
The implemented row stores terms identity and the three required permission decisions, but it
does not replace review of the underlying agreement or model every possible legal clause.

TWC's [Common Usage Guide](https://developer.weather.com/docs/api-common-usage-guide) describes
cache/expiration controls. Technical access therefore cannot be interpreted as indefinite raw
retention authority.

### 4.2 Semantic-equivalence gate

The reviewed parser policy must match the contract's exact event, `rules_hash`, source URL,
station, unit, decimal precision, finalization rule, and local observation window. The API derives
Wunderground History contract semantics independently as local calendar day
`00:00 -> 00:00 next day`; it does not copy the candidate product's window into the contract.

The documented TWC
[Historical Conditions - Daily Summary v3](https://developer.weather.com/docs/openapi/historical-conditions-daily-summary-3-0)
uses a 07:00-local to 07:00-next-day window. A parser configured for that product therefore fails
the local-calendar equivalence check and cannot be attested for the current Wunderground contract.
Brand proximity, an ICAO lookup, or a JSON schema that happens to contain high/low fields does not
make the products equivalent.

Only `wunderground` currently has an independently frozen contract-window mapping in the operator
API. Other source families fail closed with `no independently reviewed source-window mapping`
until a reviewed mapping is implemented. This does not mean Wunderground is authorized: all
external rights and exact product equivalence still have to be proven contract by contract.

## 5. Implemented acquisition and extraction flow

### 5.1 Server-root, hash-addressed artifact import

The only operator-facing acquisition path currently implemented is offline import. It is disabled
unless `STRATUS_OFFICIAL_RESOLUTION_ARTIFACT_ROOT` is configured. The optional size limit is
`STRATUS_OFFICIAL_RESOLUTION_ARTIFACT_MAX_BYTES` (default 1,000,000; allowed 1,024-10,000,000).

The sole accepted relative layout is:

```text
imports/<source-family>/<contract-id>/<sha256>.bin
```

The API accepts only contract id, exact `rules_hash`, normalized relative path, actor, and reason.
It cannot submit a source value, body, body hash, byte length, finality flag, publication time,
retrieval time, absolute path, or HTTP metadata.

The server resolves the configured root, rejects absolute/traversal/backslash/ADS paths,
symlinks and Windows reparse points, wrong source family/contract id, non-regular or changing
files, oversize/empty files, and filename/content hash mismatch. It opens and reads the file
itself, recomputes SHA-256, then seals the exact bytes in PostgreSQL. The filesystem is only a
controlled acquisition location; PostgreSQL is the durable evidence ledger.

### 5.2 Versioned JSON adapters

The implemented adapters are:

- `licensed-json-field-map` version `1`;
- `wunderground-licensed-json-field-map` version `1`, a fail-closed wrapper requiring an explicitly
  reviewed JSON map.

Parser configuration supports scalar objects or parallel arrays and frozen paths/literals for
date, high, low, schema, station, unit, precision, finalization, observation window, publication,
and finalization witness. It bounds body bytes, JSON depth/node count, and array length; rejects
duplicate JSON keys; preserves exact integer/decimal lexemes; and validates station/date/window/
unit/precision/finality before producing a record.

For the current API import path, no trusted transport metadata supplies publication or finality.
The imported JSON must therefore expose the configured publication and finalization evidence; an
operator cannot assert `is_final` in the request.

### 5.3 Licensed HTTP is a sealed protocol, not an active collector

The service and database understand `licensed-http-v1`: exact credential-free HTTPS endpoint,
GET, verified TLS, status 200, bounded JSON body, and ordered request/receipt/retrieval times.
Revision 0039 does **not** implement or schedule a production HTTP collector, redirect handling,
retry ledger, or network credential workflow. No source request starts automatically from an API
key or attestation. Those capabilities require a later reviewed revision after the external
permission and product-equivalence blockers are cleared.

## 6. Implemented operator API

Monitoring reads remain local operator endpoints and never expose raw response bytes:

- `GET /api/v1/official-resolution-sources` returns runtime/protocol identities, state,
  integrity, artifact-root enablement, active/current/expired/blocked contract counts,
  attestation/response/exact/projection counts, coverage, stored byte total, and latest times.
- `GET /api/v1/official-resolution-sources/contracts?limit=&offset=&contract_id=` returns at most
  200 rows of explicit contract/licence/response/extraction/projection metadata and hashes. It
  does not select or serialize `response_body`.

Authenticated mutations require the configured operator key:

- `POST /api/v1/official-resolution-sources/license-policy-hash` previews the canonical policy,
  parser hash, and policy hash after semantic checks.
- `POST /api/v1/official-resolution-sources/license-attestations` requires the exact externally
  approved policy hash plus reviewer/time/rationale and appends the contract-scoped attestation.
- `POST /api/v1/official-resolution-sources/import` reads the server-side hash-addressed artifact,
  selects a current attestation, extracts the value, and atomically persists the full chain.

Legacy `POST /api/v1/settlement-actuals` is permanently retired and returns HTTP **410 Gone** even
when authenticated. There is no raw-body/value bypass endpoint and no artifact-detail endpoint in
revision 0039.

## 7. Health audit behavior

The implemented health audit emits:

- `official_resolution_authorization_blocked` as informational when active contracts exist but
  none has a current attestation;
- `official_resolution_evidence_backlog` as informational when current licensed contracts exceed
  sealed exact actuals;
- `official_resolution_license_expired` as a warning when an attested active contract has no
  current replacement;
- `official_resolution_evidence_integrity` as blocking when response, exact-actual, and projected
  settlement counts differ.

Missing external authorization is expected fail-closed state: it does not fabricate truth and
does not itself engage the kill switch. A broken stored chain is different and is included in the
new blocking issue codes. Revision 0039 does not silently change other health or risk semantics.

The current audit is deliberately count-level. Per-contract collection attempts, overdue source
SLAs, schema-error histories, byte-by-byte periodic revalidation, and unauthorized-network-call
detection do not exist in this revision and must not be inferred from the summary.

## 8. Cockpit behavior

The local WinUI Cockpit includes an independently fetched **Official resolution truth** card. It
shows:

- `INTEGRITY ERROR`, `EXTERNAL AUTH BLOCKED`, `COLLECTING`, or `UNAVAILABLE` state;
- current licensed/active contracts plus blocked, expired, and attestation counts;
- immutable response count/bytes and whether the artifact import root is configured;
- exact actual coverage, projected settlement count, latest response/exact ages;
- the boundary, definition hash, and licence/response/extraction protocol identities.

`active_contracts = 0` renders `No active contracts`/not applicable rather than false 100%
coverage. Endpoint failure degrades only this card to `UNAVAILABLE`; the last core runtime/risk
render remains independent. The card does not expose raw bytes, legal documents, or a bypass
toggle. Contract-row drill-down is available through the read API but is not yet an interactive
Cockpit view.

## 9. Reconciliation, evaluation, and calibration boundary

An imported exact actual is projected into the existing `settlement_actuals` table with immutable
provenance. Existing reconciliation can then compare exact source, METAR implication, and Gamma
outcome as separate authorities. Revision 0039 does not rewrite old forecast, Gamma,
reconciliation, validation, promotion, signal, order, or position rows.

An exact value may support future prospective evaluation only when the corresponding forecast was
sealed before the outcome and every point-in-time rule in the forecast provenance protocols is
satisfied. A later training integration must enforce:

```text
official_source_first_seen_at <= training_cutoff < target_prediction.generated_at
```

Revision 0039 does not itself add truth rows to calibration fits, register an accuracy-promotion
family, activate an expert, change model weights, or change the live gate. Those integrations are
future reviewed work after real authorized evidence exists.

## 10. Security properties and limitations

- Canonical URLs must be credential-free HTTPS; known secret query-parameter names are rejected.
- Credentials never belong in evidence rows, artifact paths, logs, API DTOs, or Cockpit.
- Import is disabled by default and bounded by root, identity, file type, size, stability, and
  SHA-256 checks.
- Raw licensed bytes are stored only in
  `official_resolution_source_responses.response_body`; read APIs expose metadata only.
- Terms and policy decisions are immutable and contract-scoped; a new `rules_hash` needs a new
  reviewed attestation.
- Expiry stops new acquisitions but does not rewrite previously valid evidence.

Database backups containing licensed raw bytes must use access control, encryption, off-site
handling, retention, and deletion rules compatible with the actual agreement. If those terms are
incompatible with immutable evidence and backup retention, the source is ineligible.

## 11. Local verification and release acceptance

The local implementation includes automated coverage for:

- clean migration, exact runtime constants, zero backfill, append-only triggers, DB-clock fields,
  independent hash/conversion/projection checks, retry/conflict behavior, and downgrade refusal;
- contract/policy/parser/response/finality semantics, exact decimal tokens, bounds, malformed or
  duplicate-key JSON, and the 07:00-to-07:00 mismatch;
- artifact traversal, absolute/backslash/ADS paths, symlink/reparse escape, wrong contract/source,
  changing/non-regular/oversize content, and filename/hash mismatch;
- repository atomicity, safe summary/drill-down serialization, operator API end-to-end import,
  legacy endpoint retirement, and health classification;
- WinUI model/client/card implementation and x64 build verification as part of final release
  acceptance.

Release acceptance passed full Ruff, strict mypy, 575 non-PostgreSQL tests, 195 real-PostgreSQL
integration tests, one Alembic head, Docker Compose validation, API/worker/migration image parity,
a full backup/restore and production-like migration drill, and a packaged x64 WinUI build with
zero warnings and zero errors.

A synthetic JSON fixture proves software behavior only. It cannot prove legal permission,
product equivalence, provider publication timing, or production connectivity. A real source
canary is intentionally blocked until those external facts are documented.

## 12. Deployment and rollback procedure

Production rollout followed this procedure:

1. create a custom-format pre-0039 database backup and record its SHA-256;
2. prove the backup with `pg_restore --list` and an isolated restore/full-chain test;
3. tag the current API/worker image as the application rollback point;
4. deploy API, worker, and migration from the same immutable candidate image;
5. install 0039 with artifact root unset and confirm zero evidence backfill;
6. verify the read endpoints, health state, PAPER mode, kill-switch state, logs, and Cockpit;
7. configure a root and attest/import a real artifact only after contract-specific external
   permission and semantic equivalence are approved.

### Production deployment record, 2026-07-15

- Production is at the single Alembic head `20260715_0039`.
- API, worker, and migration use immutable image
  `sha256:e4edd3c4e35228d2f8bea93d058c963bc6e92eb2b35986c6694b862fd79adec8`.
- The exact pre-release application remains tagged `stratus:rollback-pre-0039` at
  `sha256:f64718d1c503acd9db7eeb4b8dfd7d82464e9a3ec977c7e9c99d82d504cd30f5`.
- The guarded custom-format backup is
  `backups/stratus_pre_0039_20260715T093831Z.dump`, 541,389,370 bytes, SHA-256
  `CB2F7D46B12D2BDFD726167FDA84BF6377BF1AF39E0C87B03292D1AE5DC37FF5`.
  Its TOC was verified and the complete dump was restored into an isolated database. The restore
  reproduced revision 0038, 155 active contracts, and zero settlement actuals.
- The restored production snapshot passed 0038 -> 0039 -> 0038 -> 0039. Upgrade created exactly
  one runtime boundary and zero attestations, source responses, exact actuals, or settlement
  projections; downgrade was permitted only while that evidence vector was empty. Automated tests
  separately prove downgrade refusal once primary evidence exists.
- Runtime smoke checks returned `healthz=200`, `readyz=200`, source integrity `valid`, state
  `disabled_external_authorization`, artifact import disabled, 155 active/0 licensed contracts,
  and zero responses/exact actuals/projections. The retired direct endpoint returned HTTP 410.
- PAPER mode and `kill_switch=false` were preserved. A manual health audit returned non-blocking
  `warning` with only `official_resolution_authorization_blocked` at `info` for this subsystem;
  42 fresh post-deployment scheduler jobs all completed successfully and API/worker logs contained
  no error, traceback, or critical line.
- The packaged Cockpit was rebuilt and left running. UI Automation confirmed
  `EXTERNAL AUTH BLOCKED`, `0/155`, `integrity=valid`, the frozen protocol/hash identities, and the
  explicit `METAR is not official truth` boundary.

Application rollback may stop new imports while retaining the 0039 schema. Once any attestation
or evidence exists, schema downgrade is intentionally unavailable. No rollback may delete
licensed evidence, erase the database boundary, or recreate direct settlement entry.

## 13. Explicit external blockers

The following remain unresolved by code and block Wunderground/TWC collection:

1. Written permission for the exact product and grantee covering commercial automated use,
   processing, exact value/raw-response retention for the STRATUS audit and backup lifetime, and
   all attribution/deletion/geographical obligations.
2. Written product-level proof that the licensed endpoint is semantically identical to the value
   named by the applicable market rules: exact station, statistic, local-day window, timezone,
   unit, precision, rounding, finalization, and revision behavior.
3. A frozen documented schema and terms for any ICAO JSON endpoint. Endpoint existence and an API
   key are not approval.
4. Equivalent source-specific permission and semantic proof for HKO or a national meteorological
   source, plus a reviewed source-window mapping in code.
5. A reviewed attestation for every new contract/`rules_hash`; an existing station/city mapping
   cannot authorize future rule text.
6. Operational credentials, rate limits, availability expectations, and a retention/backup
   policy compatible with immutable evidence.

The TWC 07:00-to-07:00 Daily Summary does not satisfy a Wunderground local-calendar contract. Until
at least one exact product clears every applicable blocker, the correct runtime state is
`disabled_external_authorization` / `EXTERNAL AUTH BLOCKED`, exact coverage remains zero, and no
source network request is made.

## 14. Explicitly out of scope for 0039

The following were present in earlier design drafts but are **not** implemented by 0039:

- scheduled HTTP collection and provider credentials;
- a `resolution_source_collection_attempts` ledger or attempt-result taxonomy;
- separate `resolution_source_artifacts` and `resolution_source_extractions` tables;
- authorization-document file ingestion or an `authorizations/...` filesystem layout;
- redirect-chain/header-manifest allowlist machinery;
- per-contract due/finality/SLA scheduling and alerts;
- a raw-artifact metadata detail endpoint;
- interactive Cockpit contract drill-down;
- automatic reconciliation/calibration/promotion changes based on exact evidence;
- legal approval, source licence procurement, or provider equivalence certification.

These are candidate successor revisions, not implied acceptance criteria for the local 0039
schema. Any successor must preserve all existing evidence immutably and define a new version/hash
when it changes acquisition, semantics, or evidence identity.

## 15. Completion definition

The 0039 engineering and deployment definition is complete: migration, immutable evidence chain,
reviewed-policy hash flow, secure artifact import, deterministic extraction, strict settlement
projection, read API, health integration, Cockpit card, tests, backup/restore, immutable rollout,
zero-backfill confirmation, runtime smoke, and final Cockpit verification passed together.

Source activation is a separate external milestone and remains blocked until a legally authorized,
settlement-equivalent real product is available. No empty table, synthetic fixture, manual value,
green test run, or successful deployment substitutes for that external canary.
