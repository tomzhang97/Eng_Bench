#!/usr/bin/env python3
"""Find reviewed Eng_Bench rows that are mergeable but not active gold."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


MICROTEXT_MERGEABLE = {"accepted", "edited"}
VISUALDIFF_MERGEABLE = {"accepted", "edited", "valid", "edit"}
TOTAL_KEYS = (
    "mergeable_review_rows",
    "already_active_by_candidate_id",
    "already_active_by_pair_id",
    "already_active_by_region",
    "blocked_missing_evidence",
    "unmerged_mergeable",
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def status_for(row: dict[str, Any]) -> str:
    for key in (
        "human_review_status",
        "human_status",
        "review_status",
        "annotation_status",
        "status",
    ):
        value = str(row.get(key) or "").strip().lower()
        if value:
            return value
    return ""


def bbox_key(row: dict[str, Any], bbox_field: str = "bbox") -> tuple[int, int, int, int]:
    bbox = row.get(bbox_field) or []
    if not isinstance(bbox, list) or len(bbox) != 4:
        return (0, 0, 0, 0)
    return tuple(int(round(float(value))) for value in bbox)


def micro_region_key(row: dict[str, Any]) -> tuple[str, str, int, tuple[int, int, int, int]]:
    return (
        str(row.get("doc_id") or ""),
        str(row.get("version_id") or "unknown"),
        int(row.get("page_index") or 0),
        bbox_key(row),
    )


def visual_region_key(row: dict[str, Any]) -> tuple[str, int, int, tuple[int, int, int, int], tuple[int, int, int, int]]:
    return (
        str(row.get("project_id") or ""),
        int(row.get("page_index_old") or row.get("page_old") or 0),
        int(row.get("page_index_new") or row.get("page_new") or 0),
        bbox_key(row, "bbox_old"),
        bbox_key(row, "bbox_new"),
    )


def path_fields(row: dict[str, Any]) -> list[str]:
    paths = []
    for key, value in row.items():
        if not isinstance(value, str) or not value.strip():
            continue
        if key.endswith("_path") or key in {"image_path", "image_old", "image_new"}:
            paths.append(value)
    return paths


def missing_paths(root: Path, row: dict[str, Any]) -> list[str]:
    missing = []
    for value in path_fields(row):
        path = Path(value)
        full_path = path if path.is_absolute() else root / path
        if not full_path.exists():
            missing.append(value)
    return missing


def active_microtext_keys(root: Path) -> tuple[set[str], set[tuple[str, str, int, tuple[int, int, int, int]]]]:
    rows = read_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl")
    candidate_ids = {str(row.get("source_candidate_id")) for row in rows if row.get("source_candidate_id")}
    regions = {micro_region_key(row) for row in rows}
    return candidate_ids, regions


def active_visualdiff_keys(root: Path) -> tuple[set[str], set[tuple[str, int, int, tuple[int, int, int, int], tuple[int, int, int, int]]]]:
    rows = read_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl")
    pair_ids = {str(row.get("pair_id") or row.get("id")) for row in rows if row.get("pair_id") or row.get("id")}
    regions = {visual_region_key(row) for row in rows}
    return pair_ids, regions


def audit_microtext_file(
    root: Path,
    path: Path,
    active_candidate_ids: set[str],
    active_regions: set[tuple[str, str, int, tuple[int, int, int, int]]],
) -> dict[str, Any]:
    rows = read_jsonl(path)
    counts: Counter[str] = Counter()
    examples: dict[str, list[dict[str, Any]]] = {
        "unmerged_mergeable": [],
        "blocked_missing_evidence": [],
    }
    for row in rows:
        status = status_for(row)
        if status not in MICROTEXT_MERGEABLE:
            continue
        counts["mergeable_review_rows"] += 1
        candidate_id = str(row.get("candidate_id") or "")
        if candidate_id and candidate_id in active_candidate_ids:
            counts["already_active_by_candidate_id"] += 1
            continue
        if micro_region_key(row) in active_regions:
            counts["already_active_by_region"] += 1
            continue
        missing = missing_paths(root, row)
        if missing:
            counts["blocked_missing_evidence"] += 1
            if len(examples["blocked_missing_evidence"]) < 10:
                examples["blocked_missing_evidence"].append(
                    {"id": candidate_id, "path": path.relative_to(root).as_posix(), "missing": missing}
                )
            continue
        counts["unmerged_mergeable"] += 1
        if len(examples["unmerged_mergeable"]) < 10:
            examples["unmerged_mergeable"].append(
                {
                    "id": candidate_id,
                    "path": path.relative_to(root).as_posix(),
                    "doc_id": row.get("doc_id"),
                    "category": row.get("category"),
                    "status": status,
                }
            )
    return {
        "path": path.relative_to(root).as_posix(),
        "kind": "microtext",
        **dict(counts),
        "examples": examples,
    }


def audit_visualdiff_file(
    root: Path,
    path: Path,
    active_pair_ids: set[str],
    active_regions: set[tuple[str, int, int, tuple[int, int, int, int], tuple[int, int, int, int]]],
) -> dict[str, Any]:
    rows = read_jsonl(path)
    counts: Counter[str] = Counter()
    examples: dict[str, list[dict[str, Any]]] = {
        "unmerged_mergeable": [],
        "blocked_missing_evidence": [],
    }
    for row in rows:
        status = status_for(row)
        if status not in VISUALDIFF_MERGEABLE:
            continue
        counts["mergeable_review_rows"] += 1
        pair_id = str(row.get("pair_id") or row.get("id") or "")
        if pair_id and pair_id in active_pair_ids:
            counts["already_active_by_pair_id"] += 1
            continue
        if visual_region_key(row) in active_regions:
            counts["already_active_by_region"] += 1
            continue
        missing = missing_paths(root, row)
        if missing:
            counts["blocked_missing_evidence"] += 1
            if len(examples["blocked_missing_evidence"]) < 10:
                examples["blocked_missing_evidence"].append(
                    {"id": pair_id, "path": path.relative_to(root).as_posix(), "missing": missing}
                )
            continue
        counts["unmerged_mergeable"] += 1
        if len(examples["unmerged_mergeable"]) < 10:
            examples["unmerged_mergeable"].append(
                {
                    "id": pair_id,
                    "path": path.relative_to(root).as_posix(),
                    "project_id": row.get("project_id"),
                    "status": status,
                }
            )
    return {
        "path": path.relative_to(root).as_posix(),
        "kind": "visualdiff",
        **dict(counts),
        "examples": examples,
    }


def unique_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(path)
    return result


def audit(
    root: Path,
    extra_microtext_files: list[Path] | None = None,
    extra_visualdiff_files: list[Path] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    micro_candidate_ids, micro_regions = active_microtext_keys(root)
    visual_pair_ids, visual_regions = active_visualdiff_keys(root)
    files = []
    microtext_paths = list(sorted((root / "microtext" / "annotations").glob("microtext_review*.jsonl")))
    visualdiff_paths = list(sorted((root / "visualdiff" / "annotations").glob("visualdiff_review*.jsonl")))
    microtext_paths.extend(extra_microtext_files or [])
    visualdiff_paths.extend(extra_visualdiff_files or [])
    for path in unique_paths(microtext_paths):
        files.append(audit_microtext_file(root, path, micro_candidate_ids, micro_regions))
    for path in unique_paths(visualdiff_paths):
        files.append(audit_visualdiff_file(root, path, visual_pair_ids, visual_regions))

    totals: Counter[str] = Counter()
    for item in files:
        for key, value in item.items():
            if key in {"path", "kind", "examples"}:
                continue
            totals[key] += int(value)
    for key in TOTAL_KEYS:
        totals.setdefault(key, 0)
    priority = [
        item
        for item in files
        if int(item.get("unmerged_mergeable", 0)) > 0 and int(item.get("blocked_missing_evidence", 0)) == 0
    ]
    priority.sort(key=lambda item: int(item.get("unmerged_mergeable", 0)), reverse=True)
    return {
        "extra_inputs": {
            "microtext": [path.relative_to(root).as_posix() for path in (extra_microtext_files or [])],
            "visualdiff": [path.relative_to(root).as_posix() for path in (extra_visualdiff_files or [])],
        },
        "totals": dict(sorted(totals.items())),
        "priority_unmerged_files": priority[:20],
        "files": files,
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Unmerged Reviewed Rows Audit",
        "",
        f"- Mergeable review rows: `{totals.get('mergeable_review_rows', 0)}`",
        f"- Already active by candidate/pair ID: `{totals.get('already_active_by_candidate_id', 0) + totals.get('already_active_by_pair_id', 0)}`",
        f"- Already active by region: `{totals.get('already_active_by_region', 0)}`",
        f"- Blocked by missing evidence: `{totals.get('blocked_missing_evidence', 0)}`",
        f"- Unmerged mergeable rows: `{totals.get('unmerged_mergeable', 0)}`",
        "",
        "## Priority Files",
        "",
        "| File | Kind | Unmerged Mergeable | Blocked Missing Evidence |",
        "| --- | --- | ---: | ---: |",
    ]
    for item in report["priority_unmerged_files"]:
        lines.append(
            f"| `{Path(item['path']).name}` | {item['kind']} | "
            f"{item.get('unmerged_mergeable', 0)} | {item.get('blocked_missing_evidence', 0)} |"
        )
    lines.extend(["", "## Notes", ""])
    lines.append(
        "This audit is read-only. Any listed unmerged rows still need maintainer inspection before merge, especially if the source has rights or split-policy concerns."
    )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit mergeable reviewed rows not present in active gold.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--output-json", default="derived/quality/unmerged_reviewed_rows_2026-06-02.json")
    parser.add_argument("--output-md", default="derived/quality/unmerged_reviewed_rows_2026-06-02.md")
    parser.add_argument("--extra-microtext", action="append", default=[])
    parser.add_argument("--extra-visualdiff", action="append", default=[])
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    resolve = lambda value: Path(value) if Path(value).is_absolute() else root / value
    report = audit(
        root,
        extra_microtext_files=[resolve(value) for value in args.extra_microtext],
        extra_visualdiff_files=[resolve(value) for value in args.extra_visualdiff],
    )
    write_json(root / args.output_json, report)
    md_path = root / args.output_md
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"[OK] Wrote {root / args.output_json}")
    print(f"[OK] Wrote {md_path}")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
