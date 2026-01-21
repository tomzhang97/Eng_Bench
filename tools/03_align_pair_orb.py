#!/usr/bin/env python3
"""
Registration-first alignment for a Visual-Diff pair using ORB + RANSAC homography.
Inputs:
  derived/pages_300dpi/<docA>/page_XXX.png
  derived/pages_300dpi/<docB>/page_XXX.png
Outputs:
  derived/align/<pair_id>/H_page_XXX.json
  derived/align/<pair_id>/diffmap_page_XXX.png (absdiff heatmap-style grayscale)
Notes:
- Assumes page mapping by index (same page count/order).
- If alignment fails, stores status with reason.
"""
import argparse, json
from pathlib import Path
import cv2
import numpy as np

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
    # keep top fraction
    keep = matches[:min(len(matches), 800)]
    ptsA = np.float32([kA[m.queryIdx].pt for m in keep]).reshape(-1,1,2)
    ptsB = np.float32([kB[m.trainIdx].pt for m in keep]).reshape(-1,1,2)
    H, mask = cv2.findHomography(ptsA, ptsB, cv2.RANSAC, 5.0)
    if H is None or mask is None:
        return None, {"status":"fail", "reason":"homography_failed"}
    inliers = int(mask.sum())
    return H, {"status":"ok", "matches":len(matches), "kept":len(keep), "inliers":inliers, "inlier_ratio": inliers/max(1,len(keep))}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=str, default=".")
    ap.add_argument("--pair_id", type=str, required=True)
    ap.add_argument("--docA", type=str, required=True)
    ap.add_argument("--docB", type=str, required=True)
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--pages", type=str, default="0-2", help="page range like 0-10 or comma list")
    args = ap.parse_args()

    root = Path(args.root)
    pages_dir = root / "derived" / f"pages_{args.dpi}dpi"
    out_dir = root / "derived" / "align" / args.pair_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # parse pages
    pages=[]
    if "-" in args.pages:
        a,b=args.pages.split("-")
        pages=list(range(int(a), int(b)+1))
    else:
        pages=[int(x) for x in args.pages.split(",") if x.strip()]

    for i in pages:
        pA = pages_dir / args.docA / f"page_{i:03d}.png"
        pB = pages_dir / args.docB / f"page_{i:03d}.png"
        imgA = load_gray(pA)
        imgB = load_gray(pB)
        H, info = orb_homography(imgA, imgB)
        rec = {"page_index": i, "info": info, "H": H.tolist() if H is not None else None}
        (out_dir / f"H_page_{i:03d}.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")

        if H is not None:
            warped = cv2.warpPerspective(imgA, H, (imgB.shape[1], imgB.shape[0]))
            diff = cv2.absdiff(warped, imgB)
            # mild blur to reduce noise
            diff = cv2.GaussianBlur(diff, (3,3), 0)
            cv2.imwrite(str(out_dir / f"diffmap_page_{i:03d}.png"), diff)

    print(f"[OK] Aligned pages {pages} -> {out_dir}")

if __name__ == "__main__":
    main()
