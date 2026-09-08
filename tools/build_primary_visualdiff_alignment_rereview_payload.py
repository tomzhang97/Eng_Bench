#!/usr/bin/env python3
"""Build the fail-closed primary-review payload for corrected VisualDiff evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ACTIVE_GOLD_FILES = (
    "eng_bench.jsonl",
    "manifest.jsonl",
    "microtext/annotations/microtext_items.jsonl",
    "microtext/annotations/microtext_questions.jsonl",
    "visualdiff/annotations/visualdiff_pairs.jsonl",
    "visualdiff/annotations/visualdiff_questions.jsonl",
)

TYPE_ALIASES = {
    "addition": "addition",
    "addition+text": "addition",
    "connection": "connection_wiring_change",
    "connection_change": "connection_wiring_change",
    "connection_wiring_change": "connection_wiring_change",
    "deletion": "deletion",
    "deletion+text": "deletion",
    "dimension": "dimension_change",
    "dimension_change": "dimension_change",
    "geometry": "geometry_change",
    "geometry_change": "geometry_change",
    "layout": "layout_only_no_change",
    "layout_only_no_change": "layout_only_no_change",
    "symbol": "symbol_component_change",
    "symbol_component_change": "symbol_component_change",
    "text": "text_change",
    "text_change": "text_change",
    "text_change_candidate": "text_change",
    "unclear": "unclear",
    "wiring": "connection_wiring_change",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"expected object at {path}:{line_number}")
            rows.append(row)
    return rows


def active_gold_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in ACTIVE_GOLD_FILES:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"missing active Gold file: {path}")
        hashes[relative] = sha256_file(path)
    return hashes


def resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def display_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def normalized_change_type(value: object) -> str:
    raw = str(value or "").strip().lower()
    return TYPE_ALIASES.get(raw, "unclear")


def joined_text(value: object) -> str:
    if isinstance(value, list):
        return " | ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def build_payload(
    root: Path,
    triage_path: Path,
    primary_payload_path: Path,
    expected_rows: int | None = 254,
) -> tuple[dict[str, Any], dict[str, Any]]:
    root = root.resolve()
    triage_path = resolve_path(root, triage_path).resolve()
    primary_payload_path = resolve_path(root, primary_payload_path).resolve()
    before = active_gold_hashes(root)

    triage_rows = read_jsonl(triage_path)
    primary_payload = json.loads(primary_payload_path.read_text(encoding="utf-8"))
    primary_rows = primary_payload.get("rows")
    if not isinstance(primary_rows, list):
        raise ValueError("primary payload must contain a rows list")

    source_by_id: dict[str, dict[str, Any]] = {}
    for row in primary_rows:
        record_id = str(row.get("record_id") or row.get("pair_id") or "").strip()
        if not record_id:
            raise ValueError("primary payload row has no record identity")
        if record_id in source_by_id:
            raise ValueError(f"duplicate primary payload identity: {record_id}")
        source_by_id[record_id] = row

    if expected_rows is not None and len(triage_rows) != expected_rows:
        raise ValueError(
            f"expected {expected_rows} corrected-evidence rows, found {len(triage_rows)}"
        )

    output_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for triage in triage_rows:
        pair_id = str(triage.get("pair_id") or "").strip()
        if not pair_id:
            raise ValueError("triage row has no pair_id")
        if pair_id in seen:
            raise ValueError(f"duplicate triage identity: {pair_id}")
        seen.add(pair_id)
        source = source_by_id.get(pair_id)
        if source is None:
            raise ValueError(f"triage identity is absent from primary payload: {pair_id}")
        if triage.get("triage_lane") != "rerender_aligned_evidence_then_human_rereview":
            raise ValueError(f"unexpected triage lane for {pair_id}: {triage.get('triage_lane')}")
        if not triage.get("requires_new_human_review"):
            raise ValueError(f"row is not marked for new human review: {pair_id}")

        panel_path = resolve_path(root, str(triage.get("inspection_panel") or "")).resolve()
        if not panel_path.is_file():
            raise FileNotFoundError(f"missing corrected evidence panel: {panel_path}")
        actual_panel_hash = sha256_file(panel_path)
        expected_panel_hash = str(triage.get("inspection_panel_sha256") or "").lower()
        if expected_panel_hash and actual_panel_hash != expected_panel_hash:
            raise ValueError(f"corrected evidence hash mismatch: {pair_id}")

        triage_index = int(triage.get("primary_index"))
        source_index = int(source.get("primary_index"))
        if triage_index != source_index:
            raise ValueError(
                f"primary index mismatch for {pair_id}: {triage_index} != {source_index}"
            )

        machine_type_raw = str(source.get("change_type") or "unclear").strip()
        output_rows.append(
            {
                "review_index": 0,
                "primary_index": source_index,
                "original_primary_index": source_index,
                "task": "visualdiff",
                "record_id": pair_id,
                "pair_id": pair_id,
                "project_id": str(triage.get("project_id") or source.get("project_id") or ""),
                "source_group": str(source.get("source_group") or triage.get("project_id") or ""),
                "reserved_split": str(source.get("reserved_split") or triage.get("reserved_split") or ""),
                "capacity_cohort": str(source.get("capacity_cohort") or ""),
                "evidence_path": str(panel_path),
                "evidence_sha256": actual_panel_hash,
                "machine_change_type_raw": machine_type_raw,
                "change_type": normalized_change_type(machine_type_raw),
                "change_description": str(source.get("change_description") or "").strip(),
                "corrected_old_text": joined_text(triage.get("corrected_old_text")),
                "corrected_new_text": joined_text(triage.get("corrected_new_text")),
                "corrected_text_relation": str(triage.get("corrected_text_relation") or ""),
                "original_reviewer_decision_code": int(triage.get("reviewer_decision_code") or 0),
                "original_reviewer_status": str(triage.get("reviewer_status") or ""),
                "original_reviewer_basis": str(triage.get("reviewer_basis") or "").strip(),
                "engineering_required": True,
                "engineering_reason": (
                    "原证据存在跨位置或红框错位风险。请以四联图第2栏 OLD aligned at NEW "
                    "和第3栏 NEW crop 为主，判断同一坐标是否有真实工程语义变化。"
                ),
                "bbox_old_recommended_from_new": triage.get("bbox_old_recommended_from_new"),
                "bbox_new_current": triage.get("bbox_new_current"),
                "homography_path": str(triage.get("homography_path") or ""),
                "homography_inlier_ratio": triage.get("homography_inlier_ratio"),
                "triage_lane": str(triage.get("triage_lane") or ""),
                "hold_reason": str(triage.get("hold_reason") or ""),
                "requires_new_human_review": True,
                "safe_to_merge_gold": False,
            }
        )

    output_rows.sort(key=lambda row: (row["primary_index"], row["record_id"]))
    for index, row in enumerate(output_rows, start=1):
        row["review_index"] = index

    after = active_gold_hashes(root)
    if before != after:
        raise RuntimeError("active Gold hashes changed while building the rereview payload")

    counts = {
        "total": len(output_rows),
        "visualdiff": len(output_rows),
        "engineering_required": len(output_rows),
        "formal_gate_pass": 5,
        "formal_gate_total": 9,
        "machine_closed_no_more_human": 424,
        "human_confirmed_no_change": 306,
        "machine_confirmed_no_engineering_change": 118,
        "corrected_evidence_rereview": len(output_rows),
    }
    payload = {
        "goal": "Gold v2.0 Global",
        "mode": "corrected_visualdiff_evidence_primary_rereview",
        "instructions_version": "2026-09-08-aligned-evidence-v1",
        "counts": counts,
        "rows": output_rows,
        "safety": {
            "safe_to_merge_gold": False,
            "active_gold_modified": False,
            "promotion_requires_return_processing_and_release_gates": True,
        },
    }
    report = {
        "status": "PASS",
        "goal": payload["goal"],
        "mode": payload["mode"],
        "counts": counts,
        "inputs": {
            "triage": display_path(root, triage_path),
            "triage_sha256": sha256_file(triage_path),
            "primary_payload": display_path(root, primary_payload_path),
            "primary_payload_sha256": sha256_file(primary_payload_path),
        },
        "identity_checks": {
            "unique_pair_ids": len(seen),
            "missing_primary_rows": 0,
            "missing_evidence_panels": 0,
            "evidence_hash_mismatches": 0,
        },
        "active_gold_hashes_before": before,
        "active_gold_hashes_after": after,
        "active_gold_modified": False,
        "safe_to_merge_gold": False,
    }
    return payload, report


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--triage", type=Path, required=True)
    parser.add_argument("--primary-payload", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-rows", type=int, default=254)
    args = parser.parse_args()

    root = args.root.resolve()
    payload, report = build_payload(
        root,
        args.triage,
        args.primary_payload,
        expected_rows=args.expected_rows,
    )
    output_path = resolve_path(root, args.output)
    report_path = resolve_path(root, args.report)
    write_json(output_path, payload)
    report["artifacts"] = {
        "payload": display_path(root, output_path),
        "payload_sha256": sha256_file(output_path),
        "report": display_path(root, report_path),
    }
    write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
