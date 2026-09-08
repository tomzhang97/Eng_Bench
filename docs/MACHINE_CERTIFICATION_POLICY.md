# Eng_Bench Machine Certification Policy

**Target:** Gold v2.0 Global  
**Policy version:** 1.6  
**Effective:** 2026-08-22

## Current Frozen Cohort

Wave482 supersedes Wave427 before any machine-certified row was released. The
current frozen cohort contains 8,247 eligible objective train MicroText rows:
1,149 non-pin rows and 7,098 pin labels. The stricter rerun holds one pin label
that equals its source document's recorded revision token. The remaining
21,786 staged decisions stay human-required. No active Gold row was modified.

The eligibility report is
`derived/quality/v2_0_machine_certification_2026-08-22-wave482-revision-safe/eligibility_report.json`
with SHA-256
`603e38b0cca29f00c5ec0236511168a70210027266543622390efba405356006`.

Wave483 retrospectively evaluated the same pre-human pin-label rule against all
available release-safe completed review outcomes, including accepts, edits,
and rejects. Selection used only pre-human evidence. Of the rows passing every
current machine gate, 389/389 agreed with the historical human outcome. The
one-sided 95% precision lower bound is `0.99232847`, above the `0.99` policy
floor. This qualifies the 7,098 pin-label rows without a new row-by-row human
calibration. Component, tolerance, process, and dimension categories do not
have enough passing historical evidence and remain under the independent
human-calibration path.

Wave484 hash-bound all 7,098 qualified rows as machine-certified and
balance-deferred. Wave486 then held 1,485 strict duplicate-QA rows. The final
5,613 strict-unique rows pass every read-only promotion gate in Wave487 and
would produce a 9,344-row combined benchmark. They remain outside active Gold
because adding them now would worsen the already excessive pin-label share.

Wave493 supersedes the mixed-category Wave482 calibration handoff. The new
calibration cohort contains only the 1,149 non-pin eligible rows and uses a
300-row sample: 247 component values, 22 dimensions, 20 tolerances, and 11
process values. It contains zero pin labels, reuses 1,149 frozen independent
OCR results, and has zero eligibility holds. Its report is
`derived/quality/v2_0_machine_certification_2026-08-22-wave493-nonpin-only/eligibility_report.json`
with SHA-256
`2cffc79e2f608fddaa306ba30fc0c32b0c5d2c7687f3d1f2f5ca553fd9ebb170`.

Wave637 attempted to eliminate that remaining sample through historical
calibration rather than assuming it was necessary. The machine ran 769 new
independent RapidOCR checks against completed human outcomes and recovered 401
zero-error machine-eligible rows. Of those, 389 were the already-qualified pin
lane and only 12 were non-pin dimensions. The dimension lane's one-sided 95%
precision lower bound is `0.77907781`, below `0.99`; component, tolerance, and
process-value lanes had no qualifying historical sample. Therefore no non-pin
category qualifies historically and the frozen 300-row non-pin calibration
remains the smallest defensible human sample.

Wave680 adds a complete machine visual second opinion over the same frozen
300-row sample. All 15 contact sheets and all 300 rows were inspected; the
displayed values and categories had zero observed machine-opinion errors. The
result is hash-bound in `calibration/machine_second_opinion_attestation.json`
and the reviewer-aid CSV. It reduces the human task to independent confirmation
of a machine-prechecked sample, but it does not count as the human calibration
decision and cannot authorize Gold by itself.

Wave681 applies the policy to the 69-row NASA TULIP intake. It routes all 69
rows to `human_required`: the source is raster/OCR-derived rather than an
allowlisted deterministic text layer, and 47 rows use semantic equipment or
instrument categories. This fail-closed result is intentional; machine-first
means automating everything that can be proved, not weakening the evidence
contract for the remainder.

Waves754-Wave768 extend that boundary to five additional official NASA NTRS
process and flow-diagram sources. The machine processed 1,066 OCR detections,
held 841 pattern, duplicate, caption, fragment, or visual failures, corrected
41 retained proposals, and delivered only 225 semantic confirmations. The
packet passes evidence, Gold-collision, physical-region, workbook, split, and
promotion-contract checks. All 225 remain `human_required` because the labels
are raster-derived semantic categories or dev/test truth. This records a
78.8931% reduction in row-by-row human work without treating machine visual
inspection as independent human certification.

Waves771-Wave787 apply the same boundary to five more official NASA NTRS
facility and engine-flow sources. The machine merged 278 OCR proposals, held
245 pattern or full-visual-QA failures, corrected 13 retained proposals, and
left 33 semantic confirmations. All 33 remain `human_required`; 16 are train
semantic labels and 17 are evaluation-split truth. The verified packet and
promotion preflight have zero identity, evidence, image, split, or Gold-
collision failures. The 88.1295% labor reduction is machine ownership of
repeatable curation, not permission to relabel semantic rows as automatic Gold.

Waves788-Wave807 exercise the complete policy against five additional NASA
facility, wind-tunnel, flowmeter, thruster, and engine-cycle sources. The
machine reduced 1,111 merged candidates to 137 usable regions, correcting 34
proposals and removing 976 immediate human decisions. Independent RapidOCR and
exact text-layer evidence identify two objective train process values as
`auto_eligible_pending_calibration`; they remain outside Gold until calibration
passes. The other 135 rows are explicitly `human_required` because they need
semantic category judgment or supply dev/test evaluation truth. The verified
review pack contains only those 135 irreducible decisions.

This pass also makes accounting enforceable. The curation-funnel audit must
reconcile raw candidates to machine filter holds, visual holds, calibration
rows, certification holds, and the exact human packet. A missing or extra row,
an active-Gold hash change, or a certification/packet mismatch fails the run.
Machine confidence alone is never a promotion event.

Wave467 adds a separate, non-frozen eligibility backlog from the official
Arduino Leonardo pinout. Of 58 distinct, split-locked train pin labels, 54 pass
the full deterministic text-layer, source provenance, crop, category, dedup,
and independent RapidOCR `>=0.98` contract; four remain human-required after a
strict multi-view rescue recovered none. These 54 rows are not part of the
Wave427 frozen cohort or its 300-row calibration handoff. They must be batched
into a later frozen cohort and calibrated under this policy before promotion.
No active Gold row was modified.

Wave384 extended the prior exact-text cohort with 116 labels proven by a
boundary-safe source-subspan contract. A complete crop-and-context audit found
and removed five apparent gains that lost engineering meaning (`USB`, `3.3V`,
two `GND` rows, and `200mm`). Wave396 adds another 142 visually audited rows:
137 complete power-rail labels, one explicit inch dimension, and four power
rails recovered by strict multi-view OCR. The same rescue attempted 278 newly
exposed OCR-only rows and kept the other 274 human-required. Wave427 then
exhaustively tested 221 previously unattempted objective OCR cases and admitted
two additional exact pin labels (`SPIO_SCK` and `GPIOEX_I2C_SDA`) without
changing the 300-row calibration sample.

## Purpose

Eng_Bench uses human effort where judgment changes benchmark validity and uses
deterministic machine certification where the label can be independently
reconstructed. This policy reduces repetitive review without describing a
machine decision as human certification.

## Maximum Machine Responsibility Rule

The execution goal is to maximize machine ownership under the release evidence
contract and minimize human row actions without lowering Gold quality. Before
any row is assigned to a person, the machine must exhaust source and rights
preflight, rendering, extraction, OCR or text-layer comparison, crop and
context construction, deterministic category checks, deduplication, split
reservation, leakage checks, evidence hashing, and reviewer prefill.

Every row that remains human-owned must carry an explicit reason from this
closed set: `evaluation_truth`, `unresolved_visualdiff_semantics`,
`semantic_engineering_category`, `ambiguous_or_clipped_evidence`,
`independent_calibration`, `independent_agreement`, or `policy_exception`.
Human review is never requested merely to repeat a machine-verifiable
transcription, file check, localization, canonical formatting operation, or
previously preserved semantic decision.

This rule does not weaken the release boundary. Machine-certified rows stay
train-only and carry disclosed certification metadata; human authority remains
mandatory for dev/test truth and irreducible semantics. Both lanes still pass
the same strict promotion, provenance, split, leakage, deduplication, and hash
gates before Gold changes.

For source-intake semantic lanes, humans must never receive the raw OCR
discovery pool. The machine must first classify every proposal as retained or
held, inspect retained crops, correct visible text and category proposals,
remove overlapping and duplicate fragments, reserve source-level splits, and
pass the standalone packet and staged-promotion contracts. Each intake must
record raw, held, corrected, and remaining row counts plus the resulting human-
work reduction rate. Human work begins only at the smallest irreducible set:
confirm, edit, or reject the machine-prefilled semantic answer. Returned rows
then re-enter machine-owned ingestion, reconciliation, deduplication, leakage,
provenance, preview, and release validation.

## Certification Tiers

| Tier | Eligible data | Release use |
|---|---|---|
| `human_gold` | Human-accepted or human-edited rows | Train, dev, or test |
| `human_semantics_machine_localized` | Existing human semantic decision with machine localization and evidence reconciliation | Train or dev; test only when the underlying human decision already satisfies the release protocol |
| `auto_gold_train` | Calibrated machine-certified objective MicroText | Train only |
| `human_required` | Semantic, ambiguous, or evaluation labels | Outside Gold until reviewed |
| `reject_or_hold` | Broken provenance, evidence, split, duplicate, or schema contract | Outside Gold |

Unreviewed machine certification is prohibited for:

- every dev or test row;
- every VisualDiff row unless a separate objective train-only VisualDiff rule
  has passed the same frozen zero-error calibration standard; no such rule is
  active as of policy version 1.5;
- room, process-label, equipment-tag, instrument-tag, and pipe-line-tag semantics;
- clipped, ambiguous, unknown-category, or context-dependent labels;
- sources without release-safe rights, a local payload, and a matching SHA-256;
- rows without a valid frozen train split reservation.

## Auto-Eligibility Contract

An `auto_gold_train` candidate must pass all of the following:

1. The task is MicroText and the reserved split is `train`.
2. The source payload, source URL, redistribution status, manifest record, and
   inventory record pass the active provenance policy.
3. The page image exists, its SHA-256 is recorded, and the bbox is valid and
   inside the image.
4. The candidate comes from an allowlisted deterministic text-layer extractor.
5. The normalized source-text evidence satisfies one recorded contract:
   `exact_all_fields`; `exact_raw_target_no_proposed` when the proposal is
   absent and raw/target/answer agree; or `boundary_safe_exact_subspan` when
   target and answer agree and the answer is an exact, complete engineering
   token inside the raw source text. Subspan boundaries reject clipped
   decimals, range endpoints, polarity suffixes, parenthetical qualifiers,
   and prose fragments. Pin-label subspans must contain a digit and come from
   uppercase technical text. Ambiguous standalone voltage-like pin labels are
   excluded from the missing-proposal contract.
6. The category and label match a conservative objective pattern. Dimension
   values require an explicit engineering unit, degree mark, or feet-inch
   syntax; ASCII and typographic inch/foot marks are accepted, while unitless
   electronics values such as `2M` remain excluded. Pin-label power rails must
   use a complete form such as `+3V3`, `+5V`, or `+24V`; malformed signs and
   decimal-voltage variants outside that grammar remain excluded. A pin label
   equal to any recorded source-document revision token is also excluded; this
   blocks repeated title-block revisions such as `C3` from entering the pin
   lane.
7. The row has no active-Gold, staged identity, source-payload alias, or exact
   physical-region collision.
8. Independent RapidOCR recognition matches the proposed text exactly with
   confidence at least `0.98`.
9. Every evidence field, including the selected source-text contract and the
   normalized raw/target/proposed text for non-exact contracts, is included in
   a per-row SHA-256 certification record.

Rows that fail only the initial OCR check may enter a supplemental multi-view
rescue audit. Rescue never lowers the `0.98` floor: at least two distinct image
transformations must independently reproduce the exact expected text above the
same threshold. The main eligibility audit then reruns every contract check
against the hash-bound combined OCR cache. Each eligible row records the full
OCR evidence payload and its SHA-256; the eligibility report records the exact
cache hash used. `--ocr-cache` is always read-only. Any newly computed OCR
records must be written to a different, previously absent path with
`--ocr-cache-output`; the auditor refuses an input/output collision.

OCR rescue selection must use the same task inference as the eligibility
auditor, including MicroText rows whose task is inferred from their schema.
Split-plan and OCR-cache inputs must be combined with
`tools/merge_staged_split_plans.py` and
`tools/merge_machine_ocr_caches.py`; both tools fail on incompatible duplicate
assignments instead of silently choosing a value.

Passing eligibility is not promotion. Eligible rows remain
`safe_to_merge_gold=false` and `auto_eligible_pending_calibration`.

## Calibration Gate

Each frozen machine-policy cohort receives a deterministic, category-stratified
sample of at least 300 rows. A human checks the crop, context, text, and
category and records `correct`, `incorrect`, or `unclear`.

Before handoff, the machine may render contact sheets and record a hash-bound
second opinion for every sampled row. This prefill is a reviewer aid and an
audit trail, not a human decision and not release authority. The human must
still inspect the frozen sample independently and fill the dedicated human
decision field; the finalizer ignores the machine opinion when evaluating the
human calibration gate.

The cohort can proceed only when:

- every sampled row has a decision;
- all decisions are `correct`;
- the one-sided 95% precision lower bound is at least `0.99`;
- checklist IDs and all evidence artifacts match their recorded SHA-256 values.

One `incorrect` or `unclear` decision fails the entire cohort. The policy rule
must be revised and the cohort must be re-audited with a newly frozen sample.

### Historical Calibration Alternative

A category may avoid a new calibration handoff only when a read-only audit of
completed historical review satisfies the same statistical gate. The audit
must select rows using pre-human evidence only, include accepted, edited, and
rejected outcomes, enforce the current provenance/text/crop/dedup/OCR rules,
and evaluate the human outcome only after selection. Qualification is
category-specific and still requires at least 300 rows, zero incorrect or
unclear outcomes, and a one-sided 95% precision lower bound of at least 0.99.

Historical calibration does not authorize dev/test rows, semantic categories,
VisualDiff, or category-imbalanced release. Its attestation and labeled
evidence must be hash-bound and pass the normal promotion preview. Wave483 is
the first passing use of this alternative and applies only to `pin_label`.

After calibration, the finalizer emits a new staged JSONL with:

```text
review_status=accepted
review_source=machine_certification_policy
human_reviewed=false
certification_method=machine_verified
certification_tier=auto_gold_train
certification_policy_version=1.0
```

The output still cannot modify active Gold directly. It must pass the normal
read-only promotion preview, signed split plan, additive transaction, strict
v2 validator, provenance audit, leakage audit, and duplicate audit.

## Existing Human Semantics Recovery

Machine localization is not machine-only certification. A completed human
VisualDiff decision may be recovered without a second human pass when all of
the following are true:

1. The original human description and final review status are preserved.
2. The old/new evidence is rendered and reconciled against that semantic
   decision; contradictions are held instead of rewritten into Gold.
3. The canonical English description and change type are recorded separately.
4. `desc_source=human_semantics_machine_localized` and
   `localization_method=machine_translation_and_visual_reconciliation` are
   explicit.
5. A frozen evidence-sheet reference is attached.
6. The normal split, leakage, duplicate, provenance, annotation, strict-v2,
   preview-hash, snapshot, and atomic-promotion gates pass.

Wave620-Wave631 recovered 15 prior human decisions under this contract.
Fourteen were localized and promoted; one contradictory row was machine-held.
This lane eliminates repeat review while keeping the semantic authority human
and the machine contribution visible. The Wave633 responsibility audit removes
all 15 from the remaining human partition and raises verified machine-owned
actions avoided to 8,686.

## VisualDiff Primary/Backup Review Compression

Machine-written VisualDiff semantics are reviewer proposals, not release
authority. Before a new family is sent to a person, the machine must compare
aligned old/new evidence, reject pixel-identical and weak cross-location
examples, prefer one strongly localized primary row, write a concise proposed
change type and description, and bind a same-family backup. The backup is not
reviewed unless the primary is rejected.

The partition must be SHA-256-bound to its input queue, account for every row,
reference rendered contact-sheet evidence, and remain
`safe_to_merge_gold=false`. A primary may enter Gold only after a human
confirms or corrects its semantics and the normal promotion gates pass.

Wave640-Wave659 applied this rule to 52 candidates from 26 not-yet-active
families. Visual QA selected 18 clear primaries, deferred 18 bound backups,
and held 16 reserve rows. Every primary has a machine-written proposal, all 18
pass queue QA, and none is pixel-identical or near-identical. This reduces the
immediate family-gate review from 36 rows to 18 while preserving the projected
12-to-30 family path.

## Human Work That Remains

Humans retain responsibility for evaluation labels and unresolved VisualDiff
meaning,
semantic engineering categories (including equipment, instrument, and pipe or
line tags), correction of machine-policy exceptions, and the independent
185-row agreement audit. The machine performs extraction, preflight, evidence
hashing, OCR consensus, deterministic category checks, deduplication, split and
rights verification, recovery/localization of existing human decisions, and
reviewer-prefill work before a human sees a row.
Routine objective train-label transcription is delegated to the machine lane
whenever the contract above can prove it; row-by-row human certification is not
required for that calibrated tier.

The current responsibility boundary is now explicit: 7,098 frozen pin labels
require no new row-by-row human certification; 5,613 of them are also strict-
unique and promotion-preview clean, while 1,485 remain duplicate holds. The
1,149 objective non-pin rows now require only the frozen 300-row
calibration sample, not 1,149 row-by-row reviews. Humans should not be assigned
the certified pin lane again.

Wave488 independently verifies that partition and projects 7,947 net
row-by-row decisions avoided after the one-time 300-row non-pin calibration.
Machine effort must prioritize non-pin balance and source-family diversity;
machine-certified pin rows remain staged until their promotion improves, or at
least does not worsen, the release balance gate.

The agreement audit is not interchangeable with the simplified 1/2/3 auditor
screens used during candidate expansion. The Wave449 candidate contract uses
those prior screens to avoid repeating preliminary quality checks, but the
formal gate still requires two independent reviewers to complete answer,
corrected-answer, bbox, corrected-evidence, accept/reject, ambiguity, and rights
fields on the frozen active-Gold sample. Machine checks may prefill immutable
source and evidence fields and may compute the final metrics; they may not fill
either reviewer's judgment fields.

## Complete-History Calibration And Reservation Safety

Wave726 expands historical calibration to the complete processed-return
history while explicitly excluding canonical Gold. It finds 692/692 eligible
pin-label decisions correct, with a 95% one-sided lower confidence bound of
0.99568026. Pin labels therefore qualify for the calibrated machine lane under
the existing policy. Dimensions are 15/15 correct but do not meet the minimum
sample size, and no other non-pin category qualifies. This result does not
permit semantic categories, dev/test truth, or unresolved VisualDiff meaning
to bypass human judgment.

Machine certification must also prove novelty against every frozen machine
cohort, not only active Gold and completed human returns. Reservation-aware
exact and near-region exclusion is mandatory before a candidate is counted as
new capacity. Wave732 demonstrates the rule: 98 of 102 apparent non-pin rows
duplicate a frozen cohort by ID and the remaining four collide by physical
region. The entire cohort is audit-only and contributes zero promotable rows.

Waves737-Wave748 exhaust the current local non-pin conversion reservoir under
that rule. Existing exact, substring, text-layer, and bounded OCR routes
produce no new non-pin rows; two remaining raster sources produce only 153
new pin labels, which are deferred for balance. Machine responsibility now
requires acquiring new rights-clean non-pin-rich engineering sources before
claiming further scale progress. Exhausted sources are recorded in
`SOURCE_CONVERSION_EXHAUSTION.csv` and must not be re-mined as nominally new
capacity.

## Commands

```powershell
.venv-ocr\Scripts\python.exe tools\audit_machine_certification_eligibility.py `
  --root . `
  --cohort future=<staged.jsonl> `
  --split-plan <split-plan.json> `
  --date-label <date-label> `
  --output-dir derived\quality\<machine-certification-run> `
  --ocr-mode rapidocr `
  --ocr-cache derived\machine_audit\machine_certification_rapidocr_cache.jsonl `
  --ocr-cache-output derived\machine_audit\<new-ocr-cache-output>.jsonl
```

Rows that become eligible after a cohort was frozen must be recorded as an
additive delta rather than silently appended to that cohort:

```powershell
python tools\build_machine_certification_delta_audit.py `
  --root . `
  --baseline-jsonl <frozen-auto-eligible.jsonl> `
  --candidate-addition-jsonl <new-auto-eligible.jsonl> `
  --output-dir derived\quality\<new-delta-audit> `
  --date-label <date-label>
```

Repeat `--candidate-addition-jsonl` for multiple additions. The tool writes a
hash-bound combined candidate snapshot and a visual audit pack. An additive
delta remains calibration-pending and cannot inherit a previously frozen
sample unless a later policy audit explicitly proves that coverage.

After the calibration checklist is returned:

```powershell
python tools\process_machine_calibration_return.py `
  --root . `
  --cohort-dir derived\quality\<machine-certification-run> `
  --completed-checklist <completed-checklist.csv> `
  --output-dir derived\quality\<new-return-control-run> `
  --date-label <date-label> `
  --require-ready
```

The controller snapshots the returned CSV, recovers the eligibility-report
hash from the frozen cohort attestation (or accepts an explicitly supplied
expected hash), verifies the eligible JSONL and split-plan hashes, and invokes
the all-or-nothing finalizer. A blank, invalid, `incorrect`, or `unclear`
calibration emits zero certified rows and does not run promotion preview. A
passing calibration enters the normal read-only promotion preview
automatically. The output directory must be new and under `derived/quality`;
the controller records active-file hashes before and after and never applies
rows to Gold.

Historical pin-label calibration and strict-dedup commands:

```powershell
.venv-ocr\Scripts\python.exe tools\audit_historical_machine_calibration.py `
  --root . --cohort <auto-eligible.jsonl> --output-dir <audit-dir> `
  --date-label <date-label> --ocr-mode rapidocr --ocr-cache-output <new-cache.jsonl>

python tools\finalize_historical_machine_certification.py `
  --root . --eligibility-dir <eligibility-dir> --calibration-dir <audit-dir> `
  --output-dir <finalization-dir> --date-label <date-label> --require-ready

python tools\filter_machine_certified_strict_dedup.py `
  --root . --certified-input <certified.jsonl> --preview-dir <preview-dir> `
  --output-dir <dedup-dir> --date-label <date-label> --require-clean
```
