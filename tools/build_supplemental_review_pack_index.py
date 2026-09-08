#!/usr/bin/env python3
"""Index simple crop-based review packs outside the dated handoff index."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any


MICROTEXT_STATUSES = {"accepted", "edited", "rejected", "needs_full_page"}
VISUALDIFF_STATUSES = {"valid", "edit", "reject_unclear", "needs_full_page"}
REFERENCE_KEYS = (
    "crop_path",
    "page_path",
    "image_path",
    "panel_path",
    "old_crop_path",
    "new_crop_path",
    "old_page_path",
    "new_page_path",
    "image_old",
    "image_new",
)
CSV_FIELDS = [
    "packet_id",
    "kind",
    "folder_path",
    "manifest_rows",
    "checklist_files",
    "checklist_rows",
    "blank_rows",
    "invalid_rows",
    "missing_evidence_refs",
    "crop_files",
    "page_files",
    "has_index_html",
    "has_instructions",
    "ready_to_send",
    "human_complete",
    "issues",
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def read_csv_with_issue(path: Path) -> tuple[list[dict[str, str]], str]:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with path.open("r", encoding=encoding, newline="") as f:
                rows = list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
        issue = "" if encoding == "utf-8-sig" else f"non_utf8_csv:{path.name}"
        return rows, issue
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return list(csv.DictReader(f)), f"csv_decode_replacement:{path.name}"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def portable_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def resolve_reference(root: Path, pack_dir: Path, value: Any) -> Path | None:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return None
    path = Path(text)
    if path.is_absolute():
        return path
    if text.startswith(("derived/", "images/", "microtext/", "visualdiff/")):
        return root / path
    return pack_dir / path


def infer_kind(manifest_rows: list[dict[str, Any]], checklist_rows: list[dict[str, str]]) -> str:
    fields = set()
    for row in manifest_rows[:10]:
        fields.update(row)
    for row in checklist_rows[:10]:
        fields.update(row)
    if "pair_id" in fields or "human_status" in fields:
        return "visualdiff"
    if "candidate_id" in fields or "review_status" in fields:
        return "microtext"
    return "unknown"


def checklist_status_counts(rows: list[dict[str, str]], kind: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    if not rows:
        return counts
    if kind == "visualdiff":
        allowed = VISUALDIFF_STATUSES
        key = "human_status"
    else:
        allowed = MICROTEXT_STATUSES
        key = "review_status"
    for row in rows:
        status = str(row.get(key, "")).strip().lower()
        if not status:
            counts["blank"] += 1
        elif status in allowed:
            counts[status] += 1
        else:
            counts["invalid"] += 1
    return counts


def summarize_checklists(pack_dir: Path, kind: str) -> dict[str, Any]:
    checklist_paths = sorted(pack_dir.glob("*.csv"))
    rows: list[dict[str, str]] = []
    issues: list[str] = []
    for path in checklist_paths:
        try:
            csv_rows, issue = read_csv_with_issue(path)
            rows.extend(csv_rows)
            if issue:
                issues.append(issue)
        except csv.Error:
            issues.append(f"bad_csv:{path.name}")
    counts = checklist_status_counts(rows, kind)
    return {
        "checklist_files": len(checklist_paths),
        "checklist_rows": len(rows),
        "status_counts": dict(sorted(counts.items())),
        "blank_rows": int(counts.get("blank", 0)),
        "invalid_rows": int(counts.get("invalid", 0)),
        "issues": issues,
    }


def count_missing_refs(root: Path, pack_dir: Path, manifest_rows: list[dict[str, Any]]) -> tuple[int, list[str]]:
    missing: list[str] = []
    for row in manifest_rows:
        row_id = str(row.get("candidate_id") or row.get("pair_id") or "")
        for key in REFERENCE_KEYS:
            ref_path = resolve_reference(root, pack_dir, row.get(key))
            if ref_path is not None and not ref_path.exists():
                missing.append(f"{row_id}:{key}:{portable_path(ref_path, root)}")
    return len(missing), missing[:20]


def summarize_pack(root: Path, pack_dir: Path) -> dict[str, Any]:
    manifest_path = pack_dir / "manifest.jsonl"
    manifest_rows = read_jsonl(manifest_path)
    preliminary_checklist_rows: list[dict[str, str]] = []
    for path in sorted(pack_dir.glob("*.csv"))[:2]:
        csv_rows, _issue = read_csv_with_issue(path)
        preliminary_checklist_rows.extend(csv_rows)
    kind = infer_kind(manifest_rows, preliminary_checklist_rows)
    checklist = summarize_checklists(pack_dir, kind)
    missing_refs, missing_ref_examples = count_missing_refs(root, pack_dir, manifest_rows)
    crop_files = sum(1 for folder in ("crops", "old", "new", "panels") for _ in (pack_dir / folder).glob("*.png"))
    page_files = sum(1 for folder in ("pages", "pages_old", "pages_new") for _ in (pack_dir / folder).glob("*.*"))
    issues: list[str] = []
    if not manifest_rows:
        issues.append("empty_manifest")
    if checklist["checklist_files"] == 0:
        issues.append("missing_checklist")
    if missing_refs:
        issues.append("missing_evidence_refs")
    if not (pack_dir / "index.html").exists():
        issues.append("missing_index_html")
    if not any((pack_dir / name).exists() for name in ("HUMAN_REVIEW_STEPS.md", "README.md")):
        issues.append("missing_instructions")
    issues.extend(checklist["issues"])
    ready_to_send = (
        bool(manifest_rows)
        and checklist["checklist_rows"] >= len(manifest_rows)
        and missing_refs == 0
        and (pack_dir / "index.html").exists()
        and checklist["checklist_files"] > 0
    )
    human_complete = (
        checklist["checklist_rows"] >= len(manifest_rows) > 0
        and checklist["blank_rows"] == 0
        and checklist["invalid_rows"] == 0
        and missing_refs == 0
    )
    return {
        "packet_id": pack_dir.name,
        "kind": kind,
        "folder_path": portable_path(pack_dir, root),
        "manifest_rows": len(manifest_rows),
        "checklist_files": checklist["checklist_files"],
        "checklist_rows": checklist["checklist_rows"],
        "blank_rows": checklist["blank_rows"],
        "invalid_rows": checklist["invalid_rows"],
        "status_counts": checklist["status_counts"],
        "missing_evidence_refs": missing_refs,
        "missing_ref_examples": missing_ref_examples,
        "crop_files": crop_files,
        "page_files": page_files,
        "has_index_html": (pack_dir / "index.html").exists(),
        "has_instructions": any((pack_dir / name).exists() for name in ("HUMAN_REVIEW_STEPS.md", "README.md")),
        "ready_to_send": ready_to_send,
        "human_complete": human_complete,
        "issues": sorted(set(issues)),
    }


def discover_pack_dirs(root: Path, review_pack_root: Path) -> list[Path]:
    if not review_pack_root.is_absolute():
        review_pack_root = root / review_pack_root
    if not review_pack_root.exists():
        return []
    return sorted(path for path in review_pack_root.iterdir() if path.is_dir() and (path / "manifest.jsonl").exists())


def build_report(
    root: str | Path,
    *,
    review_pack_root: str | Path = "derived/review_packs",
    date_label: str | None = None,
) -> dict[str, Any]:
    root = Path(root)
    pack_dirs = discover_pack_dirs(root, Path(review_pack_root))
    packs = [summarize_pack(root, pack_dir) for pack_dir in pack_dirs]
    totals: dict[str, Any] = {
        "date_label": date_label or date.today().isoformat(),
        "packs": len(packs),
        "ready_to_send": sum(1 for row in packs if row["ready_to_send"]),
        "human_complete": sum(1 for row in packs if row["human_complete"]),
        "manifest_rows": sum(int(row["manifest_rows"]) for row in packs),
        "checklist_rows": sum(int(row["checklist_rows"]) for row in packs),
        "blank_rows": sum(int(row["blank_rows"]) for row in packs),
        "invalid_rows": sum(int(row["invalid_rows"]) for row in packs),
        "missing_evidence_refs": sum(int(row["missing_evidence_refs"]) for row in packs),
        "missing_checklists": sum(1 for row in packs if "missing_checklist" in row["issues"]),
    }
    return {
        "date_label": totals["date_label"],
        "review_pack_root": portable_path(root / review_pack_root, root)
        if not Path(review_pack_root).is_absolute()
        else Path(review_pack_root).as_posix(),
        "totals": totals,
        "packs": packs,
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Supplemental Review Pack Index",
        "",
        f"- Date label: `{report['date_label']}`",
        f"- Review pack root: `{report['review_pack_root']}`",
        f"- Packs: `{totals['packs']}`",
        f"- Ready to send: `{totals['ready_to_send']}`",
        f"- Human complete: `{totals['human_complete']}`",
        f"- Manifest rows: `{totals['manifest_rows']}`",
        f"- Checklist rows: `{totals['checklist_rows']}`",
        f"- Blank checklist rows: `{totals['blank_rows']}`",
        f"- Missing evidence refs: `{totals['missing_evidence_refs']}`",
        f"- Missing checklists: `{totals['missing_checklists']}`",
        "- This index is for review-pack visibility only; it does not merge rows into gold.",
        "",
        "## Packs",
        "",
        "| Pack | Kind | Rows | Checklist | Blank | Missing Refs | Ready | Complete | Issues |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for row in report["packs"]:
        issues = ", ".join(row["issues"])
        lines.append(
            f"| `{row['packet_id']}` | {row['kind']} | {row['manifest_rows']} | "
            f"{row['checklist_rows']} | {row['blank_rows']} | {row['missing_evidence_refs']} | "
            f"{row['ready_to_send']} | {row['human_complete']} | {issues} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Index simple supplemental Eng_Bench review packs.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--review-pack-root", default="derived/review_packs")
    parser.add_argument("--date-label", default=date.today().isoformat())
    parser.add_argument("--output-json", default="")
    parser.add_argument("--output-md", default="")
    parser.add_argument("--output-csv", default="")
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = build_report(root, review_pack_root=args.review_pack_root, date_label=args.date_label)
    output_json = Path(args.output_json or f"derived/quality/supplemental_review_pack_index_{args.date_label}.json")
    output_md = Path(args.output_md or f"derived/quality/supplemental_review_pack_index_{args.date_label}.md")
    output_csv = Path(args.output_csv or f"derived/quality/supplemental_review_pack_index_{args.date_label}.csv")
    if not output_json.is_absolute():
        output_json = root / output_json
    if not output_md.is_absolute():
        output_md = root / output_md
    if not output_csv.is_absolute():
        output_csv = root / output_csv
    write_json(output_json, report)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    write_csv(output_csv, report["packs"])
    print(f"[OK] Wrote {output_json}")
    print(f"[OK] Wrote {output_md}")
    print(f"[OK] Wrote {output_csv}")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
