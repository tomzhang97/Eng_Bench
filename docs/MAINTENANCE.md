# Eng_Bench Maintenance Policy

This policy keeps Eng_Bench versioned, auditable, and fair for leaderboard use.

## Version Levels

| Level | Meaning | Required evidence |
| --- | --- | --- |
| v0.95 Packaging-Clean | Local package and validators are clean, but scale/gold gates remain open. | validators, leakage checks, health report, release manifest |
| v1.0 Gold | First credible human-gold benchmark release. | human-adjudicated dev/test, enough rows/sources/test volume, baseline table |
| v1.5 Public | Stable public specialty benchmark. | larger source coverage, public packaging, stronger test volume, external-facing docs |
| v2.0 Global | Large-scale benchmark with challenge infrastructure. | 25k+ rows, 150+ sources, hidden/public test split, leaderboard, paper-ready provenance |

## Label Corrections

Use this flow for any reported label problem:

1. Reproduce the issue from the released row ID and image path.
2. Classify the issue as `typo`, `bbox`, `wrong_answer`, `rights`, `duplicate`, or `ambiguous`.
3. Decide whether the row is corrected, quarantined, or retained.
4. Record the decision in the changelog and release notes.
5. If hidden-test labels change, issue a patch version and rescore future submissions only under that patch version.

Do not silently mutate hidden labels after leaderboard reports have been produced.

## Adding Rows

New rows enter through the review queue, not directly into gold JSONL:

1. Import source and capture source URL, local path, rights posture, and hashes.
2. Render pages and extract text layers or image regions.
3. Mine candidates and export review packs.
4. Human reviewer marks accepted, edited, rejected, or needs_full_page.
5. Build a read-only reviewed-Gold preview and resolve every hold.
6. Promote only through `tools/apply_reviewed_gold_promotion.py`, first as a
   dry run and then with an exact preview SHA-256 plus a new snapshot directory.
7. Rebuild public inputs, manifests, and health reports.

Rows with unclear rights, missing evidence, unresolved split leakage, or ambiguous answers stay outside active gold.

## Active-Gold Provenance Audit

Run the strict source-provenance audit after changing active annotations,
`manifest.jsonl`, or `SOURCE_INVENTORY.csv`:

```powershell
python tools\audit_active_gold_provenance.py --root . --date-label 2026-06-05
```

This writes:

- `derived/quality/active_gold_provenance_audit_2026-06-05.{json,md}`
- `docs/ACTIVE_GOLD_PROVENANCE.csv`

The audit resolves every active microtext row to its source document and every
active visualdiff row to both revision documents. Release-ready provenance
requires an inventory row, manifest row, existing local source file, source
URL, and non-blocked rights status. Paper-ready provenance additionally
requires the recorded manifest SHA-256 to match the local file. The v1.0
health gate and v1.5 gate require release-ready provenance; the v2.0 gate
requires paper-ready provenance.

Release-safe inventory counting is intentionally stricter than "has a public
URL." Rows whose status contains `unknown`, `restricted`, `proprietary`,
`reference_only`, `not_promoted`, `rights_uncertain`, `release_review_needed`,
or `internal_only` are excluded from v1.5/v2.0 source-volume gates. A
reference-only or rights-held source can remain in the inventory and manifest
for lineage without inflating public-release readiness.

## Provenance Replacement Migration

Rights-blocked active rows may be replaced only by the complete, frozen,
one-for-one provenance replacement contract. Process the returned primary
workbook into reviewed JSONL first, then run the read-only readiness audit:

For the authoritative 2,000-row primary workbook, use the fail-closed control
entry point rather than invoking the individual processors manually:

```powershell
python tools\control_primary_intern_return.py `
  --root . `
  --workbook <returned-primary-workbook.xlsx> `
  --payload derived\quality\primary_intern_catchup_payload_2000_engineering_537_2026-08-20-wave225.json `
  --output-dir derived\quality\primary_return_control_<date> `
  --date-label <date> `
  --require-primary-complete
```

This command snapshots the returned workbook, verifies all 2,000 identities and
537 engineering tasks, separates ordinary additions from exact provenance
replacements, runs the standard promotion audit, audits the complete 1,437-row
replacement contract, and builds read-only previews only when their inputs are
complete. It never applies Gold. When the later 399-row provenance continuation
has also been reviewed, add each processed continuation JSONL with
`--additional-provenance-reviewed <path>`. Do not use
`--require-all-previews-ready` until both the primary return and the complete
replacement contract are present.

```powershell
python tools\audit_provenance_migration_readiness.py `
  --root . `
  --plan derived\quality\v2_0_provenance_replacement_plan_2026-08-13-wave164-provenance-continuity.json `
  --affected derived\quality\v2_0_provenance_affected_gold_2026-08-13-wave164.jsonl `
  --candidates derived\review_queues\v2_0_provenance_replacement_candidates_2026-08-13-wave164.jsonl `
  --reviewed <processed_return_1.jsonl> <processed_return_2.jsonl> `
  --date-label <date> `
  --output-json derived\quality\v2_0_provenance_migration_readiness_<date>.json `
  --output-md derived\quality\v2_0_provenance_migration_readiness_<date>.md `
  --require-ready
```

The readiness audit requires every contract row exactly once and checks that
reviewed task, source, split, identity, evidence path, bbox, provenance, and
rights metadata still match the frozen candidate. A human decision alone is
not sufficient when the returned evidence metadata changed.

When readiness passes, build a read-only atomic preview:

```powershell
python tools\preview_provenance_replacement_migration.py `
  --root . `
  --plan derived\quality\v2_0_provenance_replacement_plan_2026-08-13-wave164-provenance-continuity.json `
  --affected derived\quality\v2_0_provenance_affected_gold_2026-08-13-wave164.jsonl `
  --candidates derived\review_queues\v2_0_provenance_replacement_candidates_2026-08-13-wave164.jsonl `
  --reviewed <processed_return_1.jsonl> <processed_return_2.jsonl> `
  --split-plan derived\quality\v2_0_staged_split_plan_2026-08-17-wave214.json `
  --output-dir derived\quality\v2_0_provenance_migration_preview_<date> `
  --date-label <date> `
  --require-ready
```

The preview removes the exact affected underlying rows only inside the preview,
adds their reviewed replacements, and reruns annotation, split leakage,
question leakage, strict v2, source-provenance, and blocked-source checks. It
also verifies row/task/split preservation and hashes active files before and
after. `ready_for_atomic_apply=true` authorizes a separately reviewed apply
transaction; the preview command itself never changes active Gold.

## Independent Agreement Audit

Do not rebuild the final packet until provenance migration is complete. First
audit whether the release-ready 95/90 dev/test sample is feasible:

```powershell
python tools\audit_agreement_rebuild_capacity.py `
  --root . `
  --provenance derived\quality\active_gold_provenance_audit_<date>.json `
  --microtext-rows 95 `
  --visualdiff-rows 90 `
  --output-json derived\quality\agreement_rebuild_capacity_<date>.json `
  --output-md derived\quality\agreement_rebuild_capacity_<date>.md `
  --strict
```

Only when `exact_sample_feasible=true`, build and verify the current
two-reviewer dev/test audit packet:

```powershell
python tools\build_agreement_audit_packet.py `
  --root . `
  --input eng_bench.jsonl `
  --output-dir derived\human_adjudication\2026-06-05_agreement_audit `
  --zip-output derived\human_adjudication\Eng_Bench_agreement_audit_2026-06-05.zip `
  --sample-size 185 `
  --microtext-rows 95 `
  --visualdiff-rows 90 `
  --provenance-report derived\quality\active_gold_provenance_audit_<date>.json `
  --require-release-ready `
  --min-per-stratum 1 `
  --seed engbench-agreement-v2-release

python tools\verify_agreement_audit_packet.py `
  --packet-dir derived\human_adjudication\2026-06-05_agreement_audit `
  --output-json derived\quality\agreement_audit_packet_folder_verification_2026-06-05.json `
  --output-md derived\quality\agreement_audit_packet_folder_verification_2026-06-05.md

python tools\verify_agreement_audit_packet.py `
  --zip derived\human_adjudication\Eng_Bench_agreement_audit_2026-06-05.zip `
  --output-json derived\quality\agreement_audit_packet_zip_verification_2026-06-05.json `
  --output-md derived\quality\agreement_audit_packet_zip_verification_2026-06-05.md
```

Give Reviewer A and Reviewer B separate copies. They must work independently,
fill only their own checklist, and avoid discussion until both CSVs are
returned. The packet contains reference answers because reviewers judge
existing active-gold correctness; it also contains source document IDs, URLs,
and statuses for the rights-concern decision.

After both CSVs return, compute metrics:

```powershell
python tools\agreement_audit.py `
  --reference derived\human_adjudication\2026-06-05_agreement_audit\sample_reference.csv `
  --reviewer-a derived\human_adjudication\2026-06-05_agreement_audit\reviewer_a_checklist.csv `
  --reviewer-b derived\human_adjudication\2026-06-05_agreement_audit\reviewer_b_checklist.csv `
  --output-json results\annotation\agreement_report.json `
  --output-md results\annotation\agreement_report.md
```

Do not merge either reviewer sheet into gold. Adjudicate disagreements in a
separate maintainer pass, record every gold correction or quarantine decision,
then rebuild and validate the release.

The v1.5 and v2.0 gate audits read the canonical
`results/annotation/agreement_report.json` when present, otherwise the latest
dated pending report. They pass the human-agreement gate only when every
reference row has two complete reviews and the agreement thresholds pass.

## Expansion Planning

Regenerate the source-to-gold expansion plan before starting a new import or human-review tranche:

```powershell
python tools\build_gold_expansion_plan.py --root . --date-label 2026-06-06 --limit 40
```

This writes:

- `derived/quality/gold_expansion_plan_2026-06-06.{json,md}`
- `docs/GOLD_EXPANSION_HUMAN_QUEUE.csv`
- `docs/GOLD_EXPANSION_MACHINE_QUEUE.csv`

Use the human queue only for release-safe, fresh, unpacketed rows. The planner
subtracts active packet rows, rows already resolved by reviewed files or active
gold, and rights-held rows. A header-only `GOLD_EXPANSION_HUMAN_QUEUE.csv`
means there is no new raw queue to assign; continue the verified packet index
instead. Use the machine queue to choose release-safe sources for
render/text-layer/candidate generation. When
`derived/quality/human_packet_index_<date>.json` exists, the Markdown/JSON
report also includes a current human-packet forecast: ready packet counts,
inspectable manifest rows, and a conservative upper-bound projection if every
packet row were accepted. The report uses strict active-source provenance and
the same release-safe rights blockers as the v1.5/v2.0 gates. It is read-only
and must not be treated as gold promotion.

Regenerate the source conversion readiness audit when deciding what machine work can proceed without human labels:

```powershell
python tools\sync_source_inventory_exhaustion.py --root .
python tools\sync_source_inventory_exhaustion.py --root . --apply `
  --report-json derived\quality\source_inventory_exhaustion_sync_<date>.json
python tools\sync_source_inventory_exhaustion.py --root .
python tools\audit_source_conversion_readiness.py --root . --date-label 2026-06-06
```

Run the check-only sync before and after applying an exhaustion-ledger update.
Check mode exits nonzero when inventory fields drift from
`SOURCE_CONVERSION_EXHAUSTION.csv`; apply mode updates only
`conversion_exhaustion_status`, `conversion_exhaustion_evidence`, `next_step`,
and `priority_score`. It fails closed for duplicate ledger decisions, unknown
statuses, or ledger documents missing from inventory. Multiple inventory asset
rows may intentionally share one `doc_id`; each receives the same document-
level state. Snapshot `SOURCE_INVENTORY.csv` before applying the sync.

This writes:

- `derived/quality/source_conversion_readiness_2026-06-06.{json,md}`
- `docs/SOURCE_CONVERSION_LOCAL_QUEUE.csv`
- `docs/SOURCE_CONVERSION_CANDIDATE_QUEUE.csv`
- `docs/SOURCE_INVENTORY_REPAIR_QUEUE.csv`

Use `await_human_return` rows to avoid duplicate packaging; those rows are already represented in the active human packet index for that date. Use `human_review` and `human_review_partial_packeted` rows for intern/adjudicator scheduling, paying attention to `fresh_open_review_rows`; rows marked `reviewed_sibling_or_active_gold` are stale open queue entries and should not be re-sent. Use `mine_candidates_and_export_review`, `ocr_or_manual_region_proposal`, `extract_textlayer_or_ocr`, and `import_render_extract` rows for machine-side work. Treat `machine_exhausted`, `machine_exhausted_no_candidate`, and `machine_exhausted_after_reviewed_pass` as terminal under the current assets and detector; candidate-level `machine_exhausted` means all linked sources are exhausted but their leaf outcomes are mixed. The candidate queue uses only explicit candidate-to-local lineage from intake-log manifest IDs or exact local paths, source-import manifests, unambiguous inventory notes, and annotation `source_candidate_id` fields. Do not infer lineage from shared URLs or name similarity, and do not treat browser validation alone as a completed import. Review `SOURCE_INVENTORY_REPAIR_QUEUE.csv` before adding missing inventory rows: confirm the exact local path, rights posture, and attribution/license metadata first. The audit is read-only and does not apply checklist decisions. The queue CSVs are full queues, not top-N excerpts.

When building a follow-on packet from open queues that may partially overlap active packets, filter by the active packet index instead of using a hard overlap root:

```powershell
python tools\build_next_review_batch.py `
  --root . `
  --date-label 2026-06-04 `
  --batch-dir derived\human_adjudication\2026-06-04_v2_0_unpacketed_review_batch `
  --zip-output derived\human_adjudication\Eng_Bench_v2_0_unpacketed_review_batch_2026-06-04.zip `
  --exclude-active-packet-index 2026-06-03 `
  --queue microtext/annotations/microtext_review_v1_priority_2026-05-18.jsonl=microtext_v1_priority_unpacketed_2026-06-04 `
  --queue microtext/annotations/microtext_review_pixhawk_debug_adapter_2026-05-22.jsonl=microtext_pixhawk_debug_adapter_2026-06-04 `
  --queue microtext/annotations/microtext_review_pixhawk_fmuv3_region_proposals_2026-06-04.jsonl=microtext_pixhawk_fmuv3_region_proposals_2026-06-04 `
  --queue microtext/annotations/microtext_review_pixhawk_fmuv2_region_proposals_2026-06-04.jsonl=microtext_pixhawk_fmuv2_region_proposals_2026-06-04 `
  --queue microtext/annotations/microtext_review_wikimedia_akm_region_proposals_2026-06-04.jsonl=microtext_wikimedia_akm_region_proposals_2026-06-04

python tools\verify_review_batch_package.py `
  --zip derived\human_adjudication\Eng_Bench_v2_0_unpacketed_review_batch_2026-06-04.zip `
  --output-json derived\quality\unpacketed_review_batch_zip_verification_2026-06-04.json `
  --output-md derived\quality\unpacketed_review_batch_zip_verification_2026-06-04.md

python tools\process_next_review_batch_return.py `
  --root . `
  --batch-root derived\human_adjudication\2026-06-04_v2_0_unpacketed_review_batch `
  --output-dir derived\human_adjudication\processed_returns\2026-06-04_unpacketed_review_batch_status

python tools\build_human_packet_index.py --root . --date-label 2026-06-04 --base-date-label 2026-06-03
```

Use `--strict` on `process_next_review_batch_return.py` only after humans return completed CSVs; unfilled smoke checks intentionally exit cleanly without `--strict`.

The 2026-06-04 unpacketed packet currently contains `254` rows across five packs: `126` v1-priority microtext rows, `29` Pixhawk debug-adapter rows, `40` Pixhawk FMUv3 region proposals, `44` Pixhawk FMUv2 region proposals, and `15` Wikimedia AKM process-diagram label proposals. Pixhawk source inventory rows are release-candidate only with CC BY-SA 3.0/open-hardware evidence captured from the local `pixhawk-hardware-master.zip`; attribution and share-alike metadata still need release packaging before publication. The FMUv2/FMUv3/AKM proposal rows are OCR/manual-entry rows: humans must use `edited`, fill `corrected_text`, and set `corrected_category`. `tolerances_table_iso` remains on rights/provenance hold and should not be packeted until cleared.

The 2026-06-05 follow-on packet adds `100` review-only pin-label rows from the official Adafruit ESP32 Feather V2 pinout. The source is candidate `pcb_019`, and its local README/license require Limor Fried/Ladyada attribution plus CC BY-SA 3.0 share-alike metadata. Human reviewers should use `accepted` only when the proposed label is the intended visible label, `edited` when a multi-token signal or extraction needs correction, `rejected` for legend/header text, and `needs_full_page` when neighboring labels make the crop ambiguous.

```powershell
python tools\mine_microtext_candidates.py `
  --root . `
  --doc-ids adafruit_esp32_feather_v2_pinout `
  --categories pin_label `
  --max-per-doc-category 500 `
  --output microtext\annotations\microtext_candidates_adafruit_esp32_feather_v2_pinout_2026-06-05.jsonl

python tools\microtext_review.py `
  --root . `
  --candidates microtext\annotations\microtext_candidates_adafruit_esp32_feather_v2_pinout_2026-06-05.jsonl `
  --output microtext\annotations\microtext_review_adafruit_esp32_feather_v2_pinout_2026-06-05.jsonl `
  --limit 100 `
  --max-per-category 100 `
  --categories pin_label `
  --doc-ids adafruit_esp32_feather_v2_pinout `
  --source-candidate-id pcb_019

python tools\build_next_review_batch.py `
  --root . `
  --date-label 2026-06-05 `
  --batch-dir derived\human_adjudication\2026-06-05_v2_0_unpacketed_review_batch `
  --zip-output derived\human_adjudication\Eng_Bench_v2_0_unpacketed_review_batch_2026-06-05.zip `
  --exclude-active-packet-index 2026-06-04 `
  --queue microtext/annotations/microtext_review_adafruit_esp32_feather_v2_pinout_2026-06-05.jsonl=microtext_adafruit_esp32_feather_v2_pinout_2026-06-05 `
  --queue visualdiff/annotations/visualdiff_review_pixhawk_fmuv2_4_5_to_4_6_2026-06-05.jsonl=visualdiff_pixhawk_fmuv2_4_5_to_4_6_2026-06-05 `
  --queue visualdiff/annotations/visualdiff_review_pixhawk_fmuv1_1_7_to_1_7_1_2026-06-05.jsonl=visualdiff_pixhawk_fmuv1_1_7_to_1_7_1_2026-06-05 `
  --queue visualdiff/annotations/visualdiff_review_pixhawk_fmuv2_4_3_to_4_5_2026-06-05.jsonl=visualdiff_pixhawk_fmuv2_4_3_to_4_5_2026-06-05 `
  --pad-px 32

python tools\build_human_packet_index.py `
  --root . `
  --date-label 2026-06-05 `
  --base-date-label 2026-06-03 `
  --carry-forward-date-label 2026-06-04
```

The 2026-06-06 follow-on packet adds `122` exact-source-text rows from the
Apache-2.0 Antmicro Jetson Orin Baseboard schematic. It contains `101`
semantic schematic signal labels, spans nine pages, and excludes generic
component references such as `R12`, `C4`, `U3`, and `J5`. For dense
schematics, use `--max-per-page` to keep one page from dominating a review
batch and `--exclude-reference-designators` to avoid low-value reference-only
labels:

```powershell
python tools\microtext_review.py `
  --root . `
  --candidates microtext\annotations\microtext_candidates_jetson_orin_baseboard_2026-06-06.jsonl `
  --output microtext\annotations\microtext_review_jetson_orin_baseboard_2026-06-06.jsonl `
  --limit 150 `
  --max-per-category 150 `
  --max-per-text 1 `
  --max-per-page 15 `
  --exact-text-only `
  --exclude-reference-designators `
  --categories pin_label,dimension_value,equipment_tag `
  --doc-ids jetson_orin_baseboard `
  --source-candidate-id ds_028

python tools\build_human_packet_index.py `
  --root . `
  --date-label 2026-06-06 `
  --base-date-label 2026-06-03 `
  --carry-forward-date-label 2026-06-04 `
  --carry-forward-date-label 2026-06-05 `
  --carry-forward-date-label 2026-06-05-pixhawk
```

The `2026-06-06-refinery` follow-on packet adds `25` exact-source-text
equipment-unit rows from the Wikimedia Commons `RefineryFlow.svg`,
`NatGasProcessing.svg`, and `AmineTreating.svg` diagrams. The miner only
recognizes tested complete process-unit headings; split headings and
bullet-list process descriptions remain excluded. Rebuild and verify the
combined packet with:

```powershell
python tools\build_next_review_batch.py `
  --root . `
  --date-label 2026-06-06-refinery `
  --batch-dir derived\human_adjudication\2026-06-06-refinery_v2_0_unpacketed_review_batch `
  --zip-output derived\human_adjudication\Eng_Bench_v2_0_unpacketed_review_batch_2026-06-06-refinery.zip `
  --exclude-active-packet-index 2026-06-06 `
  --pad-px 48 `
  --queue microtext\annotations\microtext_review_wikimedia_refinery_flow_2026-06-06.jsonl=microtext_wikimedia_refinery_flow_2026-06-06 `
  --queue microtext\annotations\microtext_review_wikimedia_nat_gas_processing_2026-06-06.jsonl=microtext_wikimedia_nat_gas_processing_2026-06-06 `
  --queue microtext\annotations\microtext_review_wikimedia_amine_treating_2026-06-06.jsonl=microtext_wikimedia_amine_treating_2026-06-06

python tools\build_human_packet_index.py `
  --root . `
  --date-label 2026-06-06-refinery `
  --base-date-label 2026-06-03 `
  --carry-forward-date-label 2026-06-04 `
  --carry-forward-date-label 2026-06-05 `
  --carry-forward-date-label 2026-06-05-pixhawk `
  --carry-forward-date-label 2026-06-06
```

`wikimedia_absorption_chiller_scheme` was recovered in Wave183 with
`tools/render_legacy_svg.py`. The compatibility path decodes the declared SVG
encoding, inlines text styles, removes obsolete embedded fonts, renders at an
exact DPI through local Chromium, and writes nondegenerate text boxes. The
importer automatically uses this path when a legacy SVG render is mostly black
or has too few usable text spans. The audited source now has 13 inspectable
spans: seven equipment or pipe-line labels are train-reserved review rows and
six bare numeric cross-references are machine-held. Do not promote any of them
without human review and the strict promotion transaction.

For receipt-backed sources that were imported before their manifest row was
complete, use `tools/register_receipt_sources.py --refresh-existing`. The mode
only refreshes a hash-matched existing document and does not create a second
manifest row. Exact receipt `source_url` values take precedence over category
pages, and supported Commons API licenses are normalized to release-safe public
statuses. Wave184 used this path for `pid_021_sch_ma_p_id1`; its byte-identical
`wikimedia_schema_pid1` document ID remains a duplicate alias and must not be
counted as a second source.

`tools/propose_microtext_ocr_regions.py --include-pid-labels` enables the tested
P&ID instrument-prefix superset, including compound forms such as `LAHH`, `HMY`,
and `HMS`. Keep this opt-in: the default classifier must not reinterpret general
engineering text as instrument tags. Inspect both crop and full-page context,
hold partial or duplicate OCR alternatives, and run cross-cohort deduplication
before any candidate enters strict assembly.

When an input may reuse a byte-identical source under another document ID, add
payload-alias suppression to the cross-cohort filter:

```powershell
python tools\filter_microtext_review_against_cohorts.py `
  --root . `
  --input <candidate_jsonl> `
  --exclude microtext\annotations\microtext_items.jsonl `
  --exclude <canonical_staged_jsonl> `
  --exclude-near-regions `
  --payload-alias-report derived\quality\<source_payload_audit>.json `
  --exclude-payload-alias-regions `
  --kept-output <kept_jsonl> `
  --held-output <held_jsonl> `
  --output-json <audit_json> `
  --output-md <audit_md>
```

This opt-in mode maps hash-equivalent document IDs to one canonical payload and
normalizes boxes by page dimensions before exact and near-region comparison.
It prevents differently sized renders of the same page from entering separate
review cohorts. Keep the payload audit current and include both active Gold and
all canonical staged cohorts as exclusions.

Use `--carry-forward-date-label` once for each still-active optional follow-on packet. This prevents a newer dated packet index from silently dropping an earlier unreturned follow-on packet.

For image-like sources with readable labels but no regex-mineable text layer, build a proposal pack instead of promoting anything directly:

```powershell
python tools\propose_microtext_regions.py `
  --root . `
  --doc-ids <comma_separated_doc_ids> `
  --pages all `
  --limit-per-page 30 `
  --total-limit 50 `
  --category unknown_microtext `
  --output microtext\annotations\<review_jsonl>.jsonl

python tools\export_review_packs.py microtext `
  --root . `
  --input microtext\annotations\<review_jsonl>.jsonl `
  --output-dir derived\review_packs\<pack_name> `
  --pad-px 32

python tools\microtext_review_checklist.py export `
  --root . `
  --input derived\review_packs\<pack_name>\manifest.jsonl `
  --output derived\review_packs\<pack_name>\<checklist_name>.csv
```

These proposal rows usually have blank `proposed_text`; humans must use `edited`, fill `corrected_text`, and set `corrected_category` before any merge audit.

Before promoting old reviewed files, confirm there is not a duplicate/already-active backlog:

```powershell
python tools\audit_unmerged_reviewed_rows.py --root .
```

Rows reported as unmerged still require maintainer inspection before merge. Rows already active by candidate ID or region should not be promoted again.

To rebuild the current intern handoff folder and zip from the live reports plus the 5/25 packet:

```powershell
python tools\build_parallel_human_handoff.py `
  --root . `
  --date-label 2026-06-03 `
  --limit 40 `
  --handoff-dir derived\human_adjudication\2026-06-03_v2_0_parallel_handoff `
  --zip-output derived\human_adjudication\Eng_Bench_v2_0_parallel_human_handoff_2026-06-03.zip `
  --extra-review-pack derived\review_packs\microtext_region_proposals_2026-06-03
```

This regenerates the gold expansion plan, unmerged-reviewed audit, source-conversion readiness audit, ranked queue CSVs, dated handoff folder, and dated ZIP. The script verifies required archive entries and does not mutate active gold annotations.

The handoff also includes `review_queue_inventory_<date>.md`, which classifies
raw open rows as actionable fresh, active-packet, stale/resolved, or
rights-blocked and summarizes missing evidence references. Only actionable
release-safe fresh rows belong in a new human assignment. Extra
machine-generated packs passed with `--extra-review-pack` are copied under
`03_new_machine_review_packs/`; microtext packs include `crops/`, `pages/`,
`index.html`, `manifest.jsonl`, and checklist CSVs when exported.

Before sending a handoff zip, run the stronger package verifier:

```powershell
python tools\verify_handoff_package.py `
  --zip derived\human_adjudication\Eng_Bench_v2_0_parallel_human_handoff_2026-06-03.zip `
  --output-json derived\quality\handoff_verification_2026-06-03.json `
  --output-md derived\quality\handoff_verification_2026-06-03.md
```

This checks required handoff files, extra review-pack manifests, localized checklist crop/page paths, crop image counts, and full-page evidence counts.

## Building A Follow-On Human Review Batch

When the current handoff has been sent, package additional evidence-complete open queues as a separate follow-on batch:

```powershell
python tools\build_next_review_batch.py `
  --root . `
  --date-label 2026-06-03 `
  --batch-dir derived\human_adjudication\2026-06-03_v2_0_next_review_batch `
  --zip-output derived\human_adjudication\Eng_Bench_v2_0_next_review_batch_2026-06-03.zip `
  --pad-px 32 `
  --exclude-root derived\human_adjudication\2026-06-03_v2_0_parallel_handoff
```

The default batch uses the highest-yield evidence-complete microtext queues that are not part of the current 5/25 packet. The `--exclude-root` preflight scans existing packet manifests and blocks duplicate `candidate_id` rows before rebuilding the folder or ZIP. By default, the builder also excludes rows already present in sibling `*_reviewed.jsonl` files and rows whose candidate IDs are already active microtext gold. The builder regenerates crop/full-page evidence, localized checklist CSVs, `NEXT_REVIEW_BATCH_MANIFEST.csv`, `HUMAN_REVIEW_STEPS.md`, and a ZIP. This is review-only; do not merge rows until the completed CSVs are returned and staged through the merge/audit tools.

For a diversity-first civil/architectural packet, use explicit queues and exclude both current packets:

```powershell
python tools\build_next_review_batch.py `
  --root . `
  --date-label 2026-06-03 `
  --batch-dir derived\human_adjudication\2026-06-03_v2_0_diversity_review_batch `
  --zip-output derived\human_adjudication\Eng_Bench_v2_0_diversity_review_batch_2026-06-03.zip `
  --pad-px 32 `
  --exclude-root derived\human_adjudication\2026-06-03_v2_0_parallel_handoff `
  --exclude-root derived\human_adjudication\2026-06-03_v2_0_next_review_batch `
  --queue microtext\annotations\microtext_review_loc_haer_fort_belvoir_bridge_2026-05-19.jsonl=microtext_loc_haer_fort_belvoir_bridge `
  --queue microtext\annotations\microtext_review_loc_habs_woodlawn_architecture_2026-05-20.jsonl=microtext_loc_habs_woodlawn_architecture `
  --queue microtext\annotations\microtext_review_v1_priority_batch2_2026-05-19.jsonl=microtext_v1_priority_batch2
```

This packet targets civil/architectural breadth. The LOC raster packs require humans to use `edited` plus `corrected_text` for useful labels, because machine OCR is not available locally.

Before sending a standalone review batch ZIP, verify the archive itself:

```powershell
python tools\verify_review_batch_package.py `
  --zip derived\human_adjudication\Eng_Bench_v2_0_next_review_batch_2026-06-03.zip `
  --output-json derived\quality\next_review_batch_zip_verification_2026-06-03.json `
  --output-md derived\quality\next_review_batch_zip_verification_2026-06-03.md

python tools\verify_review_batch_package.py `
  --zip derived\human_adjudication\Eng_Bench_v2_0_diversity_review_batch_2026-06-03.zip `
  --output-json derived\quality\diversity_review_batch_zip_verification_2026-06-03.json `
  --output-md derived\quality\diversity_review_batch_zip_verification_2026-06-03.md
```

This checks top-level batch instructions, `NEXT_REVIEW_BATCH_MANIFEST.csv`, each listed review pack, localized checklist evidence paths, crop counts, full-page evidence counts, and manifest/checklist row-count agreement.

After the handoff and standalone batch ZIPs are verified, generate the packet index:

```powershell
python tools\build_human_packet_index.py --root . --date-label 2026-06-03
```

This writes:

- `derived\human_adjudication\HUMAN_PACKET_INDEX_2026-06-03.md`
- `derived\human_adjudication\HUMAN_PACKET_INDEX_2026-06-03.csv`
- `derived\quality\human_packet_index_2026-06-03.json`

Use this index as the current human-handoff control sheet. It lists the ZIPs to send, review row counts, archive verification status, missing-evidence counts, excluded duplicate/already-gold rows, and the exact return-processing command for each packet. If a new batch supersedes an older ZIP, regenerate the index and send only the ZIPs listed under `ZIPs To Send`.

When the completed follow-on batch is returned, stage it without mutating gold:

```powershell
python tools\process_next_review_batch_return.py `
  --root . `
  --batch-root <returned_next_batch_folder> `
  --output-dir derived\human_adjudication\processed_returns\<date> `
  --strict
```

For a structural smoke test on an unfilled packet, omit `--strict`. A clean unfilled packet should report the expected row count, `0` missing evidence refs, `0` mergeable rows, and all rows blank.

Standalone review batches can contain both microtext and visualdiff queues.
Visualdiff packs include localized `old/`, `new/`, `panels/`, `pages_old/`,
and `pages_new/` evidence plus a CSV checklist. For a usable TODO visualdiff
row, humans should use `edit` and write `human_description`; `valid` is only
allowed when the existing description is already complete.

The current June 5 superseding packet can be rebuilt with:

```powershell
python tools\build_next_review_batch.py `
  --root . `
  --date-label 2026-06-05 `
  --batch-dir derived\human_adjudication\2026-06-05_v2_0_unpacketed_review_batch `
  --zip-output derived\human_adjudication\Eng_Bench_v2_0_unpacketed_review_batch_2026-06-05.zip `
  --pad-px 64 `
  --exclude-active-packet-index 2026-06-04 `
  --queue microtext\annotations\microtext_review_adafruit_esp32_feather_v2_pinout_2026-06-05.jsonl=microtext_adafruit_esp32_feather_v2_pinout_2026-06-05 `
  --queue visualdiff\annotations\visualdiff_review_pixhawk_fmuv2_4_5_to_4_6_2026-06-05.jsonl=visualdiff_pixhawk_fmuv2_4_5_to_4_6_2026-06-05
```

## Release Gate Audits

Run maturity gates after source, review, baseline, or release-packaging changes:

```powershell
python tools\audit_v1_5_gate.py --root . --date-label <yyyy-mm-dd>
python tools\audit_v2_0_gate.py --root . --date-label <yyyy-mm-dd>
```

Each audit writes both `results\health\<gate>_audit_<date>.json` and `results\health\<gate>_audit_<date>.md`. The JSON file is the machine-readable record for automation and the Markdown file is the maintainer-facing summary. Use `--output-json` and `--output-md` only when a custom report location is needed.

Maturity gates count paired, content-distinct baseline/system artifacts only.
Files with `smoke` or `oracle` in the report or submission name are
evaluator/debug evidence and must stay out of baseline-count gates. Renamed
copies of the same predictions count once.

## Processing Returned Human Packets

For the v1.5 725-row handoff, first validate and stage the returned CSVs without mutating active gold:

```powershell
python tools\process_v1_5_human_return.py `
  --root . `
  --packet-root <returned_folder>\derived\human_adjudication\2026-05-25_v1_5_review_handoff `
  --output-dir derived\human_adjudication\processed_returns\<date> `
  --strict
```

The processor maps each checklist back to its source review JSONL, rejects unsafe visualdiff decisions such as `valid` on `CHANGE_DESC_GT_TODO`, checks evidence references, and writes staging JSONL plus `processing_summary.{json,md}`. Only after the summary is complete and error-free should accepted/edited rows be fed into the microtext/visualdiff merge path.

## Hidden Labels

Generated private labels live under `release/private_labels/`.

- Do not publish generated JSON/JSONL files from that directory.
- Do not attach hidden-label files to public issues, papers, or releases.
- Keep hidden scoring reports aggregate-only unless a row-level disclosure is explicitly part of a maintainer audit.

## Leaderboard Submissions

Submissions must pass:

```powershell
python tools\validate_submission_format.py --inputs <input_jsonl> --predictions <prediction_jsonl>
```

Maintainers score hidden submissions with:

```powershell
python tools\benchmark_runner.py --gt release\private_labels\eng_bench_hidden_test_labels.jsonl --pred <prediction_jsonl> --split all --task all --bootstrap-samples 1000 --report-json <report_json>
```

Register the scored result:

```powershell
python tools\register_leaderboard_submission.py `
  --root . `
  --dataset-version <frozen_version> `
  --manifest release\private_labels\challenge_split_manifest.json `
  --predictions <prediction_jsonl> `
  --report <report_json> `
  --scorer-command "<exact scorer command>"
```

Only schema-valid entries with a unique prediction SHA-256, aggregate metrics,
and matching confidence intervals count toward the v2.0 adoption gate. Empty
or duplicate JSON files do not count.

## SVG/EAGLE Text-Layer Review Batches

Use `tools\extract_svg_textlayer.py` for deterministic text geometry from
multipage SVG renders:

```powershell
python tools\extract_svg_textlayer.py `
  --root . `
  --doc-id pixhawk_fmuv1_1_7_1_eagle `
  --svg-dir derived\eagle_svg\pixhawk_fmuv1_1_7_1_eagle `
  --image-dir derived\pages_300dpi\pixhawk_fmuv1_1_7_1_eagle
```

When staging a source-faithful review queue, use the quality controls together:

```powershell
python tools\microtext_review.py `
  --root . `
  --candidates microtext\annotations\microtext_candidates_pixhawk_eagle_2026-06-05.jsonl `
  --output microtext\annotations\microtext_review_pixhawk_eagle_2026-06-05.jsonl `
  --categories pin_label `
  --doc-ids pixhawk_fmuv1_1_7_1_eagle,pixhawk_fmuv2_4_6_eagle `
  --source-candidate-id pcb_005 `
  --limit 100 `
  --max-per-text 2 `
  --max-per-doc 50 `
  --exact-text-only
```

`--exact-text-only` rejects partial regex matches. `--max-per-text` and
`--max-per-doc` keep the human batch diverse and balanced. These outputs are
review-only; package and verify them before sending, and never promote them
without a returned checklist.

## Payload-Alias Capacity Auditing

Byte-identical source payloads may be registered under more than one document
ID and rendered at different resolutions. Before counting a staged cohort as
Gold v2.0 capacity, run the capacity audit with the current payload-duplicate
report:

```powershell
python tools\audit_staged_v2_capacity.py `
  --root . `
  --payload-alias-report derived\quality\source_payload_duplicate_audit.json `
  <current cohort arguments> `
  --output-json derived\quality\v2_0_staged_capacity_<label>.json `
  --output-md derived\quality\v2_0_staged_capacity_<label>.md
```

The audit normalizes each MicroText region by the authoritative page dimensions
and canonical payload hash. It fails closed when a staged row overlaps active
Gold through a payload alias, overlaps an earlier staged alias region, or cannot
resolve the image dimensions needed for normalization. Do not rewrite a
historical cohort to repair a failure. Preserve it, write the held rows and
audit receipt, create a dated clean successor, and rerun the full capacity audit
against only the clean successor.

## Retirement And Quarantine

Rows are quarantined, not deleted, when they are useful for audit history but unsuitable for release. Common reasons:

- no visible difference in the annotated visualdiff crop
- difference lies outside the marked region
- microtext crop is prose, fragmentary, or semantically miscategorized
- source rights are unclear
- duplicate source/document leakage is discovered

## Number-Leading P&ID Line Identifiers

`tools/propose_microtext_ocr_regions.py --include-pid-labels` recognizes two
guarded line-designator families: the existing alphabetic equipment-line form
and structured number-leading P&ID identifiers such as
`18-30003-STM-150-W40CB01-HC-100` or `01-623-AIR-50-W16CB01`. The
number-leading form requires an initial numeric system code, an alphabetic
service segment paired with a numeric sequence, and at least one additional
engineering-code segment. Dates, short numeric fragments, and section labels
remain rejected.

Keep this behavior opt-in. New source-specific line formats must add positive
and negative regression cases before entering conversion. OCR output is still
candidate-only: inspect every crop and full-page context, hold partial or
duplicate readings, run exact/near/payload-alias deduplication, bind retained
rows to a staged split reservation, and require human review before Gold.

## NASA NTRS Rights Receipts

For NASA Technical Reports Server intake, preserve the exact citation API JSON
and direct payload URL. `GOV_PUBLIC_USE_PERMITTED` may be registered as
`government_public_use_permitted_nasa_candidate` only when the source URL is on
`ntrs.nasa.gov`; do not convert it to a public-domain claim. Record distribution,
third-party-material flags, local payload SHA-256, rights evidence path, and the
exact citation URL. `PUBLIC_USE_PERMITTED` without the government determination
does not receive this mapping and must remain rights-reviewed.

Some legacy NTRS download endpoints prepend an ingestion envelope before the
actual PDF. Preserve the original bytes, then use
`tools/unwrap_ntrs_pdf_payload.py` to create a renderable derivative. The tool
fails unless the PDF signature is near the start, an EOF marker is near the
end, and any linearized `/L` declaration matches the extracted byte count.
Record the raw and extracted paths, byte counts, hashes, prefix length, and
transform receipt in the download receipt. Registration, rendering, and review
must use the extracted PDF hash; provenance must retain the original payload
hash and must not imply that envelope removal changes the rights determination.

## Unicode Process Values

RapidOCR commonly emits the single Unicode code points `℃` and `℉` for
temperatures on modern engineering diagrams. The guarded process-value
classifier accepts these forms as well as `DEG C`, `DEG F`, `°C`, and `°F`.
Keep positive regression cases for every supported representation and negative
cases for prose-like temperature fragments. A classifier match only creates a
review candidate: crop and page-context review, taxonomy correction, physical-
region deduplication, provenance, split reservation, and human adjudication are
still mandatory before Gold promotion.

## Exact-Span P&ID Instrument Abbreviations

`tools\mine_microtext_candidates.py` recognizes common full-span P&ID
instrument abbreviations including `AAH`, `AE`, `AIT`, `FCV`, `LSH`, `PCV`,
`PS`, `RHI`, `TA`, `TAH`, `TE`, `TS`, and `TSH`. These tokens are accepted only
when the complete normalized text span is the abbreviation. Embedded prose such
as `The PCV controls pressure.` must not match.

The exact-span guard does not establish that a detected token is an engineering
tag. Inspect every crop and full-page context. Hold tokens embedded in vertical
words, narrative prose, captions, and same-page legends that duplicate the
operational diagram. Retained rows still require evidence, provenance, exact/
near and payload-alias deduplication, split reservation, and human adjudication
before Gold promotion.
