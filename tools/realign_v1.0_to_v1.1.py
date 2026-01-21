#!/usr/bin/env python3
"""
Realign v1.0->v1.1 with correct page mapping.

Based on user observation:
- v1.0 has 22 pages (0-21)
- v1.1 has 24 pages (0-23)
- Pages 0-11: 1:1 match  
- v1.1 page 12 is ADDED (new table)
- v1.0 page 12 → v1.1 page 13
- v1.0 page 13 → v1.1 page 14
- v1.0 page 14 → v1.1 page 15 (partially matching)
- v1.0 page 15+ → v1.1 page 16+ (offset by 1)
- v1.0 page 21 → v1.1 page 22
- v1.1 pages 23 is ADDED (or maybe 22-23 are both added?)
"""
import json
import cv2
import numpy as np
from pathlib import Path
import shutil

ROOT = Path(".")
PAIR_ID = "vdiff__viola__pcbV1.0__to__pcbV1.1"  # Note: using correct pair_id format
DOC_A = "toradex_viola_v1.0"
DOC_B = "toradex_viola_v1.1"

# Custom page mapping: (v1.0_page, v1.1_page)
# v1.0 has 22 pages (0-21), v1.1 has 24 pages (0-23)
PAGE_MAPPING = [
    # Pages 0-11: 1:1
    (0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (5, 5),
    (6, 6), (7, 7), (8, 8), (9, 9), (10, 10), (11, 11),
    # After v1.1 page 12 was added, offset by 1:
    (12, 13),  # v1.0 page 12 → v1.1 page 13
    (13, 14),  # v1.0 page 13 → v1.1 page 14
    (14, 15),  # v1.0 page 14 → v1.1 page 15 (partially matching)
    (15, 16), (16, 17), (17, 18), (18, 19), (19, 20),
    (20, 21), (21, 22),
]

# Added pages in v1.1 (no corresponding v1.0 page)
ADDED_PAGES_B = [12, 23]  # v1.1 page 12 is new, page 23 might also be new


def load_gray(p: Path):
    img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(p)
    return img


def orb_homography(imgA, imgB, max_features=5000):
    orb = cv2.ORB_create(nfeatures=max_features)
    kA, dA = orb.detectAndCompute(imgA, None)
    kB, dB = orb.detectAndCompute(imgB, None)
    if dA is None or dB is None or len(kA) < 20 or len(kB) < 20:
        return None, {"status":"fail", "reason":"insufficient_features", "kA":len(kA), "kB":len(kB)}
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = bf.match(dA, dB)
    matches = sorted(matches, key=lambda m: m.distance)
    if len(matches) < 20:
        return None, {"status":"fail", "reason":"insufficient_matches", "m":len(matches)}
    keep = matches[:min(len(matches), 800)]
    ptsA = np.float32([kA[m.queryIdx].pt for m in keep]).reshape(-1,1,2)
    ptsB = np.float32([kB[m.trainIdx].pt for m in keep]).reshape(-1,1,2)
    H, mask = cv2.findHomography(ptsA, ptsB, cv2.RANSAC, 5.0)
    if H is None or mask is None:
        return None, {"status":"fail", "reason":"homography_failed"}
    inliers = int(mask.sum())
    return H, {"status":"ok", "matches":len(matches), "kept":len(keep), "inliers":inliers, "inlier_ratio": inliers/max(1,len(keep))}


def find_page_image(pages_dir: Path, doc_id: str, page: int) -> Path:
    """Find page image with various naming conventions."""
    base = pages_dir / doc_id
    candidates = [
        f"page_{page:03d}.png",
        f"p{page:04d}.png",
        f"page_{page:04d}.png",
    ]
    for name in candidates:
        p = base / name
        if p.exists():
            return p
    return base / candidates[0]  # Return first pattern even if missing


def main():
    pages_dir = ROOT / "derived" / "pages_300dpi"
    
    # Check for alternate pair_id format
    out_dir = ROOT / "derived" / "align" / PAIR_ID
    alt_pair_id = "vdiff__viola__pcbV1.0_to__pcbV1.1"
    alt_out_dir = ROOT / "derived" / "align" / alt_pair_id
    
    # Use whichever exists, prefer the one from manifest
    if alt_out_dir.exists() and not out_dir.exists():
        out_dir = alt_out_dir
        print(f"[INFO] Using alternate path: {alt_out_dir}")
    
    # Backup old alignment
    backup_dir = out_dir.parent / f"{out_dir.name}_backup"
    if out_dir.exists() and not backup_dir.exists():
        print(f"[INFO] Backing up old alignment to {backup_dir}")
        shutil.copytree(out_dir, backup_dir)
    
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Clear old files
    for f in out_dir.glob("*.json"):
        f.unlink()
    for f in out_dir.glob("*.png"):
        f.unlink()
    
    print(f"[INFO] Realigning v1.0→v1.1 with custom page mapping...")
    print(f"       DOC_A: {DOC_A} (22 pages)")
    print(f"       DOC_B: {DOC_B} (24 pages)")
    
    # Process matched pages
    for pageA, pageB in PAGE_MAPPING:
        pA = find_page_image(pages_dir, DOC_A, pageA)
        pB = find_page_image(pages_dir, DOC_B, pageB)
        
        if not pA.exists():
            print(f"  [WARN] Missing {pA}")
            continue
        if not pB.exists():
            print(f"  [WARN] Missing {pB}")
            continue
        
        imgA = load_gray(pA)
        imgB = load_gray(pB)
        H, info = orb_homography(imgA, imgB)
        
        # Save with page_B index (since annotations are on v1.1 images)
        rec = {
            "page_index": pageB,
            "page_A": pageA,
            "page_B": pageB,
            "info": info,
            "H": H.tolist() if H is not None else None
        }
        (out_dir / f"H_page_{pageB:03d}.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
        
        if H is not None:
            warped = cv2.warpPerspective(imgA, H, (imgB.shape[1], imgB.shape[0]))
            diff = cv2.absdiff(warped, imgB)
            diff = cv2.GaussianBlur(diff, (3,3), 0)
            cv2.imwrite(str(out_dir / f"diffmap_page_{pageB:03d}.png"), diff)
            print(f"  Page {pageA}→{pageB}: inlier_ratio={info.get('inlier_ratio', 0):.2%}")
        else:
            print(f"  Page {pageA}→{pageB}: FAILED - {info.get('reason')}")
    
    # Mark added pages
    for pageB in ADDED_PAGES_B:
        rec = {
            "page_index": pageB,
            "page_A": None,
            "page_B": pageB,
            "info": {"status": "added_page", "reason": "no_corresponding_page_in_A"},
            "H": None
        }
        (out_dir / f"H_page_{pageB:03d}.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
        print(f"  Page {pageB}: marked as ADDED (no v1.0 correspondence)")
    
    print(f"\n[OK] Realignment complete -> {out_dir}")
    print(f"     Matched pairs: {len(PAGE_MAPPING)}")
    print(f"     Added pages in v1.1: {ADDED_PAGES_B}")


if __name__ == "__main__":
    main()
