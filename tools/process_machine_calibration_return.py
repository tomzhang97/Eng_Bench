#!/usr/bin/env python3
"""Validate a returned machine-calibration checklist and build read-only release previews.

This is the maintainer entry point after the calibration reviewer returns the
CSV. It snapshots the return, verifies the frozen eligibility cohort, finalizes
all-or-nothing machine certification, and runs the normal promotion preview
only when calibration passes. It never edits active Gold.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Callable


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import finalize_machine_certification as finalizer
import preview_reviewed_gold_promotion as promotion_preview


PreviewBuilder = Callable[..., dict[str, Any]]


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def display(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def expected_report_hash(
    cohort_dir: Path,
    eligibility_report_path: Path,
    explicit_hash: str,
) -> tuple[str, str, list[str]]:
    issues: list[str] = []
    if explicit_hash.strip():
        return explicit_hash.strip().lower(), "command_line", issues

    lock_path = cohort_dir / "cohort_lock.json"
    if lock_path.is_file():
        lock = read_json(lock_path)
        locked_path = str(lock.get("eligibility_report") or "").strip()
        locked_hash = str(lock.get("eligibility_report_sha256") or "").strip().lower()
        if locked_path and Path(locked_path).name != eligibility_report_path.name:
            issues.append("cohort_lock_eligibility_report_path_mismatch")
        if locked_hash:
            return locked_hash, display(cohort_dir, lock_path), issues
        issues.append("cohort_lock_missing_eligibility_report_sha256")

    pending_path = cohort_dir / "finalization_pending_report.json"
    if pending_path.is_file():
        pending = read_json(pending_path)
        inputs = pending.get("inputs") or {}
        locked_hash = str(inputs.get("eligibility_report_sha256") or "").strip().lower()
        locked_path = str(inputs.get("eligibility_report") or "").strip()
        if locked_path and Path(locked_path).name != eligibility_report_path.name:
            issues.append("pending_attestation_eligibility_report_path_mismatch")
        if locked_hash:
            return locked_hash, display(cohort_dir, pending_path), issues
        issues.append("pending_attestation_missing_eligibility_report_sha256")

    issues.append("trusted_eligibility_report_sha256_unavailable")
    return "0" * 64, "unavailable", issues


def synthetic_finalization_report(
    *,
    root: Path,
    checklist_path: Path,
    certified_path: Path,
    date_label: str,
    issues: list[str],
) -> dict[str, Any]:
    finalizer.write_jsonl(certified_path, [])
    checklist_rows = (
        len(finalizer.calibration_rows(checklist_path)) if checklist_path.is_file() else 0
    )
    return {
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "active_gold_modified": False,
        "machine_certification_release_ready": False,
        "issues": unique(issues),
        "counts": {
            "eligible_rows": 0,
            "calibration_rows": checklist_rows,
            "calibration_correct": 0,
            "calibration_incorrect": 0,
            "calibration_unclear": 0,
            "certified_output_rows": 0,
        },
        "statistics": {
            "observed_precision": 0.0,
            "confidence": 0.95,
            "one_sided_precision_lower_bound": 0.0,
            "minimum_precision_lower_bound": 0.99,
            "allowed_incorrect_or_unclear": 0,
        },
        "inputs": {"calibration_checklist": display(root, checklist_path)},
        "output": {"certified_jsonl": display(root, certified_path)},
        "interpretation": (
            "Preflight failed before certification. The certified artifact is intentionally empty, "
            "active Gold was not modified, and no promotion preview was attempted."
        ),
    }


def next_actions(report: dict[str, Any]) -> list[str]:
    issues = set(report.get("issues") or [])
    finalization = report.get("finalization") or {}
    final_issues = set(finalization.get("issues") or [])
    preview = report.get("promotion_preview") or {}
    actions: list[str] = []
    if "calibration_incomplete_or_invalid_decisions" in final_issues:
        actions.append(
            "Return the same checklist to the calibration reviewer and fill every reviewer_decision with correct, incorrect, or unclear."
        )
    if {"calibration_contains_incorrect_rows", "calibration_contains_unclear_rows"} & final_issues:
        actions.append(
            "Do not certify this cohort. Revise the machine policy for the failed examples, rerun eligibility, and freeze a new calibration sample."
        )
    if any("sha256" in issue or "hash" in issue for issue in issues | final_issues):
        actions.append(
            "Restore the frozen cohort artifacts or use the expected eligibility-report SHA-256 recorded before handoff; do not bypass the mismatch."
        )
    if report.get("promotion_preview_attempted") and not preview.get("ready_for_apply"):
        actions.append(
            "Resolve every hold and failed gate in promotion_preview/promotion_preview_report.json, then rerun this processor with a new output directory."
        )
    if report.get("ready_for_separate_hash_locked_apply"):
        actions.append(
            "Create a fresh mutable-file snapshot, then use the separate hash-locked apply transaction and rerun all release gates."
        )
    if not actions:
        actions.append("Resolve the reported preflight issue and rerun with a new output directory.")
    return actions


def render_markdown(report: dict[str, Any]) -> str:
    finalization = report.get("finalization") or {}
    counts = finalization.get("counts") or {}
    preview = report.get("promotion_preview") or {}
    lines = [
        "# Machine Calibration Return Control",
        "",
        f"- Goal: **{report['goal']}**",
        f"- Calibration release ready: `{str(bool(finalization.get('machine_certification_release_ready'))).lower()}`",
        f"- Certified rows staged: `{counts.get('certified_output_rows', 0)}`",
        f"- Promotion preview attempted: `{str(report['promotion_preview_attempted']).lower()}`",
        f"- Promotion preview ready: `{str(bool(preview.get('ready_for_apply'))).lower()}`",
        f"- Ready for separate hash-locked apply: `{str(report['ready_for_separate_hash_locked_apply']).lower()}`",
        f"- Active Gold modified: `{str(report['active_gold_modified']).lower()}`",
        "- Safe to merge Gold directly: `false`",
        "",
        "## Control Issues",
        "",
    ]
    lines.extend(f"- `{issue}`" for issue in report.get("issues") or [])
    if not report.get("issues"):
        lines.append("- none")
    lines.extend(["", "## Finalization Issues", ""])
    lines.extend(f"- `{issue}`" for issue in finalization.get("issues") or [])
    if not finalization.get("issues"):
        lines.append("- none")
    lines.extend(["", "## Next Actions", ""])
    lines.extend(f"{index}. {action}" for index, action in enumerate(report["next_actions"], 1))
    lines.extend(
        [
            "",
            "This control is read-only. Even a green result stages evidence only; it never edits active Gold.",
            "",
        ]
    )
    return "\n".join(lines)


def run_control(
    *,
    root: Path,
    cohort_dir: Path,
    completed_checklist: Path,
    output_dir: Path,
    date_label: str,
    expected_eligibility_report_sha256: str = "",
    minimum_sample_rows: int = 300,
    confidence: float = 0.95,
    minimum_precision_bound: float = 0.99,
    preview_builder: PreviewBuilder = promotion_preview.build_preview,
) -> dict[str, Any]:
    root = root.resolve()
    cohort_dir = cohort_dir.resolve()
    completed_checklist = completed_checklist.resolve()
    output_dir = output_dir.resolve()
    quality_root = (root / "derived" / "quality").resolve()
    if output_dir != quality_root and quality_root not in output_dir.parents:
        raise ValueError("output-dir must be under derived/quality")
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    if completed_checklist.suffix.lower() != ".csv":
        raise ValueError("completed-checklist must be a CSV returned from the calibration pack")
    output_dir.mkdir(parents=True)
    active_before = promotion_preview.active_hashes(root)
    issues: list[str] = []

    snapshot_path = output_dir / "returned_calibration_checklist.csv"
    if completed_checklist.is_file():
        shutil.copy2(completed_checklist, snapshot_path)
    else:
        snapshot_path.write_text("", encoding="utf-8")
        issues.append("completed_calibration_checklist_missing")

    eligibility_report_path = cohort_dir / "eligibility_report.json"
    eligibility: dict[str, Any] = {}
    eligible_path = cohort_dir / "auto_eligible_pending_calibration.jsonl"
    split_plan_path = Path()
    expected_hash = "0" * 64
    hash_source = "unavailable"
    if not eligibility_report_path.is_file():
        issues.append("eligibility_report_missing")
    else:
        try:
            eligibility = read_json(eligibility_report_path)
        except (OSError, json.JSONDecodeError):
            issues.append("eligibility_report_invalid_json")
        if eligibility:
            artifact = (eligibility.get("artifacts") or {}).get("auto_eligible") or {}
            artifact_path = str(artifact.get("path") or "").strip()
            if artifact_path:
                eligible_path = resolve(root, artifact_path)
            if eligible_path != cohort_dir and cohort_dir not in eligible_path.parents:
                issues.append("eligible_artifact_outside_cohort")
            split_text = str(eligibility.get("split_plan") or "").strip()
            if split_text:
                split_plan_path = resolve(root, split_text)
            else:
                issues.append("eligibility_report_split_plan_missing")
            expected_hash, hash_source, hash_issues = expected_report_hash(
                cohort_dir,
                eligibility_report_path,
                expected_eligibility_report_sha256,
            )
            issues.extend(hash_issues)
            if finalizer.file_sha256(eligibility_report_path).lower() != expected_hash:
                issues.append("eligibility_report_sha256_mismatch")
            if not eligible_path.is_file():
                issues.append("eligible_jsonl_missing")
            elif str(artifact.get("sha256") or "").lower() != finalizer.file_sha256(eligible_path).lower():
                issues.append("eligible_jsonl_sha256_mismatch")
            if not split_plan_path.is_file():
                issues.append("split_plan_missing")
            elif str(eligibility.get("split_plan_sha256") or "").lower() != finalizer.file_sha256(split_plan_path).lower():
                issues.append("split_plan_sha256_mismatch")

    certified_path = output_dir / "certified_machine_rows.jsonl"
    final_report_path = output_dir / "machine_certification_finalization_report.json"
    final_report_md = output_dir / "machine_certification_finalization_report.md"
    required_files_present = all(
        path.is_file()
        for path in (eligibility_report_path, eligible_path, snapshot_path, split_plan_path)
    )
    if required_files_present:
        try:
            final_report, _ = finalizer.build_report(
                root,
                eligible_path,
                eligibility_report_path,
                snapshot_path,
                certified_path,
                expected_eligibility_report_sha256=expected_hash,
                date_label=date_label,
                minimum_sample_rows=max(1, minimum_sample_rows),
                confidence=confidence,
                minimum_precision_bound=minimum_precision_bound,
            )
        except Exception as error:
            issue = f"finalizer_exception:{type(error).__name__}:{error}"
            issues.append(issue)
            final_report = synthetic_finalization_report(
                root=root,
                checklist_path=snapshot_path,
                certified_path=certified_path,
                date_label=date_label,
                issues=[issue],
            )
    else:
        final_report = synthetic_finalization_report(
            root=root,
            checklist_path=snapshot_path,
            certified_path=certified_path,
            date_label=date_label,
            issues=issues,
        )
    write_json(final_report_path, final_report)
    finalizer.write_markdown(final_report_md, final_report)

    finalization_ready = bool(
        final_report.get("machine_certification_release_ready") and not issues
    )
    preview_attempted = False
    preview_report: dict[str, Any] = {
        "ready_for_apply": False,
        "state": "not_run_calibration_blocked",
    }
    if finalization_ready:
        preview_attempted = True
        preview_dir = output_dir / "promotion_preview"
        try:
            preview_report = preview_builder(
                root,
                [certified_path],
                [],
                split_plan_path,
                preview_dir,
                date_label,
            )
            write_json(preview_dir / "promotion_preview_report.json", preview_report)
            (preview_dir / "promotion_preview_report.md").write_text(
                promotion_preview.render_markdown(preview_report), encoding="utf-8"
            )
        except Exception as error:
            issue = f"promotion_preview_exception:{type(error).__name__}:{error}"
            issues.append(issue)
            preview_report = {
                "ready_for_apply": False,
                "state": "preview_exception",
                "issue": issue,
            }
            write_json(preview_dir / "promotion_preview_report.json", preview_report)

    active_after = promotion_preview.active_hashes(root)
    active_modified = active_before != active_after
    if active_modified:
        issues.append("active_gold_files_changed_during_control")
    ready = bool(
        finalization_ready
        and preview_attempted
        and preview_report.get("ready_for_apply")
        and not active_modified
    )
    report = {
        "goal": "Gold v2.0 Global",
        "date_label": date_label,
        "mode": "read_only_machine_calibration_return_control",
        "safe_to_merge_gold": False,
        "active_gold_modified": active_modified,
        "ready_for_separate_hash_locked_apply": ready,
        "issues": unique(issues),
        "cohort": {
            "directory": display(root, cohort_dir),
            "eligibility_report": display(root, eligibility_report_path),
            "eligibility_report_sha256": (
                finalizer.file_sha256(eligibility_report_path)
                if eligibility_report_path.is_file()
                else ""
            ),
            "trusted_expected_eligibility_report_sha256": expected_hash,
            "expected_hash_source": hash_source,
            "eligible_jsonl": display(root, eligible_path),
            "split_plan": display(root, split_plan_path) if split_plan_path else "",
        },
        "returned_checklist": {
            "source": display(root, completed_checklist),
            "snapshot": display(root, snapshot_path),
            "snapshot_sha256": finalizer.file_sha256(snapshot_path),
        },
        "finalization": final_report,
        "finalization_report": display(root, final_report_path),
        "promotion_preview_attempted": preview_attempted,
        "promotion_preview": preview_report,
        "promotion_preview_report": (
            display(root, output_dir / "promotion_preview" / "promotion_preview_report.json")
            if preview_attempted
            else ""
        ),
        "active_file_hashes_before": active_before,
        "active_file_hashes_after": active_after,
    }
    report["next_actions"] = next_actions(report)
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--cohort-dir", type=Path, required=True)
    parser.add_argument("--completed-checklist", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--expected-eligibility-report-sha256", default="")
    parser.add_argument("--minimum-sample-rows", type=int, default=300)
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--minimum-precision-bound", type=float, default=0.99)
    parser.add_argument("--require-ready", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    output_dir = resolve(root, args.output_dir)
    report = run_control(
        root=root,
        cohort_dir=resolve(root, args.cohort_dir),
        completed_checklist=resolve(root, args.completed_checklist),
        output_dir=output_dir,
        date_label=args.date_label,
        expected_eligibility_report_sha256=args.expected_eligibility_report_sha256,
        minimum_sample_rows=args.minimum_sample_rows,
        confidence=args.confidence,
        minimum_precision_bound=args.minimum_precision_bound,
    )
    report_path = output_dir / "machine_calibration_return_control_report.json"
    report_md = output_dir / "machine_calibration_return_control_report.md"
    write_json(report_path, report)
    report_md.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "goal": report["goal"],
                "calibration_release_ready": report["finalization"].get(
                    "machine_certification_release_ready", False
                ),
                "certified_rows_staged": report["finalization"].get("counts", {}).get(
                    "certified_output_rows", 0
                ),
                "promotion_preview_attempted": report["promotion_preview_attempted"],
                "ready_for_separate_hash_locked_apply": report[
                    "ready_for_separate_hash_locked_apply"
                ],
                "active_gold_modified": report["active_gold_modified"],
                "issues": report["issues"],
                "finalization_issues": report["finalization"].get("issues", []),
                "report": display(root, report_path),
            },
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if args.require_ready and not report["ready_for_separate_hash_locked_apply"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
