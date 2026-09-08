#!/usr/bin/env python3
"""Summarize human handoff packet completion status."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


MICROTEXT_STATUSES = {"accepted", "edited", "rejected", "needs_full_page"}
VISUALDIFF_STATUSES = {"valid", "edit", "reject_unclear", "needs_full_page"}
AGREEMENT_DECISION_FIELDS = ("answer_correct", "bbox_correct", "accept_reject")
VISUALDIFF_LAYOUT_STATUS_GUIDANCE = (
    "layout is not a human_status. Use human_status=edit with human_description "
    "when a specific object/label/symbol/wire/table cell moved relative to nearby "
    "drawing content; use reject_unclear when old/new are identical or only the "
    "whole crop/page shifted."
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def nonempty(value: Any) -> bool:
    return bool(str(value or "").strip())


def visualdiff_invalid_status_message(status: str) -> str:
    if status == "layout":
        return VISUALDIFF_LAYOUT_STATUS_GUIDANCE
    return f"invalid human_status={status}"


def summarize_microtext(path: Path, rows: list[dict[str, str]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    issues: list[dict[str, str]] = []
    for row in rows:
        status = str(row.get("review_status", "")).strip().lower()
        if not status:
            counts["blank"] += 1
            continue
        if status not in MICROTEXT_STATUSES:
            counts["invalid_status"] += 1
            issues.append(
                {
                    "row_id": row.get("candidate_id", ""),
                    "issue": f"invalid review_status={status}",
                }
            )
            continue
        counts[status] += 1
        if status == "edited" and not (
            nonempty(row.get("corrected_text"))
            or nonempty(row.get("corrected_category"))
        ):
            counts["edited_missing_correction"] += 1
            issues.append(
                {
                    "row_id": row.get("candidate_id", ""),
                    "issue": "edited row missing corrected_text and corrected_category",
                }
            )
        if status == "accepted" and not nonempty(row.get("proposed_text")):
            counts["accepted_missing_proposed_text"] += 1
            issues.append(
                {
                    "row_id": row.get("candidate_id", ""),
                    "issue": "accepted row missing proposed_text; use edited with corrected_text",
                }
            )
    return {
        "path": path.as_posix(),
        "kind": "microtext",
        "rows": len(rows),
        "status_counts": dict(sorted(counts.items())),
        "complete": counts["blank"] == 0
        and counts["invalid_status"] == 0
        and counts["edited_missing_correction"] == 0
        and counts["accepted_missing_proposed_text"] == 0,
        "issues": issues,
    }


def summarize_visualdiff(path: Path, rows: list[dict[str, str]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    issues: list[dict[str, str]] = []
    for row in rows:
        status = str(row.get("human_status", "")).strip().lower()
        if not status:
            counts["blank"] += 1
            continue
        if status not in VISUALDIFF_STATUSES:
            counts["invalid_status"] += 1
            issues.append(
                {
                    "row_id": row.get("pair_id", ""),
                    "issue": visualdiff_invalid_status_message(status),
                }
            )
            continue
        counts[status] += 1
        if status == "edit" and not nonempty(row.get("human_description")):
            counts["edit_missing_human_description"] += 1
            issues.append(
                {
                    "row_id": row.get("pair_id", ""),
                    "issue": "edit row missing human_description",
                }
            )
    return {
        "path": path.as_posix(),
        "kind": "visualdiff",
        "rows": len(rows),
        "status_counts": dict(sorted(counts.items())),
        "complete": counts["blank"] == 0
        and counts["invalid_status"] == 0
        and counts["edit_missing_human_description"] == 0,
        "issues": issues,
    }


def summarize_source_selection(path: Path, rows: list[dict[str, str]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    issues: list[dict[str, str]] = []
    selected_rows = [row for row in rows if nonempty(row.get("selected_for_next_import"))]
    counts["selected"] = len(selected_rows)
    counts["not_selected"] = len(rows) - len(selected_rows)
    for row in selected_rows:
        cid = row.get("candidate_id", "")
        if not nonempty(row.get("human_rights_note")):
            counts["selected_missing_rights_note"] += 1
            issues.append({"row_id": cid, "issue": "selected row missing human_rights_note"})
        if not nonempty(row.get("downloaded_file_or_exact_url")):
            counts["selected_missing_file_or_url"] += 1
            issues.append({"row_id": cid, "issue": "selected row missing downloaded_file_or_exact_url"})
    return {
        "path": path.as_posix(),
        "kind": "source_selection",
        "rows": len(rows),
        "status_counts": dict(sorted(counts.items())),
        "complete": counts["selected"] >= 3
        and counts["selected_missing_rights_note"] == 0
        and counts["selected_missing_file_or_url"] == 0,
        "issues": issues,
    }


def summarize_agreement_audit(path: Path, rows: list[dict[str, str]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    issues: list[dict[str, str]] = []
    for row in rows:
        filled = [field for field in AGREEMENT_DECISION_FIELDS if nonempty(row.get(field))]
        if not filled:
            counts["blank"] += 1
            continue
        if len(filled) != len(AGREEMENT_DECISION_FIELDS):
            counts["partial"] += 1
            issues.append(
                {
                    "row_id": row.get("id", ""),
                    "issue": "agreement row has only partial decision fields",
                }
            )
            continue
        counts["reviewed"] += 1
    return {
        "path": path.as_posix(),
        "kind": "agreement_audit",
        "rows": len(rows),
        "status_counts": dict(sorted(counts.items())),
        "complete": counts["blank"] == 0 and counts["partial"] == 0,
        "issues": issues,
    }


def summarize_csv(path: Path) -> dict[str, Any]:
    rows = read_csv(path)
    if not rows:
        return {
            "path": path.as_posix(),
            "kind": "empty",
            "rows": 0,
            "status_counts": {},
            "complete": False,
            "issues": [{"row_id": "", "issue": "empty csv"}],
        }
    fields = set(rows[0])
    if "review_status" in fields and "candidate_id" in fields:
        return summarize_microtext(path, rows)
    if "human_status" in fields and "pair_id" in fields:
        return summarize_visualdiff(path, rows)
    if "selected_for_next_import" in fields and "candidate_id" in fields:
        return summarize_source_selection(path, rows)
    if "id" in fields and any(field in fields for field in AGREEMENT_DECISION_FIELDS):
        return summarize_agreement_audit(path, rows)
    return {
        "path": path.as_posix(),
        "kind": "unknown",
        "rows": len(rows),
        "status_counts": {},
        "complete": False,
        "issues": [{"row_id": "", "issue": "unrecognized csv schema"}],
    }


def summarize_packet(packet_root: Path, recursive: bool = False, packet_id: str | None = None) -> dict[str, Any]:
    csv_paths = sorted(packet_root.rglob("*.csv") if recursive else packet_root.glob("*.csv"))
    files = [summary for path in csv_paths if (summary := summarize_csv(path))["kind"] != "unknown"]
    return {
        "packet_id": packet_id or packet_root.name,
        "packet_root": packet_root.as_posix(),
        "files": files,
        "complete_files": sum(1 for file in files if file["complete"]),
        "incomplete_files": sum(1 for file in files if not file["complete"]),
        "total_issues": sum(len(file["issues"]) for file in files),
    }


def blank_rows(file_summary: dict[str, Any]) -> int:
    return int(file_summary.get("status_counts", {}).get("blank", 0))


def rollup_files(files: list[dict[str, Any]]) -> dict[str, int]:
    totals: Counter[str] = Counter()
    for file in files:
        totals["files"] += 1
        totals["rows"] += int(file.get("rows", 0))
        totals["blank_rows"] += blank_rows(file)
        totals["issues"] += len(file.get("issues", []))
        totals["complete_files"] += int(bool(file.get("complete")))
        totals["incomplete_files"] += int(not file.get("complete"))
    return dict(sorted(totals.items()))


def summarize_handoff(handoff_root: Path) -> dict[str, Any]:
    packet_root = handoff_root / "01_packets"
    if packet_root.exists():
        packet_dirs = sorted(path for path in packet_root.iterdir() if path.is_dir())
        packets = [summarize_packet(path, recursive=True, packet_id=path.name) for path in packet_dirs]
    else:
        single_packet = summarize_packet(handoff_root, recursive=False, packet_id=handoff_root.name)
        packets = [single_packet] if single_packet["files"] else []
    agreement_root = handoff_root / "02_agreement"
    agreement_paths = sorted(agreement_root.rglob("*.csv")) if agreement_root.exists() else []
    agreement = [
        summary
        for path in agreement_paths
        if (summary := summarize_csv(path))["kind"] == "agreement_audit"
    ]
    files = [file for packet in packets for file in packet["files"]] + agreement
    layout_issues: list[dict[str, str]] = []
    if not files:
        layout_issues.append(
            {
                "scope": handoff_root.as_posix(),
                "issue": "no recognized checklist csv files found",
            }
        )
    totals_counter: Counter[str] = Counter(rollup_files(files))
    totals_counter["issues"] += len(layout_issues)
    for key in ("files", "rows", "blank_rows", "issues", "complete_files", "incomplete_files"):
        totals_counter.setdefault(key, 0)
    totals = dict(sorted(totals_counter.items()))
    return {
        "handoff_root": handoff_root.as_posix(),
        "complete": bool(files) and totals.get("incomplete_files", 0) == 0 and totals.get("issues", 0) == 0,
        "packet_roots": len(packets),
        "agreement_files": len(agreement),
        "totals": totals,
        "packets": packets,
        "agreement": agreement,
        "issues": layout_issues,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Human Packet Status",
        "",
        f"- Packet: `{report['packet_root']}`",
        f"- Complete files: `{report['complete_files']}`",
        f"- Incomplete files: `{report['incomplete_files']}`",
        f"- Issues: `{report['total_issues']}`",
        "",
        "| File | Kind | Rows | Complete | Status Counts |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for file in report["files"]:
        counts = ", ".join(
            f"{key}={value}" for key, value in file.get("status_counts", {}).items()
        )
        lines.append(
            f"| `{Path(file['path']).name}` | {file['kind']} | {file['rows']} | "
            f"{str(file['complete']).lower()} | {counts or '-'} |"
        )
    issue_rows = [
        (Path(file["path"]).name, issue)
        for file in report["files"]
        for issue in file.get("issues", [])
    ]
    if issue_rows:
        lines.extend(["", "## Issues", ""])
        for filename, issue in issue_rows[:100]:
            lines.append(
                f"- `{filename}` `{issue.get('row_id', '')}`: {issue.get('issue', '')}"
            )
        if len(issue_rows) > 100:
            lines.append(f"- ... {len(issue_rows) - 100} additional issues omitted")
    lines.append("")
    return "\n".join(lines)


def render_handoff_markdown(report: dict[str, Any]) -> str:
    totals = report.get("totals", {})
    lines = [
        "# Human Handoff Status",
        "",
        f"- Handoff: `{report['handoff_root']}`",
        f"- Complete: `{str(report['complete']).lower()}`",
        f"- Packet folders: `{report['packet_roots']}`",
        f"- Agreement files: `{report['agreement_files']}`",
        f"- Files: `{totals.get('files', 0)}`",
        f"- Rows: `{totals.get('rows', 0)}`",
        f"- Blank rows: `{totals.get('blank_rows', 0)}`",
        f"- Issues: `{totals.get('issues', 0)}`",
        "",
        "| Scope | File | Kind | Rows | Complete | Status Counts |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    scoped_files: list[tuple[str, dict[str, Any]]] = []
    for packet in report.get("packets", []):
        scoped_files.extend((packet.get("packet_id", ""), file) for file in packet.get("files", []))
    scoped_files.extend(("agreement", file) for file in report.get("agreement", []))
    for scope, file in scoped_files:
        counts = ", ".join(
            f"{key}={value}" for key, value in file.get("status_counts", {}).items()
        )
        lines.append(
            f"| `{scope}` | `{Path(file['path']).name}` | {file['kind']} | "
            f"{file['rows']} | {str(file['complete']).lower()} | {counts or '-'} |"
        )
    issue_rows = [
        (scope, Path(file["path"]).name, issue)
        for scope, file in scoped_files
        for issue in file.get("issues", [])
    ]
    if issue_rows:
        lines.extend(["", "## Issues", ""])
        for scope, filename, issue in issue_rows[:100]:
            lines.append(
                f"- `{scope}` `{filename}` `{issue.get('row_id', '')}`: {issue.get('issue', '')}"
            )
        if len(issue_rows) > 100:
            lines.append(f"- ... {len(issue_rows) - 100} additional issues omitted")
    if report.get("issues"):
        lines.extend(["", "## Layout Issues", ""])
        for issue in report["issues"]:
            lines.append(f"- `{issue.get('scope', '')}`: {issue.get('issue', '')}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize a human review packet")
    parser.add_argument(
        "--packet-root",
        default="derived/human_adjudication/2026-05-18_v1_expansion_handoff",
    )
    parser.add_argument("--handoff-root")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args(argv)

    if args.handoff_root:
        report = summarize_handoff(Path(args.handoff_root))
        renderer = render_handoff_markdown
        summary_text = (
            f"[OK] Handoff files: {report['totals'].get('files', 0)}; "
            f"incomplete: {report['totals'].get('incomplete_files', 0)}; "
            f"issues: {report['totals'].get('issues', 0)}"
        )
    else:
        report = summarize_packet(Path(args.packet_root))
        renderer = render_markdown
        summary_text = (
            f"[OK] Packet files: {len(report['files'])}; "
            f"incomplete: {report['incomplete_files']}; issues: {report['total_issues']}"
        )
    if args.output_json:
        out_json = Path(args.output_json)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"[OK] Wrote {out_json}")
    if args.output_md:
        out_md = Path(args.output_md)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(renderer(report), encoding="utf-8")
        print(f"[OK] Wrote {out_md}")
    if not args.output_json and not args.output_md:
        print(json.dumps(report, indent=2))
    print(summary_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
