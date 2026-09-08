from __future__ import annotations

import csv
import json
from pathlib import Path

from tools import build_handoff_control_sheet


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_control_sheet_summarizes_verified_zips_and_return_plan(tmp_path: Path) -> None:
    compatible = tmp_path / "derived" / "quality" / "compatible_handoff_verification_2026-06-15.json"
    split = tmp_path / "derived" / "quality" / "split_handoff_verification_2026-06-15.json"
    plan = tmp_path / "derived" / "quality" / "compatible_return_processing_plan_2026-06-15.json"
    write_json(
        compatible,
        {
            "handoff_dir": {
                "valid": True,
                "path": "derived/human_adjudication/hv_0615_compat",
                "packet_folders": 11,
                "worklist_review_rows": 2158,
                "totals": {"checklist_rows": 2289, "png_files": 3308},
            },
            "split_dir": {"valid": True, "zip_count": 2},
        },
    )
    write_json(
        split,
        {
            "zip_count": 2,
            "all_crc_ok": True,
            "all_windows_expand_archive_ok": True,
            "zips": [
                {
                    "zip": "part01.zip",
                    "assigned_paths": "01_packets/p01",
                    "mb": 70.0,
                    "entries": 100,
                    "crc_ok": True,
                    "windows_expand_archive": "ok",
                    "sha256": "abc",
                },
                {
                    "zip": "part02.zip",
                    "assigned_paths": "02_agreement",
                    "mb": 12.5,
                    "entries": 20,
                    "crc_ok": True,
                    "windows_expand_archive": "ok",
                    "sha256": "def",
                },
            ],
        },
    )
    write_json(
        plan,
        {
            "totals": {
                "steps": 15,
                "packets": 11,
                "processable_packets": 11,
                "uncovered_checklists": 0,
            },
            "steps": [
                {
                    "packet": "all",
                    "kind": "preflight",
                    "command": "python tools\\review_packet_status.py --handoff-root X",
                },
                {
                    "packet": "agreement",
                    "kind": "agreement_audit",
                    "command": "python tools\\agreement_audit.py --reference A",
                },
            ],
        },
    )

    report = build_handoff_control_sheet.build_report(
        root=tmp_path,
        compatible_report=compatible,
        split_report=split,
        return_plan=plan,
    )

    assert report["ready_to_send"] is True
    assert report["review_rows"] == 2158
    assert report["packet_count"] == 11
    assert report["split_zip_count"] == 2
    assert report["return_plan_steps"] == 15
    assert report["return_plan_uncovered_checklists"] == 0
    assert report["zips"][0]["sha256"] == "abc"
    assert report["returned_handoff_root_placeholder"] == "<returned_handoff_root>"
    assert "build_compatible_return_plan.py" in report["safe_return_commands"][0]["command"]
    assert "<returned_handoff_root>" in report["safe_return_commands"][0]["command"]
    assert "<return_label>" in report["safe_return_commands"][0]["command"]
    assert report["next_return_commands"][0]["kind"] == "preflight"
    assert report["next_return_commands"][1]["kind"] == "agreement_audit"


def test_control_sheet_cli_writes_markdown_and_csv(tmp_path: Path) -> None:
    compatible = tmp_path / "compatible.json"
    split = tmp_path / "split.json"
    plan = tmp_path / "plan.json"
    out_md = tmp_path / "control.md"
    out_csv = tmp_path / "control.csv"
    write_json(
        compatible,
        {
            "handoff_dir": {
                "valid": True,
                "path": "derived/human_adjudication/hv_0615_compat",
                "packet_folders": 1,
                "worklist_review_rows": 3,
                "totals": {"checklist_rows": 3, "png_files": 4},
            }
        },
    )
    write_json(
        split,
        {
            "all_crc_ok": True,
            "all_windows_expand_archive_ok": True,
            "zips": [
                {
                    "zip": "part01.zip",
                    "assigned_paths": "01_packets/p01",
                    "mb": 1,
                    "entries": 2,
                    "crc_ok": True,
                    "windows_expand_archive": "ok",
                    "sha256": "abc",
                }
            ],
        },
    )
    write_json(
        plan,
        {
            "totals": {"steps": 1, "packets": 1, "processable_packets": 1, "uncovered_checklists": 0},
            "steps": [{"packet": "all", "kind": "preflight", "command": "python tools\\x.py"}],
        },
    )

    exit_code = build_handoff_control_sheet.main(
        [
            "--root",
            str(tmp_path),
            "--compatible-report",
            str(compatible),
            "--split-report",
            str(split),
            "--return-plan",
            str(plan),
            "--output-md",
            str(out_md),
            "--output-csv",
            str(out_csv),
        ]
    )

    assert exit_code == 0
    markdown = out_md.read_text(encoding="utf-8")
    assert "Current Human Handoff Control Sheet" in markdown
    assert "part01.zip" in markdown
    assert "<returned_handoff_root>" in markdown
    assert "build_compatible_return_plan.py" in markdown
    assert "Do not run the reference commands on the unfilled handoff folder" in markdown
    with out_csv.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["zip"] == "part01.zip"
    assert rows[0]["sha256"] == "abc"
