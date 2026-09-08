#!/usr/bin/env python3
"""Create a reviewer-minimal package shell from a verified multi-review batch."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


ROOT_FILES = (
    "NEXT_REVIEW_BATCH_MANIFEST.csv",
    "next_review_batch_build_report.json",
    "next_review_batch_build_report.md",
)

CONTROL_FILES = (
    "audit_assignments.csv",
    "return_manifest.csv",
    "workbook_payload.json",
    "multi_reviewer_build_report.json",
    "multi_reviewer_build_report.md",
)


def safe_output(source: Path, output: Path) -> None:
    source = source.resolve()
    output = output.resolve()
    if source == output:
        raise ValueError("output batch must differ from source batch")
    if output.is_relative_to(source):
        raise ValueError("output batch must not be inside source batch")


def copy_required(source: Path, output: Path, relative: str) -> None:
    src = source / relative
    if not src.exists():
        raise FileNotFoundError(f"missing source artifact: {src}")
    dst = output / relative
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)


def build_package(source: Path, output: Path, overwrite: bool = False) -> dict[str, object]:
    source = source.resolve()
    output = output.resolve()
    safe_output(source, output)
    if not source.is_dir():
        raise FileNotFoundError(source)
    if output.exists():
        if not overwrite:
            raise FileExistsError(f"output already exists: {output}")
        shutil.rmtree(output)
    output.mkdir(parents=True)

    copy_required(source, output, "review_packs")
    for name in ROOT_FILES:
        copy_required(source, output, name)

    control = output / "03_MACHINE_CONTROL"
    control.mkdir()
    for name in CONTROL_FILES:
        copy_required(source, output, f"03_MACHINE_CONTROL/{name}")

    source_tables = control / "source_tables"
    source_tables.mkdir()
    copy_required(
        source,
        output,
        "01_PRIMARY_REVIEWER/PRIMARY_MICROTEXT_SOURCE.csv",
    )
    copy_required(
        source,
        output,
        "01_PRIMARY_REVIEWER/PRIMARY_VISUALDIFF_SOURCE.csv",
    )
    shutil.move(
        output / "01_PRIMARY_REVIEWER" / "PRIMARY_MICROTEXT_SOURCE.csv",
        source_tables / "PRIMARY_MICROTEXT_SOURCE.csv",
    )
    shutil.move(
        output / "01_PRIMARY_REVIEWER" / "PRIMARY_VISUALDIFF_SOURCE.csv",
        source_tables / "PRIMARY_VISUALDIFF_SOURCE.csv",
    )
    (output / "01_PRIMARY_REVIEWER").rmdir()

    for number in range(1, 11):
        auditor = f"auditor_{number:02d}"
        for task in ("MICROTEXT", "VISUALDIFF"):
            relative = (
                f"02_INDEPENDENT_AUDITORS/{auditor}/"
                f"AUDITOR_{number:02d}_{task}_SOURCE.csv"
            )
            copy_required(source, output, relative)
            destination = source_tables / auditor / Path(relative).name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(output / relative, destination)
        (output / "02_INDEPENDENT_AUDITORS" / auditor).rmdir()
    (output / "02_INDEPENDENT_AUDITORS").rmdir()

    primary_dir = output / "01_PRIMARY_REVIEWER"
    primary_dir.mkdir()
    (primary_dir / "OPEN_THIS_WORKBOOK_ZH.txt").write_text(
        "只需要打开 PRIMARY_REVIEW_498.xlsx。图片已经放在 Excel 里面。\n"
        "只填写黄色列，完成后只交回这个 Excel，不要改文件名。\n",
        encoding="utf-8",
    )

    auditors_dir = output / "02_INDEPENDENT_AUDITORS"
    auditors_dir.mkdir()
    for number in range(1, 11):
        auditor = f"auditor_{number:02d}"
        folder = auditors_dir / auditor
        folder.mkdir()
        (folder / "OPEN_THIS_WORKBOOK_ZH.txt").write_text(
            f"只需要打开 AUDITOR_{number:02d}_REVIEW_12.xlsx。图片已经放在 Excel 里面。\n"
            "只填写黄色列，完成后只交回这个 Excel，不要改文件名。\n"
            "请独立判断，不要查看主审核员或其他复核员的答案。\n",
            encoding="utf-8",
        )

    start = """Eng_Bench Gold v2.0 Global - 最简人工审核包

你只需要做下面几件事：

1. 完整解压 ZIP。
2. 主审核员/实习生打开：01_PRIMARY_REVIEWER/PRIMARY_REVIEW_498.xlsx
3. 十位复核员分别打开自己编号文件夹中的 AUDITOR_NN_REVIEW_12.xlsx。
4. 图片已经嵌入 Excel。只填写黄色列，不需要打开 CSV 或 HTML。
5. 不确定时，复制 Excel 中的整页路径到文件资源管理器打开。
6. 完成后，每人只交回自己的 Excel，文件名不要修改。

工作量：
- 主审核员/实习生：498 条（369 MicroText + 129 VisualDiff）。
- 每位复核员：12 条（9 MicroText + 3 VisualDiff）。
- 十位复核员必须独立工作，不要查看或复制主审核员及其他人的答案。

详细判断规则、进度统计和完成检查都已经放在每个 Excel 的“开始”工作表中。
"""
    (output / "00_START_HERE_ZH.txt").write_text(start, encoding="utf-8")
    (output / "README.md").write_text(
        """# Eng_Bench Simple Primary + Audit Handoff

Start with `00_START_HERE_ZH.txt`.

Each reviewer opens exactly one image-embedded workbook. Human-facing folders
contain no CSV or HTML controls. Evidence assets remain local so full-page links
work after complete extraction. No row is promoted to gold by this package.
""",
        encoding="utf-8",
    )
    (output / "HUMAN_REVIEW_STEPS.md").write_text(
        """# Human Review Steps

1. Extract the ZIP fully.
2. Open only the workbook assigned to the reviewer.
3. Review the embedded image and fill only yellow cells.
4. Resolve every temporary full-page decision before returning.
5. Return only the unchanged workbook filename.

Complete Chinese instructions are inside each workbook and in
`00_START_HERE_ZH.txt`.
""",
        encoding="utf-8",
    )
    (output / "WHO_DOES_WHAT_ZH.md").write_text(
        "\n".join(
            [
                "# 分工表",
                "",
                "| 人员 | 数量 | 只打开这个文件 |",
                "|---|---:|---|",
                "| 主审核员/实习生 | 498 | `01_PRIMARY_REVIEWER/PRIMARY_REVIEW_498.xlsx` |",
                *[
                    f"| 复核员 {number:02d} | 12 | `02_INDEPENDENT_AUDITORS/auditor_{number:02d}/AUDITOR_{number:02d}_REVIEW_12.xlsx` |"
                    for number in range(1, 11)
                ],
                "",
            ]
        ),
        encoding="utf-8",
    )

    report = {
        "goal": "Gold v2.0 Global",
        "source_batch": source.as_posix(),
        "output_batch": output.as_posix(),
        "primary_rows": 498,
        "auditors": 10,
        "rows_per_auditor": 12,
        "visible_workbooks_expected": 11,
        "reviewer_visible_csv_files": 0,
        "reviewer_visible_html_files": 0,
        "embedded_images_expected": 618,
        "gold_rows_modified": 0,
        "valid": True,
    }
    (control / "simple_package_build_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-batch", type=Path, required=True)
    parser.add_argument("--output-batch", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_package(args.source_batch, args.output_batch, args.overwrite)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
