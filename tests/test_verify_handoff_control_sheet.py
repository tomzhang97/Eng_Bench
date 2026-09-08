from __future__ import annotations

import csv
import hashlib
import json
import zipfile
from pathlib import Path

from tools import verify_handoff_control_sheet


def write_zip(path: Path, entries: dict[str, str]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, text in entries.items():
            zf.writestr(name, text)
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def write_control(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "zip",
                "assigned_paths",
                "mb",
                "entries",
                "crc_ok",
                "windows_expand_archive",
                "sha256",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def test_verify_control_sheet_accepts_matching_zip_hashes(tmp_path: Path) -> None:
    zip_dir = tmp_path / "zips"
    sha = write_zip(zip_dir / "part01.zip", {"README.md": "ok"})
    control = tmp_path / "control.csv"
    write_control(
        control,
        [
            {
                "zip": "part01.zip",
                "assigned_paths": "01_packets/p01",
                "mb": "1.0",
                "entries": "1",
                "crc_ok": "True",
                "windows_expand_archive": "ok",
                "sha256": sha,
            }
        ],
    )

    report = verify_handoff_control_sheet.verify_control_sheet(
        root=tmp_path,
        control_csv=control,
        zip_dir=zip_dir,
    )

    assert report["valid"] is True
    assert report["issues"] == []
    assert report["totals"]["zip_rows"] == 1
    assert report["totals"]["hash_matches"] == 1
    assert report["zips"][0]["crc_ok"] is True
    assert report["zips"][0]["nested_zip_entries"] == 0


def test_verify_control_sheet_rejects_missing_hash_mismatch_and_nested_zip(tmp_path: Path) -> None:
    zip_dir = tmp_path / "zips"
    nested_sha = write_zip(zip_dir / "part02.zip", {"nested.zip": "not allowed"})
    control = tmp_path / "control.csv"
    write_control(
        control,
        [
            {
                "zip": "part01.zip",
                "assigned_paths": "01_packets/p01",
                "mb": "1.0",
                "entries": "1",
                "crc_ok": "True",
                "windows_expand_archive": "ok",
                "sha256": "BADHASH",
            },
            {
                "zip": "part02.zip",
                "assigned_paths": "02_agreement",
                "mb": "1.0",
                "entries": "1",
                "crc_ok": "True",
                "windows_expand_archive": "ok",
                "sha256": nested_sha,
            },
        ],
    )

    report = verify_handoff_control_sheet.verify_control_sheet(
        root=tmp_path,
        control_csv=control,
        zip_dir=zip_dir,
    )

    assert report["valid"] is False
    joined = "\n".join(report["issues"])
    assert "missing zip" in joined
    assert "nested zip entries" in joined
    assert report["totals"]["missing_zips"] == 1
    assert report["totals"]["nested_zip_entries"] == 1


def test_verify_control_sheet_cli_writes_reports(tmp_path: Path) -> None:
    zip_dir = tmp_path / "zips"
    sha = write_zip(zip_dir / "part01.zip", {"README.md": "ok"})
    control = tmp_path / "control.csv"
    out_json = tmp_path / "report.json"
    out_md = tmp_path / "report.md"
    write_control(
        control,
        [
            {
                "zip": "part01.zip",
                "assigned_paths": "01_packets/p01",
                "mb": "1.0",
                "entries": "1",
                "crc_ok": "True",
                "windows_expand_archive": "ok",
                "sha256": sha,
            }
        ],
    )

    exit_code = verify_handoff_control_sheet.main(
        [
            "--root",
            str(tmp_path),
            "--control-csv",
            str(control),
            "--zip-dir",
            str(zip_dir),
            "--output-json",
            str(out_json),
            "--output-md",
            str(out_md),
        ]
    )

    assert exit_code == 0
    assert json.loads(out_json.read_text(encoding="utf-8"))["valid"] is True
    assert "Human Handoff Control Verification" in out_md.read_text(encoding="utf-8")
