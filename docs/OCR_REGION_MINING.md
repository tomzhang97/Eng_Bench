# OCR Region Mining

`tools/propose_microtext_ocr_regions.py` creates review-only microtext proposals
from raster engineering sheets. It never changes active gold files.

## Isolated setup

Use Python 3.12 and install the optional runtime outside the core environment:

```powershell
python -m venv .codex_work/rapidocr-venv
.codex_work/rapidocr-venv/Scripts/python -m pip install -r requirements-ocr.txt
```

## Mining

```powershell
.codex_work/rapidocr-venv/Scripts/python tools/propose_microtext_ocr_regions.py `
  --root . `
  --doc-prefix loc22_ `
  --version-id loc_wave22_ocr_2026_08_04 `
  --max-image-pixels 300000000 `
  --output-jsonl microtext/annotations/microtext_review_loc_wave22_ocr_raw_2026-08-04.jsonl `
  --report-json derived/quality/loc_wave22_ocr_mining_2026-08-04.json `
  --report-md derived/quality/loc_wave22_ocr_mining_2026-08-04.md
```

The miner tiles large pages, records OCR confidence and tile provenance, keeps
only supported engineering-label classes, and deduplicates overlap detections.
Every output row still requires image inspection, correction or rejection, and
the normal provenance, split, leakage, duplicate, and promotion gates.

To mine only selected pages, repeat `--page-index` for each zero-based page:

```powershell
.codex_work/rapidocr-venv/Scripts/python tools/propose_microtext_ocr_regions.py `
  --root . `
  --doc-id floor_plan_4_bedroom_rural_dwelling `
  --page-index 1 `
  --page-index 3 `
  --version-id usda_selected_pages_2026_08_05 `
  --output-jsonl derived/quality/usda_selected_pages_raw.jsonl `
  --report-json derived/quality/usda_selected_pages_report.json `
  --report-md derived/quality/usda_selected_pages_report.md
```

The command fails if a selected page is missing. This makes targeted retries
auditable and prevents an accidental silent fall-through to every rendered
page.

## Visual decision ledger

Record contact-sheet decisions in a JSON ledger and bind it to the exact input
JSONL with `input_sha256`. Apply it with
`tools/apply_microtext_visual_decisions.py`. A correction entry may set
`category`, `proposed_text`, or both. The tool preserves the original machine
text in `machine_text_corrected_from`, emits separate kept and held JSONL files,
and writes CSV/JSON/Markdown audit reports. If the input hash changes, do not
reuse the old ledger; inspect the new contact sheet and create a new decision
file.

## Compact PCB designators

Compact electronics reference designators such as `C10`, `R12`, `P4`, `T13`,
and `U1` are proposed as `pin_label`, whose benchmark question covers pin or
component labels. Instrument prefixes retain priority (`FT10` remains
`instrument_tag`), and separated process-equipment tags such as `P-101` remain
`equipment_tag`. This is proposal logic only; the contact-sheet visual pass and
human checklist must still confirm every text/category pair.
