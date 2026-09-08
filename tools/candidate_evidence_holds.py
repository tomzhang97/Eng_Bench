"""Load pinned machine evidence holds at preview and transaction time."""
import hashlib
import json
from pathlib import Path

CURRENT_HOLDS = "derived/quality/current_candidate_evidence_holds.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evidence_hold_ids(root: Path) -> set[str]:
    root = root.resolve()
    pointer = root / CURRENT_HOLDS
    if not pointer.is_file():
        return set()
    config = json.loads(pointer.read_text(encoding="utf-8"))
    if config.get("schema_version") not in {1, 2}:
        raise ValueError("unsupported candidate evidence hold schema")
    held_by_report: dict[str, set[str]] = {}
    for reference in config["reports"]:
        path = (root / reference["report_path"]).resolve()
        if not path.is_relative_to(root / "derived/quality") or digest(path) != reference["report_sha256"]:
            raise ValueError("candidate evidence hold report path or hash invalid")
        report = json.loads(path.read_text(encoding="utf-8"))
        for evidence in report.get("evidence_artifacts", []):
            evidence_path = (root / evidence["path"]).resolve()
            if (
                not evidence_path.is_relative_to(root)
                or digest(evidence_path) != evidence["sha256"]
            ):
                raise ValueError("candidate evidence artifact path or hash invalid")
        rows_name = report.get("hold_rows_artifact", "bbox_repair_proposals.jsonl")
        if Path(rows_name).name != rows_name:
            raise ValueError("candidate evidence hold rows path invalid")
        rows_path = path.parent / rows_name
        if (report.get("active_gold_modified") is not False or report.get("safe_to_merge_gold") is not False
                or digest(rows_path) != report["output_hashes"][rows_path.name]):
            raise ValueError("candidate evidence hold rows invalid or changed")
        rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        expected_count = report.get("hold_count", report.get("proposals"))
        if not isinstance(expected_count, int) or len(rows) != expected_count:
            raise ValueError("candidate evidence hold count mismatch")
        for row in rows:
            identity = next(
                (
                    row.get(key)
                    for key in ("candidate_id", "source_candidate_id", "item_id", "pair_id")
                    if isinstance(row.get(key), str) and row[key]
                ),
                None,
            )
            if not identity or row.get("safe_to_merge_gold") is not False:
                raise ValueError("candidate evidence hold identity or status invalid")
            held_by_report.setdefault(identity, set()).add(reference["report_sha256"])

    for reference in config.get("resolutions", []):
        path = (root / reference["report_path"]).resolve()
        if not path.is_relative_to(root / "derived/quality") or digest(path) != reference["report_sha256"]:
            raise ValueError("candidate evidence resolution report path or hash invalid")
        report = json.loads(path.read_text(encoding="utf-8"))
        for evidence in report.get("evidence_artifacts", []):
            evidence_path = (root / evidence["path"]).resolve()
            if (
                not evidence_path.is_relative_to(root)
                or digest(evidence_path) != evidence["sha256"]
            ):
                raise ValueError("candidate evidence resolution artifact path or hash invalid")
        rows_name = report.get("resolution_rows_artifact")
        if not rows_name or Path(rows_name).name != rows_name:
            raise ValueError("candidate evidence resolution rows path invalid")
        rows_path = path.parent / rows_name
        if (
            report.get("active_gold_modified") is not False
            or report.get("safe_to_merge_gold") is not False
            or digest(rows_path) != report["output_hashes"][rows_name]
        ):
            raise ValueError("candidate evidence resolution rows invalid or changed")
        rows = [
            json.loads(line)
            for line in rows_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if len(rows) != report.get("resolution_count"):
            raise ValueError("candidate evidence resolution count mismatch")
        for row in rows:
            identity = next(
                (
                    row.get(key)
                    for key in ("candidate_id", "source_candidate_id", "item_id", "pair_id")
                    if isinstance(row.get(key), str) and row[key]
                ),
                None,
            )
            resolved_reports = set(row.get("resolved_report_sha256s") or [])
            if (
                not identity
                or row.get("resolution_status") != "resolved"
                or not resolved_reports
                or not resolved_reports.issubset(held_by_report.get(identity, set()))
            ):
                raise ValueError("candidate evidence resolution identity or report invalid")
            held_by_report[identity] -= resolved_reports
    return {identity for identity, reports in held_by_report.items() if reports}


def is_evidence_held(row: dict, held_ids: set[str]) -> bool:
    return any(isinstance(row.get(key), str) and row[key] in held_ids
               for key in ("candidate_id", "source_candidate_id", "item_id", "pair_id"))
