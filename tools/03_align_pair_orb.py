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
- Maps pages by index by default. Use --page-pairs for explicit cross-page
  mappings such as 38:109 when report revisions reorder or renumber sheets.
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


def resize_for_matching(image, max_dimension):
    """Return a bounded matching image and its full-to-match scale matrix."""
    height, width = image.shape[:2]
    if max_dimension <= 0 or max(height, width) <= max_dimension:
        return image, np.eye(3, dtype=np.float64)
    scale = max_dimension / float(max(height, width))
    match_width = max(1, int(round(width * scale)))
    match_height = max(1, int(round(height * scale)))
    resized = cv2.resize(image, (match_width, match_height), interpolation=cv2.INTER_AREA)
    matrix = np.asarray(
        [
            [match_width / float(width), 0.0, 0.0],
            [0.0, match_height / float(height), 0.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    return resized, matrix


def normalized_orb_homography(imgA, imgB, max_dimension, max_features=5000):
    """Estimate on bounded previews and map the homography to full resolution."""
    matchA, scaleA = resize_for_matching(imgA, max_dimension)
    matchB, scaleB = resize_for_matching(imgB, max_dimension)
    match_homography, info = orb_homography(matchA, matchB, max_features=max_features)
    info = dict(info)
    info.update(
        {
            "matching_mode": "normalized_resolution",
            "match_shape_A": [int(matchA.shape[1]), int(matchA.shape[0])],
            "match_shape_B": [int(matchB.shape[1]), int(matchB.shape[0])],
        }
    )
    if match_homography is None:
        return None, info
    full_homography = np.linalg.inv(scaleB) @ match_homography @ scaleA
    if abs(float(full_homography[2, 2])) > 1e-12:
        full_homography = full_homography / full_homography[2, 2]
    return full_homography, info


def parse_page_indexes(value):
    pages = []
    if "-" in value:
        start, end = value.split("-", 1)
        pages = list(range(int(start), int(end) + 1))
    else:
        pages = [int(item) for item in value.split(",") if item.strip()]
    return pages


def parse_page_pairs(pages, explicit_pairs):
    """Return (old_page, new_page) mappings with unique destination pages."""
    if not explicit_pairs:
        return [(page, page) for page in parse_page_indexes(pages)]
    pairs = []
    for item in explicit_pairs.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            raise ValueError(f"invalid page pair {item!r}; expected OLD:NEW")
        old_page, new_page = item.split(":", 1)
        pairs.append((int(old_page), int(new_page)))
    if not pairs:
        raise ValueError("--page-pairs must contain at least one OLD:NEW mapping")
    destination_pages = [new_page for _old_page, new_page in pairs]
    if len(destination_pages) != len(set(destination_pages)):
        raise ValueError("--page-pairs destination pages must be unique")
    return pairs


def enforce_alignment_quality(homography, info, min_inliers, min_inlier_ratio):
    """Fail closed when RANSAC support is too weak for a trustworthy diffmap."""
    if homography is None or info.get("status") != "ok":
        return None, info
    inliers = int(info.get("inliers") or 0)
    inlier_ratio = float(info.get("inlier_ratio") or 0.0)
    if inliers >= min_inliers and inlier_ratio >= min_inlier_ratio:
        return homography, info
    rejected = dict(info)
    rejected.update(
        {
            "status": "fail",
            "reason": "weak_homography",
            "minimum_inliers": min_inliers,
            "minimum_inlier_ratio": min_inlier_ratio,
        }
    )
    return None, rejected

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=str, default=".")
    ap.add_argument("--pair_id", type=str, required=True)
    ap.add_argument("--docA", type=str, required=True)
    ap.add_argument("--docB", type=str, required=True)
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--pages", type=str, default="0-2", help="page range like 0-10 or comma list")
    ap.add_argument(
        "--page-pairs",
        type=str,
        default="",
        help="explicit comma-separated OLD:NEW page mappings; overrides --pages",
    )
    ap.add_argument(
        "--max-match-dimension",
        type=int,
        default=0,
        help=(
            "If positive, estimate alignment on bounded previews and map the "
            "homography back to full resolution. Useful for equal-aspect pages "
            "rendered at different canvas resolutions."
        ),
    )
    ap.add_argument(
        "--min-inliers",
        type=int,
        default=20,
        help="minimum RANSAC inlier count required to emit a diffmap",
    )
    ap.add_argument(
        "--min-inlier-ratio",
        type=float,
        default=0.08,
        help="minimum RANSAC inlier ratio required to emit a diffmap",
    )
    args = ap.parse_args()

    root = Path(args.root)
    pages_dir = root / "derived" / f"pages_{args.dpi}dpi"
    out_dir = root / "derived" / "align" / args.pair_id
    out_dir.mkdir(parents=True, exist_ok=True)

    page_pairs = parse_page_pairs(args.pages, args.page_pairs)

    for page_a, page_b in page_pairs:
        pA = pages_dir / args.docA / f"page_{page_a:03d}.png"
        pB = pages_dir / args.docB / f"page_{page_b:03d}.png"
        imgA = load_gray(pA)
        imgB = load_gray(pB)
        if args.max_match_dimension > 0:
            H, info = normalized_orb_homography(
                imgA,
                imgB,
                max_dimension=args.max_match_dimension,
            )
        else:
            H, info = orb_homography(imgA, imgB)
        H, info = enforce_alignment_quality(
            H,
            info,
            min_inliers=args.min_inliers,
            min_inlier_ratio=args.min_inlier_ratio,
        )
        rec = {
            "page_index": page_b,
            "page_index_A": page_a,
            "page_index_B": page_b,
            "info": info,
            "H": H.tolist() if H is not None else None,
        }
        (out_dir / f"H_page_{page_b:03d}.json").write_text(
            json.dumps(rec, indent=2), encoding="utf-8"
        )

        diffmap_path = out_dir / f"diffmap_page_{page_b:03d}.png"
        if H is not None:
            warped = cv2.warpPerspective(imgA, H, (imgB.shape[1], imgB.shape[0]))
            diff = cv2.absdiff(warped, imgB)
            # mild blur to reduce noise
            diff = cv2.GaussianBlur(diff, (3,3), 0)
            cv2.imwrite(str(diffmap_path), diff)
        elif diffmap_path.exists():
            diffmap_path.unlink()

    print(f"[OK] Aligned page pairs {page_pairs} -> {out_dir}")

if __name__ == "__main__":
    main()
