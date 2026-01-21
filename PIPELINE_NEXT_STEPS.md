# Eng-Bench: Minimal runnable pipeline (MVP)

This folder contains:
- `manifest.jsonl` : docs + revision pairs (BBB C→C3, Viola v1.0→v1.1)
- `tools/` : deterministic rendering, text-layer extraction, ORB alignment for visual-diff pairs
- `derived/` : example outputs already generated for BBB (first 3 pages)

## 0) Requirements
Python 3.10+ recommended.

Packages:
- PyMuPDF (`pip install pymupdf`)
- OpenCV + NumPy (`pip install opencv-python numpy`)

## 1) Put PDFs in the paths referenced by `manifest.jsonl`
For BBB, the PDFs are already placed under:
- `visualdiff/docs/bbb__schematic__docRevC__pcbRevB6.pdf`
- `visualdiff/docs/bbb__schematic__docRevC3__pcbRevC.pdf`

For Viola, place your two PDFs under:
- `visualdiff/docs/toradex_viola_v1.0.pdf`
- `visualdiff/docs/toradex_viola_v1.1.pdf`
Then (optional) fill their `sha256` fields in the manifest.

## 2) Unified rendering (300 DPI)
Render schematics in grayscale:
```bash
python tools/01_render_pdf.py --root Eng_Bench --doc_id bbb_sch_docC_pcbB6 --pdf_relpath visualdiff/docs/bbb__schematic__docRevC__pcbRevB6.pdf --dpi 300 --grayscale
python tools/01_render_pdf.py --root Eng_Bench --doc_id bbb_sch_docC3_pcbC --pdf_relpath visualdiff/docs/bbb__schematic__docRevC3__pcbRevC.pdf --dpi 300 --grayscale
```

Render Viola similarly (use `--grayscale` if it's schematic-like; omit if it's more datasheet/photo heavy):
```bash
python tools/01_render_pdf.py --root Eng_Bench --doc_id viola_v1_0 --pdf_relpath visualdiff/docs/toradex_viola_v1.0.pdf --dpi 300 --grayscale
python tools/01_render_pdf.py --root Eng_Bench --doc_id viola_v1_1 --pdf_relpath visualdiff/docs/toradex_viola_v1.1.pdf --dpi 300 --grayscale
```

Outputs:
- `derived/pages_300dpi/<doc_id>/page_000.png`

## 3) Extract embedded text layer (silver standard)
```bash
python tools/02_extract_textlayer.py --root Eng_Bench --doc_id bbb_sch_docC_pcbB6 --pdf_relpath visualdiff/docs/bbb__schematic__docRevC__pcbRevB6.pdf
python tools/02_extract_textlayer.py --root Eng_Bench --doc_id bbb_sch_docC3_pcbC --pdf_relpath visualdiff/docs/bbb__schematic__docRevC3__pcbRevC.pdf
```

Outputs:
- `derived/textlayer/<doc_id>/page_000.json`

## 4) Visual-Diff: registration-first alignment (ORB + RANSAC)
Align by page index (works when page order/count matches).
Example aligns first 3 pages:
```bash
python tools/03_align_pair_orb.py --root Eng_Bench --pair_id vdiff__bbb__docC_pcbB6__to__docC3_pcbC --docA bbb_sch_docC_pcbB6 --docB bbb_sch_docC3_pcbC --dpi 300 --pages 0-2
```

Do the same for Viola after rendering:
```bash
python tools/03_align_pair_orb.py --root Eng_Bench --pair_id vdiff__viola__v1_0__to__v1_1 --docA viola_v1_0 --docB viola_v1_1 --dpi 300 --pages 0-20
```

Outputs:
- `derived/align/<pair_id>/H_page_000.json` (homography + QC stats)
- `derived/align/<pair_id>/diffmap_page_000.png` (absolute difference after warping)

## 5) Manual annotation (CVAT)
MVP annotation targets for Visual-Diff:
- revision clouds
- delta triangles
- (optional) link to revision table row (if present)

Recommended workflow:
1) Import rendered pages from BOTH versions into the same CVAT task (two folders, or two "datasets").
2) Use `diffmap_page_XXX.png` as a visual guide (overlay in a second window) to speed up marking.
3) Export annotations to `visualdiff/annotations/` as JSONL or COCO.

## 6) Next automation you can add (once MVP works)
- Pre-annotation: threshold diffmap → connected components → propose bbox candidates.
- Page mapping by title block (when order changes).
- Split control: keep all revisions of a model in the same split to avoid leakage.
