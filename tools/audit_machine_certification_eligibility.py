#!/usr/bin/env python3
"""Classify staged rows for fail-closed Eng_Bench machine certification.

Only deterministic, train-split MicroText rows can become auto-eligible. The
tool never edits active Gold and never treats machine evidence as human review.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from PIL import Image


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_staged_v2_capacity as staged
from audit_active_gold_provenance import file_sha256, manifest_maps, read_csv
from audit_staged_promotion_contract import parse_bbox, reservation_records, source_audit
from source_rights import is_release_safe_status
from text_encoding import mojibake_signatures


POLICY_VERSION = "1.0"
AUTO_TIER = "auto_gold_train"
DETERMINISTIC_SOURCES = {
    "legacy_svg_exact_text_candidate",
    "manual_textlayer_curated_label",
    "textlayer_full_span_candidate",
    "textlayer_regex_candidate",
}
AUTO_CATEGORIES = {
    "component_value",
    "dimension_value",
    "pin_label",
    "process_value",
    "tolerance_value",
}
HUMAN_ONLY_CATEGORIES = {
    "equipment_tag",
    "instrument_tag",
    "pipe_line_tag",
    "process_label",
    "room_label",
}
UNKNOWN_CATEGORIES = {"", "unknown", "unknown_microtext"}
FINAL_HUMAN_STATUSES = {"accepted", "edited", "valid", "edit"}
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
TAG_RE = re.compile(r"^[A-Za-z]{1,8}[A-Za-z0-9]*(?:[-_/\.][A-Za-z0-9]+)+$")
PIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./+\-#()]*$")
POWER_RAIL_PIN_RE = re.compile(r"^\+\d+(?:V\d+|V)$", re.IGNORECASE)
DIMENSION_RE = re.compile(
    r"^(?:"
    r"[+\-]?(?:\d+(?:\.\d+)?|\.\d+)\s*(?:°|mm|MM|cm|CM|km|KM|m|ft|FT|in|IN|foot|FOOT|feet|FEET|'|’|\"|”)"
    r"|\d+(?:\.\d+)?(?:'|’)\s*-\s*\d+(?:\.\d+)?(?:\"|”)"
    r")$"
)
VOLTAGE_VALUE_RE = re.compile(r"\d+(?:\.\d+)?V", re.IGNORECASE)


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def display(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"expected object at {path}:{line_number}")
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_cohort(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("cohort must use NAME=PATH")
    name, path = value.split("=", 1)
    if not name.strip() or not path.strip():
        raise argparse.ArgumentTypeError("cohort must use non-empty NAME=PATH")
    return name.strip(), Path(path.strip())


def task_for(row: dict[str, Any]) -> str:
    return staged.task_for_row(row)


def resolve_split_reservation(
    row: dict[str, Any],
    aggregate_reservations: dict[tuple[str, str], dict[str, Any]],
) -> tuple[str, str, str]:
    """Resolve a row's split without weakening the authoritative plan contract."""
    reserved_split = str(row.get("reserved_split") or "").strip().lower()
    reservation_id = str(row.get("split_reservation_id") or "").strip()
    if reserved_split:
        return reserved_split, reservation_id, "row_reservation"

    unit = staged.staged_split_unit(row)
    planned = aggregate_reservations.get(unit) or {}
    locked_split = str(row.get("split") or "").strip().lower()
    planned_split = str(planned.get("split") or "").strip().lower()
    planned_id = str(planned.get("reservation_id") or "").strip()
    if (
        row.get("split_locked") is True
        and locked_split in {"train", "dev", "test"}
        and locked_split == planned_split
        and planned_id
    ):
        return locked_split, planned_id, "authoritative_plan_backfill"
    return "", reservation_id, "missing"


def identity_for(row: dict[str, Any]) -> str:
    task = task_for(row)
    fields = ("pair_id", "id") if task == "visualdiff" else (
        "candidate_id",
        "item_id",
        "id",
    )
    return next((str(row.get(field) or "").strip() for field in fields if row.get(field)), "")


def clean_text(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).strip().split())


def row_answer(row: dict[str, Any]) -> str:
    return clean_text(row.get("corrected_text") or row.get("proposed_text") or row.get("target_text"))


def boundary_safe_source_subspan(category: str, raw: str, value: str) -> bool:
    if raw == value:
        return True
    if category == "pin_label" and (
        not any(character.isdigit() for character in value)
        or any(character.islower() for character in raw)
    ):
        return False
    start = raw.find(value)
    while start >= 0:
        end = start + len(value)
        before = raw[start - 1] if start else ""
        after = raw[end] if end < len(raw) else ""
        if category == "pin_label":
            bad_before = bool(before and (before.isalnum() or before in "_.+"))
            bad_after = bool(after and (after.isalnum() or after in "_.+-"))
        else:
            bad_before = bool(before and (before.isalnum() or before in "._+-"))
            bad_after = bool(
                after
                and (
                    after.isalnum()
                    or after == "_"
                    or (after == "." and value[-1].isdigit())
                )
            )
        if not bad_before and not bad_after:
            return True
        start = raw.find(value, start + 1)
    return False


def source_text_fields_machine_safe(
    category: str,
    raw: str,
    target: str,
    proposed: str,
    answer: str,
) -> bool:
    if raw and target and proposed and raw == target == proposed:
        return True
    if raw and target and not proposed and raw == target == answer:
        return not (category == "pin_label" and VOLTAGE_VALUE_RE.fullmatch(answer))
    if not raw or not target or not answer or target != answer:
        return False
    if proposed and proposed != answer:
        return False
    return boundary_safe_source_subspan(category, raw, answer)


def source_text_contract(
    category: str,
    raw: str,
    target: str,
    proposed: str,
    answer: str,
) -> str:
    if raw and target and proposed and raw == target == proposed:
        return "exact_all_fields"
    if raw and target and not proposed and raw == target == answer:
        if category == "pin_label" and VOLTAGE_VALUE_RE.fullmatch(answer):
            return ""
        return "exact_raw_target_no_proposed"
    if source_text_fields_machine_safe(category, raw, target, proposed, answer):
        return "boundary_safe_exact_subspan"
    return ""


def category_shape_safe(category: str, text: str) -> bool:
    if not text or len(text) > 64 or "\n" in text or "\r" in text:
        return False
    tokens = text.split()
    if len(tokens) > 6 or text.endswith((".", ";", ":")):
        return False
    has_letter = bool(re.search(r"[A-Za-z]", text))
    has_digit = bool(re.search(r"\d", text))
    if category == "pin_label":
        return (
            len(text) <= 32
            and " " not in text
            and has_letter
            and bool(PIN_RE.fullmatch(text) or POWER_RAIL_PIN_RE.fullmatch(text))
        )
    if category == "dimension_value":
        return has_digit and bool(DIMENSION_RE.fullmatch(text))
    if category in {"component_value", "process_value"}:
        return has_digit
    if category == "tolerance_value":
        return has_digit and bool(re.search(r"[+\-±%]|(?:\b(?:max|min)\b)", text, re.IGNORECASE))
    if category in {"instrument_tag", "pipe_line_tag"}:
        return has_letter and has_digit and bool(TAG_RE.fullmatch(text))
    if category == "equipment_tag":
        return has_letter and has_digit and bool(TAG_RE.fullmatch(text))
    return False


def normalized_revision_tokens(manifest: dict[str, Any] | None) -> set[str]:
    """Return conservative normalized version tokens from a manifest record."""
    version = (manifest or {}).get("version")
    if not isinstance(version, dict):
        return set()
    tokens: set[str] = set()
    values: list[Any] = []
    for value in version.values():
        values.extend(value if isinstance(value, list) else [value])
    for value in values:
        token = re.sub(r"[^a-z0-9]+", "", clean_text(value).lower())
        for prefix in ("revision", "rev"):
            if token.startswith(prefix):
                token = token[len(prefix) :]
                break
        if token:
            tokens.add(token)
    return tokens


def pin_label_matches_source_revision(
    category: str,
    text: str,
    manifest: dict[str, Any] | None,
) -> bool:
    if category != "pin_label":
        return False
    token = re.sub(r"[^a-z0-9]+", "", clean_text(text).lower())
    return bool(token and token in normalized_revision_tokens(manifest))


def evidence_record_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_ocr_cache(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.is_file():
        return {}
    return {
        str(row.get("evidence_sha256") or ""): row
        for row in read_jsonl(path)
        if row.get("evidence_sha256")
    }


def recognize_crop(engine: Any, np_module: Any, crop: Image.Image, scale: int) -> tuple[str, float]:
    attempts = [crop]
    if crop.height > crop.width * 1.5:
        attempts.extend((crop.rotate(90, expand=True), crop.rotate(270, expand=True)))
    best_text = ""
    best_score = 0.0
    for image in attempts:
        if scale > 1:
            image = image.resize(
                (max(1, image.width * scale), max(1, image.height * scale)),
                Image.Resampling.LANCZOS,
            )
        result = engine(np_module.asarray(image), use_det=False, use_cls=True, use_rec=True)
        texts = list(getattr(result, "txts", None) or [])
        scores = list(getattr(result, "scores", None) or [])
        for text, score in zip(texts, scores):
            if float(score) > best_score:
                best_text = clean_text(text)
                best_score = float(score)
    return best_text, best_score


def materialize_calibration_pack(
    root: Path,
    sample: list[dict[str, Any]],
    output_dir: Path,
) -> tuple[Path, Path]:
    crops_dir = output_dir / "crops"
    contexts_dir = output_dir / "contexts"
    crops_dir.mkdir(parents=True, exist_ok=True)
    contexts_dir.mkdir(parents=True, exist_ok=True)
    checklist_path = output_dir / "machine_certification_calibration_checklist.csv"
    fields = [
        "sample_index", "candidate_id", "doc_id", "version_id", "page_index", "bbox",
        "category", "proposed_text", "ocr_text", "ocr_confidence", "crop_path", "context_path",
        "reviewer_decision", "corrected_text", "corrected_category", "reviewer_notes",
    ]
    html_rows: list[str] = []
    with checklist_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        previous_limit = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = None
        try:
            for index, row in enumerate(sample, start=1):
                identifier = identity_for(row)
                image_path = resolve(root, str(row.get("image_path") or ""))
                bbox = parse_bbox(row.get("bbox"))
                if bbox is None or not image_path.is_file():
                    raise ValueError(f"calibration evidence missing for {identifier}")
                with Image.open(image_path) as page:
                    page = page.convert("RGB")
                    x1, y1, x2, y2 = bbox
                    crop_box = (max(0, x1 - 10), max(0, y1 - 10), min(page.width, x2 + 10), min(page.height, y2 + 10))
                    context_box = (max(0, x1 - 80), max(0, y1 - 60), min(page.width, x2 + 80), min(page.height, y2 + 60))
                    crop_path = crops_dir / f"{index:04d}__{identifier}.png"
                    context_path = contexts_dir / f"{index:04d}__{identifier}.png"
                    page.crop(crop_box).save(crop_path)
                    page.crop(context_box).save(context_path)
                values = {
                    "sample_index": index,
                    "candidate_id": identifier,
                    "doc_id": row.get("doc_id") or "",
                    "version_id": row.get("version_id") or "",
                    "page_index": row.get("page_index") or 0,
                    "bbox": json.dumps(row.get("bbox"), ensure_ascii=False),
                    "category": row.get("category") or "",
                    "proposed_text": row_answer(row),
                    "ocr_text": row.get("machine_certification_ocr_text") or "",
                    "ocr_confidence": row.get("machine_certification_ocr_confidence") or "",
                    "crop_path": crop_path.relative_to(output_dir).as_posix(),
                    "context_path": context_path.relative_to(output_dir).as_posix(),
                    "reviewer_decision": "",
                    "corrected_text": "",
                    "corrected_category": "",
                    "reviewer_notes": "",
                }
                writer.writerow(values)
                html_rows.append(
                    "<tr>"
                    f"<td>{index}</td><td><img src='{html.escape(values['crop_path'])}'></td>"
                    f"<td><a href='{html.escape(values['context_path'])}'>context</a></td>"
                    f"<td>{html.escape(str(values['category']))}</td>"
                    f"<td>{html.escape(str(values['proposed_text']))}</td>"
                    f"<td>{html.escape(str(values['ocr_text']))}</td>"
                    "</tr>"
                )
        finally:
            Image.MAX_IMAGE_PIXELS = previous_limit
    index_path = output_dir / "index.html"
    index_path.write_text(
        "<!doctype html><meta charset='utf-8'><title>Machine Certification Calibration</title>"
        "<style>body{font-family:Arial,sans-serif;margin:20px}table{border-collapse:collapse}"
        "td,th{border:1px solid #bbb;padding:6px;vertical-align:top}img{max-width:420px;max-height:150px}</style>"
        "<h1>Machine Certification Calibration</h1>"
        "<p>Inspect the crop and context. Record correct, incorrect, or unclear in the CSV.</p>"
        "<table><thead><tr><th>#</th><th>Crop</th><th>Context</th><th>Category</th>"
        "<th>Proposed</th><th>Independent OCR</th></tr></thead><tbody>"
        + "".join(html_rows)
        + "</tbody></table>",
        encoding="utf-8",
    )
    (output_dir / "README.md").write_text(
        "# Machine Certification Calibration\n\n"
        "This is a statistical audit of machine-eligible train MicroText rows. Open `index.html`, then fill "
        "`machine_certification_calibration_checklist.csv`. Use only `correct`, `incorrect`, or `unclear`. "
        "Every sampled row must be completed. Any incorrect or unclear row blocks the cohort and triggers a rule revision.\n",
        encoding="utf-8",
    )
    return checklist_path, index_path


def stratified_sample(rows: list[dict[str, Any]], size: int, seed: str) -> list[dict[str, Any]]:
    if size <= 0 or not rows:
        return []
    size = min(size, len(rows))
    ranked = sorted(
        rows,
        key=lambda row: hashlib.sha256(f"{seed}:{identity_for(row)}".encode()).hexdigest(),
    )
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ranked:
        by_category[str(row.get("category") or "")].append(row)
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    per_category = max(1, min(20, size // max(1, len(by_category))))
    for category in sorted(by_category):
        for row in by_category[category][:per_category]:
            if len(selected) >= size:
                break
            selected.append(row)
            selected_ids.add(identity_for(row))
    for row in ranked:
        if len(selected) >= size:
            break
        if identity_for(row) not in selected_ids:
            selected.append(row)
            selected_ids.add(identity_for(row))
    return selected


def build_audit(
    root: Path,
    cohorts: list[tuple[str, Path]],
    split_plan_path: Path,
    *,
    date_label: str,
    ocr_mode: str,
    ocr_min_confidence: float,
    ocr_scale: int,
    ocr_cache_path: Path | None,
    max_ocr_rows: int,
    ocr_cache_output_path: Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    root = root.resolve()
    split_plan_path = resolve(root, split_plan_path)
    reservations, split_issues = staged.load_split_reservations(split_plan_path)
    if split_issues:
        raise ValueError(f"invalid split plan: {split_issues[:5]}")
    aggregate_reservations = {
        (str(item.get("task") or ""), str(item.get("unit_id") or "")): item
        for item in json.loads(split_plan_path.read_text(encoding="utf-8")).get("reservations", [])
    }
    legacy_reservation_cache: dict[Path, dict[tuple[str, str], dict[str, Any]]] = {}
    docs, _ = manifest_maps(root)
    inventory = {
        str(row.get("doc_id") or ""): row
        for row in read_csv(root / "SOURCE_INVENTORY.csv")
        if row.get("doc_id")
    }
    active_rows = read_jsonl(root / "microtext/annotations/microtext_items.jsonl") + read_jsonl(
        root / "visualdiff/annotations/visualdiff_pairs.jsonl"
    )
    active_capacity = {staged.capacity_identity(row) for row in active_rows if staged.capacity_identity(row)}
    active_regions: set[tuple[str, int, tuple[int, int, int, int]]] = set()
    source_cache: dict[str, tuple[list[str], str]] = {}
    source_file_hash_cache: dict[Path, str] = {}

    def source_result(doc_id: str) -> tuple[list[str], str]:
        if doc_id in source_cache:
            return source_cache[doc_id]
        issues = source_audit(root, doc_id, docs, inventory)
        manifest = docs.get(doc_id) or {}
        source = inventory.get(doc_id) or {}
        status = str(source.get("public_status") or manifest.get("public_status") or "")
        if not is_release_safe_status(status) and not any(issue.startswith("rights_blocked:") for issue in issues):
            issues.append("rights_blocked")
        payload_sha = str(manifest.get("sha256") or "").strip().lower()
        local_text = str(source.get("path") or manifest.get("path") or "").strip()
        local = resolve(root, local_text) if local_text else None
        if local is not None and local.is_file():
            if local not in source_file_hash_cache:
                source_file_hash_cache[local] = file_sha256(local).lower()
            payload_sha = source_file_hash_cache[local]
        source_cache[doc_id] = (sorted(set(issues)), payload_sha)
        return source_cache[doc_id]

    for row in active_rows:
        if task_for(row) != "microtext":
            continue
        doc_id = str(row.get("doc_id") or "").strip()
        bbox = parse_bbox(row.get("bbox"))
        if not doc_id or bbox is None:
            continue
        _, payload_sha = source_result(doc_id)
        if payload_sha:
            active_regions.add((payload_sha, int(row.get("page_index") or 0), bbox))

    staged_rows: list[dict[str, Any]] = []
    cohort_hashes: list[dict[str, str]] = []
    for cohort_name, raw_path in cohorts:
        path = resolve(root, raw_path)
        cohort_hashes.append({"name": cohort_name, "path": display(root, path), "sha256": file_sha256(path)})
        for row in read_jsonl(path):
            enriched = dict(row)
            enriched["machine_certification_origin_cohort"] = cohort_name
            enriched["machine_certification_origin_path"] = display(root, path)
            staged_rows.append(enriched)

    identity_counts = Counter(f"{task_for(row)}:{identity_for(row)}" for row in staged_rows)
    staged_regions: Counter[tuple[str, int, tuple[int, int, int, int]]] = Counter()
    row_context: dict[int, dict[str, Any]] = {}
    image_hash_cache: dict[Path, str] = {}
    image_size_cache: dict[Path, tuple[int, int] | None] = {}
    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = None
    try:
        for index, row in enumerate(staged_rows):
            context: dict[str, Any] = {"fatal": [], "human": [], "evidence": {}}
            row_context[index] = context
            task = task_for(row)
            identity = identity_for(row)
            split, reservation_id, split_resolution = resolve_split_reservation(
                row, aggregate_reservations
            )
            context["resolved_split"] = split
            context["resolved_split_reservation_id"] = reservation_id
            context["split_resolution"] = split_resolution
            if not identity:
                context["fatal"].append("missing_identity")
            elif identity_counts[f"{task}:{identity}"] > 1:
                context["fatal"].append("duplicate_input_identity")
            capacity_identity = staged.capacity_identity(row)
            if capacity_identity and capacity_identity in active_capacity:
                context["fatal"].append("active_gold_overlap")
            if task != "microtext":
                context["human"].append("visualdiff_requires_human_review")
                continue
            if split != "train":
                context["human"].append("evaluation_split_requires_human_review")
                continue
            status = str(row.get("review_status") or row.get("human_review_status") or "").strip().lower()
            if status in FINAL_HUMAN_STATUSES:
                context["human"].append("already_human_final_use_normal_promotion")
                continue
            doc_id = str(row.get("doc_id") or "").strip()
            version_id = str(row.get("version_id") or "").strip()
            if not doc_id:
                context["fatal"].append("missing_doc_id")
            if not version_id:
                context["fatal"].append("missing_version_id")
            source_issues, payload_sha = source_result(doc_id) if doc_id else (["missing_doc_id"], "")
            context["fatal"].extend(f"source:{issue}" for issue in source_issues)
            if not HEX64_RE.fullmatch(payload_sha):
                context["fatal"].append("source:missing_verified_payload_sha256")
            row_payload_sha = str(row.get("source_payload_sha256") or "").strip().lower()
            if row_payload_sha and row_payload_sha != payload_sha:
                context["fatal"].append("source:row_payload_sha256_mismatch")
            source_kind = str(row.get("source") or "").strip()
            if source_kind not in DETERMINISTIC_SOURCES:
                context["human"].append("non_deterministic_extraction_source")
            category = str(row.get("corrected_category") or row.get("category") or "").strip().lower()
            if category in UNKNOWN_CATEGORIES:
                context["fatal"].append("unresolved_category")
            elif category in HUMAN_ONLY_CATEGORIES:
                context["human"].append("semantic_category_requires_human_review")
            elif category not in AUTO_CATEGORIES:
                context["human"].append("category_not_machine_certifiable")
            answer = row_answer(row)
            raw = clean_text(row.get("raw_text"))
            target = clean_text(row.get("target_text"))
            proposed = clean_text(row.get("proposed_text"))
            text_contract = source_text_contract(category, raw, target, proposed, answer)
            context["source_text_contract"] = text_contract
            if not answer:
                context["fatal"].append("missing_answer")
            if not text_contract:
                context["human"].append("source_text_fields_do_not_exactly_agree")
            if not category_shape_safe(category, answer):
                context["human"].append("category_text_pattern_not_deterministic")
            if pin_label_matches_source_revision(category, answer, docs.get(doc_id)):
                context["human"].append("pin_label_matches_source_revision")
            if any(mojibake_signatures(value) for value in (answer, raw, target, proposed, category)):
                context["fatal"].append("text_encoding_error")
            unit = staged.staged_split_unit(row)
            expected_split = reservations.get(unit, "")
            if expected_split != split:
                context["fatal"].append("split_reservation_mismatch")
            if not reservation_id:
                context["fatal"].append("missing_split_reservation_id")
            elif split == "train":
                planned_id = str((aggregate_reservations.get(unit) or {}).get("reservation_id") or "")
                if reservation_id != planned_id:
                    legacy_text = str(row.get("split_reservation_plan") or "").strip()
                    legacy_path = resolve(root, legacy_text) if legacy_text else None
                    legacy_record = None
                    if legacy_path is not None and legacy_path.is_file():
                        if legacy_path not in legacy_reservation_cache:
                            legacy_reservation_cache[legacy_path] = reservation_records(legacy_path)
                        legacy_record = legacy_reservation_cache[legacy_path].get(unit)
                    legacy_valid = bool(
                        legacy_record
                        and str(legacy_record.get("reservation_id") or "") == reservation_id
                        and str(legacy_record.get("split") or "").strip().lower() == split
                    )
                    if not legacy_valid:
                        context["fatal"].append("unverifiable_split_reservation_id")
            image_text = str(row.get("image_path") or "").strip()
            image_path = resolve(root, image_text) if image_text else None
            bbox = parse_bbox(row.get("bbox"))
            if image_path is None or not image_path.is_file():
                context["fatal"].append("missing_evidence_image")
            if bbox is None:
                context["fatal"].append("invalid_evidence_bbox")
            if image_path is not None and image_path.is_file():
                if image_path not in image_size_cache:
                    try:
                        with Image.open(image_path) as image:
                            image_size_cache[image_path] = image.size
                    except OSError:
                        image_size_cache[image_path] = None
                size = image_size_cache[image_path]
                if size is None:
                    context["fatal"].append("unreadable_evidence_image")
                elif bbox is not None and (bbox[0] < 0 or bbox[1] < 0 or bbox[2] > size[0] or bbox[3] > size[1]):
                    context["fatal"].append("bbox_out_of_frame")
                if image_path not in image_hash_cache:
                    image_hash_cache[image_path] = file_sha256(image_path)
            if payload_sha and bbox is not None:
                region = (payload_sha, int(row.get("page_index") or 0), bbox)
                staged_regions[region] += 1
                context["region"] = region
                if region in active_regions:
                    context["fatal"].append("active_gold_region_overlap")
            evidence = {
                "candidate_id": identity,
                "source_payload_sha256": payload_sha,
                "page_image_sha256": image_hash_cache.get(image_path, "") if image_path else "",
                "doc_id": doc_id,
                "version_id": version_id,
                "page_index": int(row.get("page_index") or 0),
                "bbox": list(bbox) if bbox else [],
                "category": category,
                "answer": answer,
                "source": source_kind,
                "reserved_split": split,
                "split_reservation_id": reservation_id,
                "policy_version": POLICY_VERSION,
            }
            if text_contract != "exact_all_fields":
                evidence.update({
                    "source_text_contract": text_contract,
                    "normalized_raw_text": raw,
                    "normalized_target_text": target,
                    "normalized_proposed_text": proposed,
                })
            context["evidence"] = evidence
            context["evidence_sha256"] = evidence_record_sha256(evidence)
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit

    for context in row_context.values():
        region = context.get("region")
        if region is not None and staged_regions[region] > 1:
            context["fatal"].append("duplicate_source_payload_region")

    ocr_engine = None
    np_module = None
    if ocr_mode == "rapidocr":
        try:
            import numpy as np
            from rapidocr import RapidOCR
        except ImportError as exc:
            raise RuntimeError("RapidOCR unavailable; run with requirements-ocr.txt environment") from exc
        ocr_engine = RapidOCR()
        np_module = np
    cache = load_ocr_cache(ocr_cache_path)
    cache_handle = None
    if ocr_cache_output_path is not None:
        if ocr_cache_path is not None and ocr_cache_output_path.resolve() == ocr_cache_path.resolve():
            raise ValueError("OCR cache input and output paths must differ")
        ocr_cache_output_path.parent.mkdir(parents=True, exist_ok=True)
        cache_handle = ocr_cache_output_path.open("x", encoding="utf-8", newline="\n")
    ocr_runs = 0
    ocr_cache_hits = 0
    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = None
    try:
        for index, row in enumerate(staged_rows):
            context = row_context[index]
            if context["fatal"] or context["human"]:
                continue
            evidence_sha = context.get("evidence_sha256") or ""
            result = cache.get(evidence_sha)
            if result:
                ocr_cache_hits += 1
            elif ocr_engine is not None and (max_ocr_rows <= 0 or ocr_runs < max_ocr_rows):
                image_path = resolve(root, str(row.get("image_path") or ""))
                bbox = parse_bbox(row.get("bbox"))
                assert bbox is not None
                with Image.open(image_path) as page:
                    crop = page.convert("RGB").crop(bbox)
                ocr_text, ocr_score = recognize_crop(ocr_engine, np_module, crop, ocr_scale)
                result = {
                    "evidence_sha256": evidence_sha,
                    "candidate_id": identity_for(row),
                    "ocr_engine": "RapidOCR PP-OCRv6 recognition",
                    "ocr_text": ocr_text,
                    "ocr_confidence": round(ocr_score, 6),
                    "ocr_scale": ocr_scale,
                }
                cache[evidence_sha] = result
                if cache_handle is not None:
                    cache_handle.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
                    cache_handle.flush()
                ocr_runs += 1
            if not result:
                context["human"].append("independent_ocr_consensus_not_run")
                continue
            context["ocr"] = result
            expected = context["evidence"]["answer"]
            if clean_text(result.get("ocr_text")) != expected:
                context["human"].append("independent_ocr_text_mismatch")
            if float(result.get("ocr_confidence") or 0.0) < ocr_min_confidence:
                context["human"].append("independent_ocr_confidence_below_threshold")
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit
        if cache_handle is not None:
            cache_handle.close()

    auto_eligible: list[dict[str, Any]] = []
    human_required: list[dict[str, Any]] = []
    reject_or_hold: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()
    task_counts: Counter[str] = Counter()
    for index, row in enumerate(staged_rows):
        context = row_context[index]
        output = dict(row)
        task = task_for(row)
        split = str(context.get("resolved_split") or "")
        task_counts[task] += 1
        split_counts[split or "missing"] += 1
        fatal = sorted(set(context["fatal"]))
        human = sorted(set(context["human"]))
        if fatal:
            disposition = "reject_or_hold"
            reasons = fatal
            reject_or_hold.append(output)
        elif human:
            disposition = "human_required"
            reasons = human
            human_required.append(output)
        else:
            disposition = "auto_eligible_pending_calibration"
            reasons = []
            if context["split_resolution"] == "authoritative_plan_backfill":
                output["reserved_split"] = split
                output["split_reservation_id"] = context["resolved_split_reservation_id"]
                output["split_reservation_plan"] = display(root, split_plan_path)
                output["machine_certification_split_resolution"] = (
                    "authoritative_plan_backfill"
                )
            output["machine_certification_policy_version"] = POLICY_VERSION
            output["machine_certification_tier"] = AUTO_TIER
            output["machine_certification_source_text_contract"] = context["source_text_contract"]
            output["machine_certification_evidence"] = context["evidence"]
            output["machine_certification_evidence_sha256"] = context["evidence_sha256"]
            output["machine_certification_ocr_engine"] = context["ocr"]["ocr_engine"]
            output["machine_certification_ocr_text"] = context["ocr"]["ocr_text"]
            output["machine_certification_ocr_confidence"] = context["ocr"]["ocr_confidence"]
            output["machine_certification_ocr_evidence"] = dict(context["ocr"])
            output["machine_certification_ocr_evidence_sha256"] = evidence_record_sha256(context["ocr"])
            output["machine_certification_release_priority"] = (
                "deferred_pin_balance" if str(row.get("category") or "") == "pin_label" else "balance_closing_nonpin"
            )
            auto_eligible.append(output)
            category_counts[str(row.get("category") or "")] += 1
        output["machine_certification_disposition"] = disposition
        output["machine_certification_reasons"] = reasons
        output["safe_to_merge_gold"] = False
        for reason in reasons:
            reason_counts[f"{disposition}:{reason}"] += 1

    report = {
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "policy_version": POLICY_VERSION,
        "policy_tier": AUTO_TIER,
        "active_gold_modified": False,
        "cohorts": cohort_hashes,
        "split_plan": display(root, split_plan_path),
        "split_plan_sha256": file_sha256(split_plan_path),
        "counts": {
            "input_rows": len(staged_rows),
            "auto_eligible_pending_calibration": len(auto_eligible),
            "auto_eligible_nonpin": sum(row.get("category") != "pin_label" for row in auto_eligible),
            "auto_eligible_deferred_pin": sum(row.get("category") == "pin_label" for row in auto_eligible),
            "human_required": len(human_required),
            "reject_or_hold": len(reject_or_hold),
            "tasks": dict(sorted(task_counts.items())),
            "splits": dict(sorted(split_counts.items())),
            "auto_eligible_categories": dict(sorted(category_counts.items())),
            "source_documents_checked": len(source_cache),
            "evidence_images_hashed": len(image_hash_cache),
            "rapidocr_runs": ocr_runs,
            "rapidocr_cache_hits": ocr_cache_hits,
        },
        "reason_counts": dict(sorted(reason_counts.items())),
        "thresholds": {
            "ocr_mode": ocr_mode,
            "ocr_min_confidence": ocr_min_confidence,
            "ocr_scale": ocr_scale,
            "minimum_calibration_rows": 300,
            "calibration_allowed_errors": 0,
            "calibration_confidence": 0.95,
            "minimum_one_sided_precision_bound": 0.99,
        },
        "interpretation": (
            "Auto-eligible rows are not Gold. They are deterministic train MicroText candidates with verified "
            "provenance, image/bbox integrity, exact source-text agreement, category-shape checks, split locks, "
            "deduplication, and independent OCR consensus. A passing calibration report and the normal strict "
            "promotion preview are still required. Dev/test and all VisualDiff rows remain human-only."
        ),
    }
    return auto_eligible, human_required, reject_or_hold, report


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    counts = report["counts"]
    lines = [
        "# Machine Certification Eligibility Audit",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Policy: `{report['policy_version']}` / `{report['policy_tier']}`",
        f"- Input rows: `{counts['input_rows']}`",
        f"- Auto-eligible pending calibration: `{counts['auto_eligible_pending_calibration']}`",
        f"- Balance-closing non-pin rows: `{counts['auto_eligible_nonpin']}`",
        f"- Deferred pin rows: `{counts['auto_eligible_deferred_pin']}`",
        f"- Human-required rows: `{counts['human_required']}`",
        f"- Reject/hold rows: `{counts['reject_or_hold']}`",
        f"- Active Gold modified: `{str(report['active_gold_modified']).lower()}`",
        "",
        "## Auto-Eligible Categories",
        "",
        "| Category | Rows |",
        "|---|---:|",
    ]
    lines.extend(f"| {key} | {value} |" for key, value in counts["auto_eligible_categories"].items())
    if not counts["auto_eligible_categories"]:
        lines.append("| none | 0 |")
    lines.extend(["", "## Interpretation", "", report["interpretation"], ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--cohort", action="append", type=parse_cohort, required=True)
    parser.add_argument("--split-plan", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ocr-mode", choices=("none", "rapidocr"), default="none")
    parser.add_argument("--ocr-min-confidence", type=float, default=0.98)
    parser.add_argument("--ocr-scale", type=int, default=3)
    parser.add_argument("--ocr-cache", type=Path)
    parser.add_argument(
        "--ocr-cache-output",
        type=Path,
        help="New OCR results; --ocr-cache is always read-only and the paths must differ.",
    )
    parser.add_argument("--max-ocr-rows", type=int, default=0)
    parser.add_argument("--calibration-size", type=int, default=300)
    parser.add_argument("--calibration-seed", default="Eng_Bench-machine-certification-v1")
    args = parser.parse_args(argv)
    if not 0.0 <= args.ocr_min_confidence <= 1.0:
        parser.error("--ocr-min-confidence must be in [0,1]")
    if args.ocr_scale < 1 or args.ocr_scale > 4:
        parser.error("--ocr-scale must be in [1,4]")
    root = args.root.resolve()
    output_dir = resolve(root, args.output_dir)
    ocr_cache = resolve(root, args.ocr_cache) if args.ocr_cache else None
    ocr_cache_output = resolve(root, args.ocr_cache_output) if args.ocr_cache_output else None
    auto, human, held, report = build_audit(
        root,
        args.cohort,
        args.split_plan,
        date_label=args.date_label,
        ocr_mode=args.ocr_mode,
        ocr_min_confidence=args.ocr_min_confidence,
        ocr_scale=args.ocr_scale,
        ocr_cache_path=ocr_cache,
        max_ocr_rows=max(0, args.max_ocr_rows),
        ocr_cache_output_path=ocr_cache_output,
    )
    if ocr_cache is not None:
        report["ocr_cache"] = {
            "path": display(root, ocr_cache),
            "sha256": file_sha256(ocr_cache),
        }
    if ocr_cache_output is not None:
        report["ocr_cache_output"] = {
            "path": display(root, ocr_cache_output),
            "sha256": file_sha256(ocr_cache_output),
        }
    auto_path = output_dir / "auto_eligible_pending_calibration.jsonl"
    auto_nonpin_path = output_dir / "auto_eligible_balance_closing_nonpin.jsonl"
    auto_pin_path = output_dir / "auto_eligible_deferred_pin.jsonl"
    human_path = output_dir / "human_required.jsonl"
    held_path = output_dir / "reject_or_hold.jsonl"
    write_jsonl(auto_path, auto)
    write_jsonl(auto_nonpin_path, [row for row in auto if row.get("category") != "pin_label"])
    write_jsonl(auto_pin_path, [row for row in auto if row.get("category") == "pin_label"])
    write_jsonl(human_path, human)
    write_jsonl(held_path, held)
    sample = stratified_sample(auto, args.calibration_size, args.calibration_seed)
    calibration_dir = output_dir / "calibration"
    checklist_path, index_path = materialize_calibration_pack(root, sample, calibration_dir)
    sample_ids = [identity_for(row) for row in sample]
    report["artifacts"] = {
        "auto_eligible": {"path": display(root, auto_path), "sha256": file_sha256(auto_path)},
        "auto_eligible_balance_closing_nonpin": {
            "path": display(root, auto_nonpin_path), "sha256": file_sha256(auto_nonpin_path)
        },
        "auto_eligible_deferred_pin": {
            "path": display(root, auto_pin_path), "sha256": file_sha256(auto_pin_path)
        },
        "human_required": {"path": display(root, human_path), "sha256": file_sha256(human_path)},
        "reject_or_hold": {"path": display(root, held_path), "sha256": file_sha256(held_path)},
        "calibration_checklist": {"path": display(root, checklist_path), "sha256": file_sha256(checklist_path)},
        "calibration_index": {"path": display(root, index_path), "sha256": file_sha256(index_path)},
    }
    report["calibration_sample"] = {
        "rows": len(sample),
        "candidate_ids": sample_ids,
        "candidate_ids_sha256": hashlib.sha256("\n".join(sample_ids).encode()).hexdigest(),
        "seed": args.calibration_seed,
    }
    report_path = output_dir / "eligibility_report.json"
    markdown_path = output_dir / "eligibility_report.md"
    write_json(report_path, report)
    write_markdown(markdown_path, report)
    print(json.dumps({
        "input_rows": report["counts"]["input_rows"],
        "auto_eligible_pending_calibration": len(auto),
        "auto_eligible_nonpin": report["counts"]["auto_eligible_nonpin"],
        "human_required": len(human),
        "reject_or_hold": len(held),
        "calibration_rows": len(sample),
        "active_gold_modified": False,
        "report": display(root, report_path),
    }, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
