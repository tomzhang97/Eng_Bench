#!/usr/bin/env python3
"""
Realign v1.1->v1.2 with correct page mapping.

Based on user observation:
- Pages 0-11: 1:1 match
- v1.2 page 12 is ADDED (new table)
- v1.1 page 12 → v1.2 page 13 (partially matching)
- v1.1 page 13 → v1.2 page 14
- v1.1 page 14 → v1.2 page 15 (partially matching)
- v1.1 page 15+ → v1.2 page 16+ (offset by 1)

v1.1 has 24 pages (0-23)
v1.2 has 28 pages (0-27)
"""
import json
import cv2
import numpy as np
from pathlib import Path
import shutil

ROOT = Path(".")
PAIR_ID = "vdiff__viola__pcbV1.1__to__pcbV1.2"
DOC_A = "toradex_viola_v1.1"
DOC_B = "toradex_viola_v1.2"

# Custom page mapping: (v1.1_page, v1.2_page)
# Based on user observation
PAGE_MAPPING = [
    # Pages 0-11: 1:1
    (0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (5, 5),
    (6, 6), (7, 7), (8, 8), (9, 9), (10, 10), (11, 11),
    # Page 12: v1.2 added new page - we'll mark v1.2 page 12 as ADDED
    (12, 13),  # v1.1 page 12 → v1.2 page 13
    (13, 14),  # v1.1 page 13 → v1.2 page 14
    (14, 15),  # v1.1 page 14 → v1.2 page 15 (partially matching)
    # From here, offset by 1
    (15, 16), (16, 17), (17, 18), (18, 19), (19, 20),
    (20, 21), (21, 22), (22, 23), (23, 24),
]

# Added pages in v1.2 (no corresponding v1.1 page)
ADDED_PAGES_B = [12, 25, 26, 27]  # v1.2 pages that are new


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


def main():
    pages_dir = ROOT / "derived" / "pages_300dpi"
    out_dir = ROOT / "derived" / "align" / PAIR_ID
    
    # Backup old alignment
    backup_dir = ROOT / "derived" / "align" / f"{PAIR_ID}_backup"
    if out_dir.exists() and not backup_dir.exists():
        print(f"[INFO] Backing up old alignment to {backup_dir}")
        shutil.copytree(out_dir, backup_dir)
    
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Clear old files
    for f in out_dir.glob("*.json"):
        f.unlink()
    for f in out_dir.glob("*.png"):
        f.unlink()
    
    print(f"[INFO] Realigning {PAIR_ID} with custom page mapping...")
    
    # Process matched pages
    for pageA, pageB in PAGE_MAPPING:
        pA = pages_dir / DOC_A / f"page_{pageA:03d}.png"
        pB = pages_dir / DOC_B / f"page_{pageB:03d}.png"
        
        if not pA.exists():
            print(f"  [WARN] Missing {pA}")
            continue
        if not pB.exists():
            print(f"  [WARN] Missing {pB}")
            continue
        
        imgA = load_gray(pA)
        imgB = load_gray(pB)
        H, info = orb_homography(imgA, imgB)
        
        # Save with page_B index (since annotations are on v1.2 images)
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
        print(f"  Page {pageB}: marked as ADDED (no v1.1 correspondence)")
    
    print(f"\n[OK] Realignment complete -> {out_dir}")
    print(f"     Matched pairs: {len(PAGE_MAPPING)}")
    print(f"     Added pages in v1.2: {ADDED_PAGES_B}")


if __name__ == "__main__":
    main()
