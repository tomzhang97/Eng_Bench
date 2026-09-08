#!/usr/bin/env python3
"""Package a flat, verified incremental auditor delivery ZIP."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

try:
    from . import build_ultrafast_human_audit_delivery as delivery
    from . import process_partial_auditor_returns as partial_returns
except ImportError:  # Direct script execution from tools/.
    import build_ultrafast_human_audit_delivery as delivery
    import process_partial_auditor_returns as partial_returns


GUIDE_NAME = "00_READ_ME_FIRST_CN.txt"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_child(path: Path, parent: Path) -> None:
    resolved = path.resolve()
    root = parent.resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError(f"output path must be below {root}: {resolved}")


def guide_required_terms(payload: dict[str, Any]) -> list[str]:
    advanced = sorted(int(value) for value in payload.get("advanced_auditors") or [])
    preserved = sorted(int(value) for value in payload.get("preserved_auditors") or [])
    terms = [
        "Gold v2.0 Global",
        "1=是，2=否，3=看不清",
        "不能复制上一轮答案",
        "不会自动写入 Gold",
    ]
    if advanced:
        terms.append("、".join(f"{number:02d}" for number in advanced))
    if preserved:
        terms.append(
            "保持不变的复核员是 "
            + "、".join(f"{number:02d}" for number in preserved)
        )
    return terms


def continuation_prefill_expectations(
    payload: dict[str, Any], workbook_names: set[str]
) -> dict[str, int]:
    raw = payload.get("allowed_prefilled_workbooks") or {}
    if not isinstance(raw, dict):
        raise ValueError("allowed_prefilled_workbooks must be an object")
    expectations: dict[str, int] = {}
    row_counts = {
        str(auditor.get("workbook")): len(auditor.get("rows") or [])
        for auditor in payload.get("auditors") or []
    }
    for raw_name, raw_count in raw.items():
        name = str(raw_name)
        if name not in workbook_names:
            raise ValueError(f"prefilled continuation is not in payload: {name}")
        if isinstance(raw_count, bool) or not isinstance(raw_count, int):
            raise ValueError(f"prefilled continuation count must be an integer: {name}")
        row_count = row_counts.get(name, 12)
        if not 1 <= raw_count < row_count:
            raise ValueError(
                f"prefilled continuation count must be 1..{row_count - 1}: {name}"
            )
        expectations[name] = raw_count
    return expectations


def auditor_answer_count(path: Path, sheet_name: str) -> int:
    records, _, _ = delivery.matrix_records(
        path, sheet_name, delivery.PRIMARY_VISUAL_DECISION
    )
    return sum(
        bool(str(row.get(delivery.PRIMARY_VISUAL_DECISION, "")).strip())
        for row in records
    )


def write_zip(delivery_dir: Path, zip_path: Path, expected: set[str]) -> None:
    temporary = zip_path.with_name(f".{zip_path.name}.tmp")
    temporary.unlink(missing_ok=True)
    zip_path.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
            strict_timestamps=False,
        ) as archive:
            for name in sorted(expected):
                archive.write(delivery_dir / name, name)
        temporary.replace(zip_path)
    finally:
        temporary.unlink(missing_ok=True)


def package(
    root: Path,
    staging_dirs: list[Path],
    payload_path: Path,
    guide_path: Path,
    delivery_dir: Path,
    zip_path: Path,
    overwrite: bool,
    expected_auditor_count: int,
) -> dict[str, Any]:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    auditors = list(payload.get("auditors") or [])
    if len(auditors) != expected_auditor_count:
        raise ValueError(
            f"expected {expected_auditor_count} auditors, found {len(auditors)}"
        )
    auditor_numbers = [int(auditor["number"]) for auditor in auditors]
    if len(set(auditor_numbers)) != len(auditor_numbers):
        raise ValueError("payload contains duplicate auditor numbers")
    workbook_names = {str(auditor["workbook"]) for auditor in auditors}
    allowed_prefilled = continuation_prefill_expectations(payload, workbook_names)
    expected = workbook_names | {GUIDE_NAME}
    allowed_root = root / "derived/human_adjudication"
    ensure_child(delivery_dir, allowed_root)
    ensure_child(zip_path, allowed_root)
    if delivery_dir.exists():
        if not overwrite:
            raise FileExistsError(delivery_dir)
        shutil.rmtree(delivery_dir)
    delivery_dir.mkdir(parents=True)
    shutil.copy2(guide_path, delivery_dir / GUIDE_NAME)

    reports: list[dict[str, Any]] = []
    issues: list[str] = []
    for auditor in auditors:
        candidates = [
            directory / str(auditor["workbook"])
            for directory in staging_dirs
            if (directory / str(auditor["workbook"])).is_file()
        ]
        if len(candidates) != 1:
            raise FileNotFoundError(
                f"expected one source for {auditor['workbook']}, found {len(candidates)}"
            )
        source = candidates[0]
        destination = delivery_dir / source.name
        source_hash = sha256(source)
        shutil.copy2(source, destination)
        initial_states = delivery.sheet_states(destination)
        packaging_state_hardened = (
            initial_states.get(delivery.MACHINE_SHEET) != "veryHidden"
        )
        if packaging_state_hardened:
            delivery.set_sheet_state(destination, delivery.MACHINE_SHEET, "veryHidden")
        sheet_name = str(auditor.get("sheet_name") or delivery.AUDIT_SHEET)
        report, workbook_issues = delivery.validate_workbook(
            destination,
            "auditor",
            list(auditor.get("rows") or []),
            auditor_sheet_name=sheet_name,
        )
        expected_prefilled = allowed_prefilled.get(source.name)
        if expected_prefilled is not None:
            prefilled_issue = f"{destination.name}: answers are prefilled"
            workbook_issues = [
                issue for issue in workbook_issues if issue != prefilled_issue
            ]
            actual_prefilled = auditor_answer_count(destination, sheet_name)
            report["prefilled_continuation"] = True
            report["expected_prefilled_answers"] = expected_prefilled
            report["actual_prefilled_answers"] = actual_prefilled
            if actual_prefilled != expected_prefilled:
                workbook_issues.append(
                    f"{destination.name}: expected {expected_prefilled} preserved "
                    f"answers, found {actual_prefilled}"
                )
            report["issues"] = workbook_issues
            report["valid"] = not workbook_issues
        report["source_sha256"] = source_hash
        report["source_hash_preserved"] = sha256(destination) == source_hash
        report["packaging_state_hardened"] = packaging_state_hardened
        if not report["source_hash_preserved"] and not packaging_state_hardened:
            workbook_issues.append(
                f"{destination.name}: source workbook bytes changed during packaging"
            )
            report["valid"] = False
            report["issues"] = workbook_issues
        expected_hashes = [
            sha256(Path(str(row["evidence_path"]))) for row in auditor.get("rows", [])
        ]
        actual_hashes = partial_returns.embedded_evidence_hashes(
            destination,
            sheet_name,
            len(auditor.get("rows") or []),
        )
        if actual_hashes != expected_hashes:
            workbook_issues.append(f"{destination.name}: embedded evidence hash/order mismatch")
            report["valid"] = False
            report["issues"] = workbook_issues
        reports.append(report)
        issues.extend(workbook_issues)

    actual_names = {path.name for path in delivery_dir.iterdir() if path.is_file()}
    if actual_names != expected:
        issues.append(
            f"delivery file mismatch: missing={sorted(expected - actual_names)}, "
            f"extra={sorted(actual_names - expected)}"
        )
    if any(path.is_dir() for path in delivery_dir.iterdir()):
        issues.append("delivery contains subdirectories")
    guide = (delivery_dir / GUIDE_NAME).read_text(encoding="utf-8-sig")
    for term in guide_required_terms(payload):
        if term not in guide:
            issues.append(f"guide missing required term: {term}")

    if issues:
        return {
            "goal": "Gold v2.0 Global",
            "valid": False,
            "issues": issues,
            "workbooks": reports,
            "gold_rows_modified": 0,
        }
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    write_zip(delivery_dir, zip_path, expected)

    zip_issues: list[str] = []
    with zipfile.ZipFile(zip_path, "r") as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        corrupt = archive.testzip()
        if corrupt:
            zip_issues.append(f"ZIP CRC failure at {corrupt}")
        if set(names) != expected:
            zip_issues.append("ZIP root file set differs from expected delivery")
        if any("/" in name or "\\" in name for name in names):
            zip_issues.append("ZIP contains non-root paths")
        if any(name.lower().endswith(".zip") for name in names):
            zip_issues.append("ZIP contains nested ZIP files")
        with tempfile.TemporaryDirectory() as temporary:
            archive.extractall(temporary)
            extracted = Path(temporary)
            for name in expected:
                if sha256(delivery_dir / name) != sha256(extracted / name):
                    zip_issues.append(f"clean extraction hash mismatch: {name}")

    issues.extend(zip_issues)
    return {
        "goal": "Gold v2.0 Global",
        "workflow": "incremental independent auditor delivery",
        "delivery_dir": delivery_dir.resolve().as_posix(),
        "zip": zip_path.resolve().as_posix(),
        "zip_size_bytes": zip_path.stat().st_size,
        "zip_sha256": sha256(zip_path),
        "zip_entries": len(expected),
        "flat_root_files_only": True,
        "nested_zip_files": 0,
        "primary_workbook_included": False,
        "auditors": auditor_numbers,
        "workbooks": reports,
        "workbook_count": len(reports),
        "review_rows": sum(len(auditor.get("rows") or []) for auditor in auditors),
        "embedded_images": sum(report["embedded_images"] for report in reports),
        "issues": issues,
        "safe_to_merge_gold": False,
        "gold_rows_modified": 0,
        "valid": not issues and all(report["valid"] for report in reports),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", type=Path)
    parser.add_argument("--staging-dir", required=True, action="append", type=Path)
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--guide", required=True, type=Path)
    parser.add_argument("--delivery-dir", required=True, type=Path)
    parser.add_argument("--zip", dest="zip_path", required=True, type=Path)
    parser.add_argument("--report-json", required=True, type=Path)
    parser.add_argument("--expected-auditor-count", type=int, default=10)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    report = package(
        root,
        [(root / path).resolve() for path in args.staging_dir],
        (root / args.payload).resolve(),
        (root / args.guide).resolve(),
        (root / args.delivery_dir).resolve(),
        (root / args.zip_path).resolve(),
        args.overwrite,
        args.expected_auditor_count,
    )
    report_path = (root / args.report_json).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
