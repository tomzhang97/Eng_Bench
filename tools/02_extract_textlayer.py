#!/usr/bin/env python3
"""
Extract embedded text layer (silver standard) from PDFs using PyMuPDF.
Output:
  derived/textlayer/<doc_id>/page_000.json
Each JSON contains a list of spans with:
  text, bbox (x0,y0,x1,y1) in PDF points, page_index, flags, font, size, color.
"""
import argparse, json
from pathlib import Path
import fitz

fitz.TOOLS.mupdf_display_errors(False)
fitz.TOOLS.mupdf_display_warnings(False)


def extract(pdf_path: Path, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    for i in range(doc.page_count):
        page = doc[i]
        d = page.get_text("dict")
        spans = []
        for b in d.get("blocks", []):
            for l in b.get("lines", []):
                for s in l.get("spans", []):
                    txt = (s.get("text") or "").strip()
                    if not txt:
                        continue
                    spans.append({
                        "page_index": i,
                        "text": txt,
                        "bbox": s.get("bbox"),
                        "origin": s.get("origin"),
                        "size": s.get("size"),
                        "font": s.get("font"),
                        "flags": s.get("flags"),
                        "color": s.get("color"),
                    })
        out_path = out_dir / f"page_{i:03d}.json"
        out_path.write_text(json.dumps({"doc_page": i, "spans": spans}, ensure_ascii=False, indent=2), encoding="utf-8")
    doc.close()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=str, default=".")
    ap.add_argument("--doc_id", type=str, required=True)
    ap.add_argument("--pdf_relpath", type=str, required=True)
    args = ap.parse_args()
    root = Path(args.root)
    pdf_path = root / args.pdf_relpath
    out_dir = root / "derived" / "textlayer" / args.doc_id
    extract(pdf_path, out_dir)
    print(f"[OK] Extracted textlayer {pdf_path} -> {out_dir}")

if __name__ == "__main__":
    main()
