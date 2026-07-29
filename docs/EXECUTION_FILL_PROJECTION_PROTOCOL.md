# STRATUS execution fill projection protocol

Status: revision `20260715_0041` is production-deployed and accepted for migration and reviewed
continuity activation. Its natural fill-projection observation is historically inconclusive: the
kill switch expired the backing signals and stranded their maker orders. Execution remains safely
isolated from that old cohort on the accepted 0042 hotfix runtime; section 10 records the completed
zero-fill quarantine and prospective 0042 validation. Audited command 6 has resumed PAPER-only
prospective collection; LIVE remains unauthorized.

Migration: `20260715_0041`, with `20260715_0040` as its sole parent.

## 1. Incident and invariant

`orders.price` is the immutable requested decision/limit price. It is never an execution result.
The actual execution price is the share-weighted price of the immutable atomic fill legs:

`VWAP = sum(fill_leg.shares * fill_leg.price) / sum(fill_leg.shares)`.

The incident that caused revision 0041 had two independent manifestations:

1. a natural resting PAPER fill attempted to overwrite `orders.price` with its actual VWAP and was
   rejected by the dual-CLOB decision-immutability trigger; and
2. the execution-cost finalizer divided independently rounded stored notionals by shares, producing
   `0.7300000254` where PostgreSQL correctly derived `0.7300000000` from the fill legs. The
   difference exceeded the frozen `5e-9` seal tolerance.

The kill switch engaged before either invalid transaction committed. Revision 0041 corrects the
writers and their downstream consumers; it does not relax either database guard.

## 2. Authoritative values

For an order with execution-cost evidence:

- requested limit: `ExecutionOrderIntent.requested_limit_price` and immutable `Order.price`;
- actual shares: `sum(ExecutionFillLeg.shares)`;
- actual stored notional: `sum(ExecutionFillLeg.notional_usd)`;
- actual VWAP: `sum(shares * price) / sum(shares)`;
- actual fee: `sum(fee_usd)` only when every leg fee is known; otherwise a separately known
  aggregate order fee may be used explicitly, and a wholly unknown fee remains unknown; and
- position execution price: `Position.avg_price`, projected from the actual fills.

Stored notional remains a separately quantized cash-accounting value. It must not be divided by
shares to reconstruct VWAP. Positive enrollment values use the durable column precisions shared by
Python and PostgreSQL: price 6 decimals, notional 4, fee 5, and shares 6.

Only rows with no `ExecutionOrderIntent` may use the legacy `Order.price` fallback. An intent with
zero legs is corrupt/incomplete modern evidence and fails closed.

## 3. Database enforcement

Migration 0041 performs zero backfill. It replaces the prospective-enrollment insert validator so
that modern fills are re-derived from their legs and legacy rows retain their old projection.

The migration also freezes the fill-leg set. A new leg insert locks its intent and is rejected once
an obligation, prospective enrollment, or execution-cost seal exists for that order/intent. The
obligation and enrollment validators take the same intent lock before reading the leg set, so a
concurrent leg is either included before terminal projection or rejected afterwards; it cannot
silently alter an immutable seal or enrollment.

An execution intent with no legs is rejected by both the repository and PostgreSQL. Ineligible legs
or incomplete fee evidence may still be retained numerically for audit, but the prospective
enrollment is excluded with `execution_evidence_ineligible` or `execution_fee_unknown`; it cannot
be counted as confirmatory evidence.

## 4. Prospective continuity boundary

The probability-pipeline cohort alone is insufficient to identify this change. Formal validation
loads an enrollment only when its order has an `ExecutionOrderIntent` whose
`predictive_validation` and `execution_evidence` lanes match both the identity hash and the
preserved boundary of the current reviewed lanes. The resulting immutable dataset carries the
effective boundary, both lane identities, and the exact set of admitted order ids. Missing active
lanes, missing fee or fill evidence, or an identity/boundary mismatch produces an explicit empty
or excluded formal sample; it never falls back to legacy position history.

A `continue` action keeps the earlier boundary and therefore keeps compatible samples. A `fork`
opens a new boundary and resets that lane's formal denominator without deleting old rows. Legacy
enrollments and pre-fork samples remain immutable audit inventory, but are not relabelled as current
gate evidence.

`repositories/analytics.py` is part of the predictive-lane source manifest. A future scoring-only
change therefore requires a reviewed predictive fork instead of silently rescoring a mixed cohort.

The live gate, performance and edge summaries, calibration/Brier report, CLV report, root-cause
actions, and Brier/CLV drill-downs all derive their formal benchmark fields from this one current
lane-scoped dataset. A `continue` retains compatible samples; a predictive or execution fork resets
all of these formal denominators together. Historical portfolio totals may still be presented as
explicitly labelled operational context, never as a substitute for formal gate evidence.

## 5. Analytics and operator surfaces

Per-order performance, skill/luck, ROI, Brier, CLV, root-cause actions, and their drill-downs use
the formal lane-scoped order dataset. Within that dataset, requested price, actual VWAP, shares,
fees, model/market probabilities, outcome, CLV, and predictive/execution lineage are frozen per
order. Net scoring excludes a row when its fee or required formal evidence is unknown rather than
treating the missing value as zero.

The orders API preserves the compatibility field `price` as the requested limit and adds
`requested_limit_price` plus nullable `actual_fill_vwap`. Cockpit and Telegram label the two values
explicitly.

The legacy fee-backfill job is restricted to genuinely legacy PAPER evidence: an order must have
no execution intent and its actual `filled_at` must precede `ExecutionCostRuntime.opened_at`.
Position backfill additionally requires a matching positive pre-boundary filled order and rejects
any position associated with a post-boundary fill or modern intent. A post-boundary row without an
intent is ambiguous/corrupt evidence, not a legacy row eligible for reconstruction.

Telegram execution evidence uses severity-aware wording. A proven zero-fill terminal attempt is a
`WARNING no_fill`; a claimed fill without the required atomic evidence is
`CRITICAL execution_evidence_unavailable`. A cancelled order with non-zero filled quantity must not
be described as a no-fill.

## 6. Evidence-continuity release action

Revision 0041 is a material change to three lanes:

- `execution_evidence`: canonical VWAP, immutable decision price, and frozen terminal leg set;
- `predictive_validation`: fill projection, fee eligibility, current-boundary dataset, and scoring
  source manifest; and
- `selection_policy`: policy reports and decisions are restricted to the exact formal order-id set
  and to intents attested to the active selection identity and boundary.

All three lanes must fork in the reviewed activation plan. `operational_legacy`,
`station_lifecycle`, and `official_resolution_truth` may continue only when their computed
identities remain unchanged. No lane may be frozen or incomplete at activation. This is the
required semantic action set; the canonical production plan hash and observed activation rows do
not exist until the pending reviewed cutover.

The release manifest is bound to the database it is intended to run against. It reads the actual
`alembic_version`, requires exactly one row equal to `20260715_0041`, records that head in the
manifest, and hashes every material migration SQL source, including revisions 0035, 0040, and 0041
for predictive validation and 0031, 0032, 0038, 0040, and 0041 for execution evidence. A stale,
missing, multiple, or unexpected database head fails manifest construction instead of producing a
reviewable release plan.

## 7. Rollback

The pre-0041 application image contains the known decision-price mutation and is not a resumable
operational rollback. Downgrading only the 0041 trigger also makes the new writer incompatible when
limit and VWAP differ. A failure after cutover therefore requires an auditable stop, kill switch ON,
worker stopped, and a reviewed roll-forward. The pre-0041 backup is a disaster-recovery artifact;
restoring it does not authorize execution with the defective old image.

Schema reversibility is still tested on an isolated database: 0041 can restore the 0040 validators
without rewriting evidence, and upgrade back to 0041. This is a migration-integrity proof, not
permission to resume the old runtime.

## 8. Required verification

- exact unit regression for the rounded-notional counterexample;
- clean PostgreSQL migration through one 0041 head and `0041 -> 0040 -> 0041` reversibility;
- PAPER fill with requested limit different from actual VWAP, including retry, enrollment and
  aggregate projection;
- immediate unverified-LIVE seal with exact canonical VWAP and prospective exclusion;
- rejection of intent-without-legs and of a late leg after terminal projection;
- deterministic lock-race acceptance in both orders: a fill leg committed before projection is
  included, while a leg racing after obligation/enrollment projection is blocked and rejected;
- continued-lane retention and predictive/execution-fork reset of the formal dataset;
- selection-lane continue retention and selection-fork reset while the underlying immutable formal
  dataset remains unchanged;
- resolved analytics with known aggregate-fee fallback and fail-closed unknown fee;
- formal live-gate, Edge, Brier, CLV, root-cause action, and drill-down reset after a material lane
  fork, with no legacy fallback;
- release-manifest rejection unless the connected database has the one exact 0041 head;
- strict pre-boundary fee-backfill and explicit post-boundary ambiguous-row rejection;
- API/Telegram/Cockpit distinction between limit and actual VWAP;
- full non-PostgreSQL and clean-PostgreSQL suites, Ruff, strict mypy, Compose, and WinUI x64; and
- a natural post-activation resting fill plus successful backlog finalization with no matching
  error signature.

## 9. Scope limits

Revision 0041 is limited to fill-accounting integrity and cohort separation. Its engineering and
deployment acceptance does not prove predictive skill, positive CLV, profitability, station
quality, exact official-resolution truth, legal permission, or LIVE readiness. PAPER remains the
only authorized execution mode.

## 10. Production acceptance

### 10.1 Verified release and activation

The retained pre-release backup is
`backups/stratus_pre_0041_final_20260715T190832Z.dump` (802,838,676 bytes; SHA-256
`526ac9d4554f252e2a2388596658b6bc41fad674d420aff23ff5c703d010a93d`). The final migration drill
passed and production has the single Alembic head `20260715_0041`.

The immutable production image digest is
`sha256:ab2b627d77faef9d7f2114fdbf208d83947163fd81565d0f0197cec682d6b181`; both API and worker run
that image. The reviewed release plan hash is
`7eb012e66704fb70cf30f20fce0a6a344e369089fe2770616956b6054faea5d5`. It opened epoch 3 at
`2026-07-15T19:14:03.860671Z` with the required actions:

- `fork`: `predictive_validation`, `execution_evidence`, and `selection_policy`;
- `continue`: `official_resolution_truth`, `station_lifecycle`, and `operational_legacy`.

In the initial cutover window, continuity reported a matching runtime/release/lane identity and the
audit state was `warning` with zero `critical` and zero `blocking` findings. Audited command 5 then
resumed PAPER. These are dated cutover facts, not a claim about the later steady state, and they do
not authorize LIVE.

Release verification passed Ruff, strict mypy over 162 source files, 661 non-PostgreSQL tests, 227
fresh-PostgreSQL tests, the migration drill, and WinUI x64 Debug and Release builds.

### 10.2 Natural fill-projection observation is historically inconclusive

Through `2026-07-15T19:29:36Z`, the corrected runtime had produced 15 genuine intents/resting maker
orders and zero fill legs. This is an expected market state: no natural order had crossed, so there
was no fill to project or finalize. It is neither a failed acceptance check nor evidence that the
requested-limit-versus-VWAP path passed in production.

The later kill-switch transition expired the signals behind these resting orders but did not
terminalize the orders themselves. Because the maker projector requires a pending signal, the
epoch-3 sample is stranded and can no longer produce a natural fill. The observation remains
historically inconclusive rather than passed or failed. It was subsequently formalized as zero-fill
quarantine evidence, and no synthetic fill, forced crossing, historical reconstruction, or future
0042 fill may be used to rewrite the 0041 result.

### 10.3 Historical pre-0042 post-sleep/wake refresh and safe stop

After an approximately 15-hour sleep/wake interval, the safety system correctly engaged the kill
switch at `2026-07-16T10:38:35Z` because scheduler and capacity evidence were stale; 25 jobs were
recorded as missed. This did not invalidate the release/activation record and was not an 0041 fill-
projection failure.

Restart refresh restored capacity state to `within_budget`. Discovery observed 199 events, 75
targets, and 825 markets; observation collection reported 20/210, forecast collection 20/2,118,
probability generation 374, and the CLOB refresh wrote 38 of 110 due items. Audit 572 then reported
one critical issue only: the `official_resolution_truth` manifest no longer matched after the
contract inventory changed from 155 to 182.

Reviewed plan 5, hash
`cba642b35b31aa17428b5c13148daef19c0e85256235873d6093151a3a80829a`, with release-manifest hash
`dcfc5302e0a4786d8017254555743d307a1d208134269047e5950f714b353e27`, opened epoch 4 at
`2026-07-16T10:43:00.712204Z`. It forked only `official_resolution_truth`; the other five lanes
continued, preserving the 0041 predictive, execution, and selection boundaries from epoch 3.

After CLOB, settlement, and 1,000-item reconciliation refreshes, audit 573 completed as `warning`
with 14 findings, zero critical, and zero blocking. Continuity matches the running release, and the
post-epoch-4 observation window contains 49 successful jobs and zero errors.

At this historical checkpoint the kill switch remained ON because daily risk reported P&L of
`-$96.31`. The natural maker sample contained 15 stranded intents/orders, zero fill legs, and zero
prospective enrollments. The subsequent 0042 quarantine completed the required no-fill projection;
it did not change the historical 0041 observation from inconclusive.

### 10.4 Completed 0042 quarantine and prospective boundary

On 2026-07-17, before the 0042 migration and with the kill switch ON, the production quarantine
found 67 stranded PAPER orders and zero nonterminal LIVE orders. Thirty orders had immutable
execution intents and were closed through the canonical terminal-no-fill path, producing the
required obligation and ineligible seal. Thirty-seven pre-ledger orders were terminalized without
fabricating intent evidence. The operation is idempotent and refuses ambiguous PAPER fill evidence
or any unresolved LIVE order.

This cleanup does not turn the old cohort into a successful fill-projection sample. Revision 0042
opened new execution/selection/station boundaries with zero backfill; a future natural fill can
validate only that prospective cohort. Audited command 6 resumed Polymarket PAPER at
`2026-07-17T07:10:31.783545Z`; `/readyz` became 200 and the next cycle created two resting orders
whose intents attest release epoch 6 and every continuity lane. This is a prospective waiting-for-
fill state. LIVE remains unauthorized.
