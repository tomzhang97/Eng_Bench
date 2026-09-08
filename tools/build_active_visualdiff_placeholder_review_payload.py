#!/usr/bin/env python3
"""Build aligned evidence and machine proposals for active VisualDiff placeholders."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

try:
    import audit_active_gold_provenance
    import visualdiff_geometry_holds
except ModuleNotFoundError:
    from tools import audit_active_gold_provenance
    from tools import visualdiff_geometry_holds


ACTIVE_GOLD_FILES = (
    "eng_bench.jsonl",
    "manifest.jsonl",
    "microtext/annotations/microtext_items.jsonl",
    "microtext/annotations/microtext_questions.jsonl",
    "visualdiff/annotations/visualdiff_pairs.jsonl",
    "visualdiff/annotations/visualdiff_questions.jsonl",
)
PLACEHOLDERS = {"CHANGE_DESC_GT_TODO", "CHANGE_DESC_TODO", "TODO", "TBD"}
SPAN_COVERAGE_MIN = 0.25


def sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def active_hashes(root: Path) -> dict[str, str]:
    return {relative: sha256_file(root / relative) for relative in ACTIVE_GOLD_FILES}


def clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def spans_in_box(
    spans: list[dict[str, Any]], page: int, bbox: list[float]
) -> list[dict[str, Any]]:
    x0, y0, x1, y1 = [float(value) for value in bbox]
    findings: list[dict[str, Any]] = []
    for span in spans:
        if int(span.get("page") or 0) != page:
            continue
        text = clean_text(span.get("text"))
        raw = span.get("bbox_px")
        if not text or not isinstance(raw, list) or len(raw) != 4:
            continue
        sx0, sy0, sx1, sy1 = [float(value) for value in raw]
        intersection = max(0.0, min(x1, sx1) - max(x0, sx0)) * max(
            0.0, min(y1, sy1) - max(y0, sy0)
        )
        span_area = max(1.0, (sx1 - sx0) * (sy1 - sy0))
        box_area = max(1.0, (x1 - x0) * (y1 - y0))
        span_coverage = intersection / span_area
        if span_coverage < SPAN_COVERAGE_MIN:
            continue
        findings.append({
            "text": text,
            "bbox_px": raw,
            "span_coverage": round(span_coverage, 6),
            "box_coverage": round(intersection / box_area, 6),
        })
    findings.sort(key=lambda row: (row["bbox_px"][1], row["bbox_px"][0], row["text"]))
    return findings


def joined_text(spans: list[dict[str, Any]]) -> str:
    values: list[str] = []
    for span in spans:
        text = span["text"]
        if text not in values:
            values.append(text)
    return " | ".join(values)


def text_signature(text: str) -> tuple[str, ...]:
    """Compare extracted labels without treating read-order permutations as edits."""
    return tuple(sorted({" ".join(part.split()) for part in text.split(" | ") if part.strip()}))


def proposal(old_text: str, new_text: str) -> tuple[str, str, str, str]:
    if old_text and not new_text:
        return (
            "unclear",
            (
                f"The OLD text layer contains {json.dumps(old_text, ensure_ascii=False)} while the NEW box is empty; "
                "verify whether this is a true deletion or residual crop/alignment drift."
            ),
            "old_only_text",
            "unilateral_text_review_required",
        )
    if new_text and not old_text:
        return (
            "unclear",
            (
                f"The NEW text layer contains {json.dumps(new_text, ensure_ascii=False)} while the OLD box is empty; "
                "verify whether this is a true addition or residual crop/alignment drift."
            ),
            "new_only_text",
            "unilateral_text_review_required",
        )
    if old_text and new_text and text_signature(old_text) != text_signature(new_text):
        return (
            "text_change",
            f"The highlighted engineering text changed from {json.dumps(old_text, ensure_ascii=False)} to {json.dumps(new_text, ensure_ascii=False)}.",
            "different_text",
            "text_grounded_candidate",
        )
    if old_text and new_text:
        return (
            "symbol_component_change",
            "The extracted engineering text is unchanged; inspect the boxed schematic graphics for the actual revision change.",
            "same_text",
            "graphic_review_required",
        )
    return (
        "symbol_component_change",
        "Inspect the boxed schematic symbols, components, and connections to identify the engineering change.",
        "no_text",
        "graphic_review_required",
    )


def expanded_bounds(image: Image.Image, bbox: list[float], margin: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = [float(value) for value in bbox]
    return (
        max(0, math.floor(x0 - margin)),
        max(0, math.floor(y0 - margin)),
        min(image.width, math.ceil(x1 + margin)),
        min(image.height, math.ceil(y1 + margin)),
    )


def crop_panel(
    root: Path,
    image_path: str,
    bbox: list[float],
    label: str,
    *,
    margin: int,
    width: int = 360,
    height: int = 260,
) -> Image.Image:
    with Image.open(root / image_path) as source:
        image = source.convert("RGB")
        bounds = expanded_bounds(image, bbox, margin)
        crop = image.crop(bounds)
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 7), label, fill="black")
    draw.text((8, 24), str([round(float(value), 1) for value in bbox]), fill="#555555")
    available_height = height - 48
    scale = min(width / max(1, crop.width), available_height / max(1, crop.height))
    display = crop.resize(
        (max(1, round(crop.width * scale)), max(1, round(crop.height * scale))),
        Image.Resampling.LANCZOS,
    )
    ox = (width - display.width) // 2
    oy = 48 + (available_height - display.height) // 2
    canvas.paste(display, (ox, oy))
    x0, y0, x1, y1 = [float(value) for value in bbox]
    rx0 = ox + (x0 - bounds[0]) * scale
    ry0 = oy + (y0 - bounds[1]) * scale
    rx1 = ox + (x1 - bounds[0]) * scale
    ry1 = oy + (y1 - bounds[1]) * scale
    draw.rectangle((round(rx0), round(ry0), round(rx1), round(ry1)), outline="#d7191c", width=3)
    return canvas


def render_evidence(
    root: Path,
    output: Path,
    row: dict[str, Any],
    bbox_old_before: list[float],
) -> dict[str, Any]:
    panels = [
        crop_panel(root, row["image_old"], bbox_old_before, "1 OLD original mapping", margin=100),
        crop_panel(root, row["image_old"], row["bbox_old"], "2 OLD corrected region", margin=100),
        crop_panel(root, row["image_new"], row["bbox_new"], "3 NEW corresponding region", margin=100),
        crop_panel(root, row["image_new"], row["bbox_new"], "4 NEW wider context", margin=240),
    ]
    gutter = 8
    combined = Image.new(
        "RGB",
        (sum(panel.width for panel in panels) + gutter * 3, max(panel.height for panel in panels)),
        "#d8dee4",
    )
    x = 0
    for panel in panels:
        combined.paste(panel, (x, 0))
        x += panel.width + gutter
    name = "bbb_placeholder_" + hashlib.sha256(row["pair_id"].encode()).hexdigest()[:16] + ".png"
    path = output / name
    combined.save(path)
    return {"path": str(path.resolve()), "sha256": sha256_file(path)}


def current_geometry_ledger(root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    pointer_path = root / visualdiff_geometry_holds.POINTER
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    report_path = root / str(pointer["report_path"])
    if sha256_file(report_path) != str(pointer["report_sha256"]):
        raise ValueError("current geometry hold pointer is stale")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    ledger_path = root / str(report["hold_ledger"])
    if sha256_file(ledger_path) != str(report["hold_ledger_sha256"]):
        raise ValueError("current geometry hold ledger is stale")
    return (
        {str(row["pair_id"]): row for row in read_jsonl(ledger_path)},
        {
            report_path.relative_to(root).as_posix(): sha256_file(report_path),
            ledger_path.relative_to(root).as_posix(): sha256_file(ledger_path),
        },
    )


def build(root: Path, output_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = root.resolve()
    output_dir = output_dir.resolve()
    if not output_dir.is_relative_to(root / "derived/quality") or output_dir.exists():
        raise ValueError("output must be a new directory under derived/quality")
    before = active_hashes(root)
    provenance = audit_active_gold_provenance.build_report(root)
    paper_ready = {str(row["doc_id"]) for row in provenance["documents"] if row.get("paper_ready")}
    if not {"bbb_schematic_revC", "bbb_schematic_revC3"}.issubset(paper_ready):
        raise ValueError("BBB source documents are not paper-ready")
    manifest_rows = read_jsonl(root / "manifest.jsonl")
    manifest = {
        str(row["pair_id"]): row
        for row in manifest_rows
        if row.get("type") == "pair" and row.get("pair_id")
    }
    documents = {
        str(row["doc_id"]): row
        for row in manifest_rows
        if row.get("type") == "doc" and row.get("doc_id")
    }
    pair_manifest = manifest.get("vdiff__bbb__C__to__C3") or {}
    old_doc = str(pair_manifest.get("from_doc_id") or "")
    new_doc = str(pair_manifest.get("to_doc_id") or "")
    old_pages_dir = str((documents.get(old_doc, {}).get("derived") or {}).get("pages_dir") or "")
    new_pages_dir = str((documents.get(new_doc, {}).get("derived") or {}).get("pages_dir") or "")
    if not old_pages_dir or not new_pages_dir:
        raise ValueError("BBB rendered page directories are missing from manifest")
    old_textlayer_path = root / "derived/textlayer" / f"{old_doc}.jsonl"
    new_textlayer_path = root / "derived/textlayer" / f"{new_doc}.jsonl"
    old_spans = read_jsonl(old_textlayer_path)
    new_spans = read_jsonl(new_textlayer_path)
    geometry_ledger, geometry_hashes = current_geometry_ledger(root)
    held_ids = visualdiff_geometry_holds.current_hold_ids(root)
    placeholders = [
        row
        for row in read_jsonl(root / "visualdiff/annotations/visualdiff_pairs.jsonl")
        if str(row.get("change_desc_gt") or "").strip().upper() in PLACEHOLDERS
    ]
    if not placeholders or any(row.get("project_id") != "vdiff__bbb__C__to__C3" for row in placeholders):
        raise ValueError("active placeholder cohort is missing or mixed")
    if any(str(row["pair_id"]) not in held_ids for row in placeholders):
        raise ValueError("active placeholder row is not protected by geometry hold")

    output_dir.mkdir(parents=True)
    panels_dir = output_dir / "panels"
    panels_dir.mkdir()
    payload_rows: list[dict[str, Any]] = []
    proposal_counts: Counter[str] = Counter()
    for index, row in enumerate(sorted(placeholders, key=lambda item: item["pair_id"]), start=1):
        row = dict(row)
        row["image_old"] = f"{old_pages_dir}/page_{int(row.get('page_index_old') or 0):03d}.png"
        row["image_new"] = f"{new_pages_dir}/page_{int(row.get('page_index_new') or 0):03d}.png"
        if not (root / row["image_old"]).is_file() or not (root / row["image_new"]).is_file():
            raise FileNotFoundError(f"BBB rendered evidence is missing: {row['pair_id']}")
        pair_id = str(row["pair_id"])
        held = geometry_ledger.get(pair_id)
        if held is None:
            raise ValueError(f"placeholder is absent from geometry ledger: {pair_id}")
        if row.get("bbox_old") != held.get("bbox_old_after") or row.get("bbox_new") != held.get("bbox_new"):
            raise ValueError(f"placeholder geometry no longer matches hold ledger: {pair_id}")
        old_hits = spans_in_box(old_spans, int(row.get("page_index_old") or 0), row["bbox_old"])
        new_hits = spans_in_box(new_spans, int(row.get("page_index_new") or 0), row["bbox_new"])
        old_text = joined_text(old_hits)
        new_text = joined_text(new_hits)
        change_type, description, relation, lane = proposal(old_text, new_text)
        proposal_counts[lane] += 1
        rendered = render_evidence(root, panels_dir, row, held["bbox_old_before"])
        payload_rows.append({
            "review_index": index,
            "primary_index": index,
            "original_primary_index": index,
            "task": "visualdiff",
            "record_id": pair_id,
            "pair_id": pair_id,
            "project_id": row["project_id"],
            "source_group": row["project_id"],
            "reserved_split": row.get("split"),
            "capacity_cohort": "active_bbb_placeholder_semantic_hold",
            "evidence_path": rendered["path"],
            "evidence_sha256": rendered["sha256"],
            "machine_change_type_raw": change_type,
            "change_type": change_type,
            "change_description": description,
            "corrected_old_text": old_text,
            "corrected_new_text": new_text,
            "corrected_text_relation": relation,
            "old_text_spans": old_hits,
            "new_text_spans": new_hits,
            "original_reviewer_decision_code": 0,
            "original_reviewer_status": "not_reviewed_placeholder",
            "original_reviewer_basis": "",
            "engineering_required": True,
            "engineering_reason": (
                "当前 Gold 描述仍是占位符。请只比较第2栏校正后的 OLD 与第3栏 NEW，"
                "确认真实工程变化并修正机器建议。"
            ),
            "bbox_old_recommended_from_new": row["bbox_old"],
            "bbox_new_current": row["bbox_new"],
            "homography_path": f"derived/align/{row['project_id']}/H_page_{int(row.get('page_index_new') or 0):03d}.json",
            "homography_inlier_ratio": None,
            "triage_lane": lane,
            "hold_reason": "active_placeholder_and_geometry_semantics_unverified",
            "requires_new_human_review": True,
            "safe_to_merge_gold": False,
        })
    after = active_hashes(root)
    if before != after:
        raise ValueError("active Gold changed while building BBB review payload")
    counts = {
        "total": len(payload_rows),
        "visualdiff": len(payload_rows),
        "engineering_required": len(payload_rows),
        "formal_gate_pass": 5,
        "formal_gate_total": 9,
        "machine_closed_no_more_human": 0,
        "human_confirmed_no_change": 0,
        "machine_confirmed_no_engineering_change": 0,
        "corrected_evidence_rereview": len(payload_rows),
        "text_grounded_proposals": proposal_counts["text_grounded_candidate"],
        "unilateral_text_review_required": proposal_counts["unilateral_text_review_required"],
        "graphic_review_required": proposal_counts["graphic_review_required"],
    }
    payload = {
        "goal": "Gold v2.0 Global",
        "mode": "active_visualdiff_placeholder_semantic_review",
        "instructions_version": "2026-09-08-bbb-placeholder-corrected-evidence-v1",
        "counts": counts,
        "rows": payload_rows,
        "safety": {
            "safe_to_merge_gold": False,
            "active_gold_modified": False,
            "all_rows_geometry_held": True,
            "promotion_requires_human_return_and_strict_release_gates": True,
        },
    }
    report = {
        "goal": payload["goal"],
        "status": "PASS",
        "mode": payload["mode"],
        "counts": counts,
        "proposal_relation_counts": dict(Counter(row["corrected_text_relation"] for row in payload_rows)),
        "identity_checks": {
            "unique_rows": len({row["pair_id"] for row in payload_rows}),
            "geometry_hold_coverage": len(payload_rows),
            "missing_images": 0,
            "missing_panels": 0,
        },
        "input_hashes": {
            "manifest.jsonl": sha256_file(root / "manifest.jsonl"),
            old_textlayer_path.relative_to(root).as_posix(): sha256_file(old_textlayer_path),
            new_textlayer_path.relative_to(root).as_posix(): sha256_file(new_textlayer_path),
            **geometry_hashes,
        },
        "active_gold_hashes_before": before,
        "active_gold_hashes_after": after,
        "active_gold_modified": False,
        "safe_to_merge_gold": False,
        "limitation": (
            "Text-grounded descriptions are review proposals, not automatic Gold. "
            "Graphical rows still require engineering semantic judgment."
        ),
    }
    write_json(output_dir / "payload.json", payload)
    write_json(output_dir / "report.json", report)
    return payload, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    payload, report = build(root, root / args.output_dir)
    print(json.dumps({
        "status": report["status"],
        **report["counts"],
        "active_gold_modified": report["active_gold_modified"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
