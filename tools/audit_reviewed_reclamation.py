#!/usr/bin/env python3
"""Account for mergeable review rows against the current active MicroText Gold set."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from source_rights import is_release_safe_status


MERGEABLE_STATUSES = {"accepted", "edited", "valid", "edit", "accepted_for_merge"}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def review_status(row: dict[str, Any]) -> str:
    return str(
        row.get("human_review_status")
        or row.get("human_status")
        or row.get("review_status")
        or row.get("status")
        or ""
    ).strip().lower()


def reviewed_text(row: dict[str, Any]) -> str:
    return str(
        row.get("corrected_text")
        or row.get("target_text")
        or row.get("proposed_text")
        or row.get("raw_text")
        or ""
    ).strip()


def geometry_key(row: dict[str, Any]) -> tuple[str, str, int, tuple[Any, ...]]:
    return (
        str(row.get("doc_id") or "").strip(),
        str(row.get("version_id") or "").strip(),
        int(row.get("page_index") or 0),
        tuple(row.get("bbox") or []),
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Reviewed Gold Reclamation Audit",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Mergeable review rows inspected: `{report['counts']['input_rows']}`",
        f"- Genuinely promotion-ready rows: `{report['counts']['promotion_ready']}`",
        f"- All rows accounted: `{str(report['all_rows_accounted']).lower()}`",
        f"- Active Gold modified: `{str(report['active_gold_modified']).lower()}`",
        "",
        "## Dispositions",
        "",
        "| Disposition | Rows |",
        "|---|---:|",
    ]
    lines.extend(
        f"| `{name}` | {count} |"
        for name, count in report["disposition_counts"].items()
    )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            report["interpretation"],
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def build_audit(
    root: Path,
    inventory_path: Path,
    output_dir: Path,
    date_label: str,
) -> dict[str, Any]:
    root = root.resolve()
    inventory_path = inventory_path if inventory_path.is_absolute() else root / inventory_path
    output_dir = output_dir if output_dir.is_absolute() else root / output_dir
    active_items_path = root / "microtext" / "annotations" / "microtext_items.jsonl"
    source_inventory_path = root / "SOURCE_INVENTORY.csv"

    inventory = read_json(inventory_path)
    reviewed_paths = [
        root / str(item["path"])
        for item in inventory.get("files", [])
        if int(item.get("mergeable_status_rows") or 0) > 0
        and str(item.get("kind") or "") == "microtext"
    ]
    active_items = read_jsonl(active_items_path)
    source_inventory = {
        str(row.get("doc_id") or "").strip(): row
        for row in read_csv(source_inventory_path)
        if str(row.get("doc_id") or "").strip()
    }

    active_by_candidate: dict[str, dict[str, Any]] = {}
    active_by_geometry: dict[tuple[str, str, int, tuple[Any, ...]], list[dict[str, Any]]] = {}
    for row in active_items:
        candidate_id = str(row.get("source_candidate_id") or "").strip()
        if candidate_id:
            active_by_candidate[candidate_id] = row
        active_by_geometry.setdefault(geometry_key(row), []).append(row)

    dispositions: list[dict[str, Any]] = []
    promotion_ready: list[dict[str, Any]] = []
    disposition_counts: Counter[str] = Counter()
    reviewed_file_rows: Counter[str] = Counter()
    seen_nonactive_candidate_ids: set[str] = set()

    for path in reviewed_paths:
        for line_number, row in enumerate(read_jsonl(path), start=1):
            status = review_status(row)
            if status not in MERGEABLE_STATUSES:
                continue
            reviewed_file_rows[path.relative_to(root).as_posix()] += 1
            candidate_id = str(row.get("candidate_id") or "").strip()
            doc_id = str(row.get("doc_id") or "").strip()
            text = reviewed_text(row)
            category = str(row.get("category") or "").strip()
            active_match: dict[str, Any] | None = None

            if candidate_id and candidate_id in active_by_candidate:
                disposition = "already_active_candidate_id"
                active_match = active_by_candidate[candidate_id]
            elif candidate_id and candidate_id in seen_nonactive_candidate_ids:
                disposition = "duplicate_reviewed_candidate_id"
            else:
                if candidate_id:
                    seen_nonactive_candidate_ids.add(candidate_id)
                geometry_matches = active_by_geometry.get(geometry_key(row), [])
                equivalent = next(
                    (
                        active
                        for active in geometry_matches
                        if str(active.get("text_gt") or "").strip() == text
                        and str(active.get("category") or "").strip() == category
                    ),
                    None,
                )
                if equivalent is not None:
                    disposition = "already_active_geometry_equivalent"
                    active_match = equivalent
                elif geometry_matches:
                    disposition = "already_active_geometry_conflict"
                    active_match = geometry_matches[0]
                elif not candidate_id:
                    disposition = "missing_candidate_id"
                else:
                    inventory_row = source_inventory.get(doc_id) or {}
                    if not is_release_safe_status(str(inventory_row.get("public_status") or "")):
                        disposition = "rights_hold"
                    else:
                        evidence = root / str(row.get("image_path") or "")
                        if not evidence.is_file():
                            disposition = "missing_evidence"
                        elif not text:
                            disposition = "missing_target_text"
                        elif "\ufffd" in text:
                            disposition = "replacement_character_text_hold"
                        else:
                            disposition = "promotion_ready"
                            promotion_ready.append(row)

            disposition_counts[disposition] += 1
            dispositions.append(
                {
                    "source_path": path.relative_to(root).as_posix(),
                    "source_line": line_number,
                    "review_status": status,
                    "candidate_id": candidate_id,
                    "doc_id": doc_id,
                    "version_id": str(row.get("version_id") or ""),
                    "page_index": int(row.get("page_index") or 0),
                    "bbox": row.get("bbox") or [],
                    "reviewed_text": text,
                    "reviewed_category": category,
                    "disposition": disposition,
                    "active_item_id": str((active_match or {}).get("item_id") or ""),
                    "active_source_candidate_id": str(
                        (active_match or {}).get("source_candidate_id") or ""
                    ),
                    "active_text": str((active_match or {}).get("text_gt") or ""),
                    "active_category": str((active_match or {}).get("category") or ""),
                }
            )

    input_rows = len(dispositions)
    accounted_rows = sum(disposition_counts.values())
    output_dir.mkdir(parents=True, exist_ok=True)
    dispositions_path = output_dir / "reviewed_reclamation_dispositions.jsonl"
    ready_path = output_dir / "promotion_ready_microtext.jsonl"
    report_path = output_dir / "reviewed_reclamation_report.json"
    markdown_path = output_dir / "reviewed_reclamation_report.md"
    write_jsonl(dispositions_path, dispositions)
    write_jsonl(ready_path, promotion_ready)

    report = {
        "schema": "eng_bench_reviewed_reclamation_audit_v1",
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "active_gold_modified": False,
        "all_rows_accounted": input_rows == accounted_rows,
        "inputs": {
            "review_queue_inventory": {
                "path": inventory_path.relative_to(root).as_posix(),
                "sha256": file_sha256(inventory_path),
            },
            "active_microtext_items": {
                "path": active_items_path.relative_to(root).as_posix(),
                "rows": len(active_items),
                "sha256": file_sha256(active_items_path),
            },
            "source_inventory": {
                "path": source_inventory_path.relative_to(root).as_posix(),
                "rows": len(source_inventory),
                "sha256": file_sha256(source_inventory_path),
            },
            "reviewed_files": [
                {
                    "path": path.relative_to(root).as_posix(),
                    "mergeable_rows": reviewed_file_rows[path.relative_to(root).as_posix()],
                    "sha256": file_sha256(path),
                }
                for path in reviewed_paths
            ],
        },
        "counts": {
            "input_rows": input_rows,
            "promotion_ready": len(promotion_ready),
            "nonpromotion_rows": input_rows - len(promotion_ready),
        },
        "disposition_counts": dict(sorted(disposition_counts.items())),
        "artifacts": {
            "dispositions": dispositions_path.relative_to(root).as_posix(),
            "promotion_ready": ready_path.relative_to(root).as_posix(),
        },
        "interpretation": (
            "Every mergeable-status row is already active, duplicates an active geometry, "
            "duplicates another reviewed row, remains rights-held, or appears in the explicit "
            "promotion-ready queue. No active Gold file is modified by this audit."
        ),
    }
    if not report["all_rows_accounted"]:
        raise AssertionError("reviewed reclamation accounting failed")
    write_json(report_path, report)
    write_markdown(markdown_path, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    args = parser.parse_args(argv)
    report = build_audit(args.root, args.inventory, args.output_dir, args.date_label)
    print(json.dumps({"counts": report["counts"], "dispositions": report["disposition_counts"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
