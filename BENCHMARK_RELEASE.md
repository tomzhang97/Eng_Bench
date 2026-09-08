# Eng_Bench v0.9 (Silver) Release Notes

**Date:** 2026-09-08
**Status:** v0.9 Silver lineage, working dataset; not a publishable Gold v2.0 Global release
**Current Active Samples:** 1596 Visual-Diff Questions, 4098 Microtext Questions (5694 total)
**License:** Mixed public-source candidate; see [DATACARD.md](DATACARD.md) and `SOURCE_INVENTORY.csv`

## Current Verified Checkpoint

Gold v2.0 Global remains **5/9 gates PASS**. Row scale (5694/25000 minimum),
frozen challenge scale (1600/5000), formal agreement (0/185), and complete source
provenance (298/307 paper-ready documents) remain OPEN. Six MicroText category
floors, 336 machine-detectable nonfinal VisualDiff descriptions, 526 BBB rows
with corrected geometry but unverified semantics, and 49 unresolved active audit
flags also block release. Current gate:
`results/health/v2_0_gate_audit_2026-09-08-wave2295-machine-readiness.json`.
The 29 counted baseline reports remain bound to the existing 1,600-row frozen
challenge. Active test now contains 1,784 rows, so the 184 rows outside that
freeze are not yet part of the public/private challenge or its baseline scores.

The post-promotion machine lane is now current and fail-closed. Twenty-five of
the frozen 300 calibration decisions are reusable from exact accepted human
reviews, leaving 275 independent calibration decisions. After removing 385
rows already active through human review, pin rows, and 26 duplicate-QA holds,
799 non-pin train MicroText rows pass the combined-Gold forecast. They remain
outside Gold until calibration succeeds and the normal promotion transaction
passes; this readiness evidence is not row-count or release-gate credit.

The formal agreement preparation blocker is now closed. A quality-filtered,
release-safe 185-row sample (95 MicroText and 90 VisualDiff) is packaged for two
independent reviewers under
`derived/human_adjudication/Eng_Bench_Gold_v2_FORMAL_AGREEMENT_185_2026-09-08.zip`.
Review completion is still 0/185, so this preparation does not pass the
agreement gate or alter Gold.

The current routine quality-control handoff is
`derived/human_adjudication/Eng_Bench_Gold_v2_AUDITORS_12x24_GOLD_RECHECK_2026-09-08_v6.zip`.
It assigns 144 active-Gold identities to two independent auditors each (288
blank judgments total). Historical assignments, completed decisions, the 185
formal-agreement rows, 49 active audit flags, and all 338 machine-detectable
nonfinal VisualDiff descriptions are excluded, as are all 526 BBB geometry and
semantic holds. The description debts comprise 125 TODO
placeholders, 172 unvalidated machine visual descriptions, 39 tentative
generator descriptions, and two non-English descriptions. This replaces v5
and all earlier routine audit ZIPs; v5 contains pre-repair BBB evidence and
must not be distributed.

The current MicroText balance path is hash-bound and split-safe. Wave2285
promoted 1,112 completed primary-reviewed rows only after active-Gold collision,
split-reservation, duplicate-QA, answer-leakage, evidence-hold, provenance, and
strict combined-Gold checks. The active pin-label share is now 43.66%, below the
45% ceiling, and open category floors fell from eight to six. The remaining
shortfalls are dimension value 194, equipment tag 72, instrument tag 222,
pipe/line tag 69, process value 91, and tolerance value 78. No VisualDiff row
from the returned primary workbook was promoted because its alignment evidence
requires correction or rereview.

The existing 1600-row challenge export is a historical freeze, not evidence of
current release eligibility. New export generation now refuses known unresolved
test rows. The separate internal view under
`derived/quality/filtered_evaluation_2026-09-03-wave1990/` retains 614 frozen rows
(306 public, 308 hidden) after known-hold filtering. It contains 605 MicroText
and only 9 VisualDiff rows and must not be reported as a balanced Gold benchmark.
Keep `*_labels_private.jsonl` on the scoring side, never in model inputs. The
original release and all human votes remain unchanged. See
`AUDITOR_RETURN_STATUS.md` for current machine work and human actions.

## 1. Overview And Earlier Checkpoints
Eng_Bench is a benchmark for engineering diagram understanding, focusing on **Visual Diff** (detecting changes between revisions) and **Microtext** (reading dense technical text). See [DATACARD.md](DATACARD.md) for full details. 

This Silver-lineage working release validates the end-to-end schema, split policy, canonical image packaging, validation tooling, and baseline path. It is not yet a final public Gold v2.0 release: visualdiff includes human/CVAT-reviewed rows plus assisted descriptions with provenance fields, and current microtext rows were promoted after crop-level review. The machine-first target policy permits calibrated machine certification for objective train-only MicroText while preserving human authority for dev/test evaluation truth, unresolved VisualDiff meaning, and semantic or ambiguous rows. Existing human decisions do not require a second reviewer merely for English localization or canonical typing. The Wave620-Wave631 recovery pass promoted 14 such VisualDiff rows after visual reconciliation and an eight-gate hash-pinned atomic transaction, while holding one contradictory row. Wave919-Wave925 recovered and promoted two more completed family decisions through the same evidence-bound localization and atomic promotion lane. Wave926-Wave949 then accounted for all 845 ordinary reviewed rows in the latest primary return and atomically promoted 669 MicroText plus 91 VisualDiff rows; 85 rows remain explicit release holds. Wave950-Wave960 completed the first source-atomic provenance repair: seven blocked `wsdot_drainage_ds2` MicroText dev rows were replaced one-for-one with seven accepted, evidence-distinct rows under a hash-pinned preview and snapshot-backed atomic transaction. The current Wave960 checkpoint has 4,507 rows and passes strict unified validation, split leakage, question leakage, provenance-regression, baseline coverage, and snapshot-backed migration checks. Of 188 active documents, 179 are paper-ready, representing 177 distinct Gold payloads after alias deduplication. Release-safe inventory is 513/150. All 29 counted baselines cover the current 1,600-row test split, and the deterministic challenge package contains 802 public plus 798 hidden inputs. Global row/test scale, nine inherited provenance blockers, MicroText category balance, and independent agreement remain open.

Wave640-Wave659 screened 52 additional VisualDiff candidates into 18 clear,
machine-described family primaries, 18 dormant same-family backups, and 16
reserve rows. This reduces the immediate family-gate confirmation task from 36
rows to 18 without treating machine proposals as Gold.

Wave919-Wave925 reconciled those 18 primaries against current human work: two
completed decisions were evidence-localized and promoted, ten remain in the
current primary assignment, and six genuinely unassigned families were placed
in a compact engineering-confirmation packet. Active family breadth is now
14/30 at that checkpoint; later reviewed promotions raised current active
family breadth to 33/30 without admitting any unreviewed row.

Wave926-Wave949 recovered 845 completed ordinary review rows from the latest
primary controller, partitioned them through contract and duplicate gates, and
promoted 760. The remaining 85 are explicit holds: 13 duplicate-QA MicroText
rows, 60 VisualDiff rows needing change-type resolution, nine missing-split
rows, two active overlaps, and one historical conflict. The current formal
Gold v2.0 Global audit passes 5/9 gates: source payload breadth, release-safe
inventory, VisualDiff family breadth, baselines, and leaderboard
infrastructure. Total rows, test scale, agreement, and complete active-source
provenance remain open.

Wave950-Wave960 retired the first complete inherited provenance blocker without
changing release scale. Seven accepted MicroText dev replacements were swapped
for seven `wsdot_drainage_ds2` rows after a 14-gate read-only preview, hash-
pinned dry run, full active-file snapshot, and atomic apply. Active provenance
improved from 178/188 to 179/188 paper-ready documents; nine blockers remain.
The frozen fallback contract changes seven dimensions to seven pins, so this
is provenance progress rather than MicroText-balance progress.

Wave663-Wave681 extend the machine-first balance path without changing active
Gold. A balanced closure overlay now stages 614 rows across 153 documents and
would close every MicroText category floor at full acceptance. At the 65%
stress floor, only `pipe_line_tag` (9 rows short; 13 more candidates needed)
and `process_value` (4 rows short; 6 more candidates needed) remain open. The
official NASA TULIP cryogenic source added 69 visually checked, train-reserved
rows after reducing 492 OCR regions to 69; all remain human-required because
the source is raster-derived and most labels are semantic.

Waves684-Wave702 close that modeled balance gap without changing active Gold.
Two additional official NASA NTRS P&ID sources produced 358 raw OCR proposals;
full machine review of eight contact sheets held 290 and retained 68 readable
regions. The current balanced overlay now stages 633 rows from 155 documents:
104 dimensions, 67 equipment tags, 220 instrument tags, 94 pipe-line tags,
104 process values, and 44 tolerances. Every category floor now closes even at
the 65% modeled acceptance rate. The final 68-row NASA packet passes identity,
evidence, image, workbook, Gold-collision, and physical-region checks, but all
rows remain train-reserved and human-required under the fail-closed semantic
and raster-extraction policy. Review rows 1-50 are the category-closure
priority; rows 51-68 are visibly deferred reserve and need no current action.

The frozen 300-row non-pin calibration has now received a complete 300/300
machine visual second opinion with no observed mismatch. This is a reviewer
aid, not release authority: one independent person must still confirm the
sample. A passing return would authorize the existing 1,149-row deterministic
non-pin lane to proceed to strict promotion preview and would avoid 849
additional row-by-row checks. No unreviewed row was promoted.

Waves720-Wave723 supersede the first Wave708/Wave718 rights-replacement pass,
and Waves840-Wave844 minimize its review churn against the complete issued
contract.
The human-assignment justification audit exposed ten preferred candidates that
had already been promoted into active Gold. The planner and independent
readiness audit now reject underlying active pair/item identities. The rebuilt
contract rejects 14 active identities, retains 1,427 issued rows, retires the
ten promoted rows, and selects exactly ten fresh substitutes while preserving
all 1,437 affected rows, 1,437 unique evidence fingerprints, and zero
task/split gap. The default return controller now uses this minimal-churn
contract and recognizes 399 completed replacement decisions with 1,038 still
outstanding. The live preview verifies 1,437/1,437 reservations, zero uncovered
rows, and unchanged active hashes; it remains fail-closed until all reviews are
complete. Wave723 also justifies all 21,771 remaining human-owned benchmark
rows with zero unexplained assignments. Wave712 formalizes the
maximum-machine-responsibility rule and verifies 8,976 machine-owned row
decisions.

Waves726-Wave753 correct the capacity model and exhaust the current local
non-pin source reservoir without changing active Gold. Complete-history
calibration verifies 692/692 historical pin-label decisions, with a 95%
one-sided lower bound of 0.9957, so that category qualifies for the calibrated
machine lane. Other non-pin categories remain below the required calibration
sample size. The materialized responsibility ledger contains 3,603 current and
18,168 future human-owned rows, but 1,437 are provenance replacements rather
than additive growth. Corrected all-accepted active-plus-net capacity is
therefore 24,079 rows, 921 below the 25,000-row floor. Maintaining the planned
45% maximum pin share requires 5,482 additional canonical non-pin rows.

Reservation-safe audits removed frozen machine cohorts and near-region repeats
from the apparent unstaged reservoir. Re-mining the remaining 46 eligible
local sources produced no new non-pin capacity, and the final two raster PCB
sources produced 153 physically new pin labels that are deferred for balance.
The Wave748 plan consequently has zero eligible local balance sources. The
next machine-side expansion must acquire and convert new rights-clean,
non-pin-rich P&ID, process, mechanical, and civil sheets. Re-running the same
local OCR and text-layer reservoirs is recorded as exhausted and must not be
counted as progress.

Waves754-Wave770 begin that external expansion with five official NASA NTRS
process and flow-diagram sources. Eleven high-yield pages produced 1,066 OCR
candidates. Machine pattern and visual review removed 841 row decisions and
corrected 41 retained proposals, leaving 225 semantic confirmations. The
standalone packet has 225 unique crops, ten full-page contexts, HTML browsing,
CSV and validated XLSX checklists, Chinese instructions, zero Gold collisions,
and zero missing or invalid images. This is a 78.8931% reduction in row-by-row
human work. The new sources increase release-safe inventory to 493 unique
documents, but the staged rows do not count as Gold until human confirmation
and strict promotion.

Waves771-Wave787 continue the same machine-first rule with five additional
official NASA NTRS facility and engine-flow sources. Six selected pages yielded
278 merged OCR candidates. Pattern filtering held 228 rows, full visual QA held
17 more, and the machine corrected 13 retained proposals. Only 33 semantic
confirmations remain, an 88.1295% reduction in row-by-row human work. Their
verified workbook and evidence pack contain 33 unique physical regions, five
full-page contexts, zero Gold collisions, zero missing images, source-level
split reservations, and a zero-fatal promotion preflight. Release-safe
inventory reaches 498 unique documents and the responsibility ledger reaches
10,062 machine-owned row decisions; active Gold remains unchanged.

Waves788-Wave807 add five more official NASA NTRS engineering sources and
apply the maximum-responsibility policy end to end. The machine merged 1,111
raw candidates, held 926 by repeatable filtering and 48 by full visual review,
corrected 34 retained proposals, and left 137 structurally usable rows. Two
objective train rows are isolated for independent calibration; the remaining
135 semantic or evaluation-split judgments are in a self-contained review
pack. This removes 976 immediate row decisions, an 87.8488% reduction, without
promoting an unreviewed row. Release-safe inventory reaches 503 unique source
documents and the cumulative responsibility ledger reaches 11,038 machine-
owned row decisions.

## 2. Directory Structure
The dataset is self-contained in the `Eng_Bench` root:

```text
Eng_Bench/
├── images/                          # Canonical Image Repository
│   ├── viola__pcbV1.0/              # {doc_id}__{version_id}
│   │   ├── page_0000.png            # 300 DPI Rendering
│   │   └── ...
│   ├── viola__pcbV1.1/
│   ├── bbb__C/
│   └── bbb__C3/
├── visualdiff/
│   └── annotations/
│       ├── visualdiff_pairs.jsonl      # Main Dataset (Pairs)
│       └── visualdiff_questions.jsonl  # VQA Format (Q&A)
├── microtext/
│   └── annotations/
│       ├── microtext_items.jsonl       # Main Dataset (Items)
│       └── microtext_questions.jsonl   # VQA Format (Q&A)
└── splits/
    ├── visualdiff_train.txt            # Training Set (BBB Schematics)
    └── visualdiff_test.txt             # Test Set (Viola Datasheets)
```

## 3. Usage Guide

### Loading Images
Images are referenced by `doc_id`, `version_id`, and `page_index`.
**Resolution Rule:** `images/{doc_id}__{version_id}/page_{page_index:04d}.png`

```python
import json
import os

ROOT = "Eng_Bench"

with open(os.path.join(ROOT, "visualdiff/annotations/visualdiff_pairs.jsonl"), "r") as f:
    for line in f:
        row = json.loads(line)
        # Construct Image Path
        img_name = f"page_{row['page_index_old']:04d}.png"
        img_path = os.path.join(ROOT, "images", f"{row['doc_id']}__{row['version_id_old']}", img_name)
        
        print(f"Loading {img_path} for change {row['change_id']}")
```

### Splits
To prevent data leakage, we strictly separate by **Document Family**:
*   **Train**: `bbb` (BeagleBone Black Schematics). Same model, different revisions.
*   **Dev**: `viola` V1.0 to V1.1 (Toradex Viola Datasheets).
*   **Test**: `viola` V1.1 to V1.2 plus the held-out ESP32-C5 pin-layout revision family.
*   **Split File Format**: Simple list of visualdiff revision-family IDs or microtext document IDs.

Microtext has a frozen document- and family-disjoint split assignment: train 1,460 rows, dev 848 rows, and test 638 rows. The earlier ISO tolerance seed slice remains outside active gold pending source-rights review. The split is leakage-clean, but train/dev remain pin-label heavy and full P&ID/process-sheet coverage is still low-volume.

## 4. Statistics (v0.9 Silver)

| Dataset Content | Count | Source |
| :--- | :--- | :--- |
| **Visual-Diff Pairs** | **1561** | Human/CVAT-reviewed rows, assisted descriptions with audit metadata, and recovered human-semantic rows; invalid and human-polish failures remain quarantined |
| - Train | 551 | PCB schematic and engineering revision families |
| - Dev | 48 | Held-out revision families |
| - Test | 962 | Held-out revision families |
| **Microtext Items** | **2946** | Reviewed set from text-layer candidates, image-region proposals, SVG extraction, and crop-level review |
| - Train | 1460 | PCB, architectural/civil, mechanical, and process-sheet sources |
| - Dev | 848 | Document- and family-disjoint engineering sources |
| - Test | 638 | Held-out document families across multiple engineering domains |

## 5. From Silver to Gold
To upgrade this dataset to v1.0 (Gold):
1.  **Continue Visualdiff Gold Expansion**: Release-critical dev/test pending-description rows are cleared, and the 98-row human polish pack has been adjudicated. Four low-confidence rows were retained as usable, while 77 no-change rows and 17 bbox-mismatch rows were quarantined. The remaining visualdiff polish debt is 125 train-only pending descriptions. For family breadth, confirm the 18 Wave659 primaries first; their machine-written descriptions cover 18 distinct missing families and reach the 30-family upper bound if accepted. Review a bound backup only when its primary is rejected.
2.  **Source-rights cleanup**: Migrate the nine remaining rights-blocked active documents to reviewed one-for-one replacements. Wave956 retired `wsdot_drainage_ds2` only after its complete seven-row source-atomic contract passed human review and every migration gate. Rebuild the remaining replacement controller against the current active hash before issuing or applying another slice; never retire a source until all of its exact replacement rows are complete and the atomic preview passes.
3.  **Balance microtext beyond the freeze**: The 2,946-row active microtext set is split-clean and includes non-PCB and P&ID/process rows. Current counts are pin-heavy at 1,741/2,946 (59.10%), with eight category floors open. Wave691 provides a review overlay intended to close category floors, but rows still require certification and strict promotion before they count. Final public Gold also needs substantially more full-sheet and test coverage.
4.  **Baseline and package**: Twenty-nine counted baseline reports exist, and all 1,600 current test rows meet the diagnostic coverage floor: microtext rows have 20 predictions and visualdiff rows have 24. Rerun affected artifacts after every Gold migration and all artifacts after every test-set promotion.
5.  **Machine-first certification and recovery**: Apply `docs/MACHINE_CERTIFICATION_POLICY.md` to deterministic train MicroText. The current Wave482 cohort has 8,247 objective rows; 7,098 pin labels already satisfy historical zero-error calibration, while 1,149 non-pin rows need one 300-row calibration. The policy projects 7,947 avoided row-by-row checks. Reuse prior human VisualDiff semantics through the evidence-bound localization/reconciliation lane before assigning repeat review. Keep dev/test evaluation truth, unresolved VisualDiff meaning, semantic engineering tags, and all policy exceptions human-gated.

The resulting JSONL files will be drop-in replacements for these Silver files.

## 6. Known Release Blockers

The v0.95 package remains schema- and image-clean, but this does not imply Gold v2.0 release readiness. The latest formal Gold audit passes 5/9 gates and records nine active rights blockers. Gold remains blocked until the following are resolved:

* The 98 low-confidence dev/test visualdiff rows have been adjudicated; 94 were quarantined and 4 were retained.
* 125 train-only visualdiff pending descriptions remain in the BBB family.
* Global scale is still short: 4,507 active rows, 1,561 visualdiff rows, 1,600 test rows, and 177 distinct paper-ready active source payloads. The three latest NASA tranches add review-only non-pin confirmations, but receive no Gold gate credit before required human semantics and strict promotion. The newest 61-row tranche is split-reserved as 14 dev and 47 test rows, has passed machine visual, evidence, payload, and global-cohort checks, and is packaged for primary engineering review in `Eng_Bench_Wave915_NASA_Primary_Microtext_61_2026-08-29.zip`.
* Revision-family breadth is now 33/30 for Gold v2.0 Global and passes. Remaining VisualDiff work is for scale, agreement, and row quality rather than this breadth gate.
* Independent agreement is 0/185 release-usable. The legacy sample has 148 rights-blocked rows, Reviewer A has 146 schema-complete decisions, and Reviewer B has 0. The 2026-08-28 independent-auditor delivery contains 288 quality-control decisions across 12 reviewers, but these do not count toward the formal 185-row agreement gate until paired with release-ready primary truth and the agreement sample contract. Current release-ready dev/test capacity supports 555 MicroText rows but only 6 VisualDiff rows against the required 95/90 agreement design. Regenerate the final two-reviewer sample only after provenance migration closes the 84-row VisualDiff capacity gap.
* Microtext category coverage is still pin-label heavy; more full P&ID/process-sheet rows are needed before per-split category metrics are stable.
* The v2.0 baseline-count gate passes with 29 counted reports, but external submissions remain desirable adoption evidence.
* Public release packaging still needs final license language, citation metadata, and leaderboard/paper links.

The latest formal report is
`derived/quality/v2_0_gate_audit_2026-08-29-wave960-post-wsdot-ds2-migration.md`.
It passes 5/9 Gold v2.0 Global gates: distinct paper-ready Gold payloads
177/150, release-safe inventory 513/150, VisualDiff families 33/30,
baselines/submissions 29/20, and leaderboard infrastructure 7/7. Total rows
4,507/25,000, test rows 1,600/5,000, agreement 0/185, and complete provenance
179/188 remain open.

## 7. Usage (v0.9 Silver Candidate)

### Installation

```bash
pip install -r requirements.txt
pip install -e . --no-deps
```

### Loading Data (Python)

We provide a simple loader that returns unified row dictionaries with resolved image paths:

```python
from engbench import load_eng_bench

# Load Visual Diff Test Set
rows = load_eng_bench(root=".", task="visualdiff", split="test", verify_images=True)

# Access Data
item = rows[0]
print(f"Question: {item['question']}")
print(f"Images: {item['resolved_images']}")
```

### Running Evaluation

Use the `benchmark_runner.py` tool to evaluate your predictions:

```bash
python tools/benchmark_runner.py --gt eng_bench.jsonl --pred predictions.jsonl --task visualdiff
```

From the parent TraceRAG checkout, the same scoring path is also exposed through
the module CLI:

```bash
python -m tracerag.cli.main eval \
  --dataset Eng_Bench/eng_bench.jsonl \
  --predictions Eng_Bench/results/baselines/tile_zncc_diff_visualdiff_test_predictions.jsonl \
  --task visualdiff \
  --split test \
  --model-name tile_zncc_diff_visualdiff_test \
  --report-json Eng_Bench/results/smoke/tracerag_cli_tile_zncc_visualdiff_test_report.json \
  --report-md Eng_Bench/results/smoke/tracerag_cli_tile_zncc_visualdiff_test_report.md
```

## 8. Current Gold v2.0 Global Checkpoint (2026-09-08)

The current verified release state is 5/9 formal gates PASS. Active Gold remains
4,560 rows, including 1,613 test rows. Distinct paper-ready source payloads
(199/150), release-safe inventory (552/150), VisualDiff families (43/30), counted
baselines (29/20), and leaderboard infrastructure (7/7) pass. Total scale
(4,560/25,000), frozen challenge scale (1,600/5,000), formal agreement (0/185), and complete
provenance (201/210) remain open.

The 12-auditor history now contains 528 validated quality-control observations
and 49 unresolved active-Gold flags. A new paired active-Gold recheck assigns
144 previously unaudited identities to two independent reviewers each (288
answers total), balanced across both tasks and all three splits. It is quality
control only: no answer automatically promotes, removes, or rewrites Gold, and
it does not substitute for the separate formal 185-row agreement contract.

Latest gate report:
`results/health/v2_0_gate_audit_2026-09-08-github-sync-preflight.md`.

## 9. Current Gold v2.0 Global Checkpoint (2026-09-08)

The latest verified checkpoint is **5/9 formal gates PASS**. Active Gold now
contains 4,560 rows after 14 reviewed, independently supported rows passed
exact-image QA, strict preview, snapshots, atomic application, provenance,
deduplication, leakage, and strict validation. No unreviewed row was promoted.

PASS: 199/150 paper-ready active payloads, 552/150 release-safe inventory
documents, 43/30 VisualDiff revision families, 29/20 baselines, and 7/7
leaderboard infrastructure. OPEN: 4,560/25,000 active rows, 1,600/5,000 frozen
test examples, 0/185 formal agreement, and 201/210 paper-ready active source
documents. The nine blocked documents have complete local/hash provenance but
lack explicit redistribution-license evidence.

Known release constraints remain 49 unresolved active-audit flags, 92 tentative
VisualDiff descriptions, and MicroText category imbalance. The 614-row filtered
view under `derived/quality/filtered_evaluation_2026-09-08-wave2047-current/`
is suitable only for internal TraceRAG diagnostics; it is not a Gold release or
an unbiased benchmark. The authoritative gate report is
`derived/quality/v2_0_gate_audit_2026-09-08-wave2052-regression-verified.md`.

The complete repository regression suite passes all 1,321 tests in the full
imaging/PDF runtime. This is infrastructure evidence only and does not convert
unreviewed candidates, unresolved audits, or rights-blocked records into Gold.
Legacy, strict unified, and split-leakage validation pass after the suite, and
all six active release-data hashes remain unchanged.

## 10. Reviewed VisualDiff Finalization Checkpoint (2026-09-08)

Active Gold now contains 4,582 rows: 1,596 VisualDiff and 2,986 MicroText,
split 2,015 train, 932 dev, and 1,635 test. This machine-side pass promoted 22
rows that already had final primary review and hash-complete independent audit
support. It used separate provenance contracts for deterministic uncertainty
removal, English localization of human semantics, and three visually explicit
semantic descriptions. No unreviewed row was promoted.

The current formal state remains **5/9 Gold v2.0 Global gates PASS**. Active
scale, frozen challenge scale, formal agreement, and complete source rights are
still open. Seven ambiguous supported VisualDiff rows, 23 extra pin-label rows,
and one duplicate component-value row remain held rather than inflating Gold.
The full 1,334-test suite and all release validators pass.

The authoritative gate report is
`derived/quality/v2_0_gate_audit_2026-09-08-wave2078-reviewed-visualdiff-current.md`.
The current exact evidence package for 49 active audit flags is
`derived/quality/active_audit_evidence_2026-09-08-wave2079-current49/`.
The 614-row Wave2080 evaluation view remains internal-only and is not the full
publishable benchmark.

## 11. VisualDiff Description Finality Checkpoint (2026-09-08)

Active Gold remains **4,582 rows** and **5/9 Gold v2.0 Global gates PASS**. A
snapshot-backed transaction updated 45 existing VisualDiff descriptions from
tentative templates to evidence-bound final wording. Every affected row already
had human-reviewed semantics; machine acceptance additionally required aligned
boxes, one exact nearby source-text match on the claimed revision, no match on
the opposite revision, paper-ready source provenance, no active audit flag, and
no duplicate or replacement-text ambiguity. No new row was promoted.

The tentative VisualDiff queue is now 47 rows: 17 text removals, 26 text
additions, and four graphical changes, all in test. These remain excluded from
a publishable finality claim. The other open release constraints are 49 active
audit flags, 0/185 formal agreement completion, nine rights-blocked active
documents, insufficient total/test scale, and eight MicroText category floors.

All 1,465 tests and 241 subtests pass. Legacy validation passes at 1,596 VisualDiff and 2,986
MicroText records; strict unified validation covers all 4,582 rows; split and
question leakage both report zero failures. The authoritative report is
`derived/quality/v2_0_gate_audit_2026-09-08-wave2097-finality45-current.md`.

## 12. Unique-Text Relocation Finality Checkpoint (2026-09-08)

Active Gold remains **4,582 rows** and **5/9 Gold v2.0 Global gates PASS**.
Four additional existing VisualDiff answers were finalized without changing
release membership. Each row already had a high-confidence human `edit`
decision; the machine transaction required a unique exact target on both
revision pages, matching font and span shape, binding to the reviewed gap on at
least one side, a material 40-200 pixel displacement, paper-ready provenance,
and no active audit or evidence hold. The final descriptions report only the
deterministic movement direction: down, up and left, left, or right.

The preview selected 4 of the 47 remaining tentative rows and withheld 43.
The snapshot-backed transaction reduced the queue to **43**: 15 removals, 24
additions, and four graphical changes, all in test. Repeated labels, replacement
text, audit-held rows, graphical changes, and unbound source spans remain held.
No unreviewed row was promoted and no human or auditor decision was overwritten.

All **1,476 tests and 241 subtests** pass. Legacy validation passes at 1,596
VisualDiff and 2,986 MicroText records; strict unified validation covers all
4,582 rows; split leakage and question leakage both report zero failures. The
authoritative gate report is
`derived/quality/v2_0_gate_audit_2026-09-08-wave2111-relocation4-current.md`.
The applied transaction is
`derived/quality/active_visualdiff_relocation_correction_transaction_2026-09-08-wave2107-applied.json`,
and the current unified SHA-256 is
`1403a10c0e0ba0f8e7e65f880966811a9b19e764efd1266c6c1a380b10c863e7`.

## 13. Formal Agreement Packet Readiness (2026-09-08)

The agreement sampler now fails closed on both release provenance and active
quality holds. It excludes active audit identities and every VisualDiff answer
still matching a tentative template, including aliases represented by unified,
pair, item, or source-candidate identifiers. Capacity after filtering is 959
MicroText and 99 VisualDiff rows, sufficient for the 95/90 target.

The resulting 185-row packet contains complete evidence, source IDs, source
URLs, and separate blank Reviewer A and Reviewer B checklists. ZIP verification
reports 503 entries, no nested archive, no CRC error, no missing evidence, and
no rights or quality blocker. SHA-256 is
`9707f54ceeea14733fcf08895cb54bb3cb7ee17b78fe7e0f6e4f918c3b2249b2`.
The two reviewers must work independently and may not copy the current 12 x 24
quality-control audit answers.

Gold v2.0 Global remains **5/9 gates PASS** because both checklists are blank.
The Wave2122 readiness report records 185/185 release-ready rows and 0/185
completed reviews. The Wave2124 gate report is authoritative. All 1,476 tests
and 241 subtests, legacy validation, strict unified validation, split leakage,
and question leakage pass.

## 14. Same-Slot Text Replacement Finality (2026-09-08)

A third fail-closed description-finality lane corrected three existing
human-reviewed answers from tentative removal wording to exact same-slot
replacement wording: `ADC0 -> A0`, `GPIO1 -> G1`, and `0.22uF -> 2.2uF`.
Eligibility required a unique related opposite-side span within the reviewed
gap, matching page, font, size, flags, left edge and baseline, clean source
rights, and no audit or evidence hold. All three evidence panels were visually
inspected. The transaction was dry-run, snapshot-backed, hash-pinned, and
applied without rollback; no row or vote was added, removed, or overwritten.

Tentative VisualDiff descriptions fall from 43 to **40**. Neither current human
handoff contains a corrected identity, so no workbook or formal agreement row
became stale. All 1,484 tests and 241 subtests and all release validators pass.
The authoritative gate remains **5/9 PASS** at
`derived/quality/v2_0_gate_audit_2026-09-08-wave2134-replacement3-current.json`.

## 15. BBB Geometry Repair And Auditor v6 (2026-09-08)

The BBB audit found that all 526 active family rows stored the new-page box as
both `bbox_old` and `bbox_new`, although the old and new schematics have
different dimensions and orientation. A source-backed inverse-homography audit
verified valid old-page coordinates for all 526 rows. The snapshot-backed
Wave2192 transaction repaired only the old-region geometry in the pair and
unified files. It added or removed zero rows, changed zero descriptions, kept
all splits unchanged, and passed legacy and strict unified validation.

Because corrected geometry does not certify the existing change descriptions,
all 526 BBB identities are hash-bound in an active semantic hold. Routine audit
selection, evaluation release filtering, and the v2.0 gate now fail closed on
that hold. The pre-repair v5 audit ZIP is therefore stale and must not be sent.

The only current routine audit package is
`derived/human_adjudication/Eng_Bench_Gold_v2_AUDITORS_12x24_GOLD_RECHECK_2026-09-08_v6.zip`,
SHA-256
`8484e0e884d81eb86f4ec56fc9c5f88a24b74a70c44d7453588a465ec2f65ea2`.
It contains 12 flat XLSX files and one Chinese guide, 288 blank assignments over
144 unique active-Gold identities, and 288 embedded evidence images. The cohort
has zero overlap with completed decisions, formal agreement, active audit
flags, nonfinal descriptions, or BBB holds. Native Excel, archive CRC, clean
extraction, formulas, hidden machine sheets, and image counts all pass.

Wave2202 remains **5/9 Gold v2.0 Global gates PASS**: rows 4,582/25,000,
frozen challenge 1,600/5,000, formal agreement 0/185, and provenance 204/213
remain open. Additional release constraints include 338 nonfinal VisualDiff
descriptions, 526 BBB semantic holds, 49 active audit flags, and MicroText
category imbalance. No unreviewed row entered Gold.

## 16. Provenance-Safe Primary Continuation (2026-09-09)

Active Gold remains **5,694 rows** and **5/9 Gold v2.0 Global gates PASS**.
The provenance replacement planner and readiness auditor now treat the
`source_candidate_id` retained by promoted MicroText rows as an active identity.
This prevents a reviewed staging candidate from being counted again after it
has already entered Gold.

The refreshed contract provides exact one-for-one replacement capacity for all
1,430 Gold rows tied to nine blocked active documents, with matching task/split
counts and 1,430 unique evidence fingerprints. Of these replacements, 1,171
accepted/edited human reviews remain reusable and 259 are outstanding: 46
MicroText and 213 VisualDiff. The contract is structurally ready but not ready
for atomic migration; no source-bound row has been retired or inserted.

The current primary delivery is
`outputs/019e1bc5-9ba4-7ac0-b67b-631b6a8208a7/Eng_Bench_Gold_v2_Primary_Next_Action_533_2026-09-09.zip`.
It contains four flat XLSX workbooks totaling 533 actions: 34 continuation,
254 corrected-evidence VisualDiff rereviews, 46 provenance MicroText
replacements, and 199 provenance VisualDiff replacements. Its archive has no
nested ZIP, passes CRC and clean extraction, and all manifest hashes match.

The primary reviewer's VisualDiff warning is now part of the release contract:
red-box displacement or whole-crop offset is not an engineering change, and
OLD/NEW evidence from different locations cannot support a change claim. All
292 returned VisualDiff rows remain held pending corrected evidence or explicit
human resolution. No unreviewed row entered Gold.

The authoritative formal report is
`results/health/v2_0_gate_audit_2026-09-09-wave2307-rights-evidence.json`.

## 17. Active Rights Evidence Coverage (2026-09-09)

The nine blocked active documents have now been reviewed against current
official publisher policies. The evidence set is stored under
`derived/rights_evidence/2026-09-09_wave2306/` and binds every decision to the
exact local source SHA-256. Toradex does not state an open redistribution
license on the relevant hardware-document pages, WSDOT recommends requesting
permission for site content, the City of San Diego policy is restricted to
non-commercial/non-profit use with additional mirroring limits, and Peel
Region asserts copyright unless otherwise stated.

All nine decisions therefore remain `hold`. The new coverage validator reports
9/9 blocked documents and 2,333/2,333 active source references covered, with no
missing or stale decision and no source-hash mismatch. This is a release-safety
PASS, not a provenance-release PASS: active provenance remains 298/307 until
the 1,430 affected Gold rows are atomically replaced by completed reviewed
rows, or document-specific permission is obtained. No Gold file or rights
status was changed.
