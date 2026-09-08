#!/usr/bin/env python3
"""Validate and stage returned CSVs from a generated next-review batch.

This tool does not mutate active gold annotations. It reads
`NEXT_REVIEW_BATCH_MANIFEST.csv`, maps each returned checklist to its source
review JSONL, applies human statuses into reviewed staging JSONL files, and
writes a processing summary for maintainer merge audit.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

import microtext_review_checklist
import visualdiff_review_checklist


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def safe_stem(value: str) -> str:
    return Path(value).stem.replace(" ", "_")


def evidence_fields(row: dict[str, str]) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    for key in (
        "crop_path",
        "page_path",
        "image_path",
        "old_crop_path",
        "new_crop_path",
        "panel_path",
        "old_page_path",
        "new_page_path",
    ):
        value = str(row.get(key) or "").strip().replace("\\", "/")
        if value:
            fields.append((key, value))
    return fields


def evidence_path_exists(root: Path, pack_dir: Path, field: str, value: str) -> bool:
    path = Path(value)
    if path.is_absolute():
        return path.exists()
    if field in {
        "crop_path",
        "page_path",
        "old_crop_path",
        "new_crop_path",
        "panel_path",
        "old_page_path",
        "new_page_path",
    }:
        return (pack_dir / path).exists()
    return (root / path).exists() or (pack_dir / path).exists()


def missing_evidence(root: Path, pack_dir: Path, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    missing = []
    for row in rows:
        identifier = row.get("candidate_id") or row.get("pair_id") or ""
        for field, value in evidence_fields(row):
            if not evidence_path_exists(root, pack_dir, field, value):
                missing.append({"id": identifier, "field": field, "path": value})
    return missing


def process_manifest_row(
    root: Path,
    batch_root: Path,
    output_dir: Path,
    manifest_row: dict[str, str],
) -> tuple[dict[str, Any], list[str]]:
    pack_name = str(manifest_row.get("pack_name") or "").strip()
    checklist = str(manifest_row.get("checklist") or "").strip()
    source_jsonl = str(manifest_row.get("source_jsonl") or "").strip()
    errors: list[str] = []
    if not pack_name or not checklist or not source_jsonl:
        error = f"incomplete manifest row: pack_name={pack_name!r}, checklist={checklist!r}, source_jsonl={source_jsonl!r}"
        return {"pack_name": pack_name, "checklist": checklist, "source_jsonl": source_jsonl, "errors": [error]}, [error]

    pack_dir = batch_root / "review_packs" / pack_name
    checklist_path = pack_dir / checklist
    source_path = root / source_jsonl
    if not checklist_path.exists():
        errors.append(f"{pack_name}: missing checklist {checklist}")
        checklist_rows: list[dict[str, str]] = []
    else:
        checklist_rows = read_csv(checklist_path)
    if not source_path.exists():
        errors.append(f"{pack_name}: missing source_jsonl {source_jsonl}")
        source_rows: list[dict[str, Any]] = []
    else:
        source_rows = load_jsonl(source_path)

    evidence_missing = missing_evidence(root, pack_dir, checklist_rows)
    errors.extend(f"{pack_name}: missing evidence refs={len(evidence_missing)}" for _ in [0] if evidence_missing)

    kind = str(manifest_row.get("kind") or "").strip().lower()
    if not kind:
        kind = "visualdiff" if any(row.get("pair_id") or row.get("image_old") for row in source_rows) else "microtext"
    if kind == "visualdiff":
        updated_rows, stats, apply_errors = visualdiff_review_checklist.apply_checklist(
            source_rows, checklist_rows
        )
        status_field = "human_status"
    else:
        updated_rows, stats, apply_errors = microtext_review_checklist.apply_checklist(
            source_rows, checklist_rows
        )
        status_field = "review_status"
    errors.extend(apply_errors)
    output_path = output_dir / f"{safe_stem(pack_name)}_reviewed.jsonl"
    write_jsonl(output_path, updated_rows)
    status_counts = Counter(str(row.get(status_field) or "").strip().lower() for row in checklist_rows)
    complete = (
        len(checklist_rows) > 0
        and int(stats.get("blank", 0)) == 0
        and not apply_errors
        and not evidence_missing
        and not any(err for err in errors if "missing " in err)
    )
    return (
        {
            "pack_name": pack_name,
            "kind": kind,
            "checklist": checklist,
            "source_jsonl": source_jsonl,
            "output_jsonl": str(output_path),
            "source_rows": len(source_rows),
            "checklist_rows": len(checklist_rows),
            "status_counts": dict(sorted(status_counts.items())),
            "apply_stats": dict(sorted(stats.items())),
            "mergeable_rows": int(stats.get("mergeable", 0)),
            "missing_evidence_refs": len(evidence_missing),
            "missing_evidence_examples": evidence_missing[:10],
            "complete": complete,
            "errors": errors,
        },
        errors,
    )


def process_batch(root: Path, batch_root: Path, output_dir: Path) -> tuple[dict[str, Any], list[str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = batch_root / "NEXT_REVIEW_BATCH_MANIFEST.csv"
    if not manifest_path.exists():
        error = f"missing NEXT_REVIEW_BATCH_MANIFEST.csv under {batch_root}"
        report = {
            "batch_root": str(batch_root),
            "output_dir": str(output_dir),
            "complete": False,
            "totals": {},
            "errors": [error],
            "packs": [],
        }
        return report, [error]

    manifest_rows = read_csv(manifest_path)
    summaries: list[dict[str, Any]] = []
    all_errors: list[str] = []
    totals: Counter[str] = Counter()
    for manifest_row in manifest_rows:
        summary, errors = process_manifest_row(root, batch_root, output_dir, manifest_row)
        summaries.append(summary)
        all_errors.extend(errors)
        totals["packs"] += 1
        totals["source_rows"] += int(summary.get("source_rows", 0))
        totals["checklist_rows"] += int(summary.get("checklist_rows", 0))
        totals["mergeable_rows"] += int(summary.get("mergeable_rows", 0))
        totals["missing_evidence_refs"] += int(summary.get("missing_evidence_refs", 0))
        totals["complete_packs"] += int(bool(summary.get("complete", False)))
        totals["blank_rows"] += int(summary.get("apply_stats", {}).get("blank", 0))

    complete = (
        not all_errors
        and totals.get("packs", 0) > 0
        and totals.get("complete_packs", 0) == totals.get("packs", 0)
    )
    report = {
        "batch_root": str(batch_root),
        "output_dir": str(output_dir),
        "complete": complete,
        "totals": dict(sorted(totals.items())),
        "errors": all_errors,
        "packs": summaries,
    }
    return report, all_errors


def render_markdown(report: dict[str, Any]) -> str:
    totals = report.get("totals", {})
    lines = [
        "# Next Review Batch Return Processing Report",
        "",
        f"- Batch: `{report['batch_root']}`",
        f"- Output dir: `{report['output_dir']}`",
        f"- Complete: `{str(report.get('complete', False)).lower()}`",
        f"- Packs: `{totals.get('packs', 0)}`",
        f"- Complete packs: `{totals.get('complete_packs', 0)}`",
        f"- Checklist rows: `{totals.get('checklist_rows', 0)}`",
        f"- Blank rows: `{totals.get('blank_rows', 0)}`",
        f"- Mergeable rows: `{totals.get('mergeable_rows', 0)}`",
        f"- Missing evidence refs: `{totals.get('missing_evidence_refs', 0)}`",
        f"- Errors: `{len(report.get('errors', []))}`",
        "",
        "| Pack | Rows | Complete | Mergeable | Blank | Missing Evidence | Errors |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for pack in report.get("packs", []):
        apply_stats = pack.get("apply_stats", {})
        lines.append(
            f"| `{pack.get('pack_name', '')}` | {pack.get('checklist_rows', 0)} | "
            f"`{str(pack.get('complete', False)).lower()}` | {pack.get('mergeable_rows', 0)} | "
            f"{apply_stats.get('blank', 0)} | {pack.get('missing_evidence_refs', 0)} | "
            f"{len(pack.get('errors', []))} |"
        )
    if report.get("errors"):
        lines.extend(["", "## Errors", ""])
        for error in report["errors"][:100]:
            lines.append(f"- {error}")
        if len(report["errors"]) > 100:
            lines.append(f"- ... {len(report['errors']) - 100} additional errors omitted")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and stage a returned next-review batch.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--batch-root", required=True, help="Returned next-review batch folder")
    parser.add_argument(
        "--output-dir",
        default="derived/human_adjudication/processed_returns/next_review_latest",
    )
    parser.add_argument("--strict", action="store_true", help="Return nonzero if incomplete or invalid")
    args = parser.parse_args(argv)

    root = Path(args.root)
    batch_root = Path(args.batch_root)
    if not batch_root.is_absolute():
        batch_root = root / batch_root
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = root / output_dir

    report, errors = process_batch(root, batch_root, output_dir)
    write_json(output_dir / "processing_summary.json", report)
    (output_dir / "processing_summary.md").write_text(render_markdown(report), encoding="utf-8")
    print(f"[OK] Wrote {output_dir / 'processing_summary.json'}")
    print(f"[OK] Wrote {output_dir / 'processing_summary.md'}")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    if errors:
        print("[WARN] Packet errors:")
        for error in errors[:20]:
            print(f"  - {error}")
        if len(errors) > 20:
            print(f"  - ... {len(errors) - 20} additional errors omitted")
    return 1 if args.strict and (errors or not report["complete"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
