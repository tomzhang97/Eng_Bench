#!/usr/bin/env python3
"""Audit a VisualDiff family-expansion packet before human review or promotion."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops

from audit_active_gold_provenance import file_sha256, read_csv, read_jsonl, rights_blocker


EVIDENCE_FIELDS = (
    "old_crop_path",
    "new_crop_path",
    "panel_path",
    "old_page_path",
    "new_page_path",
)


def _bool(value: Any) -> bool:
    return bool(value)


def _packet_evidence_path(root: Path, packet_root: Path, value: Any) -> Path:
    relative = Path(str(value or ""))
    if not str(value or "").strip():
        return Path()

    parts = relative.parts
    if packet_root.name in parts:
        index = parts.index(packet_root.name)
        candidate = packet_root.joinpath(*parts[index + 1 :])
        if candidate.is_file():
            return candidate

    root_candidate = root / relative
    if root_candidate.is_file():
        return root_candidate
    packet_candidate = packet_root / relative
    if packet_candidate.is_file():
        return packet_candidate
    return packet_root / relative.name


def _source_path(root: Path, inventory: dict[str, str], manifest: dict[str, Any]) -> Path:
    value = str(
        inventory.get("path")
        or inventory.get("source_path")
        or manifest.get("path")
        or ""
    ).strip()
    return root / value if value else Path()


def _page_belongs_to_doc(image_path: Any, doc: dict[str, Any]) -> bool:
    image = Path(str(image_path or ""))
    derived = doc.get("derived") if isinstance(doc.get("derived"), dict) else {}
    pages_dir = Path(str(derived.get("pages_dir") or ""))
    if not image.parts or not pages_dir.parts:
        return False
    try:
        image.relative_to(pages_dir)
        return True
    except ValueError:
        return False


def _changed_pixel_ratio(old_path: Path, new_path: Path) -> tuple[tuple[int, int], tuple[int, int], float]:
    with Image.open(old_path) as old_image, Image.open(new_path) as new_image:
        old_size = old_image.size
        new_size = new_image.size
        canvas_size = (max(old_size[0], new_size[0]), max(old_size[1], new_size[1]))
        old_canvas = Image.new("RGB", canvas_size, "white")
        new_canvas = Image.new("RGB", canvas_size, "white")
        old_canvas.paste(old_image.convert("RGB"), (0, 0))
        new_canvas.paste(new_image.convert("RGB"), (0, 0))
        difference = ImageChops.difference(old_canvas, new_canvas)
        channels = difference.split()
        changed_mask = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
        histogram = changed_mask.histogram()
        total = canvas_size[0] * canvas_size[1]
        changed = total - histogram[0]
        return old_size, new_size, changed / total if total else 0.0


def _region_key(row: dict[str, Any]) -> str:
    return json.dumps(
        [
            row.get("project_id"),
            row.get("page_old"),
            row.get("page_new"),
            row.get("bbox_old"),
            row.get("bbox_new"),
        ],
        separators=(",", ":"),
        sort_keys=True,
    )


def build_report(
    root: Path,
    packet_root: Path,
    date_label: str | None = None,
    input_path: Path | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    packet_root = packet_root.resolve()
    input_path = input_path.resolve() if input_path else packet_root / "manifest.jsonl"
    packet_rows = read_jsonl(input_path)
    manifest_rows = read_jsonl(root / "manifest.jsonl")
    docs = {
        str(row.get("doc_id")): row
        for row in manifest_rows
        if row.get("type") == "doc" and row.get("doc_id")
    }
    pairs = {
        str(row.get("pair_id")): row
        for row in manifest_rows
        if row.get("type") == "pair" and row.get("pair_id")
    }
    inventory = {
        str(row.get("doc_id")): row
        for row in read_csv(root / "SOURCE_INVENTORY.csv")
        if row.get("doc_id")
    }
    active_rows = read_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl")
    active_projects = {
        str(row.get("project_id") or "")
        for row in active_rows
        if row.get("project_id")
    }

    pair_id_counts = Counter(str(row.get("pair_id") or "") for row in packet_rows)
    region_key_counts = Counter(_region_key(row) for row in packet_rows)
    packet_projects = {
        str(row.get("project_id") or "")
        for row in packet_rows
        if row.get("project_id")
    }
    referenced_doc_ids: set[str] = set()
    document_rows: dict[str, dict[str, Any]] = {}

    def audit_doc(doc_id: str) -> dict[str, Any]:
        if doc_id in document_rows:
            return document_rows[doc_id]
        manifest = docs.get(doc_id, {})
        source = inventory.get(doc_id, {})
        path = _source_path(root, source, manifest)
        path_exists = bool(str(path)) and path.is_file()
        computed_sha = file_sha256(path) if path_exists else ""
        recorded_sha = str(manifest.get("sha256") or "").strip().lower()
        public_status = str(source.get("public_status") or manifest.get("public_status") or "").strip()
        source_url = str(source.get("source_url") or manifest.get("source_url") or "").strip()
        blocker = rights_blocker(public_status)
        issues: list[str] = []
        if not source:
            issues.append("missing_inventory_record")
        if not manifest:
            issues.append("missing_manifest_doc")
        if not path_exists:
            issues.append("missing_source_payload")
        if not source_url:
            issues.append("missing_source_url")
        if blocker:
            issues.append(f"rights_blocked:{blocker}")
        if not recorded_sha:
            issues.append("missing_recorded_sha256")
        elif path_exists and computed_sha != recorded_sha:
            issues.append("source_sha256_mismatch")
        result = {
            "doc_id": doc_id,
            "inventory_present": _bool(source),
            "manifest_present": _bool(manifest),
            "source_path": str(path.relative_to(root)) if path_exists else str(path),
            "source_payload_exists": path_exists,
            "source_url": source_url,
            "public_status": public_status,
            "rights_blocker": blocker,
            "recorded_sha256": recorded_sha,
            "computed_sha256": computed_sha,
            "sha256_matches": _bool(recorded_sha and computed_sha == recorded_sha),
            "issues": issues,
            "passes": not issues,
        }
        document_rows[doc_id] = result
        return result

    row_results: list[dict[str, Any]] = []
    for row_number, row in enumerate(packet_rows, start=1):
        pair_id = str(row.get("pair_id") or "")
        project_id = str(row.get("project_id") or "")
        issues: list[str] = []
        warnings: list[str] = []
        if not pair_id:
            issues.append("missing_pair_id")
        elif pair_id_counts[pair_id] > 1:
            issues.append("duplicate_pair_id")
        if not project_id:
            issues.append("missing_project_id")
        elif project_id in active_projects:
            issues.append("project_overlaps_active_gold")
        if region_key_counts[_region_key(row)] > 1:
            issues.append("duplicate_region_key")

        manifest_pair = pairs.get(project_id, {})
        if not manifest_pair:
            issues.append("missing_manifest_revision_pair")
        old_doc_id = str(manifest_pair.get("from_doc_id") or "")
        new_doc_id = str(manifest_pair.get("to_doc_id") or "")
        if not old_doc_id or not new_doc_id:
            issues.append("manifest_revision_pair_missing_side")
        elif old_doc_id == new_doc_id:
            issues.append("revision_pair_reuses_same_doc")

        old_doc = docs.get(old_doc_id, {})
        new_doc = docs.get(new_doc_id, {})
        if old_doc_id:
            referenced_doc_ids.add(old_doc_id)
            old_source = audit_doc(old_doc_id)
            if not old_source["passes"]:
                issues.append("old_source_not_paper_ready")
        else:
            old_source = {}
        if new_doc_id:
            referenced_doc_ids.add(new_doc_id)
            new_source = audit_doc(new_doc_id)
            if not new_source["passes"]:
                issues.append("new_source_not_paper_ready")
        else:
            new_source = {}
        if (
            old_source.get("computed_sha256")
            and old_source.get("computed_sha256") == new_source.get("computed_sha256")
        ):
            issues.append("revision_source_payloads_identical")

        if not _page_belongs_to_doc(row.get("image_old"), old_doc):
            issues.append("old_source_page_not_linked_to_manifest_doc")
        if not _page_belongs_to_doc(row.get("image_new"), new_doc):
            issues.append("new_source_page_not_linked_to_manifest_doc")
        for field in ("image_old", "image_new"):
            value = str(row.get(field) or "")
            if not value or not (root / value).is_file():
                issues.append(f"missing_source_page:{field}")

        row_packet_root = packet_root
        review_pack = str(row.get("_review_pack") or "").strip()
        if review_pack:
            row_packet_root = root / "derived" / "review_packs" / review_pack
        evidence_paths: dict[str, Path] = {}
        for field in EVIDENCE_FIELDS:
            path = _packet_evidence_path(root, row_packet_root, row.get(field))
            evidence_paths[field] = path
            if not path.is_file():
                issues.append(f"missing_packet_evidence:{field}")

        old_size: tuple[int, int] | tuple[()] = ()
        new_size: tuple[int, int] | tuple[()] = ()
        changed_ratio: float | None = None
        old_crop = evidence_paths["old_crop_path"]
        new_crop = evidence_paths["new_crop_path"]
        if old_crop.is_file() and new_crop.is_file():
            try:
                old_size, new_size, changed_ratio = _changed_pixel_ratio(old_crop, new_crop)
                if old_size != new_size:
                    warnings.append("crop_dimensions_differ")
                if changed_ratio == 0:
                    issues.append("crop_pair_pixel_identical")
            except (OSError, ValueError) as exc:
                issues.append(f"crop_compare_failed:{type(exc).__name__}")

        row_results.append(
            {
                "row_number": row_number,
                "pair_id": pair_id,
                "project_id": project_id,
                "old_doc_id": old_doc_id,
                "new_doc_id": new_doc_id,
                "old_crop_size": list(old_size),
                "new_crop_size": list(new_size),
                "changed_pixel_ratio": changed_ratio,
                "issues": list(dict.fromkeys(issues)),
                "warnings": list(dict.fromkeys(warnings)),
                "passes": not issues,
            }
        )

    document_list = [document_rows[doc_id] for doc_id in sorted(document_rows)]
    issue_counts = Counter(issue for row in row_results for issue in row["issues"])
    warning_counts = Counter(warning for row in row_results for warning in row["warnings"])
    document_issue_counts = Counter(issue for row in document_list for issue in row["issues"])
    project_overlap = sorted(packet_projects & active_projects)
    ratios = [
        row["changed_pixel_ratio"]
        for row in row_results
        if isinstance(row.get("changed_pixel_ratio"), float) and row["changed_pixel_ratio"] >= 0
    ]
    totals = {
        "packet_rows": len(packet_rows),
        "unique_pair_ids": len({row["pair_id"] for row in row_results if row["pair_id"]}),
        "candidate_revision_families": len(packet_projects),
        "referenced_source_docs": len(referenced_doc_ids),
        "paper_ready_source_docs": sum(row["passes"] for row in document_list),
        "active_gold_project_overlaps": len(project_overlap),
        "passing_rows": sum(row["passes"] for row in row_results),
        "blocked_rows": sum(not row["passes"] for row in row_results),
        "exact_pixel_identical_crop_pairs": issue_counts["crop_pair_pixel_identical"],
        "minimum_changed_pixel_ratio": min(ratios) if ratios else None,
    }
    return {
        "date_label": date_label or date.today().isoformat(),
        "input_path": str(input_path.relative_to(root)) if input_path.is_relative_to(root) else str(input_path),
        "packet_root": str(packet_root.relative_to(root)) if packet_root.is_relative_to(root) else str(packet_root),
        "passes": _bool(packet_rows) and totals["blocked_rows"] == 0,
        "totals": totals,
        "issue_counts": dict(sorted(issue_counts.items())),
        "warning_counts": dict(sorted(warning_counts.items())),
        "document_issue_counts": dict(sorted(document_issue_counts.items())),
        "active_gold_project_overlaps": project_overlap,
        "documents": document_list,
        "rows": row_results,
    }


def write_json(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "row_number",
        "pair_id",
        "project_id",
        "old_doc_id",
        "new_doc_id",
        "changed_pixel_ratio",
        "passes",
        "issues",
        "warnings",
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in report["rows"]:
            output = {field: row.get(field, "") for field in fields}
            output["issues"] = ";".join(row["issues"])
            output["warnings"] = ";".join(row["warnings"])
            writer.writerow(output)


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    totals = report["totals"]
    lines = [
        "# VisualDiff Family Packet Audit",
        "",
        f"- Date: `{report['date_label']}`",
        f"- Packet: `{report['packet_root']}`",
        f"- Input rows: `{report['input_path']}`",
        f"- Gate: `{'PASS' if report['passes'] else 'BLOCKED'}`",
        f"- Rows: {totals['packet_rows']} ({totals['passing_rows']} passing, {totals['blocked_rows']} blocked)",
        f"- Candidate revision families: {totals['candidate_revision_families']}",
        f"- Source documents: {totals['paper_ready_source_docs']}/{totals['referenced_source_docs']} paper-ready",
        f"- Active-gold family overlaps: {totals['active_gold_project_overlaps']}",
        f"- Exact pixel-identical crop pairs: {totals['exact_pixel_identical_crop_pairs']}",
        f"- Minimum changed-pixel ratio: {totals['minimum_changed_pixel_ratio']}",
        "",
        "## Blocking Issues",
        "",
    ]
    if report["issue_counts"] or report["document_issue_counts"]:
        for issue, count in report["document_issue_counts"].items():
            lines.append(f"- Source `{issue}`: {count}")
        for issue, count in report["issue_counts"].items():
            lines.append(f"- Row `{issue}`: {count}")
    else:
        lines.append("- None.")
    lines.extend(["", "## Non-blocking Warnings", ""])
    if report["warning_counts"]:
        for warning, count in report["warning_counts"].items():
            lines.append(f"- Row `{warning}`: {count}")
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "A PASS means the packet is structurally ready for human review. It does not make any row gold: reviewers must still validate the visible change, category, and description, and promotion must rerun the benchmark gates.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--packet-root", type=Path, required=True)
    parser.add_argument(
        "--input",
        type=Path,
        help="Optional JSONL input. Rows with _review_pack resolve evidence under derived/review_packs/<name>.",
    )
    parser.add_argument("--date-label")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    parser.add_argument("--output-csv", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    packet_root = args.packet_root
    if not packet_root.is_absolute():
        packet_root = root / packet_root
    input_path = args.input
    if input_path is not None and not input_path.is_absolute():
        input_path = root / input_path
    report = build_report(root, packet_root, args.date_label, input_path)
    if args.output_json:
        write_json(root / args.output_json if not args.output_json.is_absolute() else args.output_json, report)
    if args.output_md:
        write_markdown(root / args.output_md if not args.output_md.is_absolute() else args.output_md, report)
    if args.output_csv:
        write_csv(root / args.output_csv if not args.output_csv.is_absolute() else args.output_csv, report)
    totals = report["totals"]
    print(
        "VisualDiff family packet audit: "
        f"{'PASS' if report['passes'] else 'BLOCKED'}; "
        f"rows={totals['packet_rows']}; families={totals['candidate_revision_families']}; "
        f"paper_ready_sources={totals['paper_ready_source_docs']}/{totals['referenced_source_docs']}; "
        f"blocked_rows={totals['blocked_rows']}"
    )
    return 0 if report["passes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
