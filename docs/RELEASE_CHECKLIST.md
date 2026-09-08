# Eng_Bench Release Checklist

Use this checklist before moving a release label forward.

## v0.95 Packaging-Clean

Required commands:

```powershell
python tools\finalize_all_images.py --root .
python tools\unify_dataset.py
python tools\validate_engbench.py --visualdiff-pairs visualdiff\annotations\visualdiff_pairs.jsonl --visualdiff-questions visualdiff\annotations\visualdiff_questions.jsonl --microtext-items microtext\annotations\microtext_items.jsonl --microtext-questions microtext\annotations\microtext_questions.jsonl
python tools\validate_engbench_v2.py --root . --input eng_bench.jsonl --manifest manifest.jsonl --skip-textlayer
python tools\audit_active_gold_provenance.py --root . --date-label 2026-06-06
python tools\question_diversity_report.py --root . --input eng_bench.jsonl --max-template-share 0.2 --output-json derived\quality\question_diversity_report.json --output-md derived\quality\question_diversity_report.md
python tools\audit_question_leakage.py --root . --input eng_bench.jsonl --output-json derived\quality\question_leakage_audit.json --output-md derived\quality\question_leakage_audit.md
python tools\loader_smoke.py --root . --output results\health\loader_smoke.json
python tools\build_dataset_infos.py --root . --input eng_bench.jsonl --output dataset_infos.json
python tools\build_baseline_table.py --root . --output results\baselines\BASELINE_TABLE.md
python tools\export_public_inputs.py --root . --input eng_bench.jsonl --split dev
python tools\export_public_inputs.py --root . --input eng_bench.jsonl --split test
python tools\build_release_manifest.py --root . --output-json results\health\release_file_manifest.json --output-md results\health\release_file_manifest.md
python tools\benchmark_health_report.py --root . --release-target v0.95
python -m unittest discover -s tests
```

## Current Human Review Queue

The current frozen auditor handoff is
`derived/human_adjudication/Eng_Bench_Gold_v2_AUDITORS_12_SEND_THIS_2026-08-13.zip`,
SHA-256
`35FB5691916BBD5B9E08FE84F45502E598634474026557EB4B15484A78A0A0CD`.
It contains 12 auditor workbooks and one Chinese instruction file, with no
nested ZIP and no primary-reviewer workbook. Do not regenerate or modify that
archive while reviews are outstanding.

The active Gold release has 3,731 rows. The authoritative primary-reviewer
workbook is
`outputs/019e1bc5-9ba4-7ac0-b67b-631b6a8208a7/Eng_Bench_Gold_v2_Primary_2000_Engineering_537_2026-08-20.xlsx`.
It contains 2,000 rows, including 537 engineering-knowledge tasks, and
supersedes the earlier 1,500-row workbook. Do not assign the superseded
workbook or the old 899-row continuation in parallel.

A separate alias-clean reservoir contains 26,427 future rows. Together with
the current primary workbook this is 28,427 unique staged review-capacity rows;
none count as Gold before human acceptance and strict promotion. The exact
post-primary provenance continuation is 399 rows and remains machine-staged at
`derived/review_queues/v2_0_provenance_replacement_post_primary_399_2026-08-20-wave227.jsonl`.
Do not send it until the current primary return is processed or a new handoff
is explicitly requested.
Fifteen finalized VisualDiff edits are held in
`derived/quality/reviewed_gold_promotion_preview_2026-08-13-wave166/` because
all need verified English descriptions and four also need semantic change-type
confirmation. The independent agreement gate remains 0/185 complete.

Returned rows must be staged through the processing command in the packet
index. Do not merge an unreviewed or structurally unverified return into gold.

## Reviewed-Gold Promotion Transaction

Never run `microtext_merge.py` or `visualdiff_merge.py --apply` directly for a
release. A returned human batch follows this fail-closed sequence:

1. Normalize the returned workbook/CSV into reviewed JSONL without changing
   active Gold.
2. Build a read-only promotion preview with the signed split plan.
3. Resolve every row in `promotion_holds_for_human.csv`, import those
   corrections into a new JSONL, and rerun the preview.
4. Record the SHA-256 of a `ready_for_apply=true` preview report.
5. Dry-run the pinned transaction:

```powershell
python tools\apply_reviewed_gold_promotion.py `
  --root . `
  --preview-report derived\quality\<preview>\promotion_preview_report.json `
  --expected-preview-sha256 <exact-preview-report-sha256> `
  --report derived\quality\<preview>\promotion_transaction_dry_run.json
```

6. Inspect the dry-run report. Apply only with a new, empty snapshot directory:

```powershell
python tools\apply_reviewed_gold_promotion.py `
  --root . `
  --preview-report derived\quality\<preview>\promotion_preview_report.json `
  --expected-preview-sha256 <exact-preview-report-sha256> `
  --snapshot-dir derived\snapshots\<date>\<promotion-name> `
  --report derived\quality\<preview>\promotion_transaction_apply.json `
  --apply
```

The transaction refuses stale active hashes, red previews, held rows, modified
artifacts, nonfinal labels, unknown taxonomy, active-row rewrites, split
conflicts, image/schema/leakage failures, and provenance regressions. It
snapshots every active annotation, split, unified, and manifest file and
restores all of them if a post-write check fails.

Pass criteria:

- `missing_image_rows=0`
- no split leakage
- no active rights blockers
- all active rows resolve to source documents and all active source documents are release-ready
- no critical audit failures
- question diversity gate passes
- question leakage audit has `critical_failures=0`
- loader smoke and `dataset_infos.json` are current
- public dev/test input exports contain no `answer` or `evidence` fields
- release file manifest has `missing_count=0`
- docs do not imply final human gold

## v1.0 Gold

Required command:

```powershell
python tools\benchmark_health_report.py --root . --release-target v1.0
```

Pass criteria:

- `total_rows >= 5000`
- `visualdiff_rows >= 2500`
- `microtext_rows >= 3000`
- `source_count >= 35`
- `test_rows >= 1000`
- `test_visualdiff_rows >= 600`
- `test_microtext_rows >= 400`
- `visualdiff_low_confidence_dev_test = 0`
- `visualdiff_release_todo_total = 0`
- `baseline_count >= 5`
- active-gold release provenance passes for every resolved source document
- all v0.95 checks still pass

## Current Gold v2.0 Status

The authoritative active-Gold report is
`derived/quality/v2_0_gate_audit_2026-08-20-wave221.json`. Three of nine
gates pass:

- PASS release-safe inventory: 430/150.
- PASS counted baselines/submissions: 29/20.
- PASS leaderboard infrastructure: 7/7.
- OPEN active rows: 3,731/25,000.
- OPEN unique paper-ready active source payloads: 43/150.
- OPEN VisualDiff revision families: 7/30.
- OPEN hidden/public test examples: 1,230/5,000.
- OPEN independent agreement: 0/185.
- OPEN paper-ready active provenance: 44/54; ten active documents remain
  rights-blocked, with zero missing paths, URLs, manifest rows, recorded hashes,
  hash mismatches, or unresolved active rows.

The current 185-row agreement sample is not final release evidence. Its
readiness audit reports 37/185 release-ready source rows, 148/185 rights-blocked
rows, Reviewer A 146/185 schema-complete, and Reviewer B 0/185. Complete the
provenance replacement migration first, then regenerate a release-safe sample
for two independent reviewers. Agreement reports must be reference-hash linked
to a passing sample-readiness report.

Wave223 release-ready capacity preflight preserves the established 95
MicroText / 90 VisualDiff balance. Current dev/test capacity is 555/95
MicroText but only 6/90 VisualDiff, so the final sample remains intentionally
unbuildable with an 84-row VisualDiff gap. The hardened builder now requires an
explicit provenance report, exact task quotas, and release-ready source fields;
the verifier rejects blocked rows or inconsistent readiness claims. See
`derived/quality/agreement_rebuild_capacity_2026-08-20-wave223.json`.

The separate MicroText release-balance constraint is also OPEN. Active
MicroText is 66.32% `pin_label` against a 45% maximum, and nine category floors
remain open: component-value 0/300, dimension 544/900, equipment 79/300,
instrument 53/300, pipe-line 11/150, process-label 0/150, process-value 8/150,
room 70/150, and tolerance 2/150. This constraint is required for
`v2_0_global_complete` even though it is reported separately from the stable
nine-gate dashboard.

The latest taxonomy-aware staged-capacity report is
`derived/quality/v2_0_staged_capacity_2026-08-20-wave233-taxonomy-v2.json`.
It contains 28,427 unique expansion rows and has a raw all-accepted upper bound
of 32,158 Gold rows, but the canonical balance-compliant upper bound is only
16,586 rows. Therefore, raw staged volume does not close Gold v2.0. Another
4,628 canonical non-pin MicroText rows are still needed in addition to human
review and acceptance. All ten category floors can be covered from strict
staged category capacity, but the pin-share constraint still limits the usable
row total. The Wave232 taxonomy audit separately qualifies 2,442 staged
`component_value` and `process_label` rows and holds 91 rows for invalid text,
missing split assignment, or both. Wave186 supersedes
the Wave184 capacity estimate after holding 26 normalized physical-region
duplicates hidden behind byte-identical source aliases. Historical inputs were
preserved, active Gold was not changed, and the clean successor cohorts have
zero active-alias, staged-alias, near-region, or cross-cohort identity overlap.
Wave187 adds eight complete train-reserved equipment labels from the registered
Commons carbon-black process diagram after repairing clipped multiline legacy
regions; all eight remain human-gated. Wave188 adds 23 complete train-reserved
labels from the public-domain Commons RI Sample ISO P&ID after adding guarded
number-leading line-designator support: eight pipe-line tags and fifteen
equipment or area labels. The standalone pack passes evidence and workbook
verification, and all 23 rows remain human-gated. Wave189 registered an exact
NASA NTRS test-facility report but correctly retained zero rows after all four
proposals failed completeness or label-type review. Wave190 registered a second
NASA NTRS process-system report, selected 19 equipment-schedule pages, and
reduced 1,707 OCR detections to 38 complete, unique, train-reserved equipment
labels. The Wave190 promotion contract has zero fatal issues and requires 38
human decisions. These additions are provenance-clean review candidates only;
they are not Gold.
Wave175 imported four
official DOE/NASA federal documents after Wave174 exhausted the local source
pool. Full machine review retained 11 NASA tolerance-table candidates. Wave176
registered three official EPA-hosted P&ID/process documents and visually
cleared 77 OCR candidates, but contractor, vendor, and embedded-diagram
provenance keeps every one machine-held and outside staged capacity. Wave177
then used the materially different image-only P&ID OCR profile on the two
paper-ready DOE handbook volumes: 7,222 detections and 113 qualified candidates
were reduced by category, deduplication, pixel evidence, and full visual review
to 15 strict-ready train-reserved rows. Wave178 then imported the official
USACE UFGS 23 09 00 HVAC-control DWG archive and deterministically rendered 35
selected control sheets. OCR, cross-cohort deduplication, a two-per-answer
diversity cap, page-pixel evidence, and full four-sheet visual review reduced
818 raw candidates to 273 strict-ready train-reserved rows: 235 instrument
tags, 30 equipment tags, and eight pipe-line tags. Further machine expansion
requires new paper-ready source intake, a materially different detector, or
provenance and rights resolution, not another identical pass over exhausted
sources. Wave179 rejected a byte-identical UFGS sequence archive, then imported
the distinct official Navy-prepared UFGS 13 49 20 RF-shielding PDF and
supplemental DWG. Full 16-page OCR, contact-sheet review, cross-cohort physical
deduplication, pixel-evidence checks, and strict split/provenance gating retained
five train-reserved review rows: three dimensions and two equipment labels.
Wave180 then imported five more distinct official WBDG/UFGS technical PDFs:
a dental utility detail, an airfield-grooving inspection diagram, power
connection layouts, transformer details, and electric-metering sketches. OCR
and full visual review reduced 86 raw candidates to 29 unique, pixel-valid,
strict-ready train-reserved rows from four source documents. The grooving sheet
produced no guarded candidate. An opt-in scaled OCR detector now maps detections
back to original 300-DPI coordinates and completed all six very large
power-layout sheets. No Wave180 row has been promoted to Gold.
Wave181 then imported three hash-distinct official WBDG/UFGS pump, crane-track,
and spall-repair attachments. Seven rendered pages yielded 21 guarded OCR
candidates plus one targeted tolerance recovery. Full visual review,
cross-cohort near-region deduplication, pixel-evidence checks, strict assembly,
and train-only source reservations retained 13 review rows: seven dimensions,
three equipment labels, two component labels, and one complete tolerance.
These rows raise the staged source-payload upper bound from 310 to 313 and
reduce the canonical tolerance floor gap from 137 to 136. No Wave181 row has
been promoted to Gold.
Wave182 then registered four official WBDG/UFGS payloads in three electrical
source families: companion DC recovery-limit figures, 47 overhead-pole detail
sheets, and a single-phase transformer detail set. Forty-four selected pages
produced 115 guarded OCR candidates. Five-sheet crop and context review retained
102 unique, pixel-valid, train-reserved rows: 100 dimensions and two equipment
names. Thirteen title, prose, instruction, note-fragment, and unsupported-
taxonomy detections remain machine-held. The strict promotion contract has zero
fatal rows and requires 102 human decisions; no Wave182 row has been promoted
to Gold.
Wave183 resolved the legacy CorelDRAW SVG rendering failure for the licensed
Wikimedia absorption-chiller process diagram. The tested Chromium compatibility
renderer recovered all 13 embedded text spans at 300 DPI. Complete span
disposition, crop and context review, cross-cohort deduplication, authoritative
pixel-evidence checks, strict assembly, and train-only source reservation
retained seven review rows: four equipment tags and three pipe-line tags. Six
bare numeric cross-references remain machine-held. The strict promotion
contract has zero fatal rows and requires seven human decisions. This source
raises release-safe inventory to 402, staged source-payload capacity to 316,
and reduces the canonical pipe-line floor gap from 119 to 116. No Wave183 row
has been promoted to Gold.
Wave184 then registered the canonical copy of the CC BY-SA 3.0 Commons
`Schema P&ID1` payload and marked the second local document ID as a duplicate
alias. An opt-in P&ID tag detector expanded the accepted ISA-style prefix set
without changing default OCR classification. From 128 OCR detections, guarded
classification retained 22 instrument candidates; full crop and context review
kept 19 readable physical tags and held three duplicate or partial OCR readings.
All 19 pass cross-cohort deduplication, authoritative pixel evidence, strict
assembly, and train-only source reservation. The promotion contract has zero
fatal rows and requires 19 human decisions. Source-payload auditing reports ten
known duplicate groups and zero local hash mismatches. No Wave184 row has been
promoted to Gold.
Wave185 hardened cross-cohort filtering for byte-identical source aliases. The
filter now has an opt-in normalized-region mode driven by the source-payload
audit. A real-data pass over the canonical English pump/tank P&ID held all 23
remaining proposals: ten duplicate active Gold through alternate document IDs,
and thirteen duplicate the canonical future reservoir or Wave170. The source
is now machine-exhausted, staged capacity is unchanged, and no row was promoted
to Gold.
Wave188 then registered the canonical public-domain RI Sample ISO P&ID from its
existing download and render receipts. RapidOCR produced 223 detections; guarded
classification retained 26 proposals, and full crop/context review kept 23
complete unique labels while holding three duplicate or partial readings. The
new number-leading line-designator path is opt-in and regression-tested against
dates and short fragments. All retained rows pass active/staged exact, near,
and payload-alias deduplication, provenance assembly, train reservation,
evidence verification, and the strict promotion contract. The contract has
zero fatal rows and requires 23 human decisions. No Wave188 row has been
promoted to Gold.

Wave191 registered NASA NTRS record 19790025407 after preserving its citation
metadata and original wrapped download. A tested fail-closed helper removed the
436-byte legacy envelope from the complete linearized PDF and recorded both
payload hashes. A 16-page thumbnail audit selected five engineering drawing
pages; 489 OCR detections yielded 53 guarded proposals. Complete crop/context
review retained 28 unique labels and held 25 clipped, duplicate, generic, or
caption rows. The retained 16 equipment, 11 process-label, and one pipe-line
candidate all pass provenance, exact/near/payload-alias deduplication, pixel
evidence, train reservation, and the strict promotion contract. The contract
requires 28 human decisions. No Wave191 row has been promoted to Gold.

## Machine-Certified Train MicroText

The machine-first lane is defined by `docs/MACHINE_CERTIFICATION_POLICY.md`.
It may reduce row-by-row human work only for objective train MicroText.

Before counting any machine-certified row as Gold, require:

- an eligibility report covering provenance, image and bbox integrity,
  deterministic source-text agreement, category-pattern safety, split locks,
  active/staged deduplication, and RapidOCR exact agreement at confidence
  `>=0.98`;
- a frozen category-stratified calibration sample with at least 300 decisions;
- zero `incorrect` or `unclear` calibration decisions;
- a one-sided 95% precision lower bound `>=0.99`;
- certification artifacts and per-row evidence SHA-256 fields;
- `human_reviewed=false`, `certification_method=machine_verified`, and
  `certification_tier=auto_gold_train`;
- a green normal promotion preview and pinned atomic promotion transaction.

Strict v2 validation rejects machine certification for VisualDiff or dev/test.
Eligibility and calibration tooling never modifies active Gold directly.

## Weekly Freeze

Run every Friday or before handing the tree to another worker:

```powershell
python tools\finalize_all_images.py --root .
python tools\unify_dataset.py
python tools\validate_engbench.py --visualdiff-pairs visualdiff\annotations\visualdiff_pairs.jsonl --visualdiff-questions visualdiff\annotations\visualdiff_questions.jsonl --microtext-items microtext\annotations\microtext_items.jsonl --microtext-questions microtext\annotations\microtext_questions.jsonl
python tools\validate_engbench_v2.py --root . --input eng_bench.jsonl --manifest manifest.jsonl --skip-textlayer
python tools\audit_active_gold_provenance.py --root . --date-label 2026-06-06
python tools\audit_microtext_quality.py --root . --output derived\quality\microtext_quality_audit.json
python tools\audit_microtext_splits.py --root . --output derived\quality\microtext_split_audit.json
python tools\audit_visualdiff_quality.py --root . --output derived\quality\visualdiff_quality_audit.json
python tools\question_diversity_report.py --root . --input eng_bench.jsonl --max-template-share 0.2 --output-json derived\quality\question_diversity_report.json --output-md derived\quality\question_diversity_report.md
python tools\audit_question_leakage.py --root . --input eng_bench.jsonl --output-json derived\quality\question_leakage_audit.json --output-md derived\quality\question_leakage_audit.md
python tools\loader_smoke.py --root . --output results\health\loader_smoke.json
python tools\build_dataset_infos.py --root . --input eng_bench.jsonl --output dataset_infos.json
python tools\build_baseline_table.py --root . --output results\baselines\BASELINE_TABLE.md
python tools\export_public_inputs.py --root . --input eng_bench.jsonl --split dev
python tools\export_public_inputs.py --root . --input eng_bench.jsonl --split test
python tools\build_release_manifest.py --root . --output-json results\health\release_file_manifest.json --output-md results\health\release_file_manifest.md
python splits\leakage_check.py --root .
python tools\benchmark_health_report.py --root . --release-target v1.0
python -m unittest discover -s tests
```

## Latest Gold v2.0 Machine Evidence

As of the verified Wave233 pass on 2026-08-20, active Gold remains 3,731 rows
and 3/9 formal Gold v2.0 gates pass. The current 2,000-row primary workbook
contains 1,038 of the 1,437 exact provenance replacements. The remaining 399
are machine-staged as a non-overlapping continuation: 204 MicroText rows and
195 VisualDiff rows, with no mandatory engineering rewrites left. All 337
mandatory VisualDiff rewrites are already in the current primary workbook.

The alias-clean capacity ledger contains 2,000 current plus 26,427 future rows,
for 28,427 unique staged candidates. If all were independently accepted and
passed strict promotion gates, the upper bound would be 32,158 rows, 346 unique
source payloads, 58 VisualDiff families, and 9,287 explicit test rows. This is
capacity evidence, not release data. The category-balanced canonical upper
bound is 16,586 rows, so 4,628 more canonical non-pin MicroText rows must still
be mined and reviewed. The independent agreement gate remains open and cannot
be credited from staged capacity.

The provenance-migration audit now verifies the returned human evidence against
the frozen candidate metadata. The read-only migration preview refuses partial,
misaligned, shared-reference, or otherwise unsafe replacements and never edits
active Gold. Before promotion, require complete human decisions, strict return
preflight, a ready atomic migration preview, source-rights filtering,
exact/near and payload-alias deduplication, locked split application,
post-merge leakage and strict-v2 validation, and frozen-release hash recording.

Use these reports as the current evidence set:

- `results/health/v2_0_gate_audit_2026-08-20-wave233-taxonomy-v2.json`
- `derived/quality/active_gold_provenance_audit_2026-08-20-wave233-taxonomy-v2.json`
- `derived/quality/v2_0_staged_capacity_2026-08-20-wave233-taxonomy-v2.json`
- `derived/quality/v2_0_taxonomy_v2_readiness_2026-08-20-wave232.json`
- `derived/quality/v2_0_canonical_future_capacity_2026-08-20-wave228-primary2000.json`
- `derived/quality/primary_intern_catchup_payload_2000_engineering_537_2026-08-20-wave225.json`
- `derived/quality/agreement_sample_readiness_2026-08-20-wave221.json`
- `derived/quality/agreement_rebuild_capacity_2026-08-20-wave223.json`
- `derived/quality/v2_0_provenance_replacement_post_primary_2026-08-20-wave227.json`
- `derived/quality/v2_0_provenance_migration_readiness_2026-08-20-wave226.json`
- `derived/quality/v2_0_provenance_migration_preview_2026-08-20-wave226/migration_preview_report.json`

### 2026-08-21 Machine-First Status

Wave330 audited all 28,883 current and future staged decisions under machine
certification policy 1.0. Independent RapidOCR recognition was run or reused
from a SHA-linked cache for every structurally eligible row. The result is
6,991 train MicroText rows pending calibration: 769 balance-closing non-pin
rows and 6,222 pin labels deferred by the 45% balance policy. Another 21,883
rows remain human-required and nine rows are held for missing split-reservation
IDs. The one 300-row calibration replaces 6,991 row-by-row decisions, avoiding
6,691 routine human decisions if it passes. It includes 300 crops, 300 context
images, an HTML index, a single CSV, and Chinese instructions; the verified ZIP
has no nested archives and SHA-256
`08e621ffce8d231cf7df2a2b97e4b172800ef157d66ec09e432ccf38d343c9ce`.

The calibration is not complete, no certified rows were emitted, and active
Gold remains unchanged. The authoritative gate report is
`derived/quality/v2_0_gate_audit_2026-08-21-wave332-machine-first.json`.
