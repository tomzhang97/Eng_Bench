#!/usr/bin/env python3
"""Replace duplicate physical regions in a primary human-review pool."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_staged_v2_capacity as staged_capacity
from audit_active_gold_provenance import file_sha256
from payload_alias_regions import load_payload_alias_map, payload_alias_region_key
from prepare_incremental_human_audit_round import (
    evidence_materializable,
    identifier,
    source_group,
    split_name,
    task,
)


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def display_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def load_auditor_overlap_ids(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    ids: set[str] = set()
    for row in payload.get("rows", []):
        if not row.get("auditor_overlap"):
            continue
        value = str(
            row.get("record_id") or row.get("candidate_id") or row.get("pair_id") or ""
        ).strip()
        if value:
            ids.add(value)
    for auditor in payload.get("auditors", []):
        for row in auditor.get("rows", []):
            value = str(
                row.get("record_id")
                or row.get("candidate_id")
                or row.get("pair_id")
                or ""
            ).strip()
            if value:
                ids.add(value)
    return ids


def assignment_index(row: dict[str, Any]) -> int:
    return int(row.get("primary_pool_index") or row.get("primary_index") or 0)


def category(row: dict[str, Any]) -> str:
    if task(row) == "visualdiff":
        return str(row.get("change_type") or "visual").strip()
    return str(row.get("category") or "unknown_microtext").strip()


def assignment_shape(row: dict[str, Any]) -> tuple[str, str, str]:
    return task(row), category(row), split_name(row)


class RegionRegistry:
    def __init__(self, root: Path, alias_map: dict[str, str]) -> None:
        self.root = root
        self.alias_map = alias_map
        self.capacity: dict[str, str] = {}
        self.aliases: dict[str, str] = {}
        self.geometry: dict[
            tuple[str, int], list[tuple[tuple[int, int, int, int], str]]
        ] = defaultdict(list)
        self.payload_alias_geometry: dict[
            tuple[str, int], list[tuple[tuple[int, int, int, int], str]]
        ] = defaultdict(list)

    def _payload_alias_geometry(
        self, row: dict[str, Any]
    ) -> tuple[tuple[str, int], tuple[int, int, int, int]] | None:
        geometry = staged_capacity.microtext_region_geometry(row)
        if not geometry or geometry[0] not in self.alias_map:
            return None
        normalized = payload_alias_region_key(
            row,
            geometry,
            root=self.root,
            alias_map=self.alias_map,
        )
        if not normalized:
            return None
        canonical_doc_id, page_index, bbox = normalized
        return (canonical_doc_id, page_index), bbox

    def unresolved_payload_alias_region(self, row: dict[str, Any]) -> bool:
        geometry = staged_capacity.microtext_region_geometry(row)
        return bool(
            geometry
            and geometry[0] in self.alias_map
            and not self._payload_alias_geometry(row)
        )

    def collision(self, row: dict[str, Any]) -> tuple[str, str] | None:
        identity = staged_capacity.capacity_identity(row)
        if identity and identity in self.capacity:
            return "capacity_identity", self.capacity[identity]
        for alias in staged_capacity.identity_aliases(row):
            key = f"{task(row)}:{alias}"
            if key in self.aliases:
                return "row_alias", self.aliases[key]

        geometry = staged_capacity.microtext_region_geometry(row)
        if geometry:
            doc_id, page_index, bbox = geometry
            for existing_bbox, origin in self.geometry.get((doc_id, page_index), []):
                if staged_capacity.bboxes_are_near_duplicates(bbox, existing_bbox):
                    return "near_region", origin

        payload_alias_geometry = self._payload_alias_geometry(row)
        if payload_alias_geometry:
            page_key, bbox = payload_alias_geometry
            for existing_bbox, origin in self.payload_alias_geometry.get(page_key, []):
                if staged_capacity.bboxes_are_near_duplicates(bbox, existing_bbox):
                    return "payload_alias_region", origin
        return None

    def add(self, row: dict[str, Any], origin: str) -> None:
        identity = staged_capacity.capacity_identity(row)
        if identity:
            self.capacity[identity] = origin
        for alias in staged_capacity.identity_aliases(row):
            self.aliases[f"{task(row)}:{alias}"] = origin
        geometry = staged_capacity.microtext_region_geometry(row)
        if geometry:
            doc_id, page_index, bbox = geometry
            self.geometry[(doc_id, page_index)].append((bbox, origin))
        payload_alias_geometry = self._payload_alias_geometry(row)
        if payload_alias_geometry:
            page_key, bbox = payload_alias_geometry
            self.payload_alias_geometry[page_key].append((bbox, origin))


def active_rows(root: Path) -> list[dict[str, Any]]:
    return staged_capacity.read_rows(
        root / "microtext" / "annotations" / "microtext_items.jsonl"
    ) + staged_capacity.read_rows(
        root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
    )


def copied_assignment_metadata(
    replacement: dict[str, Any],
    old: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    result = dict(replacement)
    old_id = identifier(old)
    index = assignment_index(old)
    result.update(
        {
            "primary_pool_index": index,
            "primary_index": index,
            "primary_pool_status": "assigned_to_primary_intern",
            "primary_pool_target": 1500,
            "primary_pool_capacity_cohort": "payload_alias_repair",
            "primary_pool_capacity_path": str(
                replacement.get("canonical_capacity_origin_path")
                or replacement.get("primary_pool_capacity_path")
                or ""
            ),
            "packet_review_status": "not_started",
            "review_status": "needs_review",
            "promotion_state": "human_review_required",
            "safe_to_merge_gold": False,
            "reserved_split": split_name(old),
            "primary_pool_replaces_id": old_id,
            "primary_pool_replacement_reason": reason,
        }
    )
    if old.get("assignment_workbook"):
        result["assignment_workbook"] = old["assignment_workbook"]
    return result


def validate_repaired_pool(
    root: Path,
    rows: list[dict[str, Any]],
    *,
    alias_map: dict[str, str],
    auditor_ids: set[str],
    expected_shapes: Counter[tuple[str, str, str]],
) -> list[str]:
    issues: list[str] = []
    indices = [assignment_index(row) for row in rows]
    if len(rows) != len(set(indices)) or sorted(indices) != list(range(1, len(rows) + 1)):
        issues.append("primary_indices_not_contiguous_unique")
    ids = [identifier(row) for row in rows]
    if not all(ids) or len(ids) != len(set(ids)):
        issues.append("record_ids_not_unique")
    if Counter(assignment_shape(row) for row in rows) != expected_shapes:
        issues.append("task_category_split_shape_changed")
    missing_auditor_ids = sorted(auditor_ids - set(ids))
    if missing_auditor_ids:
        issues.append(f"missing_auditor_overlap_ids:{len(missing_auditor_ids)}")

    registry = RegionRegistry(root, alias_map)
    for row in active_rows(root):
        registry.add(row, f"active:{identifier(row)}")
    for row in sorted(rows, key=assignment_index):
        record_id = identifier(row)
        if registry.unresolved_payload_alias_region(row):
            issues.append(f"unresolved_payload_alias_region:{record_id}")
            continue
        collision = registry.collision(row)
        if collision:
            issues.append(
                f"collision:{record_id}:{collision[0]}:{collision[1]}"
            )
            continue
        registry.add(row, f"primary:{record_id}")
    return issues


def repair_pool(
    root: Path,
    *,
    pool_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    alias_map: dict[str, str],
    auditor_ids: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    expected_shapes = Counter(assignment_shape(row) for row in pool_rows)
    registry = RegionRegistry(root, alias_map)
    for row in active_rows(root):
        registry.add(row, f"active:{identifier(row)}")

    kept: dict[int, dict[str, Any]] = {}
    replaced: dict[int, dict[str, Any]] = {}
    ordered = sorted(
        pool_rows,
        key=lambda row: (
            identifier(row) not in auditor_ids,
            assignment_index(row),
        ),
    )
    for row in ordered:
        index = assignment_index(row)
        record_id = identifier(row)
        if registry.unresolved_payload_alias_region(row):
            replaced[index] = {
                "row": row,
                "reason": "payload_alias_region_unresolved",
                "collision_origin": "",
            }
            continue
        collision = registry.collision(row)
        if collision:
            replaced[index] = {
                "row": row,
                "reason": collision[0],
                "collision_origin": collision[1],
            }
            continue
        kept[index] = row
        registry.add(row, f"primary:{record_id}")

    candidate_buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in candidate_rows:
        if staged_capacity.capacity_exclusion_reason(row):
            continue
        if not evidence_materializable(root, row):
            continue
        candidate_buckets[assignment_shape(row)].append(row)
    for rows in candidate_buckets.values():
        rows.sort(key=lambda row: (source_group(row), identifier(row)))

    selected_source_counts: Counter[str] = Counter(source_group(row) for row in kept.values())
    replacement_ledger: list[dict[str, Any]] = []
    used_candidate_ids: set[str] = set()
    for index in sorted(replaced):
        old = replaced[index]["row"]
        shape = assignment_shape(old)
        options = sorted(
            candidate_buckets.get(shape, []),
            key=lambda row: (
                selected_source_counts[source_group(row)],
                source_group(row),
                identifier(row),
            ),
        )
        chosen: dict[str, Any] | None = None
        for candidate in options:
            candidate_id = identifier(candidate)
            if not candidate_id or candidate_id in used_candidate_ids:
                continue
            if registry.unresolved_payload_alias_region(candidate):
                continue
            if registry.collision(candidate):
                continue
            chosen = candidate
            break
        if chosen is None:
            raise ValueError(
                f"no clean replacement for primary index {index} with shape {shape}"
            )
        repaired = copied_assignment_metadata(
            chosen,
            old,
            reason=replaced[index]["reason"],
        )
        kept[index] = repaired
        chosen_id = identifier(chosen)
        used_candidate_ids.add(chosen_id)
        selected_source_counts[source_group(chosen)] += 1
        registry.add(repaired, f"primary:{chosen_id}")
        replacement_ledger.append(
            {
                "primary_pool_index": index,
                "old_record_id": identifier(old),
                "new_record_id": chosen_id,
                "task": task(old),
                "category": category(old),
                "reserved_split": split_name(old),
                "reason": replaced[index]["reason"],
                "collision_origin": replaced[index]["collision_origin"],
                "auditor_overlap_preserved": identifier(old) in auditor_ids,
                "replacement_source_group": source_group(chosen),
            }
        )

    repaired_rows = [kept[index] for index in sorted(kept)]
    issues = validate_repaired_pool(
        root,
        repaired_rows,
        alias_map=alias_map,
        auditor_ids=auditor_ids,
        expected_shapes=expected_shapes,
    )
    report = {
        "goal": "Gold v2.0 Global",
        "input_rows": len(pool_rows),
        "output_rows": len(repaired_rows),
        "replaced_rows": len(replacement_ledger),
        "replacement_reason_counts": dict(
            sorted(Counter(row["reason"] for row in replacement_ledger).items())
        ),
        "auditor_overlap_ids": len(auditor_ids),
        "auditor_overlap_preserved": len(auditor_ids & {identifier(row) for row in repaired_rows}),
        "task_counts": dict(sorted(Counter(task(row) for row in repaired_rows).items())),
        "split_counts": dict(sorted(Counter(split_name(row) for row in repaired_rows).items())),
        "category_counts": dict(sorted(Counter(category(row) for row in repaired_rows).items())),
        "replacements": replacement_ledger,
        "issues": issues,
        "active_gold_modified": False,
        "valid": not issues and len(repaired_rows) == len(pool_rows),
    }
    return repaired_rows, report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Primary Pool Payload-Alias Repair",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Input rows: `{report['input_rows']}`",
        f"- Output rows: `{report['output_rows']}`",
        f"- Replaced duplicate regions: `{report['replaced_rows']}`",
        f"- Auditor-overlap IDs preserved: `{report['auditor_overlap_preserved']}/{report['auditor_overlap_ids']}`",
        f"- Active Gold modified: `{str(report['active_gold_modified']).lower()}`",
        f"- Valid: `{str(report['valid']).lower()}`",
        "",
        "## Replacements",
        "",
        "| Index | Old record | New record | Task | Category | Split | Reason |",
        "| ---: | --- | --- | --- | --- | --- | --- |",
    ]
    for row in report["replacements"]:
        lines.append(
            f"| {row['primary_pool_index']} | `{row['old_record_id']}` | "
            f"`{row['new_record_id']}` | {row['task']} | {row['category']} | "
            f"{row['reserved_split']} | `{row['reason']}` |"
        )
    if report["issues"]:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- `{issue}`" for issue in report["issues"])
    lines.extend(
        [
            "",
            "This repair changes only the unreviewed assignment pool. No row is promoted to active Gold.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--payload-alias-report", type=Path, required=True)
    parser.add_argument("--auditor-payload", type=Path, required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    pool_path = resolve_path(root, args.pool)
    candidates_path = resolve_path(root, args.candidates)
    alias_path = resolve_path(root, args.payload_alias_report)
    auditor_payload_path = resolve_path(root, args.auditor_payload)
    output_path = resolve_path(root, args.output_jsonl)
    report_json_path = resolve_path(root, args.report_json)
    report_md_path = resolve_path(root, args.report_md)

    alias_map, alias_groups = load_payload_alias_map(alias_path)
    repaired, report = repair_pool(
        root,
        pool_rows=staged_capacity.read_rows(pool_path),
        candidate_rows=staged_capacity.read_rows(candidates_path),
        alias_map=alias_map,
        auditor_ids=load_auditor_overlap_ids(auditor_payload_path),
    )
    write_jsonl_atomic(output_path, repaired)
    report.update(
        {
            "input_pool": display_path(root, pool_path),
            "input_pool_sha256": file_sha256(pool_path),
            "candidate_pool": display_path(root, candidates_path),
            "candidate_pool_sha256": file_sha256(candidates_path),
            "payload_alias_report": display_path(root, alias_path),
            "payload_alias_report_sha256": file_sha256(alias_path),
            "payload_alias_groups": alias_groups,
            "auditor_payload": display_path(root, auditor_payload_path),
            "auditor_payload_sha256": file_sha256(auditor_payload_path),
            "output_jsonl": display_path(root, output_path),
            "output_sha256": file_sha256(output_path),
        }
    )
    write_json(report_json_path, report)
    report_md_path.parent.mkdir(parents=True, exist_ok=True)
    report_md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "rows": report["output_rows"],
                "replaced": report["replaced_rows"],
                "auditor_overlap_preserved": report["auditor_overlap_preserved"],
                "valid": report["valid"],
            },
            indent=2,
        )
    )
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
