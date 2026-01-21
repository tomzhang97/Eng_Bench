#!/usr/bin/env python3
"""
Render PDFs to per-page PNGs at fixed DPI (default 300).
- Uses PyMuPDF (fitz) for deterministic rendering.
- Supports grayscale for schematics.
Output:
  derived/pages_300dpi/<doc_id>/page_000.png
"""
import argparse, json, os
from pathlib import Path
import fitz

def render_pdf(pdf_path: Path, out_dir: Path, dpi: int = 300, grayscale: bool = False):
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    cs = fitz.csGRAY if grayscale else fitz.csRGB
    for i in range(doc.page_count):
        page = doc[i]
        pix = page.get_pixmap(matrix=mat, colorspace=cs, alpha=False)
        out_path = out_dir / f"page_{i:03d}.png"
        pix.save(out_path)
    doc.close()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=str, default=".", help="Eng_Bench root")
    ap.add_argument("--doc_id", type=str, required=True)
    ap.add_argument("--pdf_relpath", type=str, required=True)
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--grayscale", action="store_true")
    args = ap.parse_args()

    root = Path(args.root)
    pdf_path = root / args.pdf_relpath
    out_dir = root / "derived" / f"pages_{args.dpi}dpi" / args.doc_id
    render_pdf(pdf_path, out_dir, dpi=args.dpi, grayscale=args.grayscale)
    print(f"[OK] Rendered {pdf_path} -> {out_dir}")

if __name__ == "__main__":
    main()
