#!/usr/bin/env python3
"""Compute inter-reviewer agreement for an Eng_Bench audit sample."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import benchmark_runner


YES_NO = {"yes", "no"}
ACCEPT_REJECT = {"accept", "reject"}
REVIEW_RESPONSE_FIELDS = {
    "answer_correct",
    "corrected_answer",
    "bbox_correct",
    "corrected_evidence_json",
    "accept_reject",
    "ambiguity",
    "rights_concern",
    "notes",
}
ADJUDICATION_FIELDS = [
    "id",
    "task",
    "stratum",
    "category",
    "doc_id",
    "question",
    "reference_answer",
    "a_answer",
    "b_answer",
    "bbox_iou",
    "a_accept_reject",
    "b_accept_reject",
    "a_ambiguity",
    "b_ambiguity",
    "a_rights_concern",
    "b_rights_concern",
    "reasons",
    "recommended_action",
    "primary_evidence_path",
    "page_path",
    "old_page_path",
    "new_page_path",
    "a_notes",
    "b_notes",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def normalized(value: Any) -> str:
    return str(value or "").strip().lower()


def row_ids(rows: list[dict[str, Any]]) -> list[str]:
    return [str(row.get("id") or "").strip() for row in rows]


def duplicate_ids(values: list[str]) -> list[str]:
    counts = Counter(value for value in values if value)
    return sorted(value for value, count in counts.items() if count > 1)


def validate_input_contract(
    reference_rows: list[dict[str, Any]],
    reviewer_a_rows: list[dict[str, str]],
    reviewer_b_rows: list[dict[str, str]],
) -> list[str]:
    issues: list[str] = []
    if not reference_rows:
        return ["reference:empty"]

    reference_ids = row_ids(reference_rows)
    if any(not identifier for identifier in reference_ids):
        issues.append("reference:missing_id")
    reference_duplicates = duplicate_ids(reference_ids)
    if reference_duplicates:
        issues.append("reference:duplicate_ids:" + ";".join(reference_duplicates[:20]))

    immutable_fields = [
        field for field in reference_rows[0].keys() if field not in REVIEW_RESPONSE_FIELDS
    ]
    required_fields = set(immutable_fields) | REVIEW_RESPONSE_FIELDS
    reference_by_id = {
        identifier: row
        for identifier, row in zip(reference_ids, reference_rows)
        if identifier and identifier not in reference_duplicates
    }

    for label, review_rows in (
        ("reviewer_a", reviewer_a_rows),
        ("reviewer_b", reviewer_b_rows),
    ):
        review_ids = row_ids(review_rows)
        if len(review_rows) != len(reference_rows):
            issues.append(
                f"{label}:row_count_mismatch:{len(review_rows)}:{len(reference_rows)}"
            )
        if any(not identifier for identifier in review_ids):
            issues.append(f"{label}:missing_id")
        review_duplicates = duplicate_ids(review_ids)
        if review_duplicates:
            issues.append(
                f"{label}:duplicate_ids:" + ";".join(review_duplicates[:20])
            )

        missing_ids = sorted(set(reference_ids) - set(review_ids) - {""})
        unexpected_ids = sorted(set(review_ids) - set(reference_ids) - {""})
        if missing_ids:
            issues.append(f"{label}:missing_ids:" + ";".join(missing_ids[:20]))
        if unexpected_ids:
            issues.append(
                f"{label}:unexpected_ids:" + ";".join(unexpected_ids[:20])
            )
        if review_ids != reference_ids:
            issues.append(f"{label}:id_order_mismatch")

        review_fields = (
            set().union(*(row.keys() for row in review_rows)) if review_rows else set()
        )
        missing_fields = sorted(required_fields - review_fields)
        if missing_fields:
            issues.append(
                f"{label}:missing_fields:" + ";".join(missing_fields[:20])
            )

        review_by_id = {
            identifier: row
            for identifier, row in zip(review_ids, review_rows)
            if identifier and identifier not in review_duplicates
        }
        for identifier in sorted(set(reference_by_id) & set(review_by_id)):
            reference = reference_by_id[identifier]
            review = review_by_id[identifier]
            mismatches = [
                field
                for field in immutable_fields
                if str(review.get(field) or "") != str(reference.get(field) or "")
            ]
            if mismatches:
                issues.append(
                    f"{label}:{identifier}:immutable_field_mismatch:"
                    + ";".join(mismatches[:20])
                )
    return issues


def parse_evidence(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [entry for entry in value if isinstance(entry, dict)]
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    return [entry for entry in parsed if isinstance(entry, dict)] if isinstance(parsed, list) else []


def valid_evidence(entries: list[dict[str, Any]]) -> bool:
    return bool(entries) and all(benchmark_runner.bbox_from_entry(entry) is not None for entry in entries)


def completed_review(reference: dict[str, Any], review: dict[str, str]) -> bool:
    answer_correct = normalized(review.get("answer_correct"))
    bbox_correct = normalized(review.get("bbox_correct"))
    if answer_correct not in YES_NO or bbox_correct not in YES_NO:
        return False
    if normalized(review.get("accept_reject")) not in ACCEPT_REJECT:
        return False
    if normalized(review.get("ambiguity")) not in YES_NO:
        return False
    if normalized(review.get("rights_concern")) not in YES_NO:
        return False
    if answer_correct == "no" and not str(review.get("corrected_answer") or "").strip():
        return False
    if bbox_correct == "no" and not valid_evidence(parse_evidence(review.get("corrected_evidence_json"))):
        return False
    if bbox_correct == "yes" and not valid_evidence(parse_evidence(reference.get("reference_evidence_json"))):
        return False
    return True


def effective_answer(reference: dict[str, Any], review: dict[str, str]) -> str:
    if normalized(review.get("answer_correct")) == "yes":
        return str(reference.get("reference_answer") or "").strip()
    return str(review.get("corrected_answer") or "").strip()


def effective_evidence(reference: dict[str, Any], review: dict[str, str]) -> list[dict[str, Any]]:
    if normalized(review.get("bbox_correct")) == "yes":
        return parse_evidence(reference.get("reference_evidence_json"))
    return parse_evidence(review.get("corrected_evidence_json"))


def row_evidence_iou(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> float:
    image_indices = sorted(
        {
            benchmark_runner.image_index_from_entry(entry)
            for entry in left + right
            if benchmark_runner.image_index_from_entry(entry) is not None
        }
    )
    if not image_indices:
        return 0.0
    scores: list[float] = []
    for image_index in image_indices:
        left_side = [
            entry for entry in left if benchmark_runner.image_index_from_entry(entry) == image_index
        ]
        right_side = [
            entry for entry in right if benchmark_runner.image_index_from_entry(entry) == image_index
        ]
        if not left_side or not right_side:
            scores.append(0.0)
            continue
        scores.append(
            max(
                benchmark_runner.compatible_evidence_iou(left_entry, right_entry)
                for left_entry in left_side
                for right_entry in right_side
            )
        )
    return sum(scores) / len(scores)


def cohen_kappa(left: list[str], right: list[str]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    observed = sum(a == b for a, b in zip(left, right)) / len(left)
    left_counts = Counter(left)
    right_counts = Counter(right)
    expected = sum(
        (left_counts[label] / len(left)) * (right_counts[label] / len(right))
        for label in set(left_counts) | set(right_counts)
    )
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1.0 - expected)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "rows": 0,
            "exact_answer_agreement": 0.0,
            "normalized_answer_agreement": 0.0,
            "bbox_iou_mean": 0.0,
            "bbox_iou_agreement_0_5": 0.0,
            "accept_reject_agreement": 0.0,
            "accept_reject_kappa": 0.0,
            "ambiguity_agreement": 0.0,
            "ambiguity_kappa": 0.0,
            "rights_concern_agreement": 0.0,
            "rights_concern_kappa": 0.0,
        }
    count = len(rows)
    categorical = {
        field: ([row[f"a_{field}"] for row in rows], [row[f"b_{field}"] for row in rows])
        for field in ("accept_reject", "ambiguity", "rights_concern")
    }
    return {
        "rows": count,
        "exact_answer_agreement": sum(row["a_answer"] == row["b_answer"] for row in rows) / count,
        "normalized_answer_agreement": sum(
            benchmark_runner.normalize_compact(row["a_answer"])
            == benchmark_runner.normalize_compact(row["b_answer"])
            for row in rows
        )
        / count,
        "bbox_iou_mean": sum(float(row["bbox_iou"]) for row in rows) / count,
        "bbox_iou_agreement_0_5": sum(float(row["bbox_iou"]) >= 0.5 for row in rows) / count,
        "accept_reject_agreement": sum(row["a_accept_reject"] == row["b_accept_reject"] for row in rows)
        / count,
        "accept_reject_kappa": cohen_kappa(*categorical["accept_reject"]),
        "ambiguity_agreement": sum(row["a_ambiguity"] == row["b_ambiguity"] for row in rows) / count,
        "ambiguity_kappa": cohen_kappa(*categorical["ambiguity"]),
        "rights_concern_agreement": sum(
            row["a_rights_concern"] == row["b_rights_concern"] for row in rows
        )
        / count,
        "rights_concern_kappa": cohen_kappa(*categorical["rights_concern"]),
    }


def adjudication_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if benchmark_runner.normalize_compact(row["a_answer"]) != benchmark_runner.normalize_compact(
        row["b_answer"]
    ):
        reasons.append("answer_disagreement")
    if float(row["bbox_iou"]) < 0.5:
        reasons.append("evidence_disagreement")
    if row["a_accept_reject"] != row["b_accept_reject"]:
        reasons.append("accept_reject_disagreement")
    if row["a_ambiguity"] != row["b_ambiguity"]:
        reasons.append("ambiguity_disagreement")
    if row["a_rights_concern"] != row["b_rights_concern"]:
        reasons.append("rights_concern_disagreement")
    if "reject" in {row["a_accept_reject"], row["b_accept_reject"]}:
        reasons.append("reviewer_reject")
    if "yes" in {row["a_ambiguity"], row["b_ambiguity"]}:
        reasons.append("reviewer_ambiguity")
    if "yes" in {row["a_rights_concern"], row["b_rights_concern"]}:
        reasons.append("reviewer_rights_concern")
    return reasons


def recommended_action(reasons: list[str]) -> str:
    if "reviewer_rights_concern" in reasons or "rights_concern_disagreement" in reasons:
        return "rights_review"
    return "adjudicate_row"


def build_adjudication_queue(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for row in rows:
        reasons = adjudication_reasons(row)
        if not reasons:
            continue
        queue.append(
            {
                "id": row["id"],
                "task": row["task"],
                "stratum": row["stratum"],
                "category": row.get("category", ""),
                "doc_id": row.get("doc_id", ""),
                "question": row.get("question", ""),
                "reference_answer": row.get("reference_answer", ""),
                "a_answer": row["a_answer"],
                "b_answer": row["b_answer"],
                "bbox_iou": f"{float(row['bbox_iou']):.4f}",
                "a_accept_reject": row["a_accept_reject"],
                "b_accept_reject": row["b_accept_reject"],
                "a_ambiguity": row["a_ambiguity"],
                "b_ambiguity": row["b_ambiguity"],
                "a_rights_concern": row["a_rights_concern"],
                "b_rights_concern": row["b_rights_concern"],
                "reasons": ";".join(reasons),
                "recommended_action": recommended_action(reasons),
                "primary_evidence_path": row.get("primary_evidence_path", ""),
                "page_path": row.get("page_path", ""),
                "old_page_path": row.get("old_page_path", ""),
                "new_page_path": row.get("new_page_path", ""),
                "a_notes": row.get("a_notes", ""),
                "b_notes": row.get("b_notes", ""),
            }
        )
    return queue


def compute_report(
    reference_rows: list[dict[str, Any]],
    reviewer_a_rows: list[dict[str, str]],
    reviewer_b_rows: list[dict[str, str]],
) -> dict[str, Any]:
    input_contract_issues = validate_input_contract(
        reference_rows, reviewer_a_rows, reviewer_b_rows
    )
    input_contract_valid = not input_contract_issues
    reference = {str(row.get("id") or ""): row for row in reference_rows if row.get("id")}
    reviewer_a = {str(row.get("id") or ""): row for row in reviewer_a_rows if row.get("id")}
    reviewer_b = {str(row.get("id") or ""): row for row in reviewer_b_rows if row.get("id")}
    paired_ids = (
        sorted(set(reference) & set(reviewer_a) & set(reviewer_b))
        if input_contract_valid
        else []
    )
    completed: list[dict[str, Any]] = []
    incomplete_ids: list[str] = sorted(set(reference) - set(paired_ids))
    for identifier in paired_ids:
        ref = reference[identifier]
        left = reviewer_a[identifier]
        right = reviewer_b[identifier]
        if not completed_review(ref, left) or not completed_review(ref, right):
            incomplete_ids.append(identifier)
            continue
        completed.append(
            {
                "id": identifier,
                "task": str(ref.get("task") or "unknown"),
                "stratum": str(ref.get("stratum") or "unknown"),
                "category": str(ref.get("category") or ""),
                "doc_id": str(ref.get("doc_id") or ""),
                "question": str(ref.get("question") or ""),
                "reference_answer": str(ref.get("reference_answer") or ""),
                "primary_evidence_path": str(ref.get("primary_evidence_path") or ""),
                "page_path": str(ref.get("page_path") or ""),
                "old_page_path": str(ref.get("old_page_path") or ""),
                "new_page_path": str(ref.get("new_page_path") or ""),
                "a_answer": effective_answer(ref, left),
                "b_answer": effective_answer(ref, right),
                "bbox_iou": row_evidence_iou(
                    effective_evidence(ref, left),
                    effective_evidence(ref, right),
                ),
                "a_accept_reject": normalized(left.get("accept_reject")),
                "b_accept_reject": normalized(right.get("accept_reject")),
                "a_ambiguity": normalized(left.get("ambiguity")),
                "b_ambiguity": normalized(right.get("ambiguity")),
                "a_rights_concern": normalized(left.get("rights_concern")),
                "b_rights_concern": normalized(right.get("rights_concern")),
                "a_notes": str(left.get("notes") or "").strip(),
                "b_notes": str(right.get("notes") or "").strip(),
            }
        )

    by_task_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_stratum_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in completed:
        by_task_rows[row["task"]].append(row)
        by_stratum_rows[row["stratum"]].append(row)
    by_task = {task: summarize(rows) for task, rows in sorted(by_task_rows.items())}
    by_stratum = {key: summarize(rows) for key, rows in sorted(by_stratum_rows.items())}
    micro = by_task.get("microtext", summarize([]))
    visual = by_task.get("visualdiff", summarize([]))
    gates = {
        "microtext_answer_agreement_0_95": {
            "current": micro["exact_answer_agreement"],
            "target": 0.95,
            "rows": micro["rows"],
            "passes": micro["rows"] > 0 and micro["exact_answer_agreement"] >= 0.95,
        },
        "visualdiff_accept_reject_agreement_0_90": {
            "current": visual["accept_reject_agreement"],
            "target": 0.90,
            "rows": visual["rows"],
            "passes": visual["rows"] > 0 and visual["accept_reject_agreement"] >= 0.90,
        },
    }
    adjudication_queue = build_adjudication_queue(completed)
    return {
        "reference_rows": len(reference),
        "input_contract_valid": input_contract_valid,
        "input_contract_issues": input_contract_issues,
        "paired_rows": len(paired_ids),
        "complete_rows": len(completed),
        "incomplete_rows": len(incomplete_ids),
        "incomplete_ids": incomplete_ids,
        "adjudication_rows": len(adjudication_queue),
        "adjudication_queue": adjudication_queue,
        "overall": summarize(completed),
        "by_task": by_task,
        "by_stratum": by_stratum,
        "gates": gates,
        "agreement_gate_complete": input_contract_valid
        and len(completed) == len(reference)
        and not incomplete_ids
        and not adjudication_queue
        and all(gate["passes"] for gate in gates.values()),
    }


def latest_agreement_report_path(root: Path) -> Path | None:
    report_dir = root / "results" / "annotation"
    candidates = list(report_dir.glob("agreement_report*.json"))
    candidates.extend(
        (root / "derived" / "human_adjudication" / "processed_returns").glob(
            "*" + "/agreement_audit/agreement_report.json"
        )
    )
    return max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name)) if candidates else None


def latest_agreement_readiness_report_path(root: Path) -> Path | None:
    candidates = list((root / "derived" / "quality").glob("agreement_sample_readiness*.json"))
    candidates.extend(
        (root / "derived" / "human_adjudication" / "processed_returns").glob(
            "*/agreement_audit/agreement_sample_readiness.json"
        )
    )
    return max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name)) if candidates else None


def agreement_release_gate(root: Path) -> dict[str, Any]:
    path = latest_agreement_report_path(root)
    target = (
        "all sampled rows complete, release-safe, reference-hash linked, "
        "and agreement thresholds pass"
    )
    if path is None:
        return {
            "current": "missing",
            "target": target,
            "passes": False,
            "report_path": "",
        }
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {
            "current": "invalid report",
            "target": target,
            "passes": False,
            "report_path": path.relative_to(root).as_posix(),
        }
    reference_rows = int(report.get("reference_rows") or 0)
    complete_rows = int(report.get("complete_rows") or 0)
    incomplete_rows = int(report.get("incomplete_rows") or 0)
    base_passes = (
        reference_rows > 0
        and bool(report.get("input_contract_valid", True))
        and complete_rows == reference_rows
        and incomplete_rows == 0
        and bool(report.get("agreement_gate_complete"))
    )
    readiness_path = latest_agreement_readiness_report_path(root)
    readiness: dict[str, Any] = {}
    readiness_valid = False
    sample_review_ready = False
    readiness_reason = "missing agreement sample readiness report"
    if readiness_path is not None:
        try:
            readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            readiness_reason = "invalid agreement sample readiness report"
        else:
            readiness_rows = int(readiness.get("reference_rows") or 0)
            report_hash = str(report.get("inputs", {}).get("reference_sha256") or "")
            readiness_hash = str(readiness.get("inputs", {}).get("reference_sha256") or "")
            sample_review_ready = bool(
                readiness.get(
                    "sample_review_ready",
                    readiness.get("release_sample_ready", False),
                )
            )
            readiness_valid = (
                bool(readiness.get("release_sample_ready"))
                and readiness_rows == reference_rows
                and int(readiness.get("fully_ready_rows") or 0) == reference_rows
                and int(readiness.get("non_release_ready_rows") or 0) == 0
                and bool(report_hash)
                and report_hash == readiness_hash
            )
            if readiness_valid:
                readiness_reason = "release-safe sample verified"
            elif sample_review_ready and complete_rows < reference_rows:
                readiness_reason = "release-safe sample verified; reviewer completion pending"
            elif not readiness.get("release_sample_ready"):
                readiness_reason = (
                    "agreement sample is not review-ready"
                    if "sample_review_ready" in readiness
                    else "agreement sample is not release-ready"
                )
            elif readiness_rows != reference_rows:
                readiness_reason = "agreement/readiness row-count mismatch"
            elif not report_hash or report_hash != readiness_hash:
                readiness_reason = "agreement/readiness reference hash mismatch"
            else:
                readiness_reason = "agreement sample readiness contract incomplete"
    passes = base_passes and readiness_valid
    return {
        "current": f"{complete_rows}/{reference_rows} complete",
        "target": target,
        "passes": passes,
        "report_path": path.relative_to(root).as_posix(),
        "incomplete_rows": incomplete_rows,
        "input_contract_valid": bool(report.get("input_contract_valid", True)),
        "sample_release_ready": readiness_valid,
        "sample_review_ready": sample_review_ready,
        "readiness_report_path": (
            readiness_path.relative_to(root).as_posix() if readiness_path is not None else ""
        ),
        "readiness_reason": readiness_reason,
        "release_ready_rows": int(readiness.get("release_ready_rows") or 0),
        "non_release_ready_rows": int(readiness.get("non_release_ready_rows") or 0),
    }


def render_markdown(report: dict[str, Any]) -> str:
    overall = report["overall"]
    lines = [
        "# Eng_Bench Human Agreement Audit",
        "",
        f"- Input contract valid: `{str(report.get('input_contract_valid', True)).lower()}`",
        f"- Reference rows: `{report['reference_rows']}`",
        f"- Paired reviewer rows: `{report['paired_rows']}`",
        f"- Complete paired rows: `{report['complete_rows']}`",
        f"- Incomplete paired rows: `{report['incomplete_rows']}`",
        f"- Adjudication queue rows: `{report.get('adjudication_rows', 0)}`",
        f"- Agreement gate complete: `{str(report['agreement_gate_complete']).lower()}`",
        "",
    ]
    if report.get("input_contract_issues"):
        lines.extend(
            ["## Input Contract Issues", ""]
            + [f"- `{issue}`" for issue in report["input_contract_issues"][:50]]
            + [""]
        )
    lines.extend(
        [
            "## Overall Metrics",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| Exact answer agreement | {overall['exact_answer_agreement']:.4f} |",
            f"| Normalized answer agreement | {overall['normalized_answer_agreement']:.4f} |",
            f"| Mean bbox IoU | {overall['bbox_iou_mean']:.4f} |",
            f"| Bbox agreement at IoU 0.5 | {overall['bbox_iou_agreement_0_5']:.4f} |",
            f"| Accept/reject agreement | {overall['accept_reject_agreement']:.4f} |",
            f"| Accept/reject Cohen's kappa | {overall['accept_reject_kappa']:.4f} |",
            f"| Ambiguity agreement | {overall['ambiguity_agreement']:.4f} |",
            f"| Rights-concern agreement | {overall['rights_concern_agreement']:.4f} |",
            "",
            "## Release Gates",
            "",
            "| Gate | Rows | Current | Target | Passes |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
    )
    for name, gate in report["gates"].items():
        lines.append(
            f"| `{name}` | {gate['rows']} | {gate['current']:.4f} | "
            f"{gate['target']:.4f} | {str(gate['passes']).lower()} |"
        )
    lines.extend(
        [
            "",
            "## By Task",
            "",
            "| Task | Rows | Exact Answer | Bbox IoU | Accept/Reject | Kappa |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for task, values in report["by_task"].items():
        lines.append(
            f"| {task} | {values['rows']} | {values['exact_answer_agreement']:.4f} | "
            f"{values['bbox_iou_mean']:.4f} | {values['accept_reject_agreement']:.4f} | "
            f"{values['accept_reject_kappa']:.4f} |"
        )
    if report["incomplete_ids"]:
        lines.extend(["", "## Incomplete IDs", ""])
        lines.extend(f"- `{identifier}`" for identifier in report["incomplete_ids"][:50])
    if report.get("adjudication_queue"):
        lines.extend(["", "## Adjudication Queue", ""])
        lines.append("| ID | Task | Reasons | Recommended Action |")
        lines.append("| --- | --- | --- | --- |")
        for row in report["adjudication_queue"][:50]:
            lines.append(
                f"| `{row['id']}` | {row['task']} | {row['reasons']} | "
                f"{row['recommended_action']} |"
            )
    lines.append("")
    return "\n".join(lines)


def render_adjudication_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Eng_Bench Agreement Adjudication Queue",
        "",
        f"- Queue rows: `{len(rows)}`",
        "",
    ]
    if not rows:
        lines.extend(["No completed reviewer disagreements or reviewer flags were found.", ""])
        return "\n".join(lines)
    lines.extend(
        [
            "| ID | Task | Reasons | Reviewer A | Reviewer B | Action |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows:
        lines.append(
            f"| `{row['id']}` | {row['task']} | {row['reasons']} | "
            f"{row['a_accept_reject']} / {row['a_answer']} | "
            f"{row['b_accept_reject']} / {row['b_answer']} | {row['recommended_action']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute Eng_Bench human agreement metrics.")
    parser.add_argument("--reference", required=True, help="Reference sample CSV")
    parser.add_argument("--reviewer-a", required=True)
    parser.add_argument("--reviewer-b", required=True)
    parser.add_argument("--output-json", default="results/annotation/agreement_report.json")
    parser.add_argument("--output-md", default="results/annotation/agreement_report.md")
    parser.add_argument("--adjudication-csv")
    parser.add_argument("--adjudication-md")
    args = parser.parse_args(argv)

    reference_path = Path(args.reference)
    reviewer_a_path = Path(args.reviewer_a)
    reviewer_b_path = Path(args.reviewer_b)
    report = compute_report(
        read_csv(reference_path),
        read_csv(reviewer_a_path),
        read_csv(reviewer_b_path),
    )
    report["inputs"] = {
        "reference": reference_path.as_posix(),
        "reference_sha256": file_sha256(reference_path),
        "reviewer_a": reviewer_a_path.as_posix(),
        "reviewer_a_sha256": file_sha256(reviewer_a_path),
        "reviewer_b": reviewer_b_path.as_posix(),
        "reviewer_b_sha256": file_sha256(reviewer_b_path),
    }
    write_json(Path(args.output_json), report)
    output_md = Path(args.output_md)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    if args.adjudication_csv:
        write_csv(Path(args.adjudication_csv), report["adjudication_queue"], ADJUDICATION_FIELDS)
    if args.adjudication_md:
        path = Path(args.adjudication_md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_adjudication_markdown(report["adjudication_queue"]), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["incomplete_rows"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
