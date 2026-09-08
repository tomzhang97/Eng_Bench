#!/usr/bin/env python3
"""Audit active Eng_Bench gold rows for traceable, reproducible source provenance."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

try:
    from .source_rights import BLOCKING_STATUS_MARKERS, rights_blocker
except ImportError:
    from source_rights import BLOCKING_STATUS_MARKERS, rights_blocker


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def normalize_identifier(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def normalize_revision(value: Any) -> str:
    normalized = normalize_identifier(value)
    for prefix in ("pcb", "revision", "rev", "doc"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    return normalized


def manifest_revision_values(row: dict[str, Any]) -> set[str]:
    version = row.get("version")
    if not isinstance(version, dict):
        return set()
    values: set[str] = set()
    for value in version.values():
        if isinstance(value, list):
            values.update(normalize_revision(item) for item in value if normalize_revision(item))
        elif normalize_revision(value):
            values.add(normalize_revision(value))
    return values


def pair_candidates(project_id: str) -> list[str]:
    candidates = [project_id]
    if "__to__" in project_id:
        candidates.append(project_id.replace("__to__", "_to__"))
    if "_to__" in project_id:
        candidates.append(project_id.replace("_to__", "__to__"))
    return candidates


def manifest_maps(
    root: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    rows = read_jsonl(root / "manifest.jsonl")
    docs = {
        str(row["doc_id"]): row
        for row in rows
        if row.get("type") == "doc" and row.get("doc_id")
    }
    pairs = {
        str(row["pair_id"]): row
        for row in rows
        if row.get("type") == "pair" and row.get("pair_id")
    }
    return docs, pairs


def visualdiff_doc_for_version(
    family: str,
    revision: str,
    docs: dict[str, dict[str, Any]],
) -> str:
    family_key = normalize_identifier(family)
    revision_key = normalize_revision(revision)
    matches = []
    for doc_id, row in docs.items():
        if row.get("task") != "visualdiff":
            continue
        haystack = normalize_identifier(
            " ".join(str(row.get(field) or "") for field in ("doc_id", "same_model_id", "path"))
        )
        if family_key and family_key not in haystack:
            continue
        if revision_key and revision_key not in manifest_revision_values(row):
            continue
        matches.append(doc_id)
    return matches[0] if len(matches) == 1 else ""


def resolve_visualdiff_docs(
    pair: dict[str, Any],
    docs: dict[str, dict[str, Any]],
    manifest_pairs: dict[str, dict[str, Any]],
) -> tuple[list[str], str]:
    project_id = str(pair.get("project_id") or pair.get("pair_id") or "")
    manifest_pair = next(
        (manifest_pairs[candidate] for candidate in pair_candidates(project_id) if candidate in manifest_pairs),
        None,
    )
    if manifest_pair:
        resolved = [
            str(manifest_pair.get(field) or "")
            for field in ("from_doc_id", "to_doc_id")
            if manifest_pair.get(field)
        ]
        return list(dict.fromkeys(resolved)), "" if len(resolved) == 2 else "manifest_pair_missing_side"

    family = str(pair.get("doc_id") or "")
    old_doc = visualdiff_doc_for_version(family, str(pair.get("version_id_old") or ""), docs)
    new_doc = visualdiff_doc_for_version(family, str(pair.get("version_id_new") or ""), docs)
    resolved = list(dict.fromkeys(doc_id for doc_id in (old_doc, new_doc) if doc_id))
    return resolved, "" if len(resolved) == 2 else "could_not_resolve_both_revision_docs"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_report(root: Path, date_label: str | None = None) -> dict[str, Any]:
    docs, manifest_pairs = manifest_maps(root)
    inventory = {
        str(row["doc_id"]): row
        for row in read_csv(root / "SOURCE_INVENTORY.csv")
        if row.get("doc_id")
    }
    microtext_items = read_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl")
    visualdiff_pairs = read_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl")

    row_counts: Counter[str] = Counter()
    unresolved_rows: list[dict[str, str]] = []
    for row in microtext_items:
        doc_id = str(row.get("doc_id") or "")
        if doc_id:
            row_counts[doc_id] += 1
        else:
            unresolved_rows.append(
                {
                    "id": str(row.get("item_id") or ""),
                    "task": "microtext",
                    "reason": "missing_doc_id",
                }
            )
    for row in visualdiff_pairs:
        resolved, reason = resolve_visualdiff_docs(row, docs, manifest_pairs)
        for doc_id in resolved:
            row_counts[doc_id] += 1
        if reason:
            unresolved_rows.append(
                {
                    "id": str(row.get("pair_id") or ""),
                    "task": "visualdiff",
                    "reason": reason,
                }
            )

    document_rows: list[dict[str, Any]] = []
    for doc_id in sorted(row_counts):
        manifest = docs.get(doc_id, {})
        source = inventory.get(doc_id, {})
        local_path = str(source.get("path") or manifest.get("path") or "").strip()
        absolute_path = root / local_path if local_path else Path()
        path_exists = bool(local_path) and absolute_path.is_file()
        computed_sha = file_sha256(absolute_path) if path_exists else ""
        recorded_sha = str(manifest.get("sha256") or "").strip().lower()
        source_url = str(source.get("source_url") or manifest.get("source_url") or "").strip()
        inventory_public_status = str(source.get("public_status") or "").strip()
        manifest_public_status = str(manifest.get("public_status") or "").strip()
        public_status = inventory_public_status or manifest_public_status
        inventory_blocker = rights_blocker(inventory_public_status) if inventory_public_status else ""
        manifest_blocker = rights_blocker(manifest_public_status) if manifest_public_status else ""
        rights_status_disagreement = bool(
            inventory_public_status
            and manifest_public_status
            and bool(inventory_blocker) != bool(manifest_blocker)
        )
        blocker = (
            "rights_status_disagreement"
            if rights_status_disagreement
            else rights_blocker(public_status)
        )
        release_ready = bool(
            source and manifest and local_path and path_exists and source_url and public_status and not blocker
        )
        paper_ready = bool(release_ready and recorded_sha and computed_sha == recorded_sha)
        document_rows.append(
            {
                "doc_id": doc_id,
                "task": source.get("task") or manifest.get("task") or "",
                "domain": source.get("domain") or manifest.get("domain") or "",
                "active_row_references": row_counts[doc_id],
                "inventory_present": bool(source),
                "manifest_present": bool(manifest),
                "local_path": local_path,
                "local_path_exists": path_exists,
                "source_url": source_url,
                "public_status": public_status,
                "inventory_public_status": inventory_public_status,
                "manifest_public_status": manifest_public_status,
                "rights_status_disagreement": rights_status_disagreement,
                "source_candidate_id": manifest.get("source_candidate_id") or "",
                "recorded_sha256": recorded_sha,
                "computed_sha256": computed_sha,
                "sha256_matches": bool(recorded_sha and computed_sha == recorded_sha),
                "blocker": blocker,
                "release_ready": release_ready,
                "paper_ready": paper_ready,
            }
        )

    totals = {
        "active_gold_rows": len(microtext_items) + len(visualdiff_pairs),
        "active_source_docs": len(document_rows),
        "unresolved_active_rows": len(unresolved_rows),
        "missing_inventory_docs": sum(not row["inventory_present"] for row in document_rows),
        "missing_manifest_docs": sum(not row["manifest_present"] for row in document_rows),
        "missing_local_paths": sum(not row["local_path_exists"] for row in document_rows),
        "missing_source_urls": sum(not row["source_url"] for row in document_rows),
        "rights_status_disagreement_docs": sum(
            row["rights_status_disagreement"] for row in document_rows
        ),
        "rights_blocked_docs": sum(bool(row["blocker"]) for row in document_rows),
        "missing_recorded_sha256_docs": sum(not row["recorded_sha256"] for row in document_rows),
        "sha256_mismatch_docs": sum(
            bool(row["recorded_sha256"]) and not row["sha256_matches"] for row in document_rows
        ),
        "release_ready_docs": sum(row["release_ready"] for row in document_rows),
        "paper_ready_docs": sum(row["paper_ready"] for row in document_rows),
    }
    return {
        "date_label": date_label or date.today().isoformat(),
        "totals": totals,
        "release_provenance_complete": not unresolved_rows
        and totals["release_ready_docs"] == totals["active_source_docs"],
        "paper_ready_provenance_complete": not unresolved_rows
        and totals["paper_ready_docs"] == totals["active_source_docs"],
        "documents": document_rows,
        "unresolved_rows": unresolved_rows,
    }


def release_provenance_gate(root: Path) -> dict[str, Any]:
    report = build_report(root)
    totals = report["totals"]
    return {
        "current": f"{totals['release_ready_docs']}/{totals['active_source_docs']} release-ready",
        "target": "all active source documents release-ready and all active rows resolved",
        "passes": report["release_provenance_complete"],
        "unresolved_active_rows": totals["unresolved_active_rows"],
        "missing_inventory_docs": totals["missing_inventory_docs"],
        "rights_status_disagreement_docs": totals["rights_status_disagreement_docs"],
        "rights_blocked_docs": totals["rights_blocked_docs"],
    }


def paper_ready_provenance_gate(root: Path) -> dict[str, Any]:
    report = build_report(root)
    totals = report["totals"]
    return {
        "current": f"{totals['paper_ready_docs']}/{totals['active_source_docs']} paper-ready",
        "target": "all active source documents release-ready with matching recorded SHA-256",
        "passes": report["paper_ready_provenance_complete"],
        "unresolved_active_rows": totals["unresolved_active_rows"],
        "missing_recorded_sha256_docs": totals["missing_recorded_sha256_docs"],
        "sha256_mismatch_docs": totals["sha256_mismatch_docs"],
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Eng_Bench Active-Gold Provenance Audit",
        "",
        f"- Date label: `{report['date_label']}`",
        f"- Active gold rows: `{totals['active_gold_rows']}`",
        f"- Active source documents: `{totals['active_source_docs']}`",
        f"- Release provenance complete: `{str(report['release_provenance_complete']).lower()}`",
        f"- Paper-ready provenance complete: `{str(report['paper_ready_provenance_complete']).lower()}`",
        f"- Unresolved active rows: `{totals['unresolved_active_rows']}`",
        f"- Missing inventory documents: `{totals['missing_inventory_docs']}`",
        f"- Inventory/manifest rights-gate disagreements: `{totals['rights_status_disagreement_docs']}`",
        f"- Rights-blocked documents: `{totals['rights_blocked_docs']}`",
        f"- Missing recorded SHA-256 documents: `{totals['missing_recorded_sha256_docs']}`",
        f"- SHA-256 mismatch documents: `{totals['sha256_mismatch_docs']}`",
        "",
        "## Documents Requiring Work",
        "",
        "| Doc ID | Rows | Inventory | Manifest | Path | Rights | Hash |",
        "| --- | ---: | --- | --- | --- | --- | --- |",
    ]
    issues = [row for row in report["documents"] if not row["paper_ready"]]
    if issues:
        for row in issues:
            lines.append(
                f"| `{row['doc_id']}` | {row['active_row_references']} | "
                f"`{row['inventory_present']}` | `{row['manifest_present']}` | "
                f"`{row['local_path_exists']}` | `{row['blocker'] or 'clear'}` | "
                f"`{row['sha256_matches']}` |"
            )
    else:
        lines.append("| none |  |  |  |  |  |  |")
    if report["unresolved_rows"]:
        lines.extend(["", "## Unresolved Active Rows", ""])
        lines.extend(
            f"- `{row['id']}` ({row['task']}): {row['reason']}" for row in report["unresolved_rows"][:100]
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit active-gold source provenance.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--date-label", default=date.today().isoformat())
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    parser.add_argument("--output-csv", default="docs/ACTIVE_GOLD_PROVENANCE.csv")
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = build_report(root, args.date_label)
    stem = f"derived/quality/active_gold_provenance_audit_{args.date_label}"
    output_json = root / (args.output_json or f"{stem}.json")
    output_md = root / (args.output_md or f"{stem}.md")
    output_csv = root / args.output_csv
    write_json(output_json, report)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    write_csv(output_csv, report["documents"])
    print(f"[OK] Wrote {output_json}")
    print(f"[OK] Wrote {output_md}")
    print(f"[OK] Wrote {output_csv}")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0 if report["release_provenance_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
