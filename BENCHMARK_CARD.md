# Eng_Bench Benchmark Card

## Dataset Description
- **Release Status**: v0.9 Silver data with a passing v0.95 package-clean health gate. This is not yet the final human-gold public release.
- **Sources**: CC/open-hardware schematics (e.g., BeagleBone Black), public vendor design resources (e.g., Aquila), technical carrier board datasheets (e.g., Toradex Viola), public-domain mechanical drawing scans, selected public architectural/civil PDFs, and reusable Wikimedia Commons P&ID diagrams. The ISO tolerance-table source is retained as repaired-but-rights-held provenance outside active gold.
- **Collection Method**: Vector PDFs were rendered to high-resolution PNG images. Optical alignment (ORB) and textual parsing were used to identify precise revision coordinates.

## Annotation Protocol
- **Creation**: Initial candidates for visual difference pairs were generated through semi-automated diff-maps using spatial alignment across document revisions.
- **Validation**: Human experts refined earlier bounding boxes and change descriptions via CVAT and structured review packets. The 2026-07-31 return integration promoted 57 visualdiff and 623 microtext rows after strict review-policy checks. Provenance passes unlocked another 100 reviewed Adafruit ESP32 Feather V2 microtext rows and two reviewed Wikimedia Commons P&ID rows only after source hashes, licenses, and attribution were verified. Current Microtext rows entered through crop-level review. Future objective train-only MicroText may enter through the separately identified, zero-error-calibrated machine-certification policy; dev/test, VisualDiff, semantic labels, and ambiguous evidence remain human-gated.

## Intended Use
- **Primary Goals**: Evaluating Vision-Language Models (VLMs) on complex documents requiring high-precision spatial reasoning.
- **Focus Areas**: 
  - Micro-text extraction (characters <8px height).
  - Visual-diff localization across revisions.
  - Multi-page evidence composition.
  - Revision-aware QA.

## Limitations
- **Failures**: Systems overly reliant on pure OCR extraction will typically fail on symbolic changes without text (visual-only). Conversely, base Vision-Language Models trained at low resolutions (e.g., 336px or 448px) often hallucinate when identifying microscopic tolerance strings.
- Eng_Bench contains specific failure modes designed to highlight when a model memorizes standard schematics but cannot detect the subtle mechanical and electrical localized edits.
- The current microtext volume and split-leakage gates are met, and train/dev now include non-PCB dimension rows plus P&ID slices. The splits are still pin-label heavy and need more full P&ID/process-sheet rows before per-split category metrics should be treated as stable. Active gold sources now have source-status candidates recorded; inactive/reference PDFs still need cleanup before packaging. The low-confidence dev/test visualdiff blocker is cleared, but 125 train-only visualdiff pending descriptions remain outside the release-critical queue.

## Current Health Gate
- The 2026-07-31 strict validation reports 3,689 unified rows, 48 active source documents, zero missing image rows, zero split leakage, and 48/48 release-ready and paper-ready source documents with matching recorded SHA-256 values.
- Packaging rehearsal artifacts now include `dataset_infos.json`, `pyproject.toml`, `engbench.load_eng_bench`, loader smoke output, question-diversity and question-leakage audits, target-specific health reports, and a release-critical file hash manifest.
- Input-only dev/test exports under `release/public_inputs/` strip `answer` and `evidence` fields for future public/hidden-label evaluation rehearsal.
- Twenty-nine baseline reports are currently counted. All 1,230 test rows meet the diagnostic coverage floor: 359 microtext rows have 20 predictions each and 871 visualdiff rows have 24 each. A Tesseract microtext script exists but remains uncounted where the OCR executable is unavailable.
- Gold v2.0 Global remains incomplete: 3,689/25,000-50,000 rows, 48/150 active source documents, 7/30 visualdiff revision families, 1,230/5,000 public/hidden test examples, and 0/185 completed independent agreement rows. The 125 pending visualdiff descriptions are train-only.
