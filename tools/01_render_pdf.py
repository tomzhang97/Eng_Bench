#!/usr/bin/env python3
"""
Render PDFs to per-page PNGs at fixed DPI (default 300).
- Uses PyMuPDF (fitz) for deterministic rendering.
- Supports grayscale for schematics.
Output:
  derived/pages_300dpi/<doc_id>/page_000.png
"""
import argparse
from pathlib import Path
import fitz
from page_ranges import parse_page_selection

def render_pdf(
    pdf_path: Path,
    out_dir: Path,
    dpi: int = 300,
    grayscale: bool = False,
    pages: str | None = None,
):
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    cs = fitz.csGRAY if grayscale else fitz.csRGB
    selected_pages = parse_page_selection(pages, doc.page_count)
    for i in selected_pages:
        page = doc[i]
        pix = page.get_pixmap(matrix=mat, colorspace=cs, alpha=False)
        out_path = out_dir / f"page_{i:03d}.png"
        pix.save(out_path)
    rendered = len(selected_pages)
    doc.close()
    return rendered, selected_pages

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=str, default=".", help="Eng_Bench root")
    ap.add_argument("--doc_id", type=str, required=True)
    ap.add_argument("--pdf_relpath", type=str, required=True)
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--grayscale", action="store_true")
    ap.add_argument(
        "--pages",
        type=str,
        default="all",
        help="1-based page selection such as '1,3-5'. Default: all.",
    )
    args = ap.parse_args()

    root = Path(args.root)
    pdf_path = root / args.pdf_relpath
    out_dir = root / "derived" / f"pages_{args.dpi}dpi" / args.doc_id
    rendered, selected_pages = render_pdf(
        pdf_path, out_dir, dpi=args.dpi, grayscale=args.grayscale, pages=args.pages
    )
    if args.pages.strip().lower() == "all":
        page_note = "all pages"
    else:
        page_note = f"{rendered} selected pages"
    print(f"[OK] Rendered {page_note} from {pdf_path} -> {out_dir}")

if __name__ == "__main__":
    main()
