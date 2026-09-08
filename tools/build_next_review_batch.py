#!/usr/bin/env python3
"""Build a review-only packet from existing evidence-complete review queues."""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any, NamedTuple

import export_review_packs
import microtext_review_checklist
import verify_handoff_package
import visualdiff_review_checklist


TERMINAL_REVIEW_STATUSES = {
    "accepted",
    "accepted_for_merge",
    "edit",
    "edited",
    "invalid",
    "reject",
    "rejected",
    "valid",
}


class QueueSpec(NamedTuple):
    source_jsonl: str
    pack_name: str
    title: str


DEFAULT_QUEUES = [
    QueueSpec(
        "microtext/annotations/microtext_review_pin_label_batch_004.jsonl",
        "microtext_pin_label_batch_004",
        "Pin Label Batch 004",
    ),
    QueueSpec(
        "microtext/annotations/microtext_review_pin_label_batch_003.jsonl",
        "microtext_pin_label_batch_003",
        "Pin Label Batch 003",
    ),
    QueueSpec(
        "microtext/annotations/microtext_review_batch_001.jsonl",
        "microtext_batch_001",
        "Mixed Microtext Batch 001",
    ),
    QueueSpec(
        "microtext/annotations/microtext_review_pin_label_batch_005.jsonl",
        "microtext_pin_label_batch_005",
        "Pin Label Batch 005",
    ),
    QueueSpec(
        "microtext/annotations/microtext_review_pin_label_batch_002.jsonl",
        "microtext_pin_label_batch_002",
        "Pin Label Batch 002",
    ),
]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def parse_queue(value: str) -> QueueSpec:
    if "=" not in value:
        raise argparse.ArgumentTypeError("queue must be SOURCE_JSONL=PACK_NAME")
    source, pack_name = value.split("=", 1)
    source = source.strip()
    pack_name = pack_name.strip()
    if not source or not pack_name:
        raise argparse.ArgumentTypeError("queue source and pack name must be non-empty")
    return QueueSpec(source, pack_name, pack_name.replace("_", " ").title())


def row_candidate_id(row: dict[str, Any]) -> str:
    return str(row.get("candidate_id") or row.get("pair_id") or row.get("id") or "").strip()


def queue_kind(rows: list[dict[str, Any]]) -> str:
    return "visualdiff" if any(row.get("pair_id") or row.get("image_old") for row in rows) else "microtext"


def status_for(row: dict[str, Any]) -> str:
    for key in ("review_status", "human_status", "annotation_status", "status"):
        value = str(row.get(key) or "").strip().lower()
        if value:
            return value
    return ""


def active_microtext_candidate_ids(root: Path) -> set[str]:
    return {
        str(row.get("source_candidate_id"))
        for row in export_review_packs.load_jsonl(
            root / "microtext" / "annotations" / "microtext_items.jsonl"
        )
        if row.get("source_candidate_id")
    }


def active_visualdiff_pair_ids(root: Path) -> set[str]:
    return {
        str(row.get("pair_id") or row.get("id"))
        for row in export_review_packs.load_jsonl(
            root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"
        )
        if row.get("pair_id") or row.get("id")
    }


def reviewed_sibling_path(root: Path, spec: QueueSpec) -> Path:
    source = root / spec.source_jsonl
    return source.with_name(f"{source.stem}_reviewed{source.suffix}")


def reviewed_sibling_candidate_ids(root: Path, spec: QueueSpec) -> set[str]:
    sibling = reviewed_sibling_path(root, spec)
    ids: set[str] = set()
    for row in export_review_packs.load_jsonl(sibling):
        if status_for(row) not in TERMINAL_REVIEW_STATUSES:
            continue
        identifier = row_candidate_id(row)
        if identifier:
            ids.add(identifier)
    return ids


def queue_candidate_ids(root: Path, spec: QueueSpec) -> set[str]:
    return {
        identifier
        for identifier in (row_candidate_id(row) for row in export_review_packs.load_jsonl(root / spec.source_jsonl))
        if identifier
    }


def resolve_under_root(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def collect_manifest_candidate_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    if path.is_file() and path.name == "manifest.jsonl":
        manifests = [path]
    elif path.exists():
        manifests = sorted(path.rglob("manifest.jsonl"))
    else:
        return ids
    for manifest in manifests:
        for row in export_review_packs.load_jsonl(manifest):
            identifier = row_candidate_id(row)
            if identifier:
                ids.add(identifier)
    return ids


def active_packet_candidate_ids(root: Path, date_label: str) -> set[str]:
    index_path = root / "derived" / "quality" / f"human_packet_index_{date_label}.json"
    if not index_path.exists():
        return set()
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    ids: set[str] = set()
    for packet in payload.get("packets", []):
        if not packet.get("ready_to_send"):
            continue
        if int(packet.get("mergeable_rows") or 0) > 0:
            continue
        folder = Path(str(packet.get("folder_path") or ""))
        packet_root = resolve_under_root(root, folder)
        ids.update(collect_manifest_candidate_ids(packet_root))
    return ids


def audit_excluded_overlap(
    root: Path,
    queues: list[QueueSpec],
    exclude_roots: list[Path],
) -> dict[str, Any]:
    selected_by_pack = {
        spec.pack_name: queue_candidate_ids(root, spec)
        for spec in queues
    }
    missing_exclude_roots: list[str] = []
    excluded_ids: set[str] = set()
    resolved_exclude_roots: list[str] = []
    for exclude_root in exclude_roots:
        resolved = resolve_under_root(root, exclude_root)
        resolved_exclude_roots.append(resolved.as_posix())
        if not resolved.exists():
            missing_exclude_roots.append(resolved.as_posix())
            continue
        excluded_ids.update(collect_manifest_candidate_ids(resolved))

    overlaps: list[dict[str, str]] = []
    for spec in queues:
        for candidate_id in sorted(selected_by_pack[spec.pack_name] & excluded_ids):
            overlaps.append(
                {
                    "candidate_id": candidate_id,
                    "pack_name": spec.pack_name,
                    "source_jsonl": spec.source_jsonl,
                }
            )
    return {
        "exclude_roots": resolved_exclude_roots,
        "missing_exclude_roots": missing_exclude_roots,
        "excluded_candidate_count": len(excluded_ids),
        "selected_candidate_count": sum(len(ids) for ids in selected_by_pack.values()),
        "overlap_count": len(overlaps),
        "overlap_examples": overlaps[:25],
    }


def reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def copy_tree_contents(source: Path, destination: Path) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    copied = 0
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
            copied += sum(1 for path in child.rglob("*") if path.is_file())
        elif child.is_file():
            shutil.copy2(child, target)
            copied += 1
    return copied


def filter_fresh_rows(
    root: Path,
    spec: QueueSpec,
    rows: list[dict[str, Any]],
    exclude_reviewed_siblings: bool,
    exclude_active_gold: bool,
    exclude_candidate_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    reviewed_ids = reviewed_sibling_candidate_ids(root, spec) if exclude_reviewed_siblings else set()
    kind = queue_kind(rows)
    if not exclude_active_gold:
        active_ids: set[str] = set()
    elif kind == "visualdiff":
        active_ids = active_visualdiff_pair_ids(root)
    else:
        active_ids = active_microtext_candidate_ids(root)
    excluded_ids = exclude_candidate_ids or set()
    filtered: list[dict[str, Any]] = []
    stats = {
        "source_rows": len(rows),
        "excluded_reviewed_rows": 0,
        "excluded_active_gold_rows": 0,
        "excluded_packet_rows": 0,
        "fresh_rows": 0,
    }
    for row in rows:
        identifier = row_candidate_id(row)
        if identifier and identifier in reviewed_ids:
            stats["excluded_reviewed_rows"] += 1
            continue
        if identifier and identifier in active_ids:
            stats["excluded_active_gold_rows"] += 1
            continue
        if identifier and identifier in excluded_ids:
            stats["excluded_packet_rows"] += 1
            continue
        filtered.append(row)
    stats["fresh_rows"] = len(filtered)
    return filtered, stats


def export_queue_pack(
    root: Path,
    spec: QueueSpec,
    pad_px: int,
    exclude_reviewed_siblings: bool,
    exclude_active_gold: bool,
    exclude_candidate_ids: set[str] | None = None,
    max_image_pixels: int | None = None,
) -> dict[str, Any]:
    source_rows = export_review_packs.load_jsonl(root / spec.source_jsonl)
    rows, freshness = filter_fresh_rows(
        root=root,
        spec=spec,
        rows=source_rows,
        exclude_reviewed_siblings=exclude_reviewed_siblings,
        exclude_active_gold=exclude_active_gold,
        exclude_candidate_ids=exclude_candidate_ids,
    )
    if not rows:
        return {
            "source_jsonl": spec.source_jsonl,
            "pack_name": spec.pack_name,
            "title": spec.title,
            "rows": 0,
            "source_pages": 0,
            "missing_images": 0,
            "out_of_frame": 0,
            "checklist": "",
            "pack_valid": True,
            "pack_issues": [],
            "skipped": True,
            **freshness,
        }

    pack_rel = Path("derived") / "review_packs" / spec.pack_name
    kind = queue_kind(rows)
    if kind == "visualdiff":
        stats = export_review_packs.export_visualdiff_pack(
            root,
            rows,
            pack_rel,
            pad_px=pad_px,
            max_image_pixels=max_image_pixels,
        )
    else:
        stats = export_review_packs.export_microtext_pack(
            root,
            rows,
            pack_rel,
            pad_px=pad_px,
            max_image_pixels=max_image_pixels,
        )
    manifest_path = root / pack_rel / "manifest.jsonl"
    manifest_rows = export_review_packs.load_jsonl(manifest_path)
    checklist_rows = (
        visualdiff_review_checklist.export_rows(manifest_rows)
        if kind == "visualdiff"
        else microtext_review_checklist.export_rows(manifest_rows)
    )
    checklist_name = f"{spec.pack_name}_validation_checklist.csv"
    if kind == "visualdiff":
        visualdiff_review_checklist.write_csv(root / pack_rel / checklist_name, checklist_rows)
    else:
        microtext_review_checklist.write_csv(root / pack_rel / checklist_name, checklist_rows)
    pack_report = verify_handoff_package.verify_pack_dir(root / pack_rel)
    return {
        "source_jsonl": spec.source_jsonl,
        "pack_name": spec.pack_name,
        "title": spec.title,
        "kind": kind,
        "rows": int(stats.get("rows", 0)),
        "source_pages": int(
            stats.get("source_pages", 0)
            or (int(stats.get("old_source_pages", 0)) + int(stats.get("new_source_pages", 0)))
        ),
        "missing_images": int(stats.get("missing_images", 0)),
        "out_of_frame": int(stats.get("out_of_frame", 0)),
        "checklist": checklist_name,
        "pack_valid": bool(pack_report["valid"]),
        "pack_issues": pack_report["issues"],
        "skipped": False,
        **freshness,
    }


def write_batch_docs(batch_dir: Path, rows: list[dict[str, Any]], date_label: str) -> None:
    total_rows = sum(int(row["rows"]) for row in rows)
    readme = "\n".join(
        [
            "# Eng_Bench v2.0 Next Review Batch",
            "",
            "This packet is additional human-review work after the current 5/25 packet is complete.",
            "",
            f"- Date: `{date_label}`",
            f"- Review rows: `{total_rows}`",
            f"- Packs: `{len(rows)}`",
            "",
            "## How To Review",
            "",
            "1. Open `HUMAN_REVIEW_STEPS.md`.",
            "2. Work one pack at a time under `review_packs/`.",
            "3. Open each pack's `index.html` for visual browsing.",
            "4. Fill only the `*_validation_checklist.csv` file in that pack.",
            "5. Do not edit `manifest.jsonl` or source JSONL files.",
            "",
        ]
    )
    steps = [
        "# Human Review Steps",
        "",
        "For microtext checklist rows, use:",
        "",
        "- `accepted`: proposed text is visible and correct.",
        "- `edited`: crop is readable but proposed text/category needs correction.",
        "- `rejected`: crop is not a single readable engineering label or is wrong.",
        "- `needs_full_page`: crop is insufficient; use the linked `pages/...` image.",
        "",
        "For visualdiff checklist rows, use:",
        "",
        "- `edit`: a real in-crop change is visible; write `human_description`.",
        "- `valid`: only for rows whose existing description is already correct.",
        "- `reject_unclear`: no in-crop change, duplicate fragment, or ambiguous evidence.",
        "- `needs_full_page`: use the linked old/new full-sheet images.",
        "- If old/new crops are visually identical, use `reject_unclear` and note `old/new identical`.",
        "- If the only difference is a whole-crop/page alignment shift, use `reject_unclear` and note `crop/alignment shift only`.",
        "- Use `edit`, not `layout`, when a specific object, label, symbol, wire, table cell, or other drawing element moved relative to nearby drawing content; describe the move in `human_description`.",
        "",
        "For `edited`, fill `corrected_text`. If the category is wrong or `unknown_microtext`, fill `corrected_category`.",
        "",
        "Recommended order:",
        "",
    ]
    for idx, row in enumerate(rows, start=1):
        steps.append(
            f"{idx}. `{row['pack_name']}`: `{row['rows']}` rows, checklist `{row['checklist']}`"
        )
    steps.extend(
        [
            "",
            "Return the full completed folder, including `review_packs/`, so evidence paths can be audited.",
            "",
        ]
    )
    batch_dir.mkdir(parents=True, exist_ok=True)
    (batch_dir / "README.md").write_text(readme, encoding="utf-8")
    (batch_dir / "HUMAN_REVIEW_STEPS.md").write_text("\n".join(steps), encoding="utf-8")


def render_report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Next Review Batch Build Report",
        "",
        f"- Valid: `{str(report.get('valid', False)).lower()}`",
        f"- Rows: `{report.get('rows', 0)}`",
        f"- Source pages: `{report.get('source_pages', 0)}`",
        f"- Copied files: `{report.get('copied_files', 0)}`",
        f"- ZIP entries: `{report.get('zip_entries', 0)}`",
        f"- Issues: `{len(report.get('issues', []))}`",
        f"- Overlap count: `{report.get('overlap_count', 0)}`",
        f"- Source rows considered: `{report.get('source_rows', 0)}`",
        f"- Excluded reviewed rows: `{report.get('excluded_reviewed_rows', 0)}`",
        f"- Excluded active-gold rows: `{report.get('excluded_active_gold_rows', 0)}`",
        f"- Excluded active-packet rows: `{report.get('excluded_packet_rows', 0)}`",
        f"- Active packet candidate IDs loaded: `{report.get('active_packet_candidate_count', 0)}`",
        f"- Opt-in raster pixel ceiling: `{report.get('max_image_pixels', '')}`",
        "",
        "## Packs",
        "",
        "| Pack | Rows | Pages | Missing Images | Out Of Frame | Valid |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in report.get("packs", []):
        lines.append(
            f"| `{row['pack_name']}` | {row['rows']} | {row['source_pages']} | "
            f"{row['missing_images']} | {row['out_of_frame']} | {str(row['pack_valid']).lower()} |"
        )
    if report.get("skipped_packs"):
        lines.extend(["", "## Skipped Packs", ""])
        lines.append("| Pack | Source Rows | Excluded Reviewed | Excluded Active Gold | Excluded Active Packet |")
        lines.append("| --- | ---: | ---: | ---: | ---: |")
        for row in report["skipped_packs"]:
            lines.append(
                f"| `{row['pack_name']}` | {row.get('source_rows', 0)} | "
                f"{row.get('excluded_reviewed_rows', 0)} | {row.get('excluded_active_gold_rows', 0)} | "
                f"{row.get('excluded_packet_rows', 0)} |"
            )
    if report.get("issues"):
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {issue}" for issue in report["issues"])
    if report.get("overlap_examples"):
        lines.extend(["", "## Overlap Examples", ""])
        for row in report["overlap_examples"]:
            lines.append(
                f"- `{row['candidate_id']}` in pack `{row['pack_name']}` from `{row['source_jsonl']}`"
            )
    lines.append("")
    return "\n".join(lines)


def zip_directory(source_dir: Path, zip_output: Path) -> int:
    zip_output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(zip_output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(source_dir.parent).as_posix())
                count += 1
    return count


def build_next_batch(
    root: Path,
    queues: list[QueueSpec],
    batch_dir: Path,
    zip_output: Path,
    date_label: str,
    pad_px: int,
    exclude_roots: list[Path] | None = None,
    allow_overlap: bool = False,
    exclude_reviewed_siblings: bool = True,
    exclude_active_gold: bool = True,
    exclude_active_packet_date_label: str = "",
    max_image_pixels: int | None = None,
) -> dict[str, Any]:
    if not batch_dir.is_absolute():
        batch_dir = root / batch_dir
    if not zip_output.is_absolute():
        zip_output = root / zip_output

    active_packet_ids = (
        active_packet_candidate_ids(root, exclude_active_packet_date_label)
        if exclude_active_packet_date_label
        else set()
    )
    overlap_report = audit_excluded_overlap(root, queues, exclude_roots or [])
    preflight_issues = [
        f"missing exclude root: {path}"
        for path in overlap_report["missing_exclude_roots"]
    ]
    if overlap_report["overlap_count"] and not allow_overlap:
        preflight_issues.append(
            f"selected queues overlap prior packet manifests: {overlap_report['overlap_count']} candidate ids"
        )
    if preflight_issues:
        batch_dir.mkdir(parents=True, exist_ok=True)
        report = {
            "batch_dir": batch_dir.as_posix(),
            "zip_output": zip_output.as_posix(),
            "date_label": date_label,
            "packs": [
                {
                    "source_jsonl": spec.source_jsonl,
                    "pack_name": spec.pack_name,
                    "title": spec.title,
                    "rows": 0,
                    "source_pages": 0,
                    "missing_images": 0,
                    "out_of_frame": 0,
                    "checklist": "",
                    "pack_valid": False,
                    "pack_issues": preflight_issues,
                }
                for spec in queues
            ],
            "pack_verifications": [],
            "copied_files": 0,
            "rows": 0,
            "source_pages": 0,
            "zip_entries": 0,
            "issues": preflight_issues,
            "valid": False,
            "excluded_packet_rows": 0,
            "active_packet_date_label": exclude_active_packet_date_label,
            "active_packet_candidate_count": len(active_packet_ids),
            "max_image_pixels": max_image_pixels or "",
            **overlap_report,
        }
        write_json(batch_dir / "next_review_batch_build_report.json", report)
        (batch_dir / "next_review_batch_build_report.md").write_text(
            render_report_markdown(report),
            encoding="utf-8",
        )
        return report

    reset_dir(batch_dir)
    review_pack_root = batch_dir / "review_packs"
    review_pack_root.mkdir(parents=True, exist_ok=True)

    pack_rows: list[dict[str, Any]] = []
    skipped_pack_rows: list[dict[str, Any]] = []
    copied_files = 0
    for spec in queues:
        row = export_queue_pack(
            root=root,
            spec=spec,
            pad_px=pad_px,
            exclude_reviewed_siblings=exclude_reviewed_siblings,
            exclude_active_gold=exclude_active_gold,
            exclude_candidate_ids=active_packet_ids,
            max_image_pixels=max_image_pixels,
        )
        if row["rows"] == 0:
            skipped_pack_rows.append(row)
            continue
        pack_rows.append(row)
        copied_files += copy_tree_contents(
            root / "derived" / "review_packs" / spec.pack_name,
            review_pack_root / spec.pack_name,
        )

    write_batch_docs(batch_dir, pack_rows, date_label)
    write_csv(batch_dir / "NEXT_REVIEW_BATCH_MANIFEST.csv", pack_rows)

    pack_verifications = [
        verify_handoff_package.verify_pack_dir(path)
        for path in sorted(review_pack_root.iterdir())
        if path.is_dir()
    ]
    issues = [
        f"{Path(pack['pack']).name}: {issue}"
        for pack in pack_verifications
        for issue in pack["issues"]
    ]
    report = {
        "batch_dir": batch_dir.as_posix(),
        "zip_output": zip_output.as_posix(),
        "date_label": date_label,
        "packs": pack_rows,
        "skipped_packs": skipped_pack_rows,
        "pack_verifications": pack_verifications,
        "copied_files": copied_files,
        "rows": sum(int(row["rows"]) for row in pack_rows),
        "source_rows": sum(int(row.get("source_rows", 0)) for row in pack_rows + skipped_pack_rows),
        "excluded_reviewed_rows": sum(
            int(row.get("excluded_reviewed_rows", 0)) for row in pack_rows + skipped_pack_rows
        ),
        "excluded_active_gold_rows": sum(
            int(row.get("excluded_active_gold_rows", 0)) for row in pack_rows + skipped_pack_rows
        ),
        "excluded_packet_rows": sum(
            int(row.get("excluded_packet_rows", 0)) for row in pack_rows + skipped_pack_rows
        ),
        "active_packet_date_label": exclude_active_packet_date_label,
        "active_packet_candidate_count": len(active_packet_ids),
        "max_image_pixels": max_image_pixels or "",
        "source_pages": sum(int(row["source_pages"]) for row in pack_rows),
        "issues": issues,
        "valid": not issues and bool(pack_rows) and all(row["pack_valid"] for row in pack_rows),
        **overlap_report,
    }
    write_json(batch_dir / "next_review_batch_build_report.json", report)
    (batch_dir / "next_review_batch_build_report.md").write_text(
        render_report_markdown(report),
        encoding="utf-8",
    )
    entries = zip_directory(batch_dir, zip_output)
    report["zip_entries"] = entries
    write_json(batch_dir / "next_review_batch_build_report.json", report)
    (batch_dir / "next_review_batch_build_report.md").write_text(
        render_report_markdown(report),
        encoding="utf-8",
    )
    zip_directory(batch_dir, zip_output)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the next Eng_Bench human review batch.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--date-label", default="2026-06-03")
    parser.add_argument(
        "--batch-dir",
        default="derived/human_adjudication/2026-06-03_v2_0_next_review_batch",
    )
    parser.add_argument(
        "--zip-output",
        default="derived/human_adjudication/Eng_Bench_v2_0_next_review_batch_2026-06-03.zip",
    )
    parser.add_argument("--pad-px", type=int, default=32)
    parser.add_argument(
        "--max-image-pixels",
        type=int,
        default=None,
        help="Opt-in Pillow safety ceiling for audited large raster sources.",
    )
    parser.add_argument(
        "--queue",
        action="append",
        type=parse_queue,
        help="Queue as SOURCE_JSONL=PACK_NAME. Defaults to the highest-yield evidence-complete queues.",
    )
    parser.add_argument(
        "--exclude-root",
        action="append",
        type=Path,
        default=[],
        help="Existing packet/review-pack folder whose manifest candidate IDs must not be repeated.",
    )
    parser.add_argument(
        "--allow-overlap",
        action="store_true",
        help="Build even if selected queues overlap exclude-root manifests.",
    )
    parser.add_argument(
        "--include-reviewed-siblings",
        action="store_true",
        help="Include rows already present in sibling *_reviewed.jsonl files.",
    )
    parser.add_argument(
        "--include-active-gold",
        action="store_true",
        help="Include rows whose candidate IDs are already active microtext gold.",
    )
    parser.add_argument(
        "--exclude-active-packet-index",
        default="",
        metavar="DATE_LABEL",
        help="Exclude candidate IDs already present in ready packets from human_packet_index_<DATE_LABEL>.json.",
    )
    args = parser.parse_args(argv)

    report = build_next_batch(
        root=Path(args.root),
        queues=args.queue or DEFAULT_QUEUES,
        batch_dir=Path(args.batch_dir),
        zip_output=Path(args.zip_output),
        date_label=args.date_label,
        pad_px=args.pad_px,
        exclude_roots=args.exclude_root,
        allow_overlap=args.allow_overlap,
        exclude_reviewed_siblings=not args.include_reviewed_siblings,
        exclude_active_gold=not args.include_active_gold,
        exclude_active_packet_date_label=args.exclude_active_packet_index,
        max_image_pixels=args.max_image_pixels,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
