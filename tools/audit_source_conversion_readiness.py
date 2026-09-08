#!/usr/bin/env python3
"""Audit Eng_Bench source conversion readiness from candidate to reviewed gold."""
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

from audit_source_payload_duplicates import build_report as build_source_payload_duplicate_report
from mine_microtext_candidates import mine_rows, version_from_doc_id
from source_rights import is_release_safe_status, rights_blocker


OPEN_STATUSES = {"", "candidate", "needs_review", "provisional_review", "todo"}
MERGEABLE_STATUSES = {"accepted", "edited", "valid", "edit", "accepted_for_merge"}
NON_ACTIONABLE_REVIEW_STATUSES = {
    "blocked",
    "blocked_rights",
    "duplicate_hold",
    "invalid",
    "machine_held",
    "machine_superseded",
    "not_reviewable",
    "policy_hold",
    "qa_duplicate_hold",
    "reject",
    "reject_unclear",
    "rejected",
    "rights_hold",
    "skip",
    "skipped",
    "superseded",
}
TERMINAL_REVIEW_STATUSES = {
    "accepted",
    "accepted_for_merge",
    "edit",
    "edited",
    "invalid",
    "machine_held",
    "machine_superseded",
    "qa_duplicate_hold",
    "reject",
    "rejected",
    "valid",
}
CANDIDATE_ID_RE = re.compile(r"\b[a-z]+_\d{3}\b")
BACKTICK_VALUE_RE = re.compile(r"`([^`]+)`")
HUMAN_PACKET_INDEX_PREFIX = "human_packet_index_"
SUPPLEMENTAL_PACKET_INDEX_PREFIX = "supplemental_review_pack_index_"
SOURCE_INVENTORY_REPAIR_FIELDS = [
    "candidate_id",
    "doc_id",
    "domain",
    "task_fit",
    "lineage_evidence",
    "candidate_next_step",
    "rendered_pages",
    "textlayer_spans",
    "review_rows",
    "open_review_rows",
    "packeted_open_review_rows",
    "gold_rows",
    "source_url",
    "recommended_action",
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with path.open("r", encoding=encoding, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return list(csv.DictReader(f))


MACHINE_EXHAUSTED_STATUSES = {
    "machine_exhausted",
    "machine_exhausted_no_candidate",
    "machine_exhausted_after_reviewed_pass",
}


def is_machine_exhausted_status(status: str) -> bool:
    return status.strip() in MACHINE_EXHAUSTED_STATUSES


def load_conversion_exhaustion(root: Path) -> dict[str, dict[str, str]]:
    return {
        str(row.get("doc_id") or "").strip(): row
        for row in read_csv(root / "SOURCE_CONVERSION_EXHAUSTION.csv")
        if str(row.get("doc_id") or "").strip()
        and is_machine_exhausted_status(str(row.get("status") or ""))
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    fieldnames: list[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(fieldnames or [])
    if not fields:
        for row in rows:
            for key in row:
                if key not in fields:
                    fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def quota_domain(domain: str | None) -> str:
    if domain in {"civil", "architectural"}:
        return "civil_architectural"
    return domain or "unknown"


def status_for(row: dict[str, Any]) -> str:
    for key in ("review_status", "human_status", "annotation_status", "status"):
        value = str(row.get(key) or "").strip().lower()
        if value:
            return value
    return ""


def review_exclusion_reason(row: dict[str, Any]) -> str:
    """Return why a row must not be recycled into an open review queue."""
    if str(row.get("superseded_by") or "").strip():
        return "superseded_by"

    status_fields = (
        "review_status",
        "human_status",
        "annotation_status",
        "status",
        "machine_qa_status",
        "cross_packet_filter_status",
        "visual_qa_status",
        "policy_status",
        "queue_status",
        "promotion_state",
    )
    for field in status_fields:
        status = str(row.get(field) or "").strip().lower()
        if not status:
            continue
        if (
            status in NON_ACTIONABLE_REVIEW_STATUSES
            or status.startswith("blocked_")
            or status.startswith("machine_held")
            or status.startswith("machine_superseded")
            or status.startswith("reject_")
        ):
            return f"{field}:{status}"

    for field in ("reservoir_disposition", "selection_disposition", "candidate_disposition"):
        disposition = str(row.get(field) or "").strip().lower()
        if disposition in {"blocked", "held", "rejected", "superseded"}:
            return f"{field}:{disposition}"

    for field in (
        "reservoir_hold_reason",
        "selection_hold_reason",
        "visual_hold_reason",
        "policy_hold_reason",
        "machine_hold_reason",
        "hold_reason",
        "blocked_reason",
    ):
        if str(row.get(field) or "").strip():
            return f"{field}:set"
    return ""


def row_identity(row: dict[str, Any], kind: str) -> str:
    keys = ("pair_id", "id", "candidate_id") if kind == "visualdiff" else ("candidate_id", "item_id", "id")
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def microtext_region_identity(
    row: dict[str, Any],
) -> tuple[str, int, tuple[int, int, int, int]] | None:
    """Return a stable physical-region key independent of candidate aliases."""
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    doc_id = str(row.get("doc_id") or metadata.get("doc_id") or "").strip()
    if not doc_id:
        return None

    page_value = row.get("page_index")
    if page_value in (None, ""):
        page_value = row.get("page")
    if page_value in (None, ""):
        page_value = metadata.get("page_index", metadata.get("page", 0))
    try:
        page_index = int(page_value or 0)
    except (TypeError, ValueError):
        return None

    bbox_value = row.get("bbox") or row.get("bbox_px")
    if not bbox_value:
        bbox_value = metadata.get("bbox") or metadata.get("bbox_px")
    if isinstance(bbox_value, str):
        bbox_value = bbox_value.strip().strip("[]()")
        bbox_value = [part.strip() for part in bbox_value.split(",")]
    if not isinstance(bbox_value, (list, tuple)) or len(bbox_value) != 4:
        return None
    try:
        bbox = tuple(int(round(float(value))) for value in bbox_value)
    except (TypeError, ValueError):
        return None
    return (doc_id, page_index, bbox)


def candidate_ids_in_text(text: str) -> set[str]:
    return set(CANDIDATE_ID_RE.findall(text or ""))


def inventory_source_path(row: dict[str, Any]) -> str:
    return str(row.get("path") or row.get("source_path") or "").strip()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_doc_records(root: Path) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("doc_id") or "").strip(): row
        for row in read_jsonl(root / "manifest.jsonl")
        if row.get("type") == "doc" and str(row.get("doc_id") or "").strip()
    }


def source_document_readiness(
    root: Path,
    row: dict[str, Any],
    manifest_docs: dict[str, dict[str, Any]],
) -> tuple[bool, str, str]:
    """Return strict source provenance readiness for a local inventory row."""
    doc_id = str(row.get("doc_id") or "").strip()
    manifest = manifest_docs.get(doc_id)
    if manifest is None:
        return False, "missing_manifest_doc", inventory_source_path(row)
    source_path = inventory_source_path(row) or str(manifest.get("path") or "").strip()
    if not source_path:
        return False, "missing_source_path", ""
    absolute = root / source_path
    if not absolute.is_file():
        return False, "missing_source_file", source_path
    recorded_sha256 = str(manifest.get("sha256") or "").strip().lower()
    if not recorded_sha256:
        return False, "missing_manifest_sha256", source_path
    if file_sha256(absolute) != recorded_sha256:
        return False, "source_sha256_mismatch", source_path
    source_url = str(row.get("source_url") or manifest.get("source_url") or "").strip()
    if not source_url:
        return False, "missing_source_url", source_path
    public_status = str(row.get("public_status") or manifest.get("public_status") or "").strip()
    if not is_release_safe_status(public_status):
        return False, "source_rights_not_release_safe", source_path
    return True, "", source_path


def imported_candidate_ids(root: Path) -> set[str]:
    imported: set[str] = set()
    for path in (root / "SOURCE_INTAKE_LOG.md",):
        if path.exists():
            imported.update(candidate_ids_in_text(path.read_text(encoding="utf-8", errors="replace")))
    return imported


def add_lineage(
    lineage: dict[str, dict[str, set[str]]],
    candidate_id: str,
    doc_id: str,
    evidence: str,
    known_candidate_ids: set[str],
    local_doc_ids: set[str],
) -> None:
    candidate_id = str(candidate_id or "").strip()
    doc_id = str(doc_id or "").strip()
    if candidate_id not in known_candidate_ids or doc_id not in local_doc_ids:
        return
    lineage.setdefault(candidate_id, {}).setdefault(doc_id, set()).add(evidence)


def lineage_from_source_intake_log(
    root: Path,
    lineage: dict[str, dict[str, set[str]]],
    known_candidate_ids: set[str],
    local_doc_ids: set[str],
) -> None:
    path = root / "SOURCE_INTAKE_LOG.md"
    if not path.exists():
        return
    inventory_path_to_doc = {
        inventory_source_path(row).replace("\\", "/").lower(): str(row.get("doc_id") or "").strip()
        for row in read_csv(root / "SOURCE_INVENTORY.csv")
        if inventory_source_path(row) and row.get("doc_id")
    }
    current_candidate = ""
    collecting_manifest_docs = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("## "):
            current_candidate = ""
            collecting_manifest_docs = False
            continue
        if line.startswith("- Candidate:"):
            candidate_ids = candidate_ids_in_text(line) & known_candidate_ids
            current_candidate = next(iter(candidate_ids)) if len(candidate_ids) == 1 else ""
            collecting_manifest_docs = False
            continue
        if line.startswith("- `"):
            candidate_ids = candidate_ids_in_text(line) & known_candidate_ids
            current_candidate = next(iter(candidate_ids)) if len(candidate_ids) == 1 else ""
            collecting_manifest_docs = False
            continue
        if line.startswith("- Manifest doc id"):
            collecting_manifest_docs = True
            for value in BACKTICK_VALUE_RE.findall(line):
                add_lineage(
                    lineage,
                    current_candidate,
                    value,
                    "source_intake_log",
                    known_candidate_ids,
                    local_doc_ids,
                )
            continue
        if current_candidate and line.startswith("  - Local "):
            for value in BACKTICK_VALUE_RE.findall(line):
                doc_id = inventory_path_to_doc.get(value.strip().replace("\\", "/").lower(), "")
                add_lineage(
                    lineage,
                    current_candidate,
                    doc_id,
                    "source_intake_local_path",
                    known_candidate_ids,
                    local_doc_ids,
                )
            continue
        if collecting_manifest_docs and line.startswith("  - "):
            for value in BACKTICK_VALUE_RE.findall(line):
                add_lineage(
                    lineage,
                    current_candidate,
                    value,
                    "source_intake_log",
                    known_candidate_ids,
                    local_doc_ids,
                )
            continue
        if line.strip():
            collecting_manifest_docs = False


def lineage_from_source_imports(
    root: Path,
    lineage: dict[str, dict[str, set[str]]],
    known_candidate_ids: set[str],
    local_doc_ids: set[str],
) -> None:
    import_root = root / "derived" / "source_imports"
    if not import_root.exists():
        return

    def walk_json(value: Any, inherited_candidate: str = "") -> None:
        if isinstance(value, dict):
            candidate_id = str(value.get("candidate_id") or inherited_candidate).strip()
            add_lineage(
                lineage,
                candidate_id,
                str(value.get("doc_id") or ""),
                "source_import_json",
                known_candidate_ids,
                local_doc_ids,
            )
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk_json(child, candidate_id)
        elif isinstance(value, list):
            for child in value:
                walk_json(child, inherited_candidate)

    for path in sorted(import_root.glob("*.json")):
        try:
            walk_json(json.loads(path.read_text(encoding="utf-8", errors="replace")))
        except (json.JSONDecodeError, OSError):
            continue
    for path in sorted(import_root.glob("*.csv")):
        for row in read_csv(path):
            add_lineage(
                lineage,
                row.get("candidate_id", ""),
                row.get("doc_id", ""),
                "source_import_csv",
                known_candidate_ids,
                local_doc_ids,
            )


def lineage_from_inventory_notes(
    inventory: list[dict[str, str]],
    lineage: dict[str, dict[str, set[str]]],
    known_candidate_ids: set[str],
    local_doc_ids: set[str],
) -> None:
    for row in inventory:
        notes = str(row.get("notes") or "")
        candidate_ids = candidate_ids_in_text(notes) & known_candidate_ids
        if len(candidate_ids) != 1:
            continue
        lowered = notes.lower()
        if not any(marker in lowered for marker in ("browser-validated", "candidate", "source_candidate")):
            continue
        add_lineage(
            lineage,
            next(iter(candidate_ids)),
            row.get("doc_id", ""),
            "inventory_note",
            known_candidate_ids,
            local_doc_ids,
        )


def lineage_from_annotation_rows(
    root: Path,
    lineage: dict[str, dict[str, set[str]]],
    known_candidate_ids: set[str],
    local_doc_ids: set[str],
) -> None:
    files = list((root / "microtext" / "annotations").glob("*.jsonl"))
    files.extend((root / "visualdiff" / "annotations").glob("*.jsonl"))
    for path in sorted(files):
        for row in read_jsonl(path):
            candidate_id = str(row.get("source_candidate_id") or "").strip()
            doc_id = str(row.get("doc_id") or "").strip()
            add_lineage(
                lineage,
                candidate_id,
                doc_id,
                "annotation_row",
                known_candidate_ids,
                local_doc_ids,
            )


def lineage_from_manifest(
    root: Path,
    lineage: dict[str, dict[str, set[str]]],
    known_candidate_ids: set[str],
    local_doc_ids: set[str],
) -> None:
    for row in read_jsonl(root / "manifest.jsonl"):
        if str(row.get("type") or "doc").strip() != "doc":
            continue
        doc_id = str(row.get("doc_id") or "").strip()
        add_lineage(
            lineage,
            str(row.get("source_candidate_id") or "").strip(),
            doc_id,
            "manifest_source_candidate_id",
            known_candidate_ids,
            local_doc_ids,
        )
        aliases = row.get("source_candidate_aliases") or row.get("candidate_aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        if not isinstance(aliases, list):
            continue
        for alias in aliases:
            add_lineage(
                lineage,
                str(alias or "").strip(),
                doc_id,
                "manifest_candidate_alias",
                known_candidate_ids,
                local_doc_ids,
            )


def lineage_from_candidate_doc_id_prefix(
    lineage: dict[str, dict[str, set[str]]],
    known_candidate_ids: set[str],
    local_doc_ids: set[str],
) -> None:
    for candidate_id in known_candidate_ids:
        prefix = f"{candidate_id}_"
        for doc_id in local_doc_ids:
            if doc_id.startswith(prefix):
                add_lineage(
                    lineage,
                    candidate_id,
                    doc_id,
                    "candidate_doc_id_prefix",
                    known_candidate_ids,
                    local_doc_ids,
                )


def lineage_from_candidate_aliases(
    root: Path,
    lineage: dict[str, dict[str, set[str]]],
    known_candidate_ids: set[str],
    local_doc_ids: set[str],
) -> None:
    for row in read_csv(root / "SOURCE_CANDIDATE_ALIASES.csv"):
        alias_id = str(row.get("alias_candidate_id") or "").strip()
        canonical_id = str(row.get("canonical_candidate_id") or "").strip()
        explicit_doc_id = str(row.get("doc_id") or "").strip()
        canonical_docs = lineage.get(canonical_id, {})
        doc_ids = [explicit_doc_id] if explicit_doc_id else sorted(canonical_docs)
        for doc_id in doc_ids:
            if doc_id not in canonical_docs:
                continue
            add_lineage(
                lineage,
                alias_id,
                doc_id,
                f"candidate_alias:{canonical_id}",
                known_candidate_ids,
                local_doc_ids,
            )


def observed_local_doc_ids(root: Path, inventory: list[dict[str, str]]) -> set[str]:
    doc_ids = {str(row.get("doc_id") or "").strip() for row in inventory}
    for annotation_root in (
        root / "microtext" / "annotations",
        root / "visualdiff" / "annotations",
    ):
        for path in annotation_root.glob("*.jsonl"):
            for row in read_jsonl(path):
                doc_ids.add(str(row.get("doc_id") or "").strip())
    pages_root = root / "derived" / "pages_300dpi"
    if pages_root.exists():
        doc_ids.update(path.name for path in pages_root.iterdir() if path.is_dir())
    textlayer_root = root / "derived" / "textlayer"
    if textlayer_root.exists():
        doc_ids.update(path.stem for path in textlayer_root.glob("*.jsonl"))
    doc_ids.discard("")
    return doc_ids


def explicit_candidate_local_lineage(root: Path) -> dict[str, dict[str, set[str]]]:
    inventory = read_csv(root / "SOURCE_INVENTORY.csv")
    candidates = read_csv(root / "SOURCE_CANDIDATES_RANKED.csv") or read_csv(root / "SOURCE_CANDIDATES.csv")
    known_candidate_ids = {str(row.get("candidate_id") or "").strip() for row in candidates}
    known_candidate_ids.discard("")
    local_doc_ids = observed_local_doc_ids(root, inventory)
    lineage: dict[str, dict[str, set[str]]] = {}
    lineage_from_source_intake_log(root, lineage, known_candidate_ids, local_doc_ids)
    lineage_from_source_imports(root, lineage, known_candidate_ids, local_doc_ids)
    lineage_from_inventory_notes(inventory, lineage, known_candidate_ids, local_doc_ids)
    lineage_from_annotation_rows(root, lineage, known_candidate_ids, local_doc_ids)
    lineage_from_manifest(root, lineage, known_candidate_ids, local_doc_ids)
    lineage_from_candidate_doc_id_prefix(lineage, known_candidate_ids, local_doc_ids)
    lineage_from_candidate_aliases(root, lineage, known_candidate_ids, local_doc_ids)
    return lineage


def count_jsonl_lines(path: Path) -> int:
    if not path.exists() or not path.is_file():
        return 0
    count = 0
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.strip():
                count += 1
    return count


def rendered_page_count(root: Path, doc_id: str) -> int:
    counts = 0
    pages_dir = root / "derived" / "pages_300dpi" / doc_id
    if pages_dir.exists():
        counts += len([path for path in pages_dir.rglob("*.png") if path.is_file()])
    images_root = root / "images"
    if images_root.exists():
        for path in images_root.glob(f"{doc_id}__*"):
            if path.is_dir():
                counts += len([image for image in path.glob("*.png") if image.is_file()])
    return counts


def textlayer_span_count(root: Path, doc_id: str) -> int:
    canonical_path = root / "derived" / "textlayer" / f"{doc_id}.jsonl"
    if canonical_path.exists():
        return count_jsonl_lines(canonical_path)

    total = 0
    layer_dir = root / "derived" / "textlayer" / doc_id
    if layer_dir.exists():
        for path in layer_dir.rglob("*.jsonl"):
            total += count_jsonl_lines(path)
        for path in layer_dir.rglob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and isinstance(payload.get("spans"), list):
                total += len(payload["spans"])
            elif isinstance(payload, list):
                total += len(payload)
    return total


def mineable_candidate_count(root: Path, doc_id: str) -> int:
    textlayer_path = root / "derived" / "textlayer" / f"{doc_id}.jsonl"
    if not textlayer_path.exists():
        return 0
    return len(
        mine_rows(
            read_jsonl(textlayer_path),
            doc_id=doc_id,
            version_id=version_from_doc_id(doc_id),
        )
    )


def path_fields(row: dict[str, Any]) -> list[str]:
    values = []
    for key, value in row.items():
        if isinstance(value, str) and value.strip() and (key.endswith("_path") or key in {"image_path", "image_old", "image_new"}):
            values.append(value.strip())
    return values


def missing_evidence_count(root: Path, row: dict[str, Any]) -> int:
    missing = 0
    for value in path_fields(row):
        path = Path(value)
        full = path if path.is_absolute() else root / path
        if not full.exists():
            missing += 1
    return missing


def review_key_for(row: dict[str, Any], kind: str) -> str:
    if kind == "visualdiff":
        return str(row.get("project_id") or row.get("doc_id") or row.get("pair_id") or "unknown")
    return str(row.get("doc_id") or "unknown")


def human_packet_index_label(index_path: Path) -> str:
    stem = index_path.stem
    if stem.startswith(HUMAN_PACKET_INDEX_PREFIX):
        return stem.removeprefix(HUMAN_PACKET_INDEX_PREFIX)
    if stem.startswith(SUPPLEMENTAL_PACKET_INDEX_PREFIX):
        return stem.removeprefix(SUPPLEMENTAL_PACKET_INDEX_PREFIX)
    return stem


def resolve_human_packet_index(root: Path, date_label: str) -> Path | None:
    quality_root = root / "derived" / "quality"
    exact_paths = [
        quality_root / f"{HUMAN_PACKET_INDEX_PREFIX}{date_label}.json",
        quality_root / f"{SUPPLEMENTAL_PACKET_INDEX_PREFIX}{date_label}.json",
    ]
    for exact_path in exact_paths:
        if exact_path.exists():
            return exact_path
    candidates = list(quality_root.glob(f"{HUMAN_PACKET_INDEX_PREFIX}*.json"))
    candidates.extend(quality_root.glob(f"{SUPPLEMENTAL_PACKET_INDEX_PREFIX}*.json"))
    return max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name)) if candidates else None


def packet_row_kind(row: dict[str, Any]) -> str:
    visualdiff_fields = (
        "pair_id",
        "old_crop_path",
        "new_crop_path",
        "panel_path",
        "old_image_path",
        "new_image_path",
        "image_old",
        "image_new",
    )
    return "visualdiff" if any(str(row.get(key) or "").strip() for key in visualdiff_fields) else "microtext"


def add_packet_row_key(keys: set[str], row: dict[str, Any]) -> None:
    key = row_identity(row, packet_row_kind(row))
    if key:
        keys.add(key)


def load_active_packet_row_keys(root: Path, date_label: str) -> tuple[set[str], Path | None]:
    index_path = resolve_human_packet_index(root, date_label)
    if index_path is None:
        return set(), None
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    packets = payload.get("packets")
    if not isinstance(packets, list):
        packets = payload.get("packs", [])
    keys: set[str] = set()
    for packet in packets:
        if not packet.get("ready_to_send"):
            continue
        if int(packet.get("mergeable_rows") or 0) > 0:
            continue
        folder = Path(str(packet.get("folder_path") or ""))
        packet_root = folder if folder.is_absolute() else root / folder
        if not packet_root.exists():
            continue
        for manifest_path in sorted(packet_root.rglob("manifest.jsonl")):
            for row in read_jsonl(manifest_path):
                add_packet_row_key(keys, row)
        for csv_path in sorted(packet_root.rglob("*.csv")):
            for row in read_csv(csv_path):
                add_packet_row_key(keys, row)
    return keys, index_path


def load_active_packet_manifest_rows(root: Path, index_path: Path | None) -> list[tuple[str, dict[str, Any]]]:
    if index_path is None:
        return []
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    packets = payload.get("packets")
    if not isinstance(packets, list):
        packets = payload.get("packs", [])
    rows: list[tuple[str, dict[str, Any]]] = []
    for packet in packets:
        if not packet.get("ready_to_send"):
            continue
        if int(packet.get("mergeable_rows") or 0) > 0:
            continue
        folder = Path(str(packet.get("folder_path") or ""))
        packet_root = folder if folder.is_absolute() else root / folder
        if not packet_root.exists():
            continue
        for manifest_path in sorted(packet_root.rglob("manifest.jsonl")):
            for row in read_jsonl(manifest_path):
                rows.append((packet_row_kind(row), row))
    return rows


def active_packet_row_keys(root: Path, date_label: str) -> set[str]:
    keys, _index_path = load_active_packet_row_keys(root, date_label)
    return keys


def active_gold_row_keys(root: Path) -> set[str]:
    keys: set[str] = set()
    for row in read_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl"):
        for key in ("source_candidate_id", "candidate_id", "item_id"):
            value = str(row.get(key) or "").strip()
            if value:
                keys.add(value)
    for row in read_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"):
        for key in ("pair_id", "id"):
            value = str(row.get(key) or "").strip()
            if value:
                keys.add(value)
    return keys


def manifest_visualdiff_pair_docs(root: Path) -> dict[str, set[str]]:
    pair_docs: dict[str, set[str]] = defaultdict(set)
    for row in read_jsonl(root / "manifest.jsonl"):
        if str(row.get("type") or "").strip().lower() != "pair":
            continue
        doc_ids = {
            str(row.get(key) or "").strip()
            for key in (
                "from_doc_id",
                "to_doc_id",
                "old_doc_id",
                "new_doc_id",
                "old_source_doc_id",
                "new_source_doc_id",
            )
        } - {""}
        for pair_key in ("pair_id", "project_id"):
            pair_value = str(row.get(pair_key) or "").strip()
            if pair_value:
                pair_docs[pair_value].update(doc_ids)
    return pair_docs


def page_path_doc_id(value: Any) -> str:
    parts = str(value or "").strip().replace("\\", "/").split("/")
    try:
        index = parts.index("pages_300dpi")
    except ValueError:
        return ""
    return parts[index + 1] if index + 1 < len(parts) else ""


def visualdiff_source_doc_ids(
    row: dict[str, Any],
    pair_docs: dict[str, set[str]] | None = None,
) -> set[str]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    doc_ids = {
        str(row.get("old_doc_id") or metadata.get("old_doc_id") or "").strip(),
        str(row.get("new_doc_id") or metadata.get("new_doc_id") or "").strip(),
        str(
            row.get("old_source_doc_id")
            or metadata.get("old_source_doc_id")
            or ""
        ).strip(),
        str(
            row.get("new_source_doc_id")
            or metadata.get("new_source_doc_id")
            or ""
        ).strip(),
        page_path_doc_id(row.get("image_old") or metadata.get("image_old")),
        page_path_doc_id(row.get("image_new") or metadata.get("image_new")),
    } - {""}
    pair_docs = pair_docs or {}
    for pair_key in ("pair_id", "project_id"):
        pair_value = str(row.get(pair_key) or metadata.get(pair_key) or "").strip()
        doc_ids.update(pair_docs.get(pair_value, set()))
    return doc_ids


def terminal_reviewed_row_keys(root: Path) -> set[str]:
    """Return review identities that downstream queue builders must not recycle.

    Human-reviewed terminal rows and machine/non-actionable holds are both
    terminal for source-conversion readiness. The queue builder already applies
    the same veto across every review file, so limiting this index to filenames
    ending in ``_reviewed.jsonl`` overstates fresh actionable capacity.
    """
    keys: set[str] = set()
    files = list((root / "microtext" / "annotations").glob("microtext_review*.jsonl"))
    files.extend((root / "visualdiff" / "annotations").glob("visualdiff_review*.jsonl"))
    for path in sorted(files):
        kind = "visualdiff" if "visualdiff" in path.name else "microtext"
        for row in read_jsonl(path):
            if (
                status_for(row) not in TERMINAL_REVIEW_STATUSES
                and not review_exclusion_reason(row)
            ):
                continue
            key = row_identity(row, kind)
            if key:
                keys.add(key)
    return keys


def collect_review_stats(
    root: Path,
    packet_row_keys: set[str] | None = None,
    resolved_row_keys: set[str] | None = None,
    packet_manifest_rows: list[tuple[str, dict[str, Any]]] | None = None,
) -> tuple[dict[str, Counter[str]], dict[str, Counter[str]]]:
    packet_row_keys = packet_row_keys or set()
    resolved_row_keys = resolved_row_keys or set()
    packet_manifest_rows = packet_manifest_rows or []
    by_doc: dict[str, Counter[str]] = defaultdict(Counter)
    by_candidate: dict[str, Counter[str]] = defaultdict(Counter)
    identities_by_doc: dict[str, dict[str, dict[str, bool]]] = defaultdict(dict)
    identities_by_candidate: dict[str, dict[str, dict[str, bool]]] = defaultdict(dict)
    seen_review_row_keys: set[str] = set()
    pair_docs = manifest_visualdiff_pair_docs(root)

    def update_identity_state(
        states_by_scope: dict[str, dict[str, dict[str, bool]]],
        scope_key: str,
        identity: str,
        *,
        is_open: bool,
        is_packeted: bool,
        is_resolved: bool,
        evidence_complete: bool,
    ) -> None:
        if not scope_key or not identity:
            return
        state = states_by_scope[scope_key].setdefault(
            identity,
            {
                "open": False,
                "packeted": False,
                "resolved": False,
                "evidence_complete": False,
            },
        )
        if not is_open:
            return
        state["open"] = True
        state["packeted"] = state["packeted"] or is_packeted
        state["resolved"] = state["resolved"] or is_resolved
        state["evidence_complete"] = state["evidence_complete"] or evidence_complete

    def add_unique_identity_counters(
        counters_by_scope: dict[str, Counter[str]],
        states_by_scope: dict[str, dict[str, dict[str, bool]]],
    ) -> None:
        for scope_key, identities in states_by_scope.items():
            counter = counters_by_scope[scope_key]
            for state in identities.values():
                if not state["open"]:
                    continue
                is_packeted = state["packeted"]
                is_resolved = state["resolved"]
                is_fresh = not is_packeted and not is_resolved
                counter["unique_open_rows"] += 1
                counter["unique_packeted_open_rows"] += int(is_packeted)
                counter["unique_unpacketed_open_rows"] += int(not is_packeted)
                counter["unique_fresh_open_rows"] += int(is_fresh)
                counter["unique_stale_open_rows"] += int(not is_packeted and is_resolved)
                counter["unique_fresh_missing_evidence_rows"] += int(
                    is_fresh and not state["evidence_complete"]
                )

    def count_row(row: dict[str, Any], kind: str, *, force_packeted: bool = False) -> None:
        status = status_for(row)
        exclusion_reason = review_exclusion_reason(row)
        is_open = status in OPEN_STATUSES and not exclusion_reason
        row_key = row_identity(row, kind)
        is_packeted = force_packeted or row_key in packet_row_keys
        is_resolved = row_key in resolved_row_keys
        doc_keys = {review_key_for(row, kind)}
        if kind == "visualdiff":
            doc_keys.update(visualdiff_source_doc_ids(row, pair_docs))
        doc_keys.discard("")
        candidate_id = str(row.get("source_candidate_id") or "")
        evidence_complete = missing_evidence_count(root, row) == 0
        target_counters = [by_doc[doc_key] for doc_key in doc_keys]
        if candidate_id:
            target_counters.append(by_candidate[candidate_id])
        for counter in target_counters:
            counter["review_rows"] += 1
            counter[f"status:{status or 'blank'}"] += 1
            counter["nonactionable_hold_rows"] += int(bool(exclusion_reason))
            counter["open_rows"] += int(is_open)
            counter["mergeable_rows"] += int(status in MERGEABLE_STATUSES)
            counter["missing_evidence_rows"] += int(not evidence_complete)
            counter["packeted_review_rows"] += int(is_packeted)
            counter["packeted_open_rows"] += int(is_open and is_packeted)
            counter["unpacketed_open_rows"] += int(is_open and not is_packeted)
            counter["fresh_open_rows"] += int(is_open and not is_packeted and not is_resolved)
            counter["stale_open_rows"] += int(is_open and not is_packeted and is_resolved)
            if kind == "microtext":
                category = str(row.get("category") or "unknown")
                counter[f"category:{category}"] += 1
        for doc_key in doc_keys:
            update_identity_state(
                identities_by_doc,
                doc_key,
                row_key,
                is_open=is_open,
                is_packeted=is_packeted,
                is_resolved=is_resolved,
                evidence_complete=evidence_complete,
            )
        if candidate_id:
            update_identity_state(
                identities_by_candidate,
                candidate_id,
                row_key,
                is_open=is_open,
                is_packeted=is_packeted,
                is_resolved=is_resolved,
                evidence_complete=evidence_complete,
            )

    files = list((root / "microtext" / "annotations").glob("microtext_review*.jsonl"))
    files.extend((root / "visualdiff" / "annotations").glob("visualdiff_review*.jsonl"))
    for path in sorted(files):
        kind = "visualdiff" if "visualdiff" in path.name else "microtext"
        for row in read_jsonl(path):
            row_key = row_identity(row, kind)
            if row_key:
                seen_review_row_keys.add(row_key)
            count_row(row, kind)
    for kind, row in packet_manifest_rows:
        row_key = row_identity(row, kind)
        if row_key and row_key in seen_review_row_keys:
            continue
        count_row(row, kind, force_packeted=True)
    add_unique_identity_counters(by_doc, identities_by_doc)
    add_unique_identity_counters(by_candidate, identities_by_candidate)
    return by_doc, by_candidate


def collect_gold_stats(root: Path) -> dict[str, Counter[str]]:
    by_doc: dict[str, Counter[str]] = defaultdict(Counter)
    pair_docs = manifest_visualdiff_pair_docs(root)
    for row in read_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl"):
        doc_id = str(row.get("doc_id") or "unknown")
        split = str(row.get("split") or "unknown")
        category = str(row.get("category") or "unknown")
        by_doc[doc_id]["gold_rows"] += 1
        by_doc[doc_id]["gold_microtext_rows"] += 1
        by_doc[doc_id][f"split:{split}"] += 1
        by_doc[doc_id][f"category:{category}"] += 1
    for row in read_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl"):
        project = str(row.get("project_id") or row.get("pair_id") or "unknown")
        doc_id = str(row.get("doc_id") or project)
        source_doc_ids = visualdiff_source_doc_ids(row, pair_docs)
        for key in {project, doc_id, *source_doc_ids}:
            by_doc[key]["gold_rows"] += 1
            by_doc[key]["gold_visualdiff_rows"] += 1
            by_doc[key][f"split:{row.get('split') or 'unknown'}"] += 1
    return by_doc


def latest_canonical_future_capacity_path(root: Path) -> Path | None:
    quality_root = root / "derived" / "quality"
    candidates = [
        path
        for path in quality_root.glob("v2_0_canonical_future_capacity_*.jsonl")
        if "_holds_" not in path.name and not path.stem.endswith("-preapply")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name))


def latest_staged_capacity_report_path(root: Path) -> Path | None:
    """Return the newest validated report that enumerates staged cohorts."""
    quality_root = root / "derived" / "quality"
    candidates: list[Path] = []
    for path in quality_root.glob("v2_0_staged_capacity_*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("capacity_input_clean") is True and isinstance(
            payload.get("cohorts"), list
        ):
            candidates.append(path)
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name))


def collect_staged_future_stats(root: Path) -> tuple[dict[str, Counter[str]], list[Path]]:
    by_doc: dict[str, Counter[str]] = defaultdict(Counter)
    canonical_path = latest_canonical_future_capacity_path(root)
    report_path = latest_staged_capacity_report_path(root)
    capacity_paths: list[Path] = []
    provenance_paths: list[Path] = []
    if canonical_path is not None:
        capacity_paths.append(canonical_path)
        provenance_paths.append(canonical_path)
    if report_path is not None:
        provenance_paths.append(report_path)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        for cohort in report["cohorts"]:
            if str(cohort.get("phase") or "") != "future":
                continue
            cohort_path = root / str(cohort.get("path") or "")
            if cohort_path.is_file() and cohort_path not in capacity_paths:
                capacity_paths.append(cohort_path)
    if not capacity_paths:
        return by_doc, []
    pair_docs = manifest_visualdiff_pair_docs(root)
    seen_rows: set[tuple[str, str]] = set()

    for capacity_path in capacity_paths:
        for row in read_jsonl(capacity_path):
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            task = str(row.get("task") or row.get("task_type") or "").strip().lower()
            is_visualdiff = task == "visualdiff" or bool(
                row.get("image_old") or row.get("image_new")
            )
            kind = "visualdiff" if is_visualdiff else "microtext"
            identity = row_identity(row, kind)
            dedup_key = (kind, identity)
            if identity and dedup_key in seen_rows:
                continue
            if identity:
                seen_rows.add(dedup_key)
            if is_visualdiff:
                keys = {
                    str(row.get("project_id") or "").strip(),
                    str(row.get("doc_id") or "").strip(),
                }
                keys.update(visualdiff_source_doc_ids(row, pair_docs))
            else:
                keys = {
                    str(row.get("doc_id") or metadata.get("doc_id") or "").strip(),
                    str(
                        row.get("source_doc_id")
                        or metadata.get("source_doc_id")
                        or ""
                    ).strip(),
                }
            for key in keys - {""}:
                by_doc[key]["staged_future_rows"] += 1
    return by_doc, provenance_paths


def local_next_step(
    row: dict[str, str],
    review: Counter[str],
    gold: Counter[str],
    pages: int,
    spans: int,
    mineable_candidates: int,
    exhaustion_status: str = "",
    duplicate_payload_alias: bool = False,
    staged_future_rows: int = 0,
    paper_ready: bool = True,
) -> str:
    if not is_release_safe_status(str(row.get("public_status") or "")):
        return "rights_review_or_hold"
    if duplicate_payload_alias:
        return "duplicate_payload_alias"
    if staged_future_rows > 0:
        return "staged_future_review_capacity"
    if is_machine_exhausted_status(exhaustion_status):
        return exhaustion_status
    open_rows = int(review.get("unique_open_rows", review.get("open_rows", 0)))
    packeted_open_rows = int(
        review.get("unique_packeted_open_rows", review.get("packeted_open_rows", 0))
    )
    fresh_open_rows = int(review.get("unique_fresh_open_rows", review.get("fresh_open_rows", 0)))
    stale_open_rows = int(review.get("unique_stale_open_rows", review.get("stale_open_rows", 0)))
    fresh_missing_evidence = int(
        review.get("unique_fresh_missing_evidence_rows", review.get("missing_evidence_rows", 0))
    )
    if open_rows > 0 and fresh_missing_evidence == 0:
        if packeted_open_rows >= open_rows:
            return "await_human_return"
        if fresh_open_rows == 0:
            if packeted_open_rows > 0:
                return "await_human_return"
            return "reviewed_sibling_or_active_gold"
        if not paper_ready:
            return "source_provenance_repair_or_hold"
        if packeted_open_rows > 0 or stale_open_rows > 0:
            return "human_review_partial_packeted"
        return "human_review"
    if fresh_missing_evidence > 0:
        return "repair_review_evidence"
    if review.get("mergeable_rows", 0) > 0 and gold.get("gold_rows", 0) == 0:
        return "maintainer_merge_audit"
    if gold.get("gold_rows", 0) > 0:
        return "active_gold_expand_later"
    if (
        int(review.get("review_rows", 0)) > 0
        and int(review.get("unique_open_rows", review.get("open_rows", 0))) == 0
        and int(review.get("mergeable_rows", 0)) == 0
    ):
        return "machine_reviewed_no_actionable_candidate"
    if str(row.get("task") or "").strip().lower() == "visualdiff":
        if pages > 0:
            return "visualdiff_pair_alignment_or_review"
        source_path = inventory_source_path(row)
        if source_path:
            return "render_pages"
        return "source_file_missing_or_manifest_repair"
    if pages > 0 and spans > 0 and mineable_candidates > 0:
        return "mine_candidates_and_export_review"
    if pages > 0 and spans > 0:
        return "ocr_or_manual_region_proposal"
    if pages > 0:
        return "extract_textlayer_or_ocr"
    source_path = inventory_source_path(row)
    if source_path and (Path(source_path).suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}):
        return "render_pages"
    return "source_file_missing_or_manifest_repair"


def local_priority(
    row: dict[str, str],
    review: Counter[str],
    gold: Counter[str],
    pages: int,
    spans: int,
    mineable_candidates: int,
    exhaustion_status: str = "",
    duplicate_payload_alias: bool = False,
    staged_future_rows: int = 0,
    paper_ready: bool = True,
) -> int:
    release_safe = is_release_safe_status(str(row.get("public_status") or ""))
    if duplicate_payload_alias:
        return -100
    if staged_future_rows > 0:
        return -50
    if is_machine_exhausted_status(exhaustion_status):
        return -100
    if not paper_ready and int(review.get("unique_open_rows", review.get("open_rows", 0))) > 0:
        return -60
    if (
        int(review.get("review_rows", 0)) > 0
        and int(review.get("unique_open_rows", review.get("open_rows", 0))) == 0
        and int(review.get("mergeable_rows", 0)) == 0
        and int(gold.get("gold_rows", 0)) == 0
    ):
        return -75
    unpacketed_open = (
        int(
            review.get(
                "unique_fresh_open_rows",
                review.get("fresh_open_rows", review.get("unpacketed_open_rows", review.get("open_rows", 0))),
            )
        )
        if release_safe
        else 0
    )
    packeted_open = int(review.get("unique_packeted_open_rows", review.get("packeted_open_rows", 0)))
    score = unpacketed_open * 10 + packeted_open * 2
    score += int(review.get("mergeable_rows", 0)) * 6
    score += 15 if gold.get("gold_rows", 0) == 0 else 0
    score += 8 if pages > 0 and spans > 0 and mineable_candidates > 0 and review.get("review_rows", 0) == 0 else 0
    score += 2 if pages > 0 and spans > 0 and mineable_candidates == 0 and review.get("review_rows", 0) == 0 else 0
    score += 5 if release_safe else -20
    if quota_domain(row.get("domain")) in {"pid", "mechanical_cad", "civil_architectural", "datasheet_spec"}:
        score += 6
    return score


def summarize_local_sources(
    root: Path,
    packet_row_keys: set[str] | None = None,
    resolved_row_keys: set[str] | None = None,
    packet_manifest_rows: list[tuple[str, dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    inventory = read_csv(root / "SOURCE_INVENTORY.csv")
    review_by_doc, _review_by_candidate = collect_review_stats(
        root,
        packet_row_keys,
        resolved_row_keys,
        packet_manifest_rows,
    )
    gold_by_doc = collect_gold_stats(root)
    staged_by_doc, staged_capacity_paths = collect_staged_future_stats(root)
    exhaustion_by_doc = load_conversion_exhaustion(root)
    duplicate_alias_doc_ids = set(build_source_payload_duplicate_report(root).get("alias_doc_ids") or [])
    manifest_docs = manifest_doc_records(root)
    rows: list[dict[str, Any]] = []
    for item in inventory:
        doc_id = str(item.get("doc_id") or "")
        pages = rendered_page_count(root, doc_id)
        spans = textlayer_span_count(root, doc_id)
        mineable_candidates = mineable_candidate_count(root, doc_id)
        review = review_by_doc.get(doc_id, Counter())
        gold = gold_by_doc.get(doc_id, Counter())
        staged = staged_by_doc.get(doc_id, Counter())
        staged_future_rows = int(staged.get("staged_future_rows", 0))
        exhaustion = exhaustion_by_doc.get(doc_id, {})
        exhaustion_status = str(exhaustion.get("status") or "")
        duplicate_payload_alias = doc_id in duplicate_alias_doc_ids
        paper_ready, provenance_issue, resolved_source_path = source_document_readiness(
            root,
            item,
            manifest_docs,
        )
        next_step = local_next_step(
            item,
            review,
            gold,
            pages,
            spans,
            mineable_candidates,
            exhaustion_status,
            duplicate_payload_alias,
            staged_future_rows,
            paper_ready,
        )
        raw_fresh_open = int(review.get("fresh_open_rows", 0))
        unique_fresh_open = int(review.get("unique_fresh_open_rows", raw_fresh_open))
        release_safe = is_release_safe_status(str(item.get("public_status") or ""))
        rows.append(
            {
                "doc_id": doc_id,
                "domain": quota_domain(item.get("domain")),
                "task": item.get("task", ""),
                "public_status": item.get("public_status", ""),
                "source_path": resolved_source_path,
                "paper_ready": paper_ready,
                "source_provenance_issue": provenance_issue,
                "rendered_pages": pages,
                "textlayer_spans": spans,
                "mineable_candidates": mineable_candidates,
                "review_rows": int(review.get("review_rows", 0)),
                "open_review_rows": int(review.get("open_rows", 0)),
                "packeted_open_review_rows": int(review.get("packeted_open_rows", 0)),
                "unpacketed_open_review_rows": int(review.get("unpacketed_open_rows", review.get("open_rows", 0))),
                "fresh_open_review_rows": raw_fresh_open if release_safe else 0,
                "rights_blocked_open_review_rows": raw_fresh_open if not release_safe else 0,
                "stale_open_review_rows": int(review.get("stale_open_rows", 0)),
                "mergeable_review_rows": int(review.get("mergeable_rows", 0)),
                "missing_review_evidence_rows": int(review.get("missing_evidence_rows", 0)),
                "unique_open_review_rows": int(review.get("unique_open_rows", review.get("open_rows", 0))),
                "unique_packeted_open_review_rows": int(
                    review.get("unique_packeted_open_rows", review.get("packeted_open_rows", 0))
                ),
                "unique_fresh_open_review_rows": unique_fresh_open if release_safe else 0,
                "fresh_missing_review_evidence_rows": int(
                    review.get("unique_fresh_missing_evidence_rows", review.get("missing_evidence_rows", 0))
                ),
                "gold_rows": int(gold.get("gold_rows", 0)),
                "gold_microtext_rows": int(gold.get("gold_microtext_rows", 0)),
                "gold_visualdiff_rows": int(gold.get("gold_visualdiff_rows", 0)),
                "staged_future_rows": staged_future_rows,
                "staged_future_capacity_path": (
                    ";".join(
                        str(path.relative_to(root)).replace("\\", "/")
                        for path in staged_capacity_paths
                    )
                ),
                "conversion_exhaustion_status": exhaustion_status,
                "conversion_exhaustion_evidence": str(exhaustion.get("evidence_path") or ""),
                "duplicate_payload_alias": duplicate_payload_alias,
                "next_step": next_step,
                "priority_score": local_priority(
                    item,
                    review,
                    gold,
                    pages,
                    spans,
                    mineable_candidates,
                    exhaustion_status,
                    duplicate_payload_alias,
                    staged_future_rows,
                    paper_ready,
                ),
                "source_url": item.get("source_url", ""),
            }
        )
    rows.sort(key=lambda row: (int(row["priority_score"]), int(row["open_review_rows"])), reverse=True)
    return rows


def validation_by_candidate(root: Path) -> dict[str, dict[str, str]]:
    return {row.get("candidate_id", ""): row for row in read_csv(root / "SOURCE_CANDIDATE_VALIDATION.csv") if row.get("candidate_id")}


def is_deliberate_hold(release_posture: str | None, next_action: str | None) -> bool:
    posture = str(release_posture or "").strip().lower()
    action = str(next_action or "").strip().lower()
    return (
        posture == "hold"
        or posture.endswith("_hold")
        or action.startswith(("hold_", "deprioritize", "prototype_only", "recheck_"))
    )


def candidate_next_step(
    candidate: dict[str, str],
    validation: dict[str, str],
    imported: bool,
    review: Counter[str],
    linked_gold_rows: int = 0,
    linked_rights_blocked: bool = False,
    linked_staged_future_rows: int = 0,
    linked_exhaustion_status: str = "",
) -> str:
    rights = str(candidate.get("rights_tier") or "").lower()
    validation_action = str(
        validation.get("next_action") or validation.get("validation_next_action") or ""
    ).lower()
    if is_deliberate_hold(validation.get("release_posture"), validation_action):
        return "select_or_deprioritize"
    if rights_blocker(rights) or linked_rights_blocked:
        return "rights_review_or_hold"
    if linked_staged_future_rows > 0:
        return "staged_future_review_capacity"
    if is_machine_exhausted_status(linked_exhaustion_status):
        return linked_exhaustion_status
    open_rows = int(review.get("unique_open_rows", review.get("open_rows", 0)))
    packeted_open_rows = int(
        review.get("unique_packeted_open_rows", review.get("packeted_open_rows", 0))
    )
    fresh_open_rows = int(review.get("unique_fresh_open_rows", review.get("fresh_open_rows", 0)))
    stale_open_rows = int(review.get("unique_stale_open_rows", review.get("stale_open_rows", 0)))
    if open_rows > 0:
        if packeted_open_rows >= open_rows:
            return "await_human_return"
        if fresh_open_rows == 0:
            if packeted_open_rows > 0:
                return "await_human_return"
            return "reviewed_sibling_or_active_gold"
        if packeted_open_rows > 0 or stale_open_rows > 0:
            return "human_review_partial_packeted"
        return "human_review"
    if linked_gold_rows > 0:
        return "linked_local_active_gold_or_reviewed"
    if review.get("mergeable_rows", 0) > 0:
        return "maintainer_merge_audit"
    if review.get("review_rows", 0) > 0:
        return "linked_local_active_gold_or_reviewed"
    if imported or validation.get("next_action", "").startswith("imported"):
        return "audit_import_outputs_or_mine_candidates"
    if validation.get("next_action") == "intake_first":
        return "import_render_extract"
    if validation:
        return "select_or_deprioritize"
    return "browser_validate"


def candidate_priority(
    candidate: dict[str, str],
    validation: dict[str, str],
    review: Counter[str],
    imported: bool,
    linked_staged_future_rows: int = 0,
    linked_exhaustion_status: str = "",
) -> int:
    if linked_staged_future_rows > 0:
        return -50
    if is_machine_exhausted_status(linked_exhaustion_status):
        return -100
    release_safe = is_release_safe_status(str(candidate.get("rights_tier") or ""))
    unpacketed_open = (
        int(
            review.get(
                "unique_fresh_open_rows",
                review.get("fresh_open_rows", review.get("unpacketed_open_rows", review.get("open_rows", 0))),
            )
        )
        if release_safe
        else 0
    )
    packeted_open = int(review.get("unique_packeted_open_rows", review.get("packeted_open_rows", 0)))
    score = unpacketed_open * 10 + packeted_open * 2 + int(review.get("mergeable_rows", 0)) * 6
    if validation.get("next_action") == "intake_first":
        score += 25
    if validation.get("release_posture") == "release_candidate":
        score += 10
    if candidate.get("rights_tier") and "public" in candidate.get("rights_tier", ""):
        score += 8
    if "visualdiff" in candidate.get("task_fit", "") and "microtext" in candidate.get("task_fit", ""):
        score += 6
    if quota_domain(candidate.get("domain")) in {"pid", "mechanical_cad", "civil_architectural", "datasheet_spec"}:
        score += 5
    if imported:
        score -= 5
    return score


def summarize_candidates(
    root: Path,
    packet_row_keys: set[str] | None = None,
    resolved_row_keys: set[str] | None = None,
    packet_manifest_rows: list[tuple[str, dict[str, Any]]] | None = None,
    local_sources: list[dict[str, Any]] | None = None,
    lineage: dict[str, dict[str, set[str]]] | None = None,
) -> list[dict[str, Any]]:
    candidates = read_csv(root / "SOURCE_CANDIDATES_RANKED.csv") or read_csv(root / "SOURCE_CANDIDATES.csv")
    validations = validation_by_candidate(root)
    imported = imported_candidate_ids(root)
    review_by_doc, review_by_candidate = collect_review_stats(
        root,
        packet_row_keys,
        resolved_row_keys,
        packet_manifest_rows,
    )
    gold_by_doc = collect_gold_stats(root)
    local_by_doc = {str(row.get("doc_id") or ""): row for row in (local_sources or [])}
    lineage = lineage or {}
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_id = candidate.get("candidate_id", "")
        if not candidate_id:
            continue
        validation = validations.get(candidate_id, {})
        direct_review = review_by_candidate.get(candidate_id, Counter())
        linked_docs = sorted(lineage.get(candidate_id, {}))
        linked_local_rows = [local_by_doc[doc_id] for doc_id in linked_docs if doc_id in local_by_doc]
        linked_orphan_docs = [doc_id for doc_id in linked_docs if doc_id not in local_by_doc]
        linked_review = Counter()
        linked_gold_rows = 0
        linked_staged_future_rows = 0
        linked_rights_blocked_source_count = 0
        linked_exhaustion_steps: list[str] = []
        for local_row in linked_local_rows:
            linked_rights_blocked_source_count += int(local_row.get("next_step") == "rights_review_or_hold")
            local_next_step = str(local_row.get("next_step") or "")
            if is_machine_exhausted_status(local_next_step):
                linked_exhaustion_steps.append(local_next_step)
            for field in (
                "review_rows",
                "open_review_rows",
                "packeted_open_review_rows",
                "unpacketed_open_review_rows",
                "fresh_open_review_rows",
                "rights_blocked_open_review_rows",
                "stale_open_review_rows",
                "mergeable_review_rows",
                "missing_review_evidence_rows",
                "unique_open_review_rows",
                "unique_packeted_open_review_rows",
                "unique_fresh_open_review_rows",
                "fresh_missing_review_evidence_rows",
            ):
                linked_review[
                    {
                        "open_review_rows": "open_rows",
                        "packeted_open_review_rows": "packeted_open_rows",
                        "unpacketed_open_review_rows": "unpacketed_open_rows",
                        "fresh_open_review_rows": "fresh_open_rows",
                        "stale_open_review_rows": "stale_open_rows",
                        "mergeable_review_rows": "mergeable_rows",
                        "missing_review_evidence_rows": "missing_evidence_rows",
                        "unique_open_review_rows": "unique_open_rows",
                        "unique_packeted_open_review_rows": "unique_packeted_open_rows",
                        "unique_fresh_open_review_rows": "unique_fresh_open_rows",
                        "fresh_missing_review_evidence_rows": "unique_fresh_missing_evidence_rows",
                    }.get(field, field)
                ] += int(local_row.get(field) or 0)
            linked_gold_rows += int(local_row.get("gold_rows") or 0)
            linked_staged_future_rows += int(local_row.get("staged_future_rows") or 0)
        for doc_id in linked_orphan_docs:
            doc_review = review_by_doc.get(doc_id, Counter())
            for field in (
                "review_rows",
                "open_rows",
                "packeted_open_rows",
                "unpacketed_open_rows",
                "fresh_open_rows",
                "stale_open_rows",
                "mergeable_rows",
                "missing_evidence_rows",
                "unique_open_rows",
                "unique_packeted_open_rows",
                "unique_fresh_open_rows",
                "unique_fresh_missing_evidence_rows",
            ):
                linked_review[field] += int(doc_review.get(field, 0))
            linked_gold_rows += int(gold_by_doc.get(doc_id, Counter()).get("gold_rows", 0))
        review = Counter(
            {
                field: max(int(direct_review.get(field, 0)), int(linked_review.get(field, 0)))
                for field in set(direct_review) | set(linked_review)
            }
        )
        linked_exhaustion_status = ""
        if linked_local_rows and len(linked_exhaustion_steps) == len(linked_local_rows):
            unique_exhaustion_steps = set(linked_exhaustion_steps)
            linked_exhaustion_status = (
                linked_exhaustion_steps[0]
                if len(unique_exhaustion_steps) == 1
                else "machine_exhausted"
            )
        is_imported = candidate_id in imported or bool(linked_docs)
        next_step = candidate_next_step(
            candidate,
            validation,
            is_imported,
            review,
            linked_gold_rows,
            linked_rights_blocked=linked_rights_blocked_source_count > 0,
            linked_staged_future_rows=linked_staged_future_rows,
            linked_exhaustion_status=linked_exhaustion_status,
        )
        raw_fresh_open = int(review.get("fresh_open_rows", 0))
        unique_fresh_open = int(review.get("unique_fresh_open_rows", raw_fresh_open))
        release_safe = is_release_safe_status(str(candidate.get("rights_tier") or ""))
        linked_rights_blocked_rows = sum(
            int(row.get("rights_blocked_open_review_rows") or 0) for row in linked_local_rows
        )
        lineage_evidence = sorted(
            {
                evidence
                for doc_id in linked_docs
                for evidence in lineage.get(candidate_id, {}).get(doc_id, set())
            }
        )
        rows.append(
            {
                "candidate_id": candidate_id,
                "domain": quota_domain(candidate.get("domain")),
                "task_fit": candidate.get("task_fit", ""),
                "rights_tier": candidate.get("rights_tier", ""),
                "release_posture": validation.get("release_posture", ""),
                "validation_next_action": validation.get("next_action", ""),
                "imported_or_staged": is_imported,
                "linked_local_source_count": len(linked_docs),
                "linked_inventory_source_count": len(linked_local_rows),
                "linked_local_doc_ids": ";".join(linked_docs),
                "linked_local_orphan_doc_ids": ";".join(linked_orphan_docs),
                "lineage_evidence": ";".join(lineage_evidence),
                "direct_candidate_review_rows": int(direct_review.get("review_rows", 0)),
                "linked_local_review_rows": int(linked_review.get("review_rows", 0)),
                "linked_local_gold_rows": linked_gold_rows,
                "linked_staged_future_rows": linked_staged_future_rows,
                "linked_rights_blocked_source_count": linked_rights_blocked_source_count,
                "linked_machine_exhausted_source_count": len(linked_exhaustion_steps),
                "linked_exhaustion_status": linked_exhaustion_status,
                "review_rows": int(review.get("review_rows", 0)),
                "open_review_rows": int(review.get("open_rows", 0)),
                "packeted_open_review_rows": int(review.get("packeted_open_rows", 0)),
                "unpacketed_open_review_rows": int(review.get("unpacketed_open_rows", review.get("open_rows", 0))),
                "fresh_open_review_rows": raw_fresh_open if release_safe else 0,
                "rights_blocked_open_review_rows": max(
                    linked_rights_blocked_rows,
                    raw_fresh_open if not release_safe else 0,
                ),
                "stale_open_review_rows": int(review.get("stale_open_rows", 0)),
                "mergeable_review_rows": int(review.get("mergeable_rows", 0)),
                "unique_open_review_rows": int(review.get("unique_open_rows", review.get("open_rows", 0))),
                "unique_packeted_open_review_rows": int(
                    review.get("unique_packeted_open_rows", review.get("packeted_open_rows", 0))
                ),
                "unique_fresh_open_review_rows": unique_fresh_open if release_safe else 0,
                "fresh_missing_review_evidence_rows": int(
                    review.get("unique_fresh_missing_evidence_rows", review.get("missing_evidence_rows", 0))
                ),
                "next_step": next_step,
                "priority_score": candidate_priority(
                    candidate,
                    validation,
                    review,
                    is_imported,
                    linked_staged_future_rows,
                    linked_exhaustion_status,
                ),
                "source_url": candidate.get("source_url", ""),
                "notes": candidate.get("notes", ""),
            }
        )
    rows.sort(key=lambda row: (int(row["priority_score"]), int(row["open_review_rows"])), reverse=True)
    return rows


def build_report(root: Path, date_label: str = "2026-06-03") -> dict[str, Any]:
    packet_row_keys, packet_index_path = load_active_packet_row_keys(root, date_label)
    packet_manifest_rows = load_active_packet_manifest_rows(root, packet_index_path)
    packet_index_label = human_packet_index_label(packet_index_path) if packet_index_path else ""
    if packet_index_path:
        try:
            packet_index_display_path = packet_index_path.relative_to(root).as_posix()
        except ValueError:
            packet_index_display_path = packet_index_path.as_posix()
    else:
        packet_index_display_path = ""
    resolved_row_keys = active_gold_row_keys(root) | terminal_reviewed_row_keys(root)
    local_sources = summarize_local_sources(
        root,
        packet_row_keys,
        resolved_row_keys,
        packet_manifest_rows,
    )
    lineage = explicit_candidate_local_lineage(root)
    candidates = summarize_candidates(
        root,
        packet_row_keys,
        resolved_row_keys,
        packet_manifest_rows,
        local_sources=local_sources,
        lineage=lineage,
    )
    candidate_lineage = [
        {
            "candidate_id": candidate_id,
            "doc_id": doc_id,
            "evidence": ";".join(sorted(evidence)),
            "inventory_present": doc_id in {row["doc_id"] for row in local_sources},
        }
        for candidate_id, docs in sorted(lineage.items())
        for doc_id, evidence in sorted(docs.items())
    ]
    candidates_with_lineage = sum(int(row["linked_local_source_count"]) > 0 for row in candidates)
    candidate_by_id = {row["candidate_id"]: row for row in candidates}
    lineage_docs_missing_inventory = {
        row["doc_id"]
        for row in candidate_lineage
        if not row["inventory_present"]
        and not is_deliberate_hold(
            candidate_by_id.get(row["candidate_id"], {}).get("release_posture"),
            candidate_by_id.get(row["candidate_id"], {}).get("validation_next_action"),
        )
    }
    review_by_doc, _review_by_candidate = collect_review_stats(
        root,
        packet_row_keys,
        resolved_row_keys,
        packet_manifest_rows,
    )
    gold_by_doc = collect_gold_stats(root)
    source_inventory_repair_queue = []
    for row in candidate_lineage:
        if row["inventory_present"]:
            continue
        candidate = candidate_by_id.get(row["candidate_id"], {})
        if is_deliberate_hold(
            candidate.get("release_posture"),
            candidate.get("validation_next_action"),
        ):
            continue
        review = review_by_doc.get(row["doc_id"], Counter())
        gold = gold_by_doc.get(row["doc_id"], Counter())
        source_inventory_repair_queue.append(
            {
                "candidate_id": row["candidate_id"],
                "doc_id": row["doc_id"],
                "domain": candidate.get("domain", "unknown"),
                "task_fit": candidate.get("task_fit", ""),
                "lineage_evidence": row["evidence"],
                "candidate_next_step": candidate.get("next_step", ""),
                "rendered_pages": rendered_page_count(root, row["doc_id"]),
                "textlayer_spans": textlayer_span_count(root, row["doc_id"]),
                "review_rows": int(review.get("review_rows", 0)),
                "open_review_rows": int(review.get("open_rows", 0)),
                "packeted_open_review_rows": int(review.get("packeted_open_rows", 0)),
                "gold_rows": int(gold.get("gold_rows", 0)),
                "source_url": candidate.get("source_url", ""),
                "recommended_action": "add_inventory_row_after_path_rights_and_license_review",
            }
        )
    source_inventory_repair_queue.sort(key=lambda row: (row["candidate_id"], row["doc_id"]))
    totals = {
        "date_label": date_label,
        "active_packet_index_label": packet_index_label,
        "active_packet_index_path": packet_index_display_path,
        "local_sources": len(local_sources),
        "candidate_sources": len(candidates),
        "candidate_sources_with_explicit_local_lineage": candidates_with_lineage,
        "candidate_sources_without_explicit_local_lineage": len(candidates) - candidates_with_lineage,
        "explicit_candidate_local_links": len(candidate_lineage),
        "explicit_lineage_docs_missing_inventory": len(lineage_docs_missing_inventory),
        "active_packet_row_keys": len(packet_row_keys),
        "resolved_review_row_keys": len(resolved_row_keys),
        "local_by_next_step": dict(sorted(Counter(row["next_step"] for row in local_sources).items())),
        "candidate_by_next_step": dict(sorted(Counter(row["next_step"] for row in candidates).items())),
        "open_review_rows_by_local_source": sum(int(row["open_review_rows"]) for row in local_sources),
        "packeted_open_review_rows_by_local_source": sum(int(row["packeted_open_review_rows"]) for row in local_sources),
        "unpacketed_open_review_rows_by_local_source": sum(int(row["unpacketed_open_review_rows"]) for row in local_sources),
        "fresh_open_review_rows_by_local_source": sum(int(row["fresh_open_review_rows"]) for row in local_sources),
        "rights_blocked_open_review_rows_by_local_source": sum(int(row["rights_blocked_open_review_rows"]) for row in local_sources),
        "stale_open_review_rows_by_local_source": sum(int(row["stale_open_review_rows"]) for row in local_sources),
        "packeted_open_review_rows_by_candidate": sum(int(row["packeted_open_review_rows"]) for row in candidates),
        "unpacketed_open_review_rows_by_candidate": sum(int(row["unpacketed_open_review_rows"]) for row in candidates),
        "fresh_open_review_rows_by_candidate": sum(int(row["fresh_open_review_rows"]) for row in candidates),
        "rights_blocked_open_review_rows_by_candidate": sum(int(row["rights_blocked_open_review_rows"]) for row in candidates),
        "stale_open_review_rows_by_candidate": sum(int(row["stale_open_review_rows"]) for row in candidates),
        "gold_rows_by_local_source": sum(int(row["gold_rows"]) for row in local_sources),
    }
    return {
        "totals": totals,
        "top_local_source_queue": local_sources[:30],
        "top_candidate_queue": candidates[:30],
        "local_sources": local_sources,
        "candidate_sources": candidates,
        "candidate_lineage": candidate_lineage,
        "source_inventory_repair_queue": source_inventory_repair_queue,
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Source Conversion Readiness Audit",
        "",
        "This read-only report tracks sources from candidate selection through local import, rendering/text layer, review staging, and active gold.",
        "",
        f"- Date label: `{totals['date_label']}`",
        f"- Active packet index: `{totals['active_packet_index_label'] or 'none'}` (`{totals['active_packet_index_path'] or 'missing'}`)",
        f"- Local sources: `{totals['local_sources']}`",
        f"- Candidate sources: `{totals['candidate_sources']}`",
        f"- Candidate sources with explicit local lineage: `{totals['candidate_sources_with_explicit_local_lineage']}`",
        f"- Explicit candidate-to-local links: `{totals['explicit_candidate_local_links']}`",
        f"- Explicitly linked local docs missing source inventory rows: `{totals['explicit_lineage_docs_missing_inventory']}`",
        f"- Local open review rows: `{totals['open_review_rows_by_local_source']}`",
        f"- Local open rows already in active human packets: `{totals['packeted_open_review_rows_by_local_source']}`",
        f"- Local open rows not yet packeted: `{totals['unpacketed_open_review_rows_by_local_source']}`",
        f"- Local fresh actionable unpacketed open rows: `{totals['fresh_open_review_rows_by_local_source']}`",
        f"- Local rights-blocked unpacketed open rows: `{totals['rights_blocked_open_review_rows_by_local_source']}`",
        f"- Local stale open rows already reviewed/active-gold: `{totals['stale_open_review_rows_by_local_source']}`",
        f"- Active packet row identities loaded: `{totals['active_packet_row_keys']}`",
        f"- Reviewed/active-gold row identities loaded: `{totals['resolved_review_row_keys']}`",
        f"- Local gold rows counted by source rows: `{totals['gold_rows_by_local_source']}`",
        "",
        "## Local Sources By Next Step",
        "",
    ]
    for step, count in totals["local_by_next_step"].items():
        lines.append(f"- {step}: `{count}`")
    lines.extend(["", "## Candidate Sources By Next Step", ""])
    for step, count in totals["candidate_by_next_step"].items():
        lines.append(f"- {step}: `{count}`")

    lines.extend(
        [
            "",
            "## Top Local Source Queue",
            "",
            "| Doc ID | Domain | Next Step | Open Review | Packeted Open | Fresh Actionable | Rights Blocked | Stale Open | Gold Rows | Pages | Text Spans | Mineable | Priority |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in report["top_local_source_queue"][:20]:
        lines.append(
            f"| `{row['doc_id']}` | {row['domain']} | {row['next_step']} | "
            f"{row['open_review_rows']} | {row['packeted_open_review_rows']} | "
            f"{row['fresh_open_review_rows']} | {row['rights_blocked_open_review_rows']} | "
            f"{row['stale_open_review_rows']} | "
            f"{row['gold_rows']} | {row['rendered_pages']} | "
            f"{row['textlayer_spans']} | {row['mineable_candidates']} | {row['priority_score']} |"
        )

    lines.extend(
        [
            "",
            "## Top Candidate Import Queue",
            "",
            "| Candidate | Domain | Linked Docs | Next Step | Validation | Open Review | Packeted Open | Fresh Actionable | Rights Blocked | Stale Open | Gold | Priority |",
            "| --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in report["top_candidate_queue"][:20]:
        lines.append(
            f"| `{row['candidate_id']}` | {row['domain']} | {row['linked_local_source_count']} | "
            f"{row['next_step']} | {row['validation_next_action'] or 'unvalidated'} | "
            f"{row['open_review_rows']} | {row['packeted_open_review_rows']} | "
            f"{row['fresh_open_review_rows']} | {row['rights_blocked_open_review_rows']} | "
            f"{row['stale_open_review_rows']} | {row['linked_local_gold_rows']} | {row['priority_score']} |"
        )
    lines.extend(
        [
            "",
            "## Candidate-To-Local Lineage",
            "",
            "Accepted evidence includes intake-log manifest IDs, source-import manifests, unambiguous inventory notes, annotation or manifest `source_candidate_id` fields, explicit candidate aliases, and exact `candidate_id_` document-ID prefixes.",
            "",
            "| Candidate | Local Doc | In Inventory | Evidence |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in report["candidate_lineage"]:
        lines.append(
            f"| `{row['candidate_id']}` | `{row['doc_id']}` | "
            f"{'yes' if row['inventory_present'] else 'no'} | {row['evidence']} |"
        )
    lines.extend(
        [
            "",
            "## Source Inventory Repair Queue",
            "",
            "These explicitly linked local documents are absent from `SOURCE_INVENTORY.csv`. Add them only after confirming the local path, rights posture, and required attribution/license metadata.",
            "",
            "| Candidate | Local Doc | Review Rows | Packeted Open | Gold Rows | Recommended Action |",
            "| --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for row in report["source_inventory_repair_queue"]:
        lines.append(
            f"| `{row['candidate_id']}` | `{row['doc_id']}` | {row['review_rows']} | "
            f"{row['packeted_open_review_rows']} | {row['gold_rows']} | {row['recommended_action']} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "Rows with `await_human_return` are already in active human packets and should not be repackaged. Rights-blocked open rows are excluded from fresh actionable counts until provenance is cleared. Rows with `reviewed_sibling_or_active_gold` are stale open queue rows that already have reviewed siblings or active-gold identities and should not be re-sent. Rows with `human_review` or `human_review_partial_packeted` need completed CSV decisions or additional packaging before gold can grow. Rows with `mine_candidates_and_export_review`, `ocr_or_manual_region_proposal`, `extract_textlayer_or_ocr`, or `import_render_extract` are machine-side candidates for the next conversion pass.",
            "Rows with `staged_future_review_capacity` already have machine-curated rows in the latest canonical future-capacity queue and should advance through human review rather than be re-mined. Rows with `machine_reviewed_no_actionable_candidate` have terminal review history and no open, mergeable, staged, or active-Gold row. Rows with `machine_exhausted_no_candidate` have explicit visual-QA evidence showing that the current rendering and detector pass yielded no taxonomy-valid, nonduplicate candidate. Rows with `machine_exhausted_after_reviewed_pass` completed the current detector, deduplication, and visual-QA pass and have no additional machine-actionable candidates beyond rows already staged or held. Candidate-level `machine_exhausted` means all linked sources are exhausted but their leaf outcomes are mixed. Revisit an exhausted state only after a materially different OCR, detector, or source asset becomes available.",
            "Rows with `duplicate_payload_alias` are exact-byte aliases of another registered source payload and must not be counted or mined as additional source diversity.",
            "Rows with `visualdiff_pair_alignment_or_review` are rendered revision assets. Pair and align old/new versions or use an existing VisualDiff review queue; do not send them through microtext OCR.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit source conversion readiness.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--date-label", default=date.today().isoformat(), help="Date label for active packet index and dated outputs")
    parser.add_argument("--output-json", help="Output JSON path; defaults to dated quality report")
    parser.add_argument("--output-md", help="Output Markdown path; defaults to dated quality report")
    parser.add_argument("--local-csv", default="docs/SOURCE_CONVERSION_LOCAL_QUEUE.csv")
    parser.add_argument("--candidate-csv", default="docs/SOURCE_CONVERSION_CANDIDATE_QUEUE.csv")
    parser.add_argument("--inventory-repair-csv", default="docs/SOURCE_INVENTORY_REPAIR_QUEUE.csv")
    args = parser.parse_args(argv)

    root = Path(args.root)
    output_json = args.output_json or f"derived/quality/source_conversion_readiness_{args.date_label}.json"
    output_md = args.output_md or f"derived/quality/source_conversion_readiness_{args.date_label}.md"
    report = build_report(root, date_label=args.date_label)
    write_json(root / output_json, report)
    md_path = root / output_md
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(report), encoding="utf-8")
    write_csv(root / args.local_csv, report["local_sources"])
    write_csv(root / args.candidate_csv, report["candidate_sources"])
    write_csv(
        root / args.inventory_repair_csv,
        report["source_inventory_repair_queue"],
        fieldnames=SOURCE_INVENTORY_REPAIR_FIELDS,
    )
    print(f"[OK] Wrote {root / output_json}")
    print(f"[OK] Wrote {md_path}")
    print(f"[OK] Wrote {root / args.local_csv}")
    print(f"[OK] Wrote {root / args.candidate_csv}")
    print(f"[OK] Wrote {root / args.inventory_repair_csv}")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
