#!/usr/bin/env python3
"""Build a delivery manifest for ready Eng_Bench human-review ZIPs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any


VISUALDIFF_RULE_MARKERS = (
    "old/new crops are visually identical",
    "whole-crop/page alignment shift",
    "Use `edit`, not `layout`",
)


def resolve(root: Path, path: str | Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else root / path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_zip(zip_path: Path) -> dict[str, Any]:
    issues: list[str] = []
    if not zip_path.exists():
        return {
            "zip_exists": False,
            "zip_bytes": 0,
            "sha256": "",
            "entry_count": 0,
            "nested_zip_entries": 0,
            "archived_readme": False,
            "archived_human_steps": False,
            "visualdiff_content": False,
            "visualdiff_instruction_rule_ok": False,
            "bad_zip_entry": "",
            "issues": ["missing zip"],
        }

    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            bad_entry = zf.testzip() or ""
            if bad_entry:
                issues.append(f"bad zip entry: {bad_entry}")
            readme_names = [name for name in names if name.endswith("README.md")]
            steps_names = [name for name in names if name.endswith("HUMAN_REVIEW_STEPS.md")]
            visualdiff_content = any("visualdiff" in name.lower() for name in names)
            steps_text = ""
            if steps_names:
                steps_text = zf.read(steps_names[0]).decode("utf-8", errors="replace")
            if not readme_names:
                issues.append("missing archived README.md")
            if not steps_names:
                issues.append("missing archived HUMAN_REVIEW_STEPS.md")
            visualdiff_rule_ok = (
                not visualdiff_content
                or bool(steps_text)
                and all(marker in steps_text for marker in VISUALDIFF_RULE_MARKERS)
            )
            if visualdiff_content and not visualdiff_rule_ok:
                issues.append("visualdiff ZIP missing identical/shift/layout rule")
            return {
                "zip_exists": True,
                "zip_bytes": zip_path.stat().st_size,
                "sha256": sha256_file(zip_path),
                "entry_count": len(names),
                "nested_zip_entries": sum(1 for name in names if name.lower().endswith(".zip")),
                "archived_readme": bool(readme_names),
                "archived_human_steps": bool(steps_names),
                "visualdiff_content": visualdiff_content,
                "visualdiff_instruction_rule_ok": visualdiff_rule_ok,
                "bad_zip_entry": bad_entry,
                "issues": issues,
            }
    except zipfile.BadZipFile as exc:
        return {
            "zip_exists": True,
            "zip_bytes": zip_path.stat().st_size,
            "sha256": sha256_file(zip_path),
            "entry_count": 0,
            "nested_zip_entries": 0,
            "archived_readme": False,
            "archived_human_steps": False,
            "visualdiff_content": False,
            "visualdiff_instruction_rule_ok": False,
            "bad_zip_entry": "",
            "issues": [f"bad zip: {exc}"],
        }


def packet_row(root: Path, packet: dict[str, Any]) -> dict[str, Any]:
    zip_rel = str(packet.get("zip_path") or "").strip()
    zip_path = resolve(root, zip_rel) if zip_rel else root / "__missing_zip_path__"
    inspected = inspect_zip(zip_path) if zip_rel else {
        "zip_exists": False,
        "zip_bytes": 0,
        "sha256": "",
        "entry_count": 0,
        "nested_zip_entries": 0,
        "archived_readme": False,
        "archived_human_steps": False,
        "visualdiff_content": False,
        "visualdiff_instruction_rule_ok": False,
        "bad_zip_entry": "",
        "issues": ["missing zip_path"],
    }
    issues = list(inspected.pop("issues"))
    if not bool(packet.get("verified")):
        issues.append("packet index marks packet unverified")
    if not int(packet.get("review_rows") or 0):
        issues.append("packet index records zero review rows")
    return {
        "packet_id": str(packet.get("packet_id") or ""),
        "kind": str(packet.get("kind") or ""),
        "intended_use": str(packet.get("intended_use") or ""),
        "folder_path": str(packet.get("folder_path") or packet.get("packet_root") or ""),
        "zip_path": zip_rel,
        "review_rows": int(packet.get("review_rows") or 0),
        "source_rows": int(packet.get("source_rows") or 0),
        "crop_files": int(packet.get("crop_files") or 0),
        "page_files": int(packet.get("page_files") or 0),
        "return_command": str(packet.get("return_command") or ""),
        **inspected,
        "issues": issues,
        "deliverable": not issues,
    }


def build_manifest(
    *,
    root: Path,
    index_path: Path,
    date_label: str,
    ready_only: bool = True,
) -> dict[str, Any]:
    root = root.resolve()
    index = json.loads(index_path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    skipped_unready = 0
    for packet in index.get("packets", []):
        if ready_only and packet.get("ready_to_send") is False:
            skipped_unready += 1
            continue
        rows.append(packet_row(root, packet))
    totals = {
        "packets": len(rows),
        "deliverable_packets": sum(1 for row in rows if row["deliverable"]),
        "skipped_unready_packets": skipped_unready,
        "review_rows": sum(int(row["review_rows"]) for row in rows),
        "zip_bytes": sum(int(row["zip_bytes"]) for row in rows),
        "zip_entries": sum(int(row["entry_count"]) for row in rows),
        "nested_zip_entries": sum(int(row["nested_zip_entries"]) for row in rows),
        "visualdiff_zips": sum(1 for row in rows if row["visualdiff_content"]),
        "visualdiff_rule_ok_zips": sum(
            1 for row in rows if row["visualdiff_content"] and row["visualdiff_instruction_rule_ok"]
        ),
        "issues": sum(len(row["issues"]) for row in rows),
    }
    return {
        "date_label": date_label,
        "source_index": index_path.as_posix(),
        "ready_only": ready_only,
        "totals": totals,
        "packets": rows,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "packet_id",
        "kind",
        "zip_path",
        "review_rows",
        "zip_bytes",
        "sha256",
        "entry_count",
        "nested_zip_entries",
        "archived_readme",
        "archived_human_steps",
        "visualdiff_content",
        "visualdiff_instruction_rule_ok",
        "deliverable",
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
        "# Eng_Bench Handoff Delivery Manifest",
        "",
        f"- Date label: `{report['date_label']}`",
        f"- Source index: `{report['source_index']}`",
        f"- Ready packets listed: `{totals['packets']}`",
        f"- Deliverable packets: `{totals['deliverable_packets']}`",
        f"- Review rows: `{totals['review_rows']}`",
        f"- ZIP bytes: `{totals['zip_bytes']}`",
        f"- ZIP entries: `{totals['zip_entries']}`",
        f"- Nested ZIP entries: `{totals['nested_zip_entries']}`",
        f"- VisualDiff ZIP rules OK: `{totals['visualdiff_rule_ok_zips']}/{totals['visualdiff_zips']}`",
        f"- Issues: `{totals['issues']}`",
        "",
        "## Send These ZIPs",
        "",
    ]
    for row in report["packets"]:
        if row["deliverable"]:
            lines.append(
                f"- `{row['zip_path']}` - `{row['review_rows']}` rows, sha256 `{row['sha256']}`"
            )
    lines.extend(
        [
            "",
            "## Packet Table",
            "",
            "| Packet | Rows | Bytes | Entries | VisualDiff Rule | Deliverable |",
            "| --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in report["packets"]:
        rule = "n/a"
        if row["visualdiff_content"]:
            rule = str(row["visualdiff_instruction_rule_ok"]).lower()
        lines.append(
            f"| `{row['packet_id']}` | {row['review_rows']} | {row['zip_bytes']} | "
            f"{row['entry_count']} | `{rule}` | `{str(row['deliverable']).lower()}` |"
        )
    issue_rows = [row for row in report["packets"] if row["issues"]]
    if issue_rows:
        lines.extend(["", "## Issues", ""])
        for row in issue_rows:
            for issue in row["issues"]:
                lines.append(f"- `{row['packet_id']}`: {issue}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--index-json", required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    parser.add_argument("--output-csv")
    parser.add_argument("--include-unready", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = build_manifest(
        root=root,
        index_path=resolve(root, args.index_json),
        date_label=args.date_label,
        ready_only=not args.include_unready,
    )
    if args.output_json:
        write_json(resolve(root, args.output_json), report)
        print(f"[OK] Wrote {args.output_json}")
    if args.output_md:
        md_path = resolve(root, args.output_md)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_markdown(report), encoding="utf-8")
        print(f"[OK] Wrote {args.output_md}")
    if args.output_csv:
        write_csv(resolve(root, args.output_csv), report["packets"])
        print(f"[OK] Wrote {args.output_csv}")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 1 if report["totals"]["issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
