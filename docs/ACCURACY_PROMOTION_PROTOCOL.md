# STRATUS accuracy promotion protocol

Status: implemented, deployed, and release-verified under database revision `20260714_0033`;
production subsequently advanced to revision `20260715_0038`. The formal experiment family is
currently unregistered: there are zero trials, evidence rows, fixed looks, and activations. This
is a fail-closed collecting/readiness state, not a promotion result.

Current diagnosis, 2026-07-15: a manual `stratus-evaluate-promotions` run at 08:18 returned
`trials=0`. The latest sealed cohort has complete promotable scopes for eight experts, but no
complete scope for the mandatory `regularized_stack` or `trajectory_nowcast`: their available
events mix `clob-dual-v1` and legacy sibling anchors. The global family therefore correctly
remains unregistered. This is neither a scheduler failure nor a reason to weaken the frozen core
roster or evidence-coherence rules.

## Purpose

Rolling accuracy reports are useful diagnostics, but they are not authority to change the
production probability. Repeatedly inspecting overlapping 30/60/90/180-day windows and many
expert/station/horizon/kind slices creates optional-stopping and multiple-testing risk. A report
that becomes favourable on one day must not silently activate a challenger.

The formal promotion protocol is therefore an immutable, prospective, fixed-look experiment.
`model_skill_reports` remain visible for diagnosis and have no activation authority.

## Frozen experiment family

The registration epoch is a source-controlled protocol identifier. One epoch registers one
complete global family containing every current candidate identity and every eligible
station/horizon/kind scope observed at registration. The family-wise alpha budget is at most
`0.05` and is divided across all registered family members with Bonferroni allocation.

Once the first trial exists, the family membership is frozen. A new model implementation,
fingerprint, station scope or hypothesis cannot silently open another 5% family. It requires an
explicit source-reviewed registration-epoch change and starts new prospective evidence.

Registration itself is fail-closed. The scheduled 05:25 UTC snapshot follows the sealed 05:18
probability cohort and requires at least one complete exact scope for every always-on expert:
regularized stack, AR(1) trajectory, remaining-day nowcast, conformal Gaussian, hierarchical
EMOS, QRF, analog, mixture and settlement beta. `regime_emos` is conditional on a detected
structural break, so it is included when present but cannot hold registration open forever. An
incomplete core roster registers nothing; the first non-empty producer response cannot freeze a
partial family.

Each trial freezes:

- expert and sealed expert version;
- the exact experiment fingerprint and baseline pipeline cohort;
- station, horizon and weather-kind scope;
- evidence, market-benchmark and resolution protocols;
- registration/evidence boundary;
- family membership, family alpha and per-trial alpha;
- exactly 60 distinct prospective blocks and one fixed look;
- the minimum acceptable mean Brier improvement;
- a content-addressed definition hash over every normalized decision parameter.

## Evidence unit and predictable range

One non-duplicated block is `station / local market date / weather kind / horizon bucket`. The
database column retains the name `independent_block_key`, but uniqueness of this key is not an
i.i.d. claim. Weather on adjacent dates may be serially dependent; the fixed-range martingale
test below is defined in terms of conditional expectations and predictable bounds instead.

A block is admitted only when a complete event vector can be reconstructed from immutable rows:

- every bucket uses an `accuracy_predictions` row with the trial's exact expert version,
  fingerprint, baseline cohort and `sealed-v2` provenance;
- all bucket predictions belong to the same event and point-in-time snapshot;
- each baseline is the exact linked `model_probs` row and uses `clob-dual-v1` evidence;
- the complete binary outcome vector is linked to an immutable `gamma-outcome-v1` event;
- the outcome became available after trial registration;
- candidate and baseline Brier scores are recomputed from the stored bucket probabilities and
  exact outcome vector.

All bucket prediction IDs, baseline IDs, market IDs and the resolution-event ID are frozen in the
block record. PostgreSQL independently validates the vector and derives scores and hashes. An
application-supplied opaque hash or one representative bucket is not sufficient evidence.

For block `t`, define improvement as

`I_t = baseline_brier_t - candidate_brier_t`.

Before the outcome is revealed, candidate and baseline probability vectors are already sealed.
PostgreSQL enumerates every possible winning bucket `j` in that complete vector and computes

`I_t(j) = mean_k[(b_tk - 1{k=j})^2 - (c_tk - 1{k=j})^2]`.

The stored predictable range is `L_t = min_j I_t(j)`, `U_t = max_j I_t(j)`, with width
`R_t = U_t - L_t`. The observed improvement must be one of those possibilities and therefore
must lie in `[L_t, U_t]`; otherwise the insert fails. Because this range is determined entirely
from point-in-time probabilities before resolution, it remains valid even when successive
station-days are not independent.

## Fixed decision

No interim significance statistic is a promotion decision. Before 60 blocks the public state is
only progress `n/60`; the decision mean, confidence bound and tail-probability bound remain
unavailable.

At 60 blocks, the selected evidence set is frozen by deterministic ordering on outcome
availability time and block key. Let `N = 60`, `mean(I)` be the observed mean improvement,
`delta` the preregistered minimum improvement and `alpha_i` the member's Bonferroni allocation.
The protocol stores

`radius = sqrt(log(1 / alpha_i) * sum_t(R_t^2) / (2 * N^2))`

and the conservative one-sided lower confidence bound

`LCB = mean(I) - radius`.

A trial passes if and only if `LCB > delta`. It also stores the corresponding Hoeffding tail
upper bound

`p_bound = exp(-2 * N^2 * max(0, mean(I) - delta)^2 / sum_t(R_t^2))`,

with the deterministic zero-range case handled separately. `p_bound` is a conservative bound,
not an exact p-value. PostgreSQL and Python use high-precision decimal arithmetic, round the
radius and probability bound upward, and round the LCB downward.

The formal null is that the average conditional expected improvement over the fixed 60-block
sequence is at most `delta`. Given the pre-outcome predictable ranges, the martingale
Hoeffding-Azuma bound controls the one-sided error without assuming i.i.d. station-days. The
family-wide error remains at most `0.05` through preregistered Bonferroni allocation. Wins,
losses and ties are retained only as descriptive telemetry; neither they nor a bootstrap interval
can authorize activation. This directly prevents frequent tiny wins plus rare large losses from
passing when mean skill is not demonstrated.

The look is unique. Later favourable outcomes cannot reverse a failed look, and later adverse
outcomes cannot rewrite a passed look. Retesting requires a genuinely new candidate and an
explicitly reviewed registration epoch; it cannot reuse the exhausted family budget.

## Activation and pipeline identity

Only a completed passing look can create an immutable activation. Activations are deferred until
**every trial in the frozen family is terminal**, then every passing scope is inserted atomically.
This prevents the first activation from changing the production cohort and stranding slower
sibling trials against an obsolete baseline. Probability calculation must match the activation's
exact expert/version/fingerprint/scope; `ModelSkillReport.eligible` is ignored. Applied component
metadata carries the activation, trial-definition and decision hashes.

The sorted active-activation manifest is content-addressed and included in the production
probability cohort. Adding an activation therefore starts a new prospective production cohort;
pre-activation trades cannot validate post-activation performance. A source change that alters
the candidate fingerprint fails closed and makes an old activation inapplicable.

PostgreSQL is the authority for this boundary. Trial insertion recomputes and overwrites the
caller-supplied `definition_hash` from the complete normalized definition. Look insertion
recomputes scores, range statistics, status and `decision_hash` from the immutable evidence set;
application-provided hashes or decision fields cannot select the result. Deferred constraint
triggers run at transaction commit and require the activation set to equal exactly all passing
looks once a family is terminal. A partial, extra or early activation set cannot commit.

## Operational invariants

- Current historical rolling reports are never translated into trials, decisions or activations.
- With zero post-registration resolved blocks, registration produces only `collecting: 0/60`.
- Trial definitions and evidence/decision/activation ledgers are append-only.
- Registration is itself an immutable prospective contract. Migration downgrade is refused after
  any trial has been registered, even when no outcome block or look exists.
- Health audit treats any newly applied challenger without a matching activation as critical.
- Operator API and Cockpit report diagnostic qualification separately from formal activation.

## Acceptance criteria

1. Duplicate bucket rows do not increase the distinct block count.
2. Cross-version, cross-fingerprint, cross-cohort, legacy benchmark or pre-registration outcome
   evidence is rejected by PostgreSQL.
3. The registered family alpha sum never exceeds 0.05.
4. Exactly the first 60 deterministic blocks form the only look.
5. UPDATE/DELETE/tamper attempts on formal evidence fail.
6. A failed look remains failed after arbitrary later favourable blocks.
7. An activation changes the probability cohort manifest.
8. No active challenger can be produced without an exact immutable activation.
9. The zero-resolution initial production run creates no decision and no activation.
10. A passing trial cannot activate while any sibling trial is still collecting.
11. Missing any always-on expert at initial readiness leaves the family unregistered.
12. PostgreSQL overwrites forged definition and decision hashes with hashes derived from the
    authoritative normalized rows and statistics.
13. A transaction containing anything other than the exact complete passing activation set is
    rejected by the deferred family constraint.
14. Serial dependence does not silently become an i.i.d. assumption: the decision uses each
    block's predictable Brier-improvement range and the conditional martingale null.

## Statistical references

- Wassily Hoeffding, "Probability Inequalities for Sums of Bounded Random Variables," *Journal
  of the American Statistical Association* 58(301), 1963:
  [DOI 10.1080/01621459.1963.10500830](https://www.tandfonline.com/doi/abs/10.1080/01621459.1963.10500830).
- Mourtada and Gaiffas, "On the optimality of the Hedge algorithm in the stochastic regime,"
  *JMLR* 20, 2019, Proposition 16 and its proof give the conditional Hoeffding-Azuma
  martingale/supermartingale form used to justify predictable bounds under sequential
  dependence: [paper](https://jmlr.org/papers/volume20/18-869/18-869.pdf).

## Production rollout record

Revision `20260714_0033` was deployed on 2026-07-14 with protocol
`fixed-range-brier-v3` and method `predictable-range-hoeffding-v1`. The rollout
passed 410 non-integration tests, 121 PostgreSQL integration tests, Ruff,
strict mypy, a warning-free WinUI x64 build, and a restored-data
`0032 -> 0033 -> 0032 -> 0033` drill with invariant row counts.

The initial formal state is intentionally `0/60`: no historical or
pre-registration row was imported into the prospective authority, and the
activation manifest contains no promotions. Paper execution remains enabled;
live remains gated. A pre-migration custom-format backup is stored as
`backups/stratus_pre_0033_v3_20260714T121843Z.dump` with SHA-256
`A4DE5E430FBB981D11F63E0CBC96F36D220D5B66B4AB86BEAF0202DA00EE4847`.
