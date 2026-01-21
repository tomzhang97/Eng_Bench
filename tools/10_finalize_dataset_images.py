#!/usr/bin/env python3
"""
10_finalize_dataset_images.py

Creates the canonical 'images/' directory for the Eng_Bench dataset.
Maps the internal 'derived/pages_300dpi' folders to the public-facing
structure expected by the JSONL files:
  images/{doc_id}__{version_id}/page_{index}.png

Usage:
  python tools/10_finalize_dataset_images.py
"""
import os
import shutil
import glob
from pathlib import Path

# Manual mapping from (doc_id, version_id) to source directory name in derived/pages_300dpi
# This bridges the gap between the heuristic ID parsing in Script 07 and the Manifest IDs.
MAPPING = {
    ("viola", "pcbV1.0"): "toradex__viola__datasheet__pcbV1.0",
    ("viola", "pcbV1.1"): "toradex__viola__datasheet__pcbV1.1",
    ("viola", "pcbV1.2"): "toradex__viola__datasheet__pcbV1.2",
    ("bbb", "C"): "bbb_schematic_revC",
    ("bbb", "C3"): "bbb_schematic_revC3",
    ("tolerances_table_iso", "iso"): "tolerances_table_iso"
    # Add Aquila if/when added to visualdiff pairs
}

def main():
    root = Path(".")
    derived_pages = root / "derived" / "pages_300dpi"
    target_images = root / "images"
    
    if target_images.exists():
        print(f"[*] Cleaning existing images directory: {target_images}")
        shutil.rmtree(target_images)
    target_images.mkdir(exist_ok=True)

    print(f"[*] Finalizing images from {derived_pages} -> {target_images}")

    for (doc_id, version_id), source_name in MAPPING.items():
        src_dir = derived_pages / source_name
        dest_dir = target_images / f"{doc_id}__{version_id}"
        
        if not src_dir.exists():
            print(f"[WARN] Source directory not found: {src_dir}")
            # Fallback check (fuzzy match?)
            # Listing available
            continue

        print(f"  Copying {source_name} -> {dest_dir.name}...")
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Copy all pngs with NORMALIZED naming
        import re
        for png in sorted(src_dir.glob("*.png")):
            # Extract page index from various formats: p0000.png, page_000.png, page_0.png
            match = re.search(r'(?:page_?)?(\d+)', png.stem)
            if match:
                page_idx = int(match.group(1))
                canonical_name = f"page_{page_idx:04d}.png"
                shutil.copy2(png, dest_dir / canonical_name)
            else:
                # Fallback: copy as-is
                shutil.copy2(png, dest_dir / png.name)
            
    print("[*] Image finalization complete.")
    
    # Also verify coverage against JSONL? (Optional but good)
    # We leave that to the validation script or user check.

if __name__ == "__main__":
    main()
