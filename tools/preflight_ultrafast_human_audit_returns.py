#!/usr/bin/env python3
"""Preflight the ultrafast 498+120 human return without modifying gold data."""
from __future__ import annotations

import argparse
import csv
import hashlib
from io import BytesIO
import json
import shutil
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from PIL import Image

try:
    from . import build_ultrafast_human_audit_delivery as workbook_contract
    from . import normalize_easiest_human_audit_returns as normalizer
    from . import prepare_ultrafast_human_audit_payload as payload_io
except ImportError:  # Direct script execution from tools/.
    import build_ultrafast_human_audit_delivery as workbook_contract
    import normalize_easiest_human_audit_returns as normalizer
    import prepare_ultrafast_human_audit_payload as payload_io


PRIMARY_WORKBOOK = "PRIMARY_REVIEW_498.xlsx"
AUDITOR_WORKBOOKS = tuple(
    f"AUDITOR_{number:02d}_REVIEW_12.xlsx" for number in range(1, 11)
)
EXPECTED_WORKBOOKS = {PRIMARY_WORKBOOK, *AUDITOR_WORKBOOKS}
ACTIVE_RELEASE_FILES = (
    "eng_bench.jsonl",
    "manifest.jsonl",
    "microtext/annotations/microtext_items.jsonl",
    "microtext/annotations/microtext_questions.jsonl",
    "visualdiff/annotations/visualdiff_pairs.jsonl",
    "visualdiff/annotations/visualdiff_questions.jsonl",
)
AGREEMENT_THRESHOLDS = {"microtext": 0.95, "visualdiff": 0.90}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            rows.append(value)
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            output = dict(row)
            for key, value in output.items():
                if isinstance(value, (list, dict)):
                    output[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
            writer.writerow(output)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def release_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in ACTIVE_RELEASE_FILES:
        path = root / relative
        hashes[relative] = sha256(path) if path.is_file() else "missing"
    return hashes


def image_pixel_hash(data: bytes) -> str:
    with Image.open(BytesIO(data)) as source:
        image = source.convert("RGBA")
        digest = hashlib.sha256()
        digest.update(f"{image.width}x{image.height}|RGBA|".encode("ascii"))
        digest.update(image.tobytes())
    return digest.hexdigest()


def workbook_anchor_pixel_hashes(path: Path) -> list[tuple[str, int, int, str]]:
    """Resolve every picture anchor to pixels, tolerating Excel media dedup."""
    anchors: list[tuple[str, int, int, str]] = []
    with zipfile.ZipFile(path, "r") as archive:
        sheet_paths = payload_io.workbook_io.workbook_sheet_paths(archive)
        for sheet_name, sheet_path in sheet_paths.items():
            sheet_relations = payload_io.relation_targets(archive, sheet_path)
            drawing_paths = sorted(
                target
                for target in sheet_relations.values()
                if "/drawings/" in target and target.endswith(".xml")
            )
            for drawing_path in drawing_paths:
                drawing_relations = payload_io.relation_targets(
                    archive, drawing_path
                )
                drawing = ET.fromstring(archive.read(drawing_path))
                for anchor in list(drawing):
                    picture = anchor.find(f"{{{payload_io.DRAWING_NS}}}pic")
                    start = anchor.find(f"{{{payload_io.DRAWING_NS}}}from")
                    if picture is None or start is None:
                        continue
                    row_node = start.find(f"{{{payload_io.DRAWING_NS}}}row")
                    col_node = start.find(f"{{{payload_io.DRAWING_NS}}}col")
                    blip = picture.find(
                        f".//{{{payload_io.DRAWINGML_NS}}}blip"
                    )
                    if row_node is None or col_node is None or blip is None:
                        raise ValueError(
                            f"{path}: incomplete picture anchor in {sheet_name}"
                        )
                    relation_id = blip.get(
                        f"{{{payload_io.OFFICE_REL_NS}}}embed", ""
                    )
                    media_path = drawing_relations.get(relation_id, "")
                    if not media_path or media_path not in archive.namelist():
                        raise ValueError(
                            f"{path}: missing picture media for {sheet_name}"
                        )
                    anchors.append(
                        (
                            sheet_name,
                            int(row_node.text or "0"),
                            int(col_node.text or "0"),
                            image_pixel_hash(archive.read(media_path)),
                        )
                    )
    return sorted(anchors)


def validate_return_workbook_integrity(
    returned: Path, canonical: Path, role: str
) -> list[str]:
    issues: list[str] = []
    try:
        with zipfile.ZipFile(returned, "r") as archive:
            corrupt = archive.testzip()
            if corrupt:
                issues.append(f"XLSX CRC failure at {corrupt}")
            names = {name.lower() for name in archive.namelist()}
            if any("vbaproject.bin" in name for name in names):
                issues.append("macro payload is not allowed")
            if any(name.startswith("xl/externallinks/") for name in names):
                issues.append("external workbook links are not allowed")
        if workbook_contract.sheet_states(returned) != workbook_contract.sheet_states(
            canonical
        ):
            issues.append("sheet names or visibility states changed")
        if workbook_anchor_pixel_hashes(returned) != workbook_anchor_pixel_hashes(
            canonical
        ):
            issues.append("embedded evidence image hashes changed")
        expected = 498 if role == "primary" else 12
        returned_states = workbook_contract.sheet_states(returned)
        single_sheet_primary = (
            role == "primary"
            and workbook_contract.SINGLE_PRIMARY_SHEET in returned_states
        )
        anchors, anchor_issues = workbook_contract.validate_picture_layout(
            returned, role, single_sheet_primary
        )
        if anchors != expected:
            issues.append(f"expected {expected} evidence anchors; found {anchors}")
        issues.extend(anchor_issues)
    except (KeyError, OSError, ValueError, zipfile.BadZipFile) as exc:
        issues.append(f"workbook integrity check failed: {exc}")
    return issues


def record_id(row: dict[str, Any]) -> str:
    task = str(row.get("task_type") or row.get("task") or "").strip()
    field = "candidate_id" if task == "microtext" else "pair_id"
    return str(row.get(field) or "").strip()


def record_key(row: dict[str, Any]) -> tuple[str, str]:
    task = str(row.get("task_type") or row.get("task") or "").strip()
    return task, record_id(row)


def primary_audit_answer(row: dict[str, Any]) -> str:
    task = str(row.get("task_type") or "")
    decision = str(row.get("decision") or "")
    if task == "microtext":
        return {
            "accepted": "yes",
            "edited": "no",
            "rejected": "no",
            "needs_full_page": "unclear",
        }.get(decision, "invalid")
    return {
        "edit": "yes",
        "reject_unclear": "no",
        "needs_full_page": "unclear",
    }.get(decision, "invalid")


def auditor_audit_answer(row: dict[str, Any]) -> str:
    task = str(row.get("task_type") or "")
    decision = str(row.get("decision") or "")
    if task == "microtext":
        return {
            "pass": "yes",
            "issue": "no",
            "needs_full_page": "unclear",
        }.get(decision, "invalid")
    return {
        "edit": "yes",
        "reject_unclear": "no",
        "needs_full_page": "unclear",
    }.get(decision, "invalid")


def unique_index(
    rows: Iterable[dict[str, Any]], label: str
) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = record_key(row)
        if not key[0] or not key[1]:
            raise ValueError(f"{label}: blank task or record ID")
        if key in result:
            raise ValueError(f"{label}: duplicate record {key[0]}:{key[1]}")
        result[key] = row
    return result


def build_agreement_pairs(
    primary_rows: list[dict[str, Any]], auditor_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    primary = unique_index(primary_rows, "primary decisions")
    auditors = unique_index(auditor_rows, "auditor decisions")
    pairs: list[dict[str, Any]] = []
    for key, audit in auditors.items():
        source = primary.get(key)
        if source is None:
            raise ValueError(f"auditor row is outside primary assignment: {key[0]}:{key[1]}")
        left = primary_audit_answer(source)
        right = auditor_audit_answer(audit)
        if "invalid" in {left, right}:
            raise ValueError(f"invalid normalized decision for {key[0]}:{key[1]}")
        reasons: list[str] = []
        if left != right:
            reasons.append("decision_disagreement")
        if left == "unclear":
            reasons.append("primary_unclear")
        if right == "unclear":
            reasons.append("auditor_unclear")
        pairs.append(
            {
                "task": key[0],
                "record_id": key[1],
                "primary_index": int(source["primary_index"]),
                "primary_decision": source["decision"],
                "primary_answer": left,
                "auditor_id": audit["reviewer_id"],
                "auditor_workbook": audit["source_workbook"],
                "auditor_decision": audit["decision"],
                "auditor_answer": right,
                "agrees": left == right,
                "needs_adjudication": bool(reasons),
                "adjudication_reasons": reasons,
            }
        )
    return sorted(pairs, key=lambda row: int(row["primary_index"]))


def agreement_metrics(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for task in ("microtext", "visualdiff"):
        selected = [row for row in pairs if row["task"] == task]
        decisive = [
            row
            for row in selected
            if "unclear" not in {row["primary_answer"], row["auditor_answer"]}
        ]
        exact = sum(bool(row["agrees"]) for row in selected)
        decisive_exact = sum(bool(row["agrees"]) for row in decisive)
        threshold = AGREEMENT_THRESHOLDS[task]
        rate = exact / len(selected) if selected else 0.0
        result[task] = {
            "rows": len(selected),
            "exact_agreements": exact,
            "exact_agreement": rate,
            "decisive_rows": len(decisive),
            "decisive_agreement": decisive_exact / len(decisive) if decisive else 0.0,
            "unclear_rows": len(selected) - len(decisive),
            "threshold": threshold,
            "passes_threshold": bool(selected) and rate >= threshold,
        }
    total_exact = sum(bool(row["agrees"]) for row in pairs)
    result["overall"] = {
        "rows": len(pairs),
        "exact_agreements": total_exact,
        "exact_agreement": total_exact / len(pairs) if pairs else 0.0,
        "adjudication_rows": sum(bool(row["needs_adjudication"]) for row in pairs),
    }
    result["all_task_thresholds_pass"] = all(
        result[task]["passes_threshold"] for task in AGREEMENT_THRESHOLDS
    )
    return result


def expected_assignment_sets(
    manifest_rows: list[dict[str, Any]], assignment_rows: list[dict[str, Any]]
) -> tuple[set[tuple[str, str, int]], set[tuple[str, str, str, int]]]:
    primary = {
        (str(row.get("task") or ""), record_id(row), int(row["primary_index"]))
        for row in manifest_rows
    }
    auditors = {
        (
            str(row.get("reviewer_workbook") or ""),
            str(row.get("task") or ""),
            record_id(row),
            int(row["primary_index"]),
        )
        for row in assignment_rows
    }
    return primary, auditors


def returned_assignment_sets(
    primary_rows: list[dict[str, Any]], auditor_rows: list[dict[str, Any]]
) -> tuple[set[tuple[str, str, int]], set[tuple[str, str, str, int]]]:
    primary = {
        (row["task_type"], record_id(row), int(row["primary_index"]))
        for row in primary_rows
    }
    auditors = {
        (
            row["source_workbook"],
            row["task_type"],
            record_id(row),
            int(row["primary_index"]),
        )
        for row in auditor_rows
    }
    return primary, auditors


def positive_primary(row: dict[str, Any]) -> bool:
    if row["task_type"] == "microtext":
        return row["decision"] in {"accepted", "edited"}
    return row["decision"] == "edit"


def stage_primary_rows(
    manifest_rows: list[dict[str, Any]],
    primary_rows: list[dict[str, Any]],
    agreement_pairs: list[dict[str, Any]],
    allowed_categories: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    manifest = unique_index(manifest_rows, "assignment manifest")
    pair_by_key = {(row["task"], row["record_id"]): row for row in agreement_pairs}
    all_rows: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    for decision in sorted(primary_rows, key=lambda row: int(row["primary_index"])):
        key = record_key(decision)
        source = manifest.get(key)
        if source is None:
            raise ValueError(f"primary decision missing from manifest: {key[0]}:{key[1]}")
        output = dict(source)
        output.update(
            {
                "record_id": key[1],
                "primary_index": int(decision["primary_index"]),
                "review_status": decision["decision"],
                "human_review_status": decision["decision"],
                "primary_reviewer_id": decision["reviewer_id"],
                "human_completion_source_path": decision["source_workbook"],
                "packet_review_status": "human_return_preflighted",
                "safe_to_merge_gold": False,
            }
        )
        reasons: list[str] = []
        agreement = pair_by_key.get(key)
        if agreement is not None:
            output.update(
                {
                    "independent_qc_reviewed": True,
                    "independent_qc_reviewer_id": agreement["auditor_id"],
                    "independent_qc_decision": agreement["auditor_decision"],
                    "independent_qc_agrees": agreement["agrees"],
                }
            )
            reasons.extend(agreement["adjudication_reasons"])
        else:
            output["independent_qc_reviewed"] = False

        if decision["task_type"] == "microtext":
            corrected_text = str(decision.get("corrected_text") or "").strip()
            corrected_category = str(decision.get("corrected_category") or "").strip()
            output["corrected_text"] = corrected_text
            output["corrected_category"] = corrected_category
            if corrected_category:
                output["machine_category"] = output.get("category")
                output["category"] = corrected_category
                if allowed_categories is not None and corrected_category not in allowed_categories:
                    reasons.append("invalid_corrected_category")
        else:
            description = str(decision.get("change_description") or "").strip()
            output["description"] = description
            output["human_description"] = description
            output["change_desc_gt"] = description

        if reasons:
            output["promotion_state"] = "human_conflict_needs_adjudication"
        elif decision["decision"] == "needs_full_page":
            output["promotion_state"] = "human_needs_full_page"
        elif positive_primary(decision):
            output["promotion_state"] = "human_positive_pending_release_gates"
        else:
            output["promotion_state"] = "human_rejected"
        output["preflight_hold_reasons"] = sorted(set(reasons))
        all_rows.append(output)
        if output["promotion_state"] == "human_positive_pending_release_gates":
            eligible.append(output)
        else:
            held.append(output)
    return all_rows, eligible, held


def allowed_microtext_categories(root: Path, manifest: list[dict[str, Any]]) -> set[str]:
    values = {
        str(row.get("category") or "").strip()
        for row in manifest
        if row.get("task") == "microtext"
    }
    active = root / "microtext" / "annotations" / "microtext_items.jsonl"
    if active.exists():
        values.update(
            str(row.get("category") or "").strip() for row in read_jsonl(active)
        )
    return {value for value in values if value and value != "unknown"}


def ensure_output_dir(output_dir: Path, root: Path, overwrite: bool) -> None:
    allowed = (root / "derived" / "human_adjudication" / "processed_returns").resolve()
    target = output_dir.resolve()
    if not target.is_relative_to(allowed) or target == allowed:
        raise ValueError(f"output directory must be a child of {allowed}")
    if target.exists():
        if not overwrite:
            raise FileExistsError(f"output directory exists: {target}")
        shutil.rmtree(target)
    target.mkdir(parents=True)


def render_summary(report: dict[str, Any]) -> str:
    metrics = report.get("agreement", {})
    lines = [
        "# Eng_Bench Ultrafast Return Preflight",
        "",
        "- Goal: **Gold v2.0 Global**",
        f"- Structurally valid: **{str(report['valid']).lower()}**",
        f"- Complete workbooks: `{report['workbook_count']}/11`",
        f"- Normalized decisions: `{report['normalized_rows']}/618`",
        f"- Primary decisions: `{report['primary_rows']}/498`",
        f"- Independent QC decisions: `{report['auditor_rows']}/120`",
        f"- Eligible merge-preview rows: `{report['eligible_preview_rows']}`",
        f"- Held or adjudication rows: `{report['held_rows']}`",
        f"- Gold rows modified: `{report['gold_rows_modified']}`",
        "",
        "## Assignment QC Agreement",
        "",
        "This 120-row measurement is assignment QC; it does not replace the separate 185-row release-agreement sample.",
        "",
        "| Task | Rows | Exact agreement | Threshold | Pass |",
        "|---|---:|---:|---:|---|",
    ]
    for task in ("microtext", "visualdiff"):
        values = metrics.get(task, {})
        lines.append(
            f"| {task} | {values.get('rows', 0)} | "
            f"{values.get('exact_agreement', 0.0):.4f} | "
            f"{values.get('threshold', AGREEMENT_THRESHOLDS[task]):.2f} | "
            f"{str(values.get('passes_threshold', False)).lower()} |"
        )
    if report.get("issues"):
        lines.extend(["", "## Structural Issues", ""])
        lines.extend(f"- {issue}" for issue in report["issues"])
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "No active release file is written by this tool. Positive rows remain staged and require provenance, duplicate, split, leakage, merge-preview, and strict validation gates.",
            "",
        ]
    )
    return "\n".join(lines)


def preflight(
    root: Path,
    returns_dir: Path,
    canonical_dir: Path,
    manifest_path: Path,
    assignments_path: Path,
    output_dir: Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    root = root.resolve()
    returns_dir = returns_dir.resolve()
    canonical_dir = canonical_dir.resolve()
    output_dir = output_dir.resolve()
    ensure_output_dir(output_dir, root, overwrite)
    before = release_hashes(root)
    issues: list[str] = []

    actual = {path.name for path in returns_dir.glob("*.xlsx") if path.is_file()}
    missing = sorted(EXPECTED_WORKBOOKS - actual)
    extra = sorted(actual - EXPECTED_WORKBOOKS)
    if missing:
        issues.append(f"missing returned workbooks: {missing}")
    if extra:
        issues.append(f"unexpected returned workbooks: {extra}")
    canonical_actual = {
        path.name for path in canonical_dir.glob("*.xlsx") if path.is_file()
    }
    if canonical_actual != EXPECTED_WORKBOOKS:
        issues.append("canonical workbook set is incomplete or contains extras")

    normalized_rows: list[dict[str, Any]] = []
    workbook_rows: dict[str, int] = {}
    if not issues:
        for name in sorted(EXPECTED_WORKBOOKS):
            role = "primary" if name == PRIMARY_WORKBOOK else "auditor"
            integrity_issues = validate_return_workbook_integrity(
                returns_dir / name, canonical_dir / name, role
            )
            if integrity_issues:
                issues.extend(f"{name}: {issue}" for issue in integrity_issues)
                continue
            try:
                rows = normalizer.normalize_workbook(
                    returns_dir / name, canonical_dir
                )
            except (KeyError, OSError, TypeError, ValueError) as exc:
                issues.append(f"{name}: {exc}")
                continue
            normalized_rows.extend(rows)
            workbook_rows[name] = len(rows)

    primary_rows = [
        row for row in normalized_rows if row.get("reviewer_role") == "primary"
    ]
    auditor_rows = [
        row for row in normalized_rows if row.get("reviewer_role") == "auditor"
    ]
    manifest_rows = read_jsonl(manifest_path)
    assignment_rows = read_jsonl(assignments_path)
    if not issues:
        try:
            expected_primary, expected_auditors = expected_assignment_sets(
                manifest_rows, assignment_rows
            )
            returned_primary, returned_auditors = returned_assignment_sets(
                primary_rows, auditor_rows
            )
            if expected_primary != returned_primary:
                issues.append("primary return identities/order do not match active manifest")
            if expected_auditors != returned_auditors:
                issues.append("auditor return assignments do not match active sidecar")
            if len(primary_rows) != 498:
                issues.append(f"expected 498 primary decisions; found {len(primary_rows)}")
            if len(auditor_rows) != 120:
                issues.append(f"expected 120 auditor decisions; found {len(auditor_rows)}")
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(str(exc))

    pairs: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}
    all_staged: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    adjudication_staged: list[dict[str, Any]] = []
    if not issues:
        try:
            pairs = build_agreement_pairs(primary_rows, auditor_rows)
            metrics = agreement_metrics(pairs)
            all_staged, eligible, held = stage_primary_rows(
                manifest_rows,
                primary_rows,
                pairs,
                allowed_microtext_categories(root, manifest_rows),
            )
            adjudication_staged = [
                row for row in all_staged if row.get("preflight_hold_reasons")
            ]
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(str(exc))

    if not issues:
        write_jsonl(output_dir / "normalized_decisions.jsonl", normalized_rows)
        write_jsonl(output_dir / "all_primary_staged.jsonl", all_staged)
        write_jsonl(output_dir / "eligible_merge_preview.jsonl", eligible)
        write_jsonl(output_dir / "held_or_adjudication.jsonl", held)
        write_csv(
            output_dir / "agreement_pairs.csv",
            pairs,
            [
                "task",
                "record_id",
                "primary_index",
                "primary_decision",
                "primary_answer",
                "auditor_id",
                "auditor_workbook",
                "auditor_decision",
                "auditor_answer",
                "agrees",
                "needs_adjudication",
                "adjudication_reasons",
            ],
        )
        write_csv(
            output_dir / "adjudication_queue.csv",
            adjudication_staged,
            [
                "task",
                "record_id",
                "primary_index",
                "review_status",
                "promotion_state",
                "independent_qc_reviewer_id",
                "independent_qc_decision",
                "independent_qc_agrees",
                "preflight_hold_reasons",
            ],
        )

    after = release_hashes(root)
    if before != after:
        issues.append("active release file hashes changed during preflight")
    decision_counts = Counter(
        f"{row.get('task_type')}:{row.get('decision')}" for row in primary_rows
    )
    report = {
        "goal": "Gold v2.0 Global",
        "mode": "strict_preflight_no_gold_merge",
        "processed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "returns_dir": returns_dir.as_posix(),
        "canonical_dir": canonical_dir.as_posix(),
        "workbooks": workbook_rows,
        "workbook_count": len(workbook_rows),
        "normalized_rows": len(normalized_rows),
        "primary_rows": len(primary_rows),
        "auditor_rows": len(auditor_rows),
        "unique_auditor_rows": len({record_key(row) for row in auditor_rows}),
        "agreement_scope": "assignment_qc_not_release_agreement_gate",
        "agreement": metrics,
        "decision_counts": dict(sorted(decision_counts.items())),
        "eligible_preview_rows": len(eligible),
        "held_rows": len(held),
        "adjudication_rows": len(adjudication_staged),
        "active_release_hashes_before": before,
        "active_release_hashes_after": after,
        "active_gold_changed": before != after,
        "gold_rows_modified": 0,
        "issues": issues,
        "valid": not issues,
    }
    write_json(output_dir / "preflight_report.json", report)
    (output_dir / "README.md").write_text(render_summary(report), encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--returns-dir", type=Path, required=True)
    parser.add_argument(
        "--canonical-dir",
        type=Path,
        default=Path(
            "derived/human_adjudication/"
            "Eng_Bench_SIMPLE_NUMERIC_Human_Audit_Primary498_10x12_2026-08-09"
        ),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(
            "derived/quality/active_human_assignments/"
            "ultrafast_2026-08-09/manifest.jsonl"
        ),
    )
    parser.add_argument(
        "--auditor-assignments",
        type=Path,
        default=Path(
            "derived/quality/active_human_assignments/"
            "ultrafast_2026-08-09/auditor_assignments.jsonl"
        ),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()

    def rooted(path: Path) -> Path:
        return path if path.is_absolute() else root / path

    report = preflight(
        root=root,
        returns_dir=rooted(args.returns_dir),
        canonical_dir=rooted(args.canonical_dir),
        manifest_path=rooted(args.manifest),
        assignments_path=rooted(args.auditor_assignments),
        output_dir=rooted(args.output_dir),
        overwrite=args.overwrite,
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 1 if args.strict and not report["valid"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
