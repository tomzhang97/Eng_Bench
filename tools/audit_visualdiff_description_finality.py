#!/usr/bin/env python3
"""Read-only localization triage for tentative active VisualDiff answers."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from visualdiff_description_finality import tentative_description_details
except ModuleNotFoundError:
    from tools.visualdiff_description_finality import tentative_description_details


ACTIVE_PATHS = (
    "eng_bench.jsonl", "visualdiff/annotations/visualdiff_pairs.jsonl",
    "visualdiff/annotations/visualdiff_questions.jsonl",
    "microtext/annotations/microtext_items.jsonl",
    "microtext/annotations/microtext_questions.jsonl",
)


def file_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def box(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        result = [float(v) for v in value]
    except (ValueError, TypeError):
        return None
    if not all(math.isfinite(v) for v in result):
        return None
    return result if result[2] > result[0] and result[3] > result[1] else None


def probe_target(spans: list[dict], target: str, page: int, crop: list,
                 radius: float = 200.0) -> dict:
    """Exact text + bounded proximity is a triage signal, never correspondence proof."""
    crop_box = box(crop)
    if crop_box is None:
        return {"status": "invalid_crop", "nearby_matches": 0, "best": None}
    x0, y0, x1, y1 = crop_box
    matches = []
    for span in spans:
        b = box(span.get("bbox_px"))
        if span.get("page") != page or str(span.get("text") or "").strip() != target or b is None:
            continue
        distance = math.hypot((b[0] + b[2] - x0 - x1) / 2,
                              (b[1] + b[3] - y0 - y1) / 2)
        overlap = max(0, min(b[2], x1) - max(b[0], x0)) * max(0, min(b[3], y1) - max(b[1], y0))
        matches.append({
            "bbox_px": b, "distance_px": round(distance, 4),
            "original_crop_span_coverage": overlap / ((b[2] - b[0]) * (b[3] - b[1])),
            "font": span.get("font"), "size": span.get("size"), "flags": span.get("flags"),
        })
    matches.sort(key=lambda match: (match["distance_px"], match["bbox_px"]))
    nearby = [match for match in matches if match["distance_px"] <= radius]
    return {
        "status": "observed", "page_matches": len(matches), "nearby_matches": len(nearby),
        "radius_px": radius, "best": nearby[0] if nearby else None,
        "nearest_page_match": matches[0] if matches else None,
    }


def triage(old: dict, new: dict) -> str:
    if old.get("status") != "observed" or new.get("status") != "observed":
        return "missing_or_invalid_textlayer_evidence"
    if old["nearby_matches"] and new["nearby_matches"]:
        if old["nearby_matches"] > 1 or new["nearby_matches"] > 1:
            return "ambiguous_repeated_text_requires_correspondence"
        return "same_text_near_both_revisions_requires_alignment"
    return "one_or_neither_side_text_is_not_semantic_proof"


def render_pair(root: Path, out: Path, finding: dict) -> dict:
    from PIL import Image, ImageDraw

    panels = []
    coordinates = {}
    for side in ("old", "new"):
        info = finding[side]
        best = info.get("best")
        b = box(best["bbox_px"] if best else info["original_bbox"])
        if b is None:
            raise ValueError("cannot render invalid bbox")
        with Image.open(root / info["image_path"]) as image:
            bounds = (max(0, math.floor(b[0] - 100)), max(0, math.floor(b[1] - 100)),
                      min(image.width, math.ceil(b[2] + 100)), min(image.height, math.ceil(b[3] + 100)))
            crop = image.crop(bounds).convert("RGB")
        panel = Image.new("RGB", (max(450, crop.width), crop.height + 55), "white")
        panel.paste(crop, (0, 55))
        draw = ImageDraw.Draw(panel)
        draw.text((8, 5), f"{side.upper()} - {'nearby exact text' if best else 'original region'}", fill="black")
        draw.text((8, 23), str([round(v, 1) for v in b]), fill="black")
        draw.rectangle(
            (
                round(b[0] - bounds[0]),
                round(b[1] - bounds[1] + 55),
                round(b[2] - bounds[0]),
                round(b[3] - bounds[1] + 55),
            ),
            outline="#d7191c",
            width=2,
        )
        panels.append(panel)
        coordinates[side] = {"crop_bbox_px": bounds, "target_bbox_px": b, "scale": 1}
    combined = Image.new("RGB", (sum(p.width for p in panels) + 12, max(p.height for p in panels)), "#dddddd")
    combined.paste(panels[0], (0, 0))
    combined.paste(panels[1], (panels[0].width + 12, 0))
    filename = f"evidence_{hashlib.sha256(finding['pair_id'].encode()).hexdigest()[:16]}.png"
    path = out / filename
    combined.save(path)
    return {"path": path.relative_to(root).as_posix(), "sha256": file_hash(path), "coordinates": coordinates}


def build_audit(root: Path, out: Path, render_ids: list[str]) -> dict:
    root = root.resolve()
    out = out.resolve()
    if not out.is_relative_to(root / "derived") or out.exists():
        raise ValueError("output must be a new directory under root/derived")
    before = {path: file_hash(root / path) for path in ACTIVE_PATHS}
    manifest_path = root / "manifest.jsonl"
    manifest = {r["pair_id"]: r for r in read_jsonl(manifest_path) if r.get("type") == "pair"}
    findings = []
    cache: dict[str, list[dict] | None] = {}
    evidence_hashes = {"manifest.jsonl": file_hash(manifest_path)}
    for row in read_jsonl(root / ACTIVE_PATHS[1]):
        details = tentative_description_details(str(row.get("change_desc_gt") or ""))
        if not details:
            continue
        finding = {"pair_id": row["pair_id"], "split": row["split"], **details,
                   "description": row["change_desc_gt"], "safe_to_merge_gold": False}
        pair = manifest.get(row.get("project_id"), {})
        for side, key in (("old", "from_doc_id"), ("new", "to_doc_id")):
            doc_id = pair.get(key, "")
            path = root / "derived" / "textlayer" / (doc_id + ".jsonl")
            if doc_id not in cache:
                cache[doc_id] = read_jsonl(path) if doc_id and path.is_file() else None
                if cache[doc_id] is not None:
                    evidence_hashes[path.relative_to(root).as_posix()] = file_hash(path)
            info = {"status": "missing_textlayer", "nearby_matches": 0, "best": None}
            if cache[doc_id] is not None and details["target"]:
                target = details.get(f"target_{side}", details["target"])
                info = probe_target(cache[doc_id], target, row[f"page_index_{side}"], row[f"bbox_{side}"])
            image_path = row[f"image_{side}"]
            if image_path not in evidence_hashes and (root / image_path).is_file():
                evidence_hashes[image_path] = file_hash(root / image_path)
            finding[side] = {**info, "doc_id": doc_id, "image_path": image_path,
                             "textlayer_path": path.relative_to(root).as_posix(), "original_bbox": row[f"bbox_{side}"]}
        if details["kind"] == "text_changed":
            finding["machine_triage"] = "proposed_text_change_requires_correspondence"
        else:
            finding["machine_triage"] = triage(finding["old"], finding["new"]) if details["target"] else "graphic_requires_visual_review"
        finding["disposition"] = "hold_for_machine_repair_then_revalidation"
        findings.append(finding)
    unknown = set(render_ids) - {r["pair_id"] for r in findings}
    if unknown:
        raise ValueError(f"render IDs not in tentative queue: {sorted(unknown)}")
    out.mkdir(parents=True)
    for finding in findings:
        if finding["pair_id"] in render_ids:
            finding["matched_evidence"] = render_pair(root, out, finding)
    queue = out / "machine_repair_queue.jsonl"
    queue.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in findings), encoding="utf-8")
    after = {path: file_hash(root / path) for path in ACTIVE_PATHS}
    report = {
        "goal": "Gold v2.0 Global", "status": "OPEN" if findings else "PASS",
        "tentative_rows": len(findings), "by_kind": dict(Counter(r["kind"] for r in findings)),
        "by_split": dict(Counter(r["split"] for r in findings)),
        "machine_triage_counts": dict(Counter(r["machine_triage"] for r in findings)),
        "rendered_pairs": len(render_ids), "active_hashes_before": before, "active_hashes_after": after,
        "active_gold_modified": before != after, "evidence_hashes": evidence_hashes,
        "queue_path": queue.relative_to(root).as_posix(), "queue_sha256": file_hash(queue),
        "safe_to_merge_gold": False, "gold_rows_modified": 0,
        "limitation": "Exact nearby text is a localization lead, not proof of unchanged engineering or topology. No semantic votes are overridden.",
    }
    if before != after:
        report["status"] = "FAIL"
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--render-pair", action="append", default=[])
    args = parser.parse_args()
    root = args.root.resolve()
    report = build_audit(root, root / args.output_dir, args.render_pair)
    print(json.dumps({k: report[k] for k in ("status", "tentative_rows", "by_kind", "by_split", "machine_triage_counts", "active_gold_modified")}, indent=2))
    return 1 if report["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
