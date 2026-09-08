#!/usr/bin/env python3
"""Normalize returned low-friction audit XLSX files without merging gold data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

try:
    from . import build_easiest_human_audit_delivery as delivery
    from . import build_ultrafast_human_audit_delivery as ultrafast
    from . import verify_multi_reviewer_handoff as workbook_io
except ImportError:  # Direct script execution from tools/.
    import build_easiest_human_audit_delivery as delivery
    import build_ultrafast_human_audit_delivery as ultrafast
    import verify_multi_reviewer_handoff as workbook_io


PRIMARY_MICRO_MAP = {
    "正确": "accepted",
    "需修改": "edited",
    "不合格": "rejected",
    "看不清": "needs_full_page",
}
AUDITOR_MICRO_MAP = {
    "通过": "pass",
    "有问题": "issue",
    "看不清": "needs_full_page",
}
VISUAL_MAP = {
    "有真实变化": "edit",
    "无有效变化": "reject_unclear",
    "看不清": "needs_full_page",
}
ULTRA_PRIMARY_MICRO_MAP = {
    "1": "accepted",
    "2": "edited",
    "3": "rejected",
    "4": "needs_full_page",
    "A": "accepted",
    "E": "edited",
    "R": "rejected",
    "U": "needs_full_page",
}
ULTRA_AUDITOR_MAP = {
    "1": "yes",
    "2": "no",
    "3": "needs_full_page",
    "Y": "yes",
    "N": "no",
    "U": "needs_full_page",
}
ULTRA_VISUAL_MAP = {
    "1": "edit",
    "2": "reject_unclear",
    "3": "needs_full_page",
    "Y": "edit",
    "N": "reject_unclear",
    "U": "needs_full_page",
}
SIMPLEST_PRIMARY_VISUAL_MAP = {
    "1": "edit",
    "2": "edit",
    "3": "reject_unclear",
    "4": "needs_full_page",
}

ULTRA_MICRO_DECISION = "\u7b54\u6848 1/2/3/4"
ULTRA_VISUAL_DECISION = "\u7b54\u6848 1/2/3"
SIMPLEST_PRIMARY_DECISION = "\u7b54\u6848\uff1a1\u901a\u8fc7 2\u4fee\u6539 3\u5254\u9664 4\u770b\u4e0d\u6e05"
ULTRA_CORRECT_TEXT = "\u6b63\u786e\u6587\u5b57\uff08\u4ec5 2 \u65f6\uff09"
ULTRA_CORRECT_CATEGORY = "\u6b63\u786e\u7c7b\u522b\uff08\u4ec5 2 \u65f6\uff09"
ULTRA_DESCRIPTION = "\u53d8\u5316\u63cf\u8ff0\uff08\u9009 1 \u65f6\u5fc5\u987b\u51c6\u786e\uff09"
SIMPLEST_VISUAL_DESCRIPTION = "\u673a\u5668\u53d8\u5316\u63cf\u8ff0\uff08\u4ec5 2 \u65f6\u4fee\u6539\uff09"
ULTRA_MICRO_DECISION_LEGACY = "\u7b54\u6848 A/E/R/U"
ULTRA_VISUAL_DECISION_LEGACY = "\u7b54\u6848 Y/N/U"
ULTRA_CORRECT_TEXT_LEGACY = "\u6b63\u786e\u6587\u5b57\uff08\u4ec5 E \u65f6\uff09"
ULTRA_CORRECT_CATEGORY_LEGACY = "\u6b63\u786e\u7c7b\u522b\uff08\u4ec5 E \u65f6\uff09"
ULTRA_DESCRIPTION_LEGACY = "\u53d8\u5316\u63cf\u8ff0\uff08Y \u65f6\u5fc5\u987b\u51c6\u786e\uff09"
ULTRA_MICRO_SUGGESTION = "\u673a\u5668\u5efa\u8bae\uff08\u6587\u5b57 + \u7c7b\u522b\uff09"
ULTRA_VISUAL_SUGGESTION = "\u673a\u5668\u63d0\u793a\uff08\u53ea\u770b\u5bf9\u6bd4\u533a\u57df\uff09"
ULTRA_AUDIT_PROMPT = "\u5224\u65ad\u9898 + \u673a\u5668\u5efa\u8bae"
SINGLE_PRIMARY_TASK = "\u7c7b\u578b\uff08\u52ff\u6539\uff09"
SINGLE_PRIMARY_TASK_LEGACY = "\u4efb\u52a1\u4e0e\u5224\u65ad\u6807\u51c6"
SINGLE_PRIMARY_SUGGESTION = "\u53ea\u770b\u8fd9\u91cc\uff1a\u95ee\u9898 + \u673a\u5668\u5185\u5bb9\uff08\u52ff\u6539\uff09"
SINGLE_PRIMARY_SUGGESTION_LEGACY = "\u673a\u5668\u5efa\u8bae\uff08\u52ff\u6539\uff09"
SINGLE_PRIMARY_SUGGESTION_LEAN = "\u95ee\u9898 + \u673a\u5668\u5185\u5bb9\uff08\u52ff\u6539\uff09"
SINGLE_PRIMARY_CORRECTION = "\u6b63\u786e\u6587\u5b57 / \u53d8\u5316\u63cf\u8ff0\uff08\u4ec5 2\uff09"
SINGLE_PRIMARY_CATEGORY = "\u6b63\u786e\u7c7b\u522b\uff08\u4ec5\u6587\u5b57\u9898\u4e14 2\uff09"


def reviewer_role(path: Path) -> tuple[str, str]:
    stem = path.stem.upper()
    if stem == "PRIMARY_REVIEW_498":
        return "primary", "primary_reviewer"
    if stem.startswith("AUDITOR_") and stem.endswith("_REVIEW_12"):
        token = stem.split("_")[1]
        if token.isdigit() and len(token) == 2:
            return "auditor", f"auditor_{token}"
    raise ValueError(f"unrecognized return filename: {path.name}")


def _require_decision(
    value: str,
    mapping: dict[str, str],
    workbook: Path,
    sheet: str,
    row_number: int,
) -> str:
    if value not in mapping:
        choices = ", ".join(mapping)
        raise ValueError(
            f"{workbook.name} {sheet} row {row_number}: "
            f"decision {value!r} is blank/invalid; expected one of {choices}"
        )
    return mapping[value]


def _first_value(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        if key in row:
            return row.get(key, "")
    return ""


def _compare_stable_rows(
    returned: list[dict[str, str]],
    canonical: list[dict[str, str]],
    extractor: Any,
    workbook: Path,
    sheet: str,
) -> None:
    if [extractor(row) for row in returned] != [extractor(row) for row in canonical]:
        raise ValueError(f"{workbook.name} {sheet}: sample IDs/source/order changed")


def _sheet_names(path: Path) -> set[str]:
    import zipfile

    with zipfile.ZipFile(path, "r") as archive:
        return set(workbook_io.workbook_sheet_paths(archive))


def _machine_rows(path: Path) -> list[dict[str, str]]:
    rows, _ = workbook_io.table_records(path, ultrafast.MACHINE_SHEET)
    return rows


def _auditor_records(
    path: Path,
    sheet_name: str = ultrafast.AUDIT_SHEET,
    limit: int | None = 12,
) -> list[dict[str, str]]:
    rows, _, _ = workbook_io.read_xlsx_sheet(path, sheet_name)
    header_index = next(
        (
            index
            for index, values in enumerate(rows)
            if ULTRA_VISUAL_DECISION in values
            or ULTRA_VISUAL_DECISION_LEGACY in values
        ),
        -1,
    )
    if header_index < 0:
        raise ValueError(f"{path.name}: missing ultrafast audit header")
    headers = rows[header_index]
    records: list[dict[str, str]] = []
    for values in rows[header_index + 1 :]:
        padded = values + [""] * max(0, len(headers) - len(values))
        record = {
            str(header): str(padded[index])
            for index, header in enumerate(headers)
            if str(header).strip()
        }
        if any(value.strip() for value in record.values()):
            records.append(record)
    return records if limit is None else records[:limit]


def _single_primary_records(path: Path) -> list[dict[str, str]]:
    rows, _, _ = workbook_io.read_xlsx_sheet(path, ultrafast.SINGLE_PRIMARY_SHEET)
    header_index = next(
        (
            index
            for index, values in enumerate(rows)
            if ultrafast.SINGLE_PRIMARY_DECISION in values
        ),
        -1,
    )
    if header_index < 0:
        raise ValueError(f"{path.name}: missing single-sheet primary header")
    headers = rows[header_index]
    records: list[dict[str, str]] = []
    for values in rows[header_index + 1 :]:
        padded = values + [""] * max(0, len(headers) - len(values))
        record = {
            str(header): str(padded[index])
            for index, header in enumerate(headers)
            if str(header).strip()
        }
        if any(value.strip() for value in record.values()):
            records.append(record)
    return records[:498]


def _compare_machine_rows(path: Path, canonical: Path) -> list[dict[str, str]]:
    returned = _machine_rows(path)
    expected = _machine_rows(canonical)
    if returned != expected:
        raise ValueError(f"{path.name}: hidden machine identities/source/order changed")
    return returned


def _validate_visible_identity(
    row: dict[str, str],
    machine: dict[str, str],
    suggestion_key: str,
    workbook: Path,
    row_number: int,
) -> None:
    if row.get("#", "").strip() != machine.get("display_index", "").strip():
        raise ValueError(f"{workbook.name} row {row_number}: display index changed")
    if row.get(suggestion_key, "").strip() != machine.get("machine_suggestion", "").strip():
        raise ValueError(f"{workbook.name} row {row_number}: machine suggestion changed")


def normalize_single_primary_workbook(
    path: Path,
    canonical: Path,
    reviewer_id: str,
    machine_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    records = _single_primary_records(path)
    canonical_records = _single_primary_records(canonical)
    if len(records) != 498 or len(canonical_records) != 498 or len(machine_rows) != 498:
        raise ValueError(f"{path.name}: expected 498 single-sheet primary rows")

    normalized: list[dict[str, Any]] = []
    for row_number, (row, source, machine) in enumerate(
        zip(records, canonical_records, machine_rows), start=7
    ):
        visible_fields = (
            ("#",),
            (SINGLE_PRIMARY_TASK, SINGLE_PRIMARY_TASK_LEGACY),
            (
                SINGLE_PRIMARY_SUGGESTION,
                SINGLE_PRIMARY_SUGGESTION_LEGACY,
                SINGLE_PRIMARY_SUGGESTION_LEAN,
            ),
        )
        for keys in visible_fields:
            if _first_value(row, *keys).strip() != _first_value(source, *keys).strip():
                raise ValueError(
                    f"{path.name} row {row_number}: visible identity/source changed"
                )
        if row.get("#", "").strip() != machine.get("primary_index", "").strip():
            raise ValueError(f"{path.name} row {row_number}: primary index changed")

        decision_code = row.get(ultrafast.SINGLE_PRIMARY_DECISION, "").strip().upper()
        correction = row.get(SINGLE_PRIMARY_CORRECTION, "").strip()
        corrected_category = row.get(SINGLE_PRIMARY_CATEGORY, "").strip()
        task = machine.get("task", "")
        common = {
            "reviewer_id": reviewer_id,
            "reviewer_role": "primary",
            "task_type": task,
            "primary_index": int(machine["primary_index"]),
            "decision_zh": decision_code,
            "note": "",
            "source_workbook": path.name,
        }
        if task == "microtext":
            decision = _require_decision(
                decision_code,
                ULTRA_PRIMARY_MICRO_MAP,
                path,
                ultrafast.SINGLE_PRIMARY_SHEET,
                row_number,
            )
            if decision == "edited" and not (correction or corrected_category):
                raise ValueError(
                    f"{path.name} row {row_number}: edit requires corrected content or category"
                )
            normalized.append(
                {
                    **common,
                    "candidate_id": machine["record_id"],
                    "decision": decision,
                    "corrected_text": correction,
                    "corrected_category": corrected_category,
                    "issue_type": "",
                }
            )
        elif task == "visualdiff":
            decision = _require_decision(
                decision_code,
                SIMPLEST_PRIMARY_VISUAL_MAP,
                path,
                ultrafast.SINGLE_PRIMARY_SHEET,
                row_number,
            )
            if decision_code == "1":
                description = machine.get("original_change_description", "").strip()
            elif decision_code == "2":
                description = correction
                if not description:
                    raise ValueError(
                        f"{path.name} row {row_number}: edit requires corrected description"
                    )
            else:
                description = ""
            if decision == "edit" and not description:
                raise ValueError(
                    f"{path.name} row {row_number}: accepted change lacks description"
                )
            normalized.append(
                {
                    **common,
                    "pair_id": machine["record_id"],
                    "decision": decision,
                    "change_description": description if decision == "edit" else "",
                }
            )
        else:
            raise ValueError(f"{path.name} row {row_number}: unknown task {task!r}")
    return normalized


def normalize_ultrafast_workbook(
    path: Path,
    canonical: Path,
    role: str,
    reviewer_id: str,
) -> list[dict[str, Any]]:
    machine_rows = _compare_machine_rows(path, canonical)
    normalized: list[dict[str, Any]] = []
    if role == "primary":
        if ultrafast.SINGLE_PRIMARY_SHEET in _sheet_names(path):
            return normalize_single_primary_workbook(
                path, canonical, reviewer_id, machine_rows
            )
        micro, _ = workbook_io.table_records(path, "MicroText")
        visual, _ = workbook_io.table_records(path, "VisualDiff")
        canonical_micro, _ = workbook_io.table_records(canonical, "MicroText")
        canonical_visual, _ = workbook_io.table_records(canonical, "VisualDiff")
        micro_machine = [row for row in machine_rows if row.get("task") == "microtext"]
        visual_machine = [row for row in machine_rows if row.get("task") == "visualdiff"]
        if len(micro) != 369 or len(visual) != 129:
            raise ValueError(f"{path.name}: expected 369 MicroText and 129 VisualDiff rows")
        if len(canonical_micro) != len(micro) or len(canonical_visual) != len(visual):
            raise ValueError(f"{path.name}: canonical row count mismatch")

        for offset, (row, source, machine) in enumerate(
            zip(micro, canonical_micro, micro_machine), start=2
        ):
            _validate_visible_identity(
                row, machine, ULTRA_MICRO_SUGGESTION, path, offset
            )
            decision_code = _first_value(
                row,
                SIMPLEST_PRIMARY_DECISION,
                ULTRA_MICRO_DECISION,
                ULTRA_MICRO_DECISION_LEGACY,
            ).strip().upper()
            decision = _require_decision(
                decision_code, ULTRA_PRIMARY_MICRO_MAP, path, "MicroText", offset
            )
            corrected_text = _first_value(
                row, ULTRA_CORRECT_TEXT, ULTRA_CORRECT_TEXT_LEGACY
            ).strip()
            corrected_category = _first_value(
                row, ULTRA_CORRECT_CATEGORY, ULTRA_CORRECT_CATEGORY_LEGACY
            ).strip()
            if decision == "edited" and not (corrected_text or corrected_category):
                raise ValueError(
                    f"{path.name} MicroText row {offset}: edit requires corrected text or category"
                )
            normalized.append(
                {
                    "reviewer_id": reviewer_id,
                    "reviewer_role": role,
                    "task_type": "microtext",
                    "primary_index": int(machine["primary_index"]),
                    "candidate_id": machine["record_id"],
                    "decision_zh": decision_code,
                    "decision": decision,
                    "corrected_text": corrected_text,
                    "corrected_category": corrected_category,
                    "issue_type": "",
                    "note": "",
                    "source_workbook": path.name,
                }
            )

        for offset, (row, source, machine) in enumerate(
            zip(visual, canonical_visual, visual_machine), start=2
        ):
            _validate_visible_identity(
                row, machine, ULTRA_VISUAL_SUGGESTION, path, offset
            )
            simplest = SIMPLEST_PRIMARY_DECISION in row
            decision_code = _first_value(
                row,
                SIMPLEST_PRIMARY_DECISION,
                ULTRA_VISUAL_DECISION,
                ULTRA_VISUAL_DECISION_LEGACY,
            ).strip().upper()
            decision = _require_decision(
                decision_code,
                SIMPLEST_PRIMARY_VISUAL_MAP if simplest else ULTRA_VISUAL_MAP,
                path,
                "VisualDiff",
                offset,
            )
            description = _first_value(
                row,
                SIMPLEST_VISUAL_DESCRIPTION,
                ULTRA_DESCRIPTION,
                ULTRA_DESCRIPTION_LEGACY,
            ).strip()
            if decision == "edit" and not description:
                raise ValueError(
                    f"{path.name} VisualDiff row {offset}: positive change requires a description"
                )
            if simplest and decision_code == "2":
                original_description = _first_value(
                    source,
                    SIMPLEST_VISUAL_DESCRIPTION,
                    ULTRA_DESCRIPTION,
                    ULTRA_DESCRIPTION_LEGACY,
                ).strip()
                if description == original_description:
                    raise ValueError(
                        f"{path.name} VisualDiff row {offset}: edit requires a corrected description"
                    )
            normalized.append(
                {
                    "reviewer_id": reviewer_id,
                    "reviewer_role": role,
                    "task_type": "visualdiff",
                    "primary_index": int(machine["primary_index"]),
                    "pair_id": machine["record_id"],
                    "decision_zh": decision_code,
                    "decision": decision,
                    "change_description": description if decision == "edit" else "",
                    "note": "",
                    "source_workbook": path.name,
                }
            )
        return normalized

    records = _auditor_records(path)
    canonical_records = _auditor_records(canonical)
    if len(records) != 12 or len(canonical_records) != 12 or len(machine_rows) != 12:
        raise ValueError(f"{path.name}: expected 12 audit rows")
    for row_number, (row, source, machine) in enumerate(
        zip(records, canonical_records, machine_rows), start=7
    ):
        if row.get("#", "").strip() != machine.get("display_index", "").strip():
            raise ValueError(f"{path.name} row {row_number}: display index changed")
        prompt = row.get(ULTRA_AUDIT_PROMPT, "").strip()
        if prompt != source.get(ULTRA_AUDIT_PROMPT, "").strip():
            raise ValueError(f"{path.name} row {row_number}: machine suggestion changed")
        decision_code = _first_value(
            row, ULTRA_VISUAL_DECISION, ULTRA_VISUAL_DECISION_LEGACY
        ).strip().upper()
        answer = _require_decision(
            decision_code, ULTRA_AUDITOR_MAP, path, ultrafast.AUDIT_SHEET, row_number
        )
        task = machine["task"]
        if task == "microtext":
            decision = (
                "pass" if answer == "yes" else "issue" if answer == "no" else answer
            )
            normalized.append(
                {
                    "reviewer_id": reviewer_id,
                    "reviewer_role": role,
                    "task_type": task,
                    "primary_index": int(machine["primary_index"]),
                    "candidate_id": machine["record_id"],
                    "decision_zh": decision_code,
                    "decision": decision,
                    "corrected_text": "",
                    "corrected_category": "",
                    "issue_type": "",
                    "note": "",
                    "source_workbook": path.name,
                }
            )
        elif task == "visualdiff":
            decision = (
                "edit"
                if answer == "yes"
                else "reject_unclear"
                if answer == "no"
                else answer
            )
            normalized.append(
                {
                    "reviewer_id": reviewer_id,
                    "reviewer_role": role,
                    "task_type": task,
                    "primary_index": int(machine["primary_index"]),
                    "pair_id": machine["record_id"],
                    "decision_zh": decision_code,
                    "decision": decision,
                    "change_description": "",
                    "note": "",
                    "source_workbook": path.name,
                }
            )
        else:
            raise ValueError(f"{path.name} row {row_number}: unknown task {task!r}")
    return normalized


def normalize_workbook(path: Path, canonical_dir: Path) -> list[dict[str, Any]]:
    role, reviewer_id = reviewer_role(path)
    canonical = canonical_dir / path.name
    if not canonical.exists():
        raise FileNotFoundError(f"missing canonical workbook: {canonical}")
    if ultrafast.MACHINE_SHEET in _sheet_names(path):
        return normalize_ultrafast_workbook(
            path, canonical, role, reviewer_id
        )
    micro, _ = workbook_io.table_records(path, "MicroText")
    visual, _ = workbook_io.table_records(path, "VisualDiff")
    canonical_micro, _ = workbook_io.table_records(canonical, "MicroText")
    canonical_visual, _ = workbook_io.table_records(canonical, "VisualDiff")
    _compare_stable_rows(
        micro,
        canonical_micro,
        delivery._stable_micro,
        path,
        "MicroText",
    )
    _compare_stable_rows(
        visual,
        canonical_visual,
        delivery._stable_visual,
        path,
        "VisualDiff",
    )

    normalized: list[dict[str, Any]] = []
    micro_mapping = PRIMARY_MICRO_MAP if role == "primary" else AUDITOR_MICRO_MAP
    for offset, row in enumerate(micro, start=2):
        decision_zh = row.get("结论（必填）", "").strip()
        decision = _require_decision(decision_zh, micro_mapping, path, "MicroText", offset)
        corrected_text = row.get("正确文字（仅需修改）", "").strip()
        corrected_category = row.get("正确类别（仅需修改）", "").strip()
        if role == "primary" and decision == "edited" and not (
            corrected_text or corrected_category
        ):
            raise ValueError(
                f"{path.name} MicroText row {offset}: 需修改 requires E or F"
            )
        normalized.append(
            {
                "reviewer_id": reviewer_id,
                "reviewer_role": role,
                "task_type": "microtext",
                "primary_index": int(row["primary_index"]),
                "candidate_id": row["candidate_id"],
                "decision_zh": decision_zh,
                "decision": decision,
                "corrected_text": corrected_text,
                "corrected_category": corrected_category,
                "issue_type": row.get("问题类型（可选）", "").strip(),
                "note": row.get("备注（可选）", "").strip(),
                "source_workbook": path.name,
            }
        )

    for offset, row in enumerate(visual, start=2):
        decision_zh = row.get("结论（必填）", "").strip()
        decision = _require_decision(decision_zh, VISUAL_MAP, path, "VisualDiff", offset)
        description = row.get("变化描述（已预填的只需核对）", "").strip()
        if role == "primary" and decision == "edit" and not description:
            raise ValueError(
                f"{path.name} VisualDiff row {offset}: 有真实变化 requires description"
            )
        normalized.append(
            {
                "reviewer_id": reviewer_id,
                "reviewer_role": role,
                "task_type": "visualdiff",
                "primary_index": int(row["primary_index"]),
                "pair_id": row["pair_id"],
                "decision_zh": decision_zh,
                "decision": decision,
                "change_description": description if decision == "edit" else "",
                "note": row.get("备注（可选）", "").strip(),
                "source_workbook": path.name,
            }
        )
    return normalized


def iter_workbooks(inputs: Iterable[Path]) -> list[Path]:
    paths: list[Path] = []
    for value in inputs:
        if value.is_dir():
            paths.extend(sorted(value.glob("*.xlsx")))
        elif value.is_file():
            paths.append(value)
        else:
            raise FileNotFoundError(value)
    deduplicated = {path.resolve(): path.resolve() for path in paths}
    return sorted(deduplicated.values(), key=lambda path: path.name)


def normalize_returns(
    inputs: Iterable[Path],
    canonical_dir: Path,
    output_jsonl: Path,
    report_json: Path,
) -> dict[str, Any]:
    workbooks = iter_workbooks(inputs)
    if not workbooks:
        raise ValueError("no returned XLSX files found")
    rows: list[dict[str, Any]] = []
    workbook_rows: dict[str, int] = {}
    for workbook in workbooks:
        normalized = normalize_workbook(workbook, canonical_dir)
        rows.extend(normalized)
        workbook_rows[workbook.name] = len(normalized)
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    output_jsonl.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    report = {
        "goal": "Gold v2.0 Global",
        "mode": "normalize_only_no_gold_merge",
        "canonical_dir": canonical_dir.resolve().as_posix(),
        "workbooks": workbook_rows,
        "workbook_count": len(workbooks),
        "rows": len(rows),
        "primary_rows": sum(row["reviewer_role"] == "primary" for row in rows),
        "auditor_rows": sum(row["reviewer_role"] == "auditor" for row in rows),
        "output_jsonl": output_jsonl.resolve().as_posix(),
        "gold_rows_modified": 0,
        "valid": True,
    }
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--canonical-dir", type=Path, required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = normalize_returns(
        args.inputs,
        args.canonical_dir.resolve(),
        args.output_jsonl,
        args.report_json,
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
