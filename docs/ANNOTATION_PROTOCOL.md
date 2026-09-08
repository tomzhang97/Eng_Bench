# Eng_Bench Annotation Protocol

This protocol defines what can enter active gold JSONL and what must stay in review or quarantine. It applies to both the current v0.9 Silver candidate and the path to Gold v2.0 Global. Evaluation labels remain human-adjudicated; objective train MicroText may use the separately calibrated machine-certification policy.

## Status Vocabulary

Use these statuses in human review files:

| Status | Meaning | Gold action |
| --- | --- | --- |
| `valid` | Existing answer/description and bbox are usable as written. | Keep active; mark `review_confidence=human_validated`. |
| `edit` | Evidence is usable but the answer/description needs correction. | Keep active with the corrected answer; mark `desc_source=human`. |
| `reject_unclear` | Crop/evidence is ambiguous or not independently verifiable. | Remove from active gold and write to a quarantine JSONL. |
| `needs_full_page` | Crop is too narrow to judge from the review pack. | Keep blocked from gold until the full page is inspected. |

Never use `valid` because the row "looks plausible." The reviewer must be able to verify the answer directly from the provided crop or source page.

## Visualdiff Review

Use `derived/review_packs/visualdiff_human_polish_2026-05-14/` for the current 98 low-confidence dev/test rows.

Required reviewer steps:

1. Open `index.html` or the row's side-by-side panel image.
2. Compare the highlighted old and new evidence regions.
3. Check whether the current description names a visible change.
4. Fill `validation_checklist.csv`:
   - `human_status`: one of `valid`, `edit`, `reject_unclear`, `needs_full_page`.
   - `human_description`: required only for `edit`.
   - `human_notes`: short reason, especially for rejected or full-page rows.
5. Apply the filled checklist:

```powershell
python tools\apply_visualdiff_polish.py --root .
python tools\rebuild_visualdiff_questions.py --root .
```

Gold requirements:

- Dev/test rows must have zero `review_confidence=low`.
- Dev/test rows must have zero pending-description placeholders.
- Gold v2.0 VisualDiff descriptions must be concise English prose. Preserve
  source labels, reference designators, values, and non-English drawing text
  verbatim inside the English sentence when they are evidence. A finalized
  non-English description remains held until a human supplies and verifies the
  English description; machine translation alone is not a Gold label.
- `reject_unclear` rows must be quarantined, not left in active dev/test.
- `needs_full_page` rows remain release blockers until resolved.

Promotion previews are read-only. For a reviewed return batch, run
`tools/preview_reviewed_gold_promotion.py` with the signed split plan. If rows
are held, give `promotion_holds_for_human.csv` to the correcting reviewer. The
reviewer must not alter the identity, evidence, split, or reason columns. They
fill `human_status` plus only the correction columns named by each reason.
Import the completed CSV with `tools/apply_promotion_hold_corrections.py` into a
new JSONL, then rerun the preview. A row remains outside Gold until that preview
reports `ready_for_apply=true`.

After a preview is green, promotion uses
`tools/apply_reviewed_gold_promotion.py`. Supply the exact preview-report
SHA-256 and run it once without `--apply`. Apply mode additionally requires a
new empty directory under `derived/snapshots`. The transaction accepts only the
preview's combined artifacts, adds their reserved document/family units to the
frozen split files, snapshots all active release files, and reruns strict
annotation, unified, image, leakage, and provenance-regression checks. Any
post-write failure restores every snapshotted file. Never call the lower-level
task merge CLIs directly for a release promotion.

## Microtext Review

Microtext rows may enter active gold only when:

- The crop visibly contains the exact `text_gt`.
- The bbox is tight enough to identify the target without depending on surrounding prose.
- The row category is correct.
- The source document is in an allowed active split and has acceptable source-status metadata.

Reject or hold rows when:

- OCR text is from prose/table noise rather than an engineering label.
- The label is truncated or ambiguous.
- The crop requires guessing from context outside the bbox.
- Source rights are unresolved.

## Machine-Certified Train MicroText

Objective MicroText rows may bypass row-by-row human review only through
[`MACHINE_CERTIFICATION_POLICY.md`](MACHINE_CERTIFICATION_POLICY.md). This lane
is limited to `train`; dev/test, VisualDiff, semantic labels, and ambiguous
evidence remain human-only.

The eligibility audit requires deterministic text-layer lineage, verified
source and page hashes, exact source-field agreement, a conservative category
pattern, independent RapidOCR agreement at confidence `>=0.98`, split locks,
and duplicate exclusion. A frozen stratified sample of at least 300 rows must
then have zero human-audit errors and a one-sided 95% precision lower bound of
at least `0.99`.

Machine-certified rows must retain `human_reviewed=false`,
`certification_method=machine_verified`, and
`certification_tier=auto_gold_train`. They enter the same strict read-only
promotion preview and atomic transaction used by human-reviewed rows. The
machine-certification tools never write active Gold directly.

## Independent Agreement Audit

The legacy 185-row agreement sample under
`derived/human_adjudication/2026-07-10b_agreement_reviewer_b_independent/`
must not be used as final Gold v2.0 agreement evidence. The 2026-08-20
readiness audit found that 148/185 rows use rights-blocked active sources;
Reviewer A has 146/185 schema-complete decisions and Reviewer B has 0/185.
Completing that packet can still provide historical QA, but it cannot close the
release gate.

After the one-for-one provenance replacement migration is complete, regenerate
a stratified dev/test sample from the final release-safe active Gold snapshot.
Freeze the sample CSV and record its SHA-256 before assigning it to two
independent reviewers. The agreement report and sample-readiness report must
contain the same reference SHA-256; `agreement_release_gate` rejects missing or
mismatched hashes and any sample with a non-release-ready row.

Preserve the established 185-row task balance: 95 MicroText and 90 VisualDiff.
Before building, run `tools/audit_agreement_rebuild_capacity.py`; require
`exact_sample_feasible=true`. The release packet build must use an explicit
current provenance report, `--require-release-ready`, `--sample-size 185`,
`--microtext-rows 95`, and `--visualdiff-rows 90`. The packet verifier rejects
false release-ready claims, nonblank source blockers, and build/reference count
mismatches.

Required reviewer workflow:

1. Assign `reviewer_a_checklist.csv` and `reviewer_b_checklist.csv` to different people.
2. Each reviewer fills only their assigned CSV and does not discuss decisions with the other reviewer until both files are returned.
3. Open `index.html`; inspect the localized crop/panel and full-page evidence when needed.
4. Inspect `source_doc_ids`, `source_url`, and `source_status` before setting `rights_concern`.
5. Fill every decision field. Corrections and rejects require a short note.
6. Return both CSVs without renaming them.

Maintainers compute agreement only after both independent sheets are complete.
The release gates are microtext exact-answer agreement of at least `0.95` and
visualdiff accept/reject agreement of at least `0.90`. Disagreements are
adjudicated separately; no audit decision changes gold automatically.

Before counting agreement as release evidence, run
`tools/audit_agreement_sample_readiness.py` against the frozen reference, both
reviewer sheets, and the latest active-Gold provenance report. Require
`release_sample_ready=true`, `fully_ready_rows=reference_rows`, zero
`non_release_ready_rows`, and the same `inputs.reference_sha256` recorded by
`tools/agreement_audit.py`.

## Quarantine Policy

Quarantine instead of deleting when a row was generated from a real source but is not gold-ready. Quarantine rows should preserve:

- `pair_id` or `item_id`
- original split
- original answer/description
- bbox and page metadata
- `quarantine_reason`
- reviewer notes, if available

Quarantined rows are not part of release counts.

## Audit Commands

Run after every merge:

```powershell
python tools\rebuild_visualdiff_questions.py --root .
python tools\unify_dataset.py
python tools\audit_visualdiff_quality.py --root . --output derived\quality\visualdiff_quality_audit.json
python tools\audit_microtext_quality.py --root . --output derived\quality\microtext_quality_audit.json
python tools\audit_microtext_splits.py --root . --output derived\quality\microtext_split_audit.json
python splits\leakage_check.py --root .
python tools\benchmark_health_report.py --root . --release-target v1.0
```
