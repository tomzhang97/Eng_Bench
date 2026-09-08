#!/usr/bin/env python3
"""Build a clean human handoff ZIP for the selected VisualDiff tranche."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from tools.export_review_packs import write_visualdiff_index
except ModuleNotFoundError:
    from export_review_packs import write_visualdiff_index


EVIDENCE_FIELDS = (
    ("old_crop_path", "old"),
    ("new_crop_path", "new"),
    ("panel_path", "panels"),
    ("old_page_path", "pages_old"),
    ("new_page_path", "pages_new"),
)

COMBINED_PREFIX_FIELDS = [
    "packet_id",
    "review_pack",
    "pack_index_html",
    "packet_priority",
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def selected_visualdiff_packets(tranche_csv: Path) -> list[dict[str, str]]:
    fields, rows = read_csv(tranche_csv)
    _ = fields
    selected = [row for row in rows if row.get("kind") == "visualdiff" and as_int(row.get("suggested_rows")) > 0]
    return selected


def checklist_path(pack_dir: Path) -> Path:
    candidates = sorted(pack_dir.glob("*validation_checklist.csv")) or sorted(pack_dir.glob("*checklist*.csv"))
    if not candidates:
        raise ValueError(f"missing checklist CSV in {pack_dir}")
    return candidates[0]


def pair_id(row: dict[str, Any]) -> str:
    return str(row.get("pair_id") or row.get("id") or "").strip()


def resolve_evidence(root: Path, source_pack: Path, value: str) -> Path | None:
    if not value:
        return None
    raw = Path(value.replace("\\", "/"))
    candidates = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.extend([source_pack / raw, root / raw, source_pack / raw.name])
    for path in candidates:
        if path.exists() and path.is_file():
            return path
    return None


def copy_selected_evidence(
    *,
    root: Path,
    source_pack: Path,
    dest_pack: Path,
    row: dict[str, Any],
) -> int:
    missing = 0
    for field, folder in EVIDENCE_FIELDS:
        value = str(row.get(field) or "")
        source = resolve_evidence(root, source_pack, value)
        target_name = Path(value.replace("\\", "/")).name
        if not target_name and source:
            target_name = source.name
        if not target_name:
            missing += 1
            continue
        target_rel = Path(folder) / target_name
        target = dest_pack / target_rel
        if source is None:
            missing += 1
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(source, target)
        row[field] = target_rel.as_posix()
    return missing


def filter_checklist_rows(
    *,
    checklist_fields: list[str],
    checklist_rows: list[dict[str, str]],
    selected_manifest: list[dict[str, Any]],
    packet_id: str,
    dest_pack_name: str,
    packet_priority: str,
) -> tuple[list[str], list[dict[str, str]], list[dict[str, str]]]:
    selected_by_pair = {pair_id(row): row for row in selected_manifest}
    retained: list[dict[str, str]] = []
    combined: list[dict[str, str]] = []
    for row in checklist_rows:
        row_pair_id = str(row.get("pair_id") or "").strip()
        if row_pair_id not in selected_by_pair:
            continue
        out = dict(row)
        out["review_index"] = str(len(retained) + 1)
        manifest_row = selected_by_pair[row_pair_id]
        for field, _folder in EVIDENCE_FIELDS:
            if field in checklist_fields:
                out[field] = str(manifest_row.get(field) or "")
        retained.append(out)
        combined_row = {
            "packet_id": packet_id,
            "review_pack": dest_pack_name,
            "pack_index_html": f"review_packs/{dest_pack_name}/index.html",
            "packet_priority": packet_priority,
        }
        combined_row.update(out)
        combined.append(combined_row)
    if len(retained) != len(selected_manifest):
        missing = sorted(set(selected_by_pair) - {str(row.get("pair_id") or "") for row in retained})
        raise ValueError(f"checklist rows do not cover selected manifest rows for {packet_id}: {missing[:3]}")
    return checklist_fields, retained, combined


def copy_filtered_pack(
    *,
    root: Path,
    packet_row: dict[str, str],
    output_dir: Path,
) -> dict[str, Any]:
    packet_id = str(packet_row.get("packet_id") or "")
    source_pack = Path(str(packet_row.get("folder_path") or ""))
    if not source_pack.is_absolute():
        source_pack = root / source_pack
    if not source_pack.is_dir():
        raise ValueError(f"missing selected review pack: {source_pack}")
    source_manifest = source_pack / "manifest.jsonl"
    if not source_manifest.exists():
        raise ValueError(f"missing manifest.jsonl: {source_pack}")
    selected_limit = as_int(packet_row.get("suggested_rows"))
    manifest_rows = read_jsonl(source_manifest)
    selected_manifest = [dict(row) for row in manifest_rows[:selected_limit]]
    if not selected_manifest:
        raise ValueError(f"no selected manifest rows for {packet_id}")

    dest_pack = output_dir / "review_packs" / source_pack.name
    dest_pack.mkdir(parents=True, exist_ok=True)
    missing_evidence = 0
    for row in selected_manifest:
        missing_evidence += copy_selected_evidence(
            root=root,
            source_pack=source_pack,
            dest_pack=dest_pack,
            row=row,
        )
    write_jsonl(dest_pack / "manifest.jsonl", selected_manifest)

    source_checklist = checklist_path(source_pack)
    checklist_fields, checklist_rows = read_csv(source_checklist)
    fields, retained_checklist, combined_rows = filter_checklist_rows(
        checklist_fields=checklist_fields,
        checklist_rows=checklist_rows,
        selected_manifest=selected_manifest,
        packet_id=packet_id,
        dest_pack_name=source_pack.name,
        packet_priority=str(packet_row.get("priority") or ""),
    )
    dest_checklist = dest_pack / source_checklist.name
    write_csv(dest_checklist, fields, retained_checklist)
    write_visualdiff_index(dest_pack, selected_manifest)
    (dest_pack / "README.md").write_text(
        "Review this filtered VisualDiff pack through index.html and the validation checklist CSV.\n",
        encoding="utf-8",
    )
    return {
        "packet_id": packet_id,
        "review_pack": source_pack.name,
        "source_folder": str(source_pack.relative_to(root)).replace("\\", "/")
        if source_pack.is_relative_to(root)
        else str(source_pack),
        "selected_rows": len(selected_manifest),
        "manifest_rows_source": len(manifest_rows),
        "checklist_rows": len(retained_checklist),
        "missing_evidence_refs": missing_evidence,
        "index_html": f"review_packs/{source_pack.name}/index.html",
        "checklist_csv": f"review_packs/{source_pack.name}/{source_checklist.name}",
        "combined_rows": combined_rows,
    }


def combined_fields(rows: list[dict[str, str]]) -> list[str]:
    fields = list(COMBINED_PREFIX_FIELDS)
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    return fields


def write_human_steps(output_dir: Path, *, date_label: str, totals: dict[str, Any]) -> None:
    text = f"""# Eng_Bench VisualDiff 家族解锁人工验证包

日期: {date_label}

目标: 帮 Eng_Bench 冲刺 Gold v2.0 Global。当前 active VisualDiff revision families 只有 3/30；这个包里的 VisualDiff 优先行如果被接受并合并，可以把该 gate 预计推进到 31/30。

## 你需要验证多少

- review packs: {totals['packet_count']}
- VisualDiff rows: {totals['selected_rows']}
- 只看本文件夹里的内容，不需要打开旧 zip。

## 操作步骤

1. 解压整个 zip 到普通文件夹，不要直接在压缩包里编辑。
2. 打开 `PACKET_INDEX.csv`，按 priority 顺序处理。
3. 对每个 pack，打开 `review_packs/<pack_name>/index.html` 看 old/new panel 和 full-page link。
4. 在对应的 `*_validation_checklist.csv` 里填写 `human_status`、`human_description`、`human_notes`。
5. 也可以直接填写根目录的 `visualdiff_selected_rows_validation_checklist.csv`，它汇总了全部 100 行。
6. 不要改 `manifest.jsonl`、图片、HTML、pair_id、文件夹名。

## human_status 怎么填

- `accepted`: old/new 图片里确实有可见的工程图变化，而且变化发生在 crop/panel 里；`human_description` 写一句准确描述。
- `edited`: 这个样本可用，但机器的变化类型或描述不准；`human_description` 写正确描述，`human_notes` 说明哪里改了。
- `rejected`: 完全一样、只是截图/配准偏移、变化在框外、看不清、不是工程图变化、重复或无效。
- `needs_full_page`: crop 不够判断，但 full page 看起来可能能判断；备注写需要看哪一侧或哪一页。

## 重要判断规则

- 两张图完全一样: `rejected`，备注写 `old/new identical, no visible engineering change`。
- 只有一丁点整体偏移、截图错位、扫描配准误差: `rejected`，备注写 `registration/crop offset only`。
- 真实工程元素的位置变化，例如标签、符号、线段、尺寸位置真的改变: 可以 `accepted` 或 `edited`，描述为 layout/position change。
- 差异在红框或 crop 外，框内一样: `rejected`，备注写 `change outside crop`。
- 如果 crop 少字母但 full page 能确认文字，样本仍可用；状态通常填 `accepted` 或 `edited`，并在备注说明 `crop partial but full page confirms`。

## 返回给我们

返回填好的 CSV 文件即可，最好保持文件名不变。不要重新生成 JSONL，也不要合并进 gold。
"""
    output_dir.joinpath("HUMAN_REVIEW_STEPS_ZH.md").write_text(text, encoding="utf-8")
    output_dir.joinpath("README.md").write_text(
        "Start with HUMAN_REVIEW_STEPS_ZH.md. This package contains only selected VisualDiff rows for human review.\n",
        encoding="utf-8",
    )


def write_packet_index(output_dir: Path, packet_reports: list[dict[str, Any]]) -> None:
    fields = [
        "priority",
        "packet_id",
        "review_pack",
        "selected_rows",
        "manifest_rows_source",
        "checklist_rows",
        "missing_evidence_refs",
        "index_html",
        "checklist_csv",
        "source_folder",
    ]
    rows = []
    for index, row in enumerate(packet_reports, start=1):
        out = {field: row.get(field, "") for field in fields}
        out["priority"] = index
        rows.append(out)
    write_csv(output_dir / "PACKET_INDEX.csv", fields, rows)


def copy_control_files(
    *,
    output_dir: Path,
    tranche_csv: Path,
    family_report_json: Path | None,
) -> None:
    control = output_dir / "control"
    control.mkdir(parents=True, exist_ok=True)
    shutil.copy2(tranche_csv, control / tranche_csv.name)
    if family_report_json and family_report_json.exists():
        shutil.copy2(family_report_json, control / family_report_json.name)
        for suffix in (".md", ".csv"):
            sibling = family_report_json.with_suffix(suffix)
            if sibling.exists():
                shutil.copy2(sibling, control / sibling.name)


def make_zip(output_dir: Path, zip_path: Path) -> dict[str, Any]:
    if zip_path.exists():
        zip_path.unlink()
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    root_name = output_dir.name
    entries = 0
    nested_zips = 0
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(output_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() == ".zip":
                nested_zips += 1
                continue
            arcname = Path(root_name) / path.relative_to(output_dir)
            zf.write(path, arcname.as_posix())
            entries += 1
    with zipfile.ZipFile(zip_path, "r") as zf:
        bad_file = zf.testzip()
    return {
        "zip_path": str(zip_path),
        "zip_entries": entries,
        "nested_zips_skipped": nested_zips,
        "zip_test_bad_file": bad_file or "",
        "zip_size_bytes": zip_path.stat().st_size,
        "zip_sha256": sha256_file(zip_path),
    }


def build_handoff(
    *,
    root: Path,
    tranche_csv: Path,
    family_report_json: Path | None,
    output_dir: Path,
    zip_path: Path,
    date_label: str,
) -> dict[str, Any]:
    root = root.resolve()
    tranche_csv = tranche_csv if tranche_csv.is_absolute() else root / tranche_csv
    if family_report_json is not None and not family_report_json.is_absolute():
        family_report_json = root / family_report_json
    output_dir = output_dir if output_dir.is_absolute() else root / output_dir
    zip_path = zip_path if zip_path.is_absolute() else root / zip_path
    reset_dir(output_dir)

    packet_rows = selected_visualdiff_packets(tranche_csv)
    packet_reports: list[dict[str, Any]] = []
    combined_rows: list[dict[str, str]] = []
    for row in packet_rows:
        report = copy_filtered_pack(root=root, packet_row=row, output_dir=output_dir)
        packet_reports.append(report)
        combined_rows.extend(report.pop("combined_rows"))

    totals = {
        "packet_count": len(packet_reports),
        "selected_rows": sum(int(row["selected_rows"]) for row in packet_reports),
        "checklist_rows": sum(int(row["checklist_rows"]) for row in packet_reports),
        "missing_evidence_refs": sum(int(row["missing_evidence_refs"]) for row in packet_reports),
    }
    family_summary = read_json(family_report_json) if family_report_json and family_report_json.exists() else {}
    write_human_steps(output_dir, date_label=date_label, totals=totals)
    write_packet_index(output_dir, packet_reports)
    if combined_rows:
        write_csv(
            output_dir / "visualdiff_selected_rows_validation_checklist.csv",
            combined_fields(combined_rows),
            combined_rows,
        )
    copy_control_files(output_dir=output_dir, tranche_csv=tranche_csv, family_report_json=family_report_json)
    report = {
        "date_label": date_label,
        "output_dir": str(output_dir),
        "tranche_csv": str(tranche_csv),
        "family_report_json": str(family_report_json or ""),
        "totals": totals,
        "family_unlock_totals": family_summary.get("totals", {}),
        "packets": packet_reports,
    }
    zip_report = make_zip(output_dir, zip_path)
    report["zip"] = zip_report
    write_json(output_dir / "handoff_manifest.json", report)
    zip_report = make_zip(output_dir, zip_path)
    report["zip"] = zip_report
    write_json(output_dir / "handoff_manifest.json", report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--tranche-csv", type=Path, required=True)
    parser.add_argument("--family-report-json", type=Path)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--zip-path", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_handoff(
        root=args.root,
        tranche_csv=args.tranche_csv,
        family_report_json=args.family_report_json,
        output_dir=args.output_dir,
        zip_path=args.zip_path,
        date_label=args.date_label,
    )
    print(
        json.dumps(
            {
                "output_dir": report["output_dir"],
                "zip_path": report["zip"]["zip_path"],
                "packet_count": report["totals"]["packet_count"],
                "selected_rows": report["totals"]["selected_rows"],
                "missing_evidence_refs": report["totals"]["missing_evidence_refs"],
                "zip_entries": report["zip"]["zip_entries"],
                "zip_test_bad_file": report["zip"]["zip_test_bad_file"],
                "zip_sha256": report["zip"]["zip_sha256"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
