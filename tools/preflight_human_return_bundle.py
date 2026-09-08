#!/usr/bin/env python3
"""Preflight an intern-returned Eng_Bench human-review bundle before staging."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import review_packet_status


def resolve(root: Path, path: str | Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else root / path


def quote(value: str | Path) -> str:
    return f'"{value}"'


def expected_folder_name(packet: dict[str, Any]) -> str:
    folder = str(packet.get("folder_path") or packet.get("packet_root") or "").strip()
    if folder:
        return Path(folder).name
    zip_path = str(packet.get("zip_path") or "").strip()
    if zip_path:
        name = Path(zip_path).name
        return name[:-4] if name.lower().endswith(".zip") else Path(name).stem
    return str(packet.get("packet_id") or "").strip()


def find_packet_dir(returned_root: Path, folder_name: str) -> tuple[Path | None, list[str]]:
    direct = returned_root / folder_name
    if direct.exists() and direct.is_dir():
        return direct, []
    matches = [path for path in returned_root.rglob(folder_name) if path.is_dir()]
    if len(matches) == 1:
        return matches[0], []
    if len(matches) > 1:
        return None, [f"multiple returned folders named {folder_name}"]
    return None, [f"missing returned folder {folder_name}"]


def row_count(summary: dict[str, Any]) -> int:
    return sum(int(file.get("rows", 0)) for file in summary.get("files", []))


def blank_count(summary: dict[str, Any]) -> int:
    return sum(
        int(file.get("status_counts", {}).get("blank", 0))
        for file in summary.get("files", [])
    )


def process_command(root: Path, packet_dir: Path, return_label: str, packet_id: str) -> str:
    output_dir = Path("derived") / "human_adjudication" / "processed_returns" / return_label / packet_id
    return (
        "python tools\\process_next_review_batch_return.py --root . "
        f"--batch-root {quote(packet_dir)} "
        f"--output-dir {quote(output_dir)} --strict"
    )


def preflight_packet(
    *,
    root: Path,
    returned_root: Path,
    packet: dict[str, Any],
    return_label: str,
) -> dict[str, Any]:
    packet_id = str(packet.get("packet_id") or "").strip()
    expected_rows = int(packet.get("review_rows") or 0)
    folder_name = expected_folder_name(packet)
    packet_dir, issues = find_packet_dir(returned_root, folder_name)
    summary: dict[str, Any] = {}
    rows = 0
    blank_rows = 0
    complete_files = 0
    incomplete_files = 0
    status_issues = 0
    if packet_dir is not None:
        summary = review_packet_status.summarize_packet(packet_dir, recursive=True, packet_id=packet_id)
        rows = row_count(summary)
        blank_rows = blank_count(summary)
        complete_files = int(summary.get("complete_files", 0))
        incomplete_files = int(summary.get("incomplete_files", 0))
        status_issues = int(summary.get("total_issues", 0))
        if rows != expected_rows:
            issues.append(f"row count mismatch: expected {expected_rows}, found {rows}")
        if incomplete_files:
            issues.append(f"incomplete checklist files: {incomplete_files}")
        if status_issues:
            issues.append(f"checklist status issues: {status_issues}")
        if blank_rows:
            issues.append(f"blank review rows: {blank_rows}")
    processable = packet_dir is not None and not issues
    return {
        "packet_id": packet_id,
        "expected_folder": folder_name,
        "returned_packet_root": packet_dir.as_posix() if packet_dir else "",
        "expected_rows": expected_rows,
        "review_rows": rows,
        "blank_rows": blank_rows,
        "complete_files": complete_files,
        "incomplete_files": incomplete_files,
        "status_issues": status_issues,
        "processable": processable,
        "process_command": process_command(root, packet_dir, return_label, packet_id) if processable and packet_dir else "",
        "issues": issues,
    }


def preflight_bundle(
    *,
    root: Path,
    delivery_manifest_path: Path,
    returned_root: Path,
    return_label: str,
) -> dict[str, Any]:
    root = root.resolve()
    returned_root = resolve(root, returned_root)
    delivery = json.loads(delivery_manifest_path.read_text(encoding="utf-8"))
    expected_packets = [
        packet
        for packet in delivery.get("packets", [])
        if packet.get("deliverable", packet.get("ready_to_send", True))
    ]
    packet_reports = [
        preflight_packet(
            root=root,
            returned_root=returned_root,
            packet=packet,
            return_label=return_label,
        )
        for packet in expected_packets
    ]
    totals = {
        "expected_packets": len(expected_packets),
        "found_packets": sum(1 for packet in packet_reports if packet["returned_packet_root"]),
        "processable_packets": sum(1 for packet in packet_reports if packet["processable"]),
        "review_rows": sum(int(packet["review_rows"]) for packet in packet_reports),
        "expected_rows": sum(int(packet["expected_rows"]) for packet in packet_reports),
        "blank_rows": sum(int(packet["blank_rows"]) for packet in packet_reports),
        "complete_files": sum(int(packet["complete_files"]) for packet in packet_reports),
        "incomplete_files": sum(int(packet["incomplete_files"]) for packet in packet_reports),
        "issues": sum(len(packet["issues"]) for packet in packet_reports),
    }
    return {
        "delivery_manifest": delivery_manifest_path.as_posix(),
        "returned_root": returned_root.as_posix(),
        "return_label": return_label,
        "complete": totals["expected_packets"] > 0
        and totals["processable_packets"] == totals["expected_packets"]
        and totals["issues"] == 0,
        "totals": totals,
        "packets": packet_reports,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "packet_id",
        "expected_folder",
        "returned_packet_root",
        "expected_rows",
        "review_rows",
        "blank_rows",
        "complete_files",
        "incomplete_files",
        "status_issues",
        "processable",
        "process_command",
        "issues",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            out = {field: row.get(field, "") for field in fields}
            out["issues"] = "; ".join(row.get("issues", []))
            writer.writerow(out)


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Human Return Bundle Preflight",
        "",
        f"- Delivery manifest: `{report['delivery_manifest']}`",
        f"- Returned root: `{report['returned_root']}`",
        f"- Return label: `{report['return_label']}`",
        f"- Complete: `{str(report['complete']).lower()}`",
        f"- Expected packets: `{totals['expected_packets']}`",
        f"- Found packets: `{totals['found_packets']}`",
        f"- Processable packets: `{totals['processable_packets']}`",
        f"- Expected rows: `{totals['expected_rows']}`",
        f"- Returned rows: `{totals['review_rows']}`",
        f"- Blank rows: `{totals['blank_rows']}`",
        f"- Issues: `{totals['issues']}`",
        "",
        "## Packet Table",
        "",
        "| Packet | Expected Folder | Rows | Blank | Processable | Issues |",
        "| --- | --- | ---: | ---: | --- | ---: |",
    ]
    for packet in report["packets"]:
        lines.append(
            f"| `{packet['packet_id']}` | `{packet['expected_folder']}` | "
            f"{packet['review_rows']}/{packet['expected_rows']} | {packet['blank_rows']} | "
            f"`{str(packet['processable']).lower()}` | {len(packet['issues'])} |"
        )
    processable = [packet for packet in report["packets"] if packet["processable"]]
    if processable:
        lines.extend(["", "## Processing Commands", ""])
        for packet in processable:
            lines.append(f"### {packet['packet_id']}")
            lines.append("")
            lines.append("```powershell")
            lines.append(packet["process_command"])
            lines.append("```")
            lines.append("")
    issue_packets = [packet for packet in report["packets"] if packet["issues"]]
    if issue_packets:
        lines.extend(["", "## Issues", ""])
        for packet in issue_packets:
            for issue in packet["issues"]:
                lines.append(f"- `{packet['packet_id']}`: {issue}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preflight an intern-returned Eng_Bench review bundle.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--delivery-manifest", required=True)
    parser.add_argument("--returned-root", required=True)
    parser.add_argument("--return-label", required=True)
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    parser.add_argument("--output-csv")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = preflight_bundle(
        root=root,
        delivery_manifest_path=resolve(root, args.delivery_manifest),
        returned_root=Path(args.returned_root),
        return_label=args.return_label,
    )
    if args.output_json:
        write_json(resolve(root, args.output_json), report)
        print(f"[OK] Wrote {args.output_json}")
    if args.output_md:
        output_md = resolve(root, args.output_md)
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(render_markdown(report), encoding="utf-8")
        print(f"[OK] Wrote {args.output_md}")
    if args.output_csv:
        write_csv(resolve(root, args.output_csv), report["packets"])
        print(f"[OK] Wrote {args.output_csv}")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 1 if args.strict and not report["complete"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
