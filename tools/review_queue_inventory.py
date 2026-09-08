#!/usr/bin/env python3
"""Inventory staged Eng_Bench review queues and evidence availability."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import audit_source_conversion_readiness as source_readiness


OPEN_STATUSES = {"", "candidate", "needs_review", "provisional_review", "todo"}
MERGEABLE_STATUSES = {"accepted", "edited", "valid", "edit"}
TERMINAL_REJECT_STATUSES = {"rejected", "reject_unclear", "blocked_rights", "quarantined"}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def status_for(row: dict[str, Any]) -> str:
    for key in ("review_status", "human_status", "annotation_status", "status"):
        value = str(row.get(key) or "").strip().lower()
        if value:
            return value
    return ""


def id_for(row: dict[str, Any]) -> str:
    for key in ("candidate_id", "pair_id", "id", "item_id", "question_id"):
        value = row.get(key)
        if value:
            return str(value)
    return ""


def doc_or_family_for(row: dict[str, Any]) -> str:
    for key in ("doc_id", "project_id", "source_candidate_id"):
        value = row.get(key)
        if value:
            return str(value)
    identifier = id_for(row)
    if "__p" in identifier:
        return identifier.split("__p", 1)[0]
    if "__" in identifier:
        return "__".join(identifier.split("__")[:3])
    return "unknown"


def path_fields(row: dict[str, Any]) -> list[tuple[str, str]]:
    fields = []
    for key, value in row.items():
        if not isinstance(value, str) or not value.strip():
            continue
        if key.endswith("_path") or key in {"image_path", "image_old", "image_new"}:
            fields.append((key, value))
    return fields


def missing_paths(root: Path, row: dict[str, Any]) -> list[dict[str, str]]:
    missing = []
    for key, value in path_fields(row):
        path = Path(value)
        full_path = path if path.is_absolute() else root / path
        if not full_path.exists():
            missing.append({"field": key, "path": value})
    return missing


def latest_packet_date_label(root: Path) -> str:
    paths = sorted((root / "derived" / "quality").glob("human_packet_index_*.json"))
    if not paths:
        return ""
    latest = max(paths, key=lambda path: (path.stat().st_mtime_ns, path.name))
    return latest.stem.removeprefix("human_packet_index_")


def default_assignment_paths(root: Path) -> list[Path]:
    """Return current handoff evidence missing from the legacy packet index."""
    paths = list((root / "derived" / "review_packs").glob("**/manifest.jsonl"))
    primary_candidates = list(
        (root / "derived" / "quality").glob(
            "primary_continuation_controller_*/processed_primary/current_assignment_capacity.jsonl"
        )
    )
    if primary_candidates:
        paths.append(
            max(primary_candidates, key=lambda path: (path.stat().st_mtime_ns, path.name))
        )
    return sorted({path.resolve() for path in paths if path.is_file()})


def resolve_assignment_paths(
    root: Path,
    assignment_paths: list[Path] | None,
    assignment_globs: list[str] | None,
) -> list[Path]:
    paths = set(default_assignment_paths(root))
    for value in assignment_paths or []:
        path = value if value.is_absolute() else root / value
        if not path.is_file():
            raise FileNotFoundError(f"assignment evidence not found: {path}")
        paths.add(path.resolve())
    for pattern in assignment_globs or []:
        matches = [path for path in root.glob(pattern) if path.is_file()]
        if not matches:
            raise FileNotFoundError(f"assignment evidence glob matched no files: {pattern}")
        paths.update(path.resolve() for path in matches)
    return sorted(paths)


def assignment_row_keys(
    root: Path,
    assignment_paths: list[Path] | None,
    assignment_globs: list[str] | None,
) -> tuple[set[str], list[dict[str, Any]]]:
    keys: set[str] = set()
    sources: list[dict[str, Any]] = []
    for path in resolve_assignment_paths(root, assignment_paths, assignment_globs):
        rows = load_jsonl(path)
        source_keys = {
            source_readiness.row_identity(row, source_readiness.packet_row_kind(row))
            for row in rows
        } - {""}
        keys.update(source_keys)
        try:
            display_path = path.relative_to(root.resolve()).as_posix()
        except ValueError:
            display_path = path.as_posix()
        sources.append(
            {
                "path": display_path,
                "rows": len(rows),
                "row_keys": len(source_keys),
            }
        )
    return keys, sources


def summarize_rows(
    root: Path,
    path: Path,
    rows: list[dict[str, Any]],
    kind: str,
    *,
    packet_row_keys: set[str],
    assigned_row_keys: set[str],
    resolved_row_keys: set[str],
    rights_blocked_doc_ids: set[str],
) -> dict[str, Any]:
    status_counts = Counter(status_for(row) for row in rows)
    doc_counts = Counter(doc_or_family_for(row) for row in rows)
    missing_evidence_rows = []
    fresh_open_rows = 0
    packeted_open_rows = 0
    assigned_open_rows = 0
    stale_open_rows = 0
    rights_blocked_fresh_open_rows = 0
    nonactionable_hold_rows = 0
    for row in rows:
        missing = missing_paths(root, row)
        if missing:
            missing_evidence_rows.append({"id": id_for(row), "missing": missing})
        if source_readiness.review_exclusion_reason(row):
            nonactionable_hold_rows += 1
            continue
        if status_for(row) not in OPEN_STATUSES:
            continue
        identity = source_readiness.row_identity(row, kind)
        if identity in assigned_row_keys:
            assigned_open_rows += 1
        elif identity in packet_row_keys:
            packeted_open_rows += 1
        elif identity in resolved_row_keys:
            stale_open_rows += 1
        else:
            fresh_open_rows += 1
            if doc_or_family_for(row) in rights_blocked_doc_ids:
                rights_blocked_fresh_open_rows += 1
    open_rows = fresh_open_rows + packeted_open_rows + assigned_open_rows + stale_open_rows
    mergeable_rows = sum(count for status, count in status_counts.items() if status in MERGEABLE_STATUSES)
    rejected_rows = sum(count for status, count in status_counts.items() if status in TERMINAL_REJECT_STATUSES)
    return {
        "path": path.as_posix(),
        "kind": kind,
        "rows": len(rows),
        "open_rows": open_rows,
        "fresh_open_rows": fresh_open_rows,
        "actionable_fresh_open_rows": fresh_open_rows - rights_blocked_fresh_open_rows,
        "rights_blocked_fresh_open_rows": rights_blocked_fresh_open_rows,
        "packeted_open_rows": packeted_open_rows,
        "assigned_open_rows": assigned_open_rows,
        "stale_open_rows": stale_open_rows,
        "mergeable_status_rows": mergeable_rows,
        "terminal_reject_rows": rejected_rows,
        "nonactionable_hold_rows": nonactionable_hold_rows,
        "status_counts": dict(sorted(status_counts.items())),
        "source_count": len(doc_counts),
        "top_sources": dict(doc_counts.most_common(10)),
        "missing_evidence_rows": len(missing_evidence_rows),
        "missing_evidence_examples": missing_evidence_rows[:10],
    }


def inventory(
    root: Path,
    packet_date_label: str | None = None,
    *,
    assignment_paths: list[Path] | None = None,
    assignment_globs: list[str] | None = None,
) -> dict[str, Any]:
    packet_date_label = packet_date_label or latest_packet_date_label(root)
    packet_row_keys = (
        source_readiness.active_packet_row_keys(root, packet_date_label)
        if packet_date_label
        else set()
    )
    assigned_row_keys, assignment_sources = assignment_row_keys(
        root,
        assignment_paths,
        assignment_globs,
    )
    resolved_row_keys = (
        source_readiness.active_gold_row_keys(root)
        | source_readiness.terminal_reviewed_row_keys(root)
    )
    rights_blocked_doc_ids = {
        str(row.get("doc_id") or "").strip()
        for row in source_readiness.read_csv(root / "SOURCE_INVENTORY.csv")
        if row.get("doc_id")
        and not source_readiness.is_release_safe_status(str(row.get("public_status") or ""))
    }
    files: list[dict[str, Any]] = []
    for path in sorted((root / "microtext" / "annotations").glob("microtext_review*.jsonl")):
        rows = load_jsonl(path)
        files.append(
            summarize_rows(
                root,
                path.relative_to(root),
                rows,
                "microtext",
                packet_row_keys=packet_row_keys,
                assigned_row_keys=assigned_row_keys,
                resolved_row_keys=resolved_row_keys,
                rights_blocked_doc_ids=rights_blocked_doc_ids,
            )
        )
    for path in sorted((root / "visualdiff" / "annotations").glob("visualdiff_review*.jsonl")):
        rows = load_jsonl(path)
        files.append(
            summarize_rows(
                root,
                path.relative_to(root),
                rows,
                "visualdiff",
                packet_row_keys=packet_row_keys,
                assigned_row_keys=assigned_row_keys,
                resolved_row_keys=resolved_row_keys,
                rights_blocked_doc_ids=rights_blocked_doc_ids,
            )
        )

    totals = Counter()
    for item in files:
        totals["files"] += 1
        totals["rows"] += int(item["rows"])
        totals["open_rows"] += int(item["open_rows"])
        totals["fresh_open_rows"] += int(item["fresh_open_rows"])
        totals["actionable_fresh_open_rows"] += int(item["actionable_fresh_open_rows"])
        totals["rights_blocked_fresh_open_rows"] += int(item["rights_blocked_fresh_open_rows"])
        totals["packeted_open_rows"] += int(item["packeted_open_rows"])
        totals["assigned_open_rows"] += int(item["assigned_open_rows"])
        totals["stale_open_rows"] += int(item["stale_open_rows"])
        totals["mergeable_status_rows"] += int(item["mergeable_status_rows"])
        totals["terminal_reject_rows"] += int(item["terminal_reject_rows"])
        totals["nonactionable_hold_rows"] += int(item["nonactionable_hold_rows"])
        totals["missing_evidence_rows"] += int(item["missing_evidence_rows"])

    next_human_candidates = [
        item
        for item in files
        if item["actionable_fresh_open_rows"] > 0 and item["missing_evidence_rows"] == 0
    ]
    next_human_candidates.sort(
        key=lambda item: (item["actionable_fresh_open_rows"], item["source_count"]),
        reverse=True,
    )
    return {
        "packet_date_label": packet_date_label,
        "active_packet_row_keys": len(packet_row_keys),
        "additional_assignment_row_keys": len(assigned_row_keys),
        "additional_assignment_sources": assignment_sources,
        "resolved_row_keys": len(resolved_row_keys),
        "rights_blocked_doc_ids": len(rights_blocked_doc_ids),
        "totals": dict(totals),
        "next_human_candidates": next_human_candidates[:20],
        "files": files,
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Review Queue Inventory",
        "",
        f"- Files: `{totals.get('files', 0)}`",
        f"- Rows: `{totals.get('rows', 0)}`",
        f"- Open rows: `{totals.get('open_rows', 0)}`",
        f"- Fresh unpacketed open rows: `{totals.get('fresh_open_rows', 0)}`",
        f"- Actionable release-safe fresh rows: `{totals.get('actionable_fresh_open_rows', 0)}`",
        f"- Rights-blocked fresh rows: `{totals.get('rights_blocked_fresh_open_rows', 0)}`",
        f"- Active-packet open rows: `{totals.get('packeted_open_rows', 0)}`",
        f"- Additional assigned open rows: `{totals.get('assigned_open_rows', 0)}`",
        f"- Stale open rows already resolved: `{totals.get('stale_open_rows', 0)}`",
        f"- Mergeable-status rows: `{totals.get('mergeable_status_rows', 0)}`",
        f"- Terminal rejected rows: `{totals.get('terminal_reject_rows', 0)}`",
        f"- Non-actionable held/superseded rows: `{totals.get('nonactionable_hold_rows', 0)}`",
        f"- Rows with missing evidence refs: `{totals.get('missing_evidence_rows', 0)}`",
        "",
        "## Highest-Yield Fresh Open Queues",
        "",
        "| File | Kind | Rows | Actionable Fresh | Sources | Missing Evidence |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for item in report["next_human_candidates"]:
        lines.append(
            f"| `{Path(item['path']).name}` | {item['kind']} | {item['rows']} | "
            f"{item['actionable_fresh_open_rows']} | {item['source_count']} | {item['missing_evidence_rows']} |"
        )
    lines.extend(["", "## All Queues", ""])
    lines.extend(
        [
            "| File | Kind | Rows | Open | Fresh | Actionable | Rights Hold | Packeted | Assigned | Stale | Mergeable | Rejected | Machine Hold | Missing Evidence |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in report["files"]:
        lines.append(
            f"| `{Path(item['path']).name}` | {item['kind']} | {item['rows']} | "
            f"{item['open_rows']} | {item['fresh_open_rows']} | "
            f"{item['actionable_fresh_open_rows']} | {item['rights_blocked_fresh_open_rows']} | "
            f"{item['packeted_open_rows']} | {item['assigned_open_rows']} | {item['stale_open_rows']} | "
            f"{item['mergeable_status_rows']} | "
            f"{item['terminal_reject_rows']} | {item['nonactionable_hold_rows']} | "
            f"{item['missing_evidence_rows']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inventory staged review queues.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--output-json", default="derived/quality/review_queue_inventory_2026-06-02.json")
    parser.add_argument("--output-md", default="derived/quality/review_queue_inventory_2026-06-02.md")
    parser.add_argument(
        "--packet-date-label",
        help="Human packet index date label; defaults to the latest available packet index.",
    )
    parser.add_argument(
        "--exclude-assignment",
        action="append",
        default=[],
        help="Additional JSONL assignment evidence to exclude; repeat as needed.",
    )
    parser.add_argument(
        "--exclude-assignment-glob",
        action="append",
        default=[],
        help="Root-relative glob of JSONL assignment evidence to exclude; repeat as needed.",
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = inventory(
        root,
        packet_date_label=args.packet_date_label,
        assignment_paths=[Path(value) for value in args.exclude_assignment],
        assignment_globs=args.exclude_assignment_glob,
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
