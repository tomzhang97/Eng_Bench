from __future__ import annotations

import csv
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from tools import build_flat_current_handoff


def write_packet(root: Path, name: str, rows: int | list[str]) -> Path:
    packet = root / "derived" / "human_adjudication" / name
    packet.mkdir(parents=True)
    (packet / "HUMAN_REVIEW_STEPS.md").write_text("# Steps\n", encoding="utf-8")
    candidate_ids = [f"cand_{index:03d}" for index in range(rows)] if isinstance(rows, int) else rows
    with (packet / "demo_candidates_checklist.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["candidate_id", "review_status"])
        writer.writeheader()
        for candidate_id in candidate_ids:
            writer.writerow({"candidate_id": candidate_id, "review_status": ""})
    review_pack = packet / "review_packs" / "demo"
    review_pack.mkdir(parents=True)
    with (review_pack / "manifest.jsonl").open("w", encoding="utf-8") as f:
        for candidate_id in candidate_ids:
            f.write(json.dumps({"candidate_id": candidate_id, "category": "instrument_tag", "proposed_text": candidate_id}) + "\n")
    (review_pack / "index.html").write_text("<html></html>\n", encoding="utf-8")
    return packet


def test_flat_handoff_preserves_packets_and_has_no_nested_zips(tmp_path: Path) -> None:
    first = write_packet(tmp_path, "2026-06-16_first_review", ["first_000", "first_001"])
    second = write_packet(tmp_path, "2026-06-16_second_review", ["second_000", "second_001", "second_002"])
    output_dir = tmp_path / "derived" / "human_adjudication" / "2026-06-23_flat"
    zip_path = tmp_path / "derived" / "human_adjudication" / "Eng_Bench_v2_0_human_review_2026-06-23.zip"

    report = build_flat_current_handoff.build_package(
        tmp_path,
        date_label="2026-06-23",
        packets=[
            build_flat_current_handoff.PacketSpec("first", first.relative_to(tmp_path).as_posix(), 1, "First packet."),
            build_flat_current_handoff.PacketSpec("second", second.relative_to(tmp_path).as_posix(), 2, "Second packet."),
        ],
        output_dir=output_dir,
        zip_path=zip_path,
    )

    assert report["valid"] is True
    assert report["totals"]["packets"] == 2
    assert report["totals"]["checklist_rows"] == 5
    assert report["zip"]["nested_zip_entries"] == 0
    assert (output_dir / "README_FIRST.md").exists()
    assert (output_dir / "P" / "p01" / "HUMAN_REVIEW_STEPS.md").exists()
    assert (output_dir / "P" / "p02" / "review_packs" / "demo" / "index.html").exists()

    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        assert archive.testzip() is None
    assert all(not name.lower().endswith(".zip") for name in names)
    assert any(name.endswith("README_FIRST.md") for name in names)
    assert any(name.endswith("P/p01/demo_candidates_checklist.csv") for name in names)
    assert report["zip"]["max_internal_path_length"] <= 220
    readme = (output_dir / "README_FIRST.md").read_text(encoding="utf-8")
    assert "image_path" in readme
    assert "provenance metadata" in readme


def test_flat_handoff_rejects_packet_without_review_instructions(tmp_path: Path) -> None:
    packet = tmp_path / "derived" / "human_adjudication" / "bad_packet"
    packet.mkdir(parents=True)
    with (packet / "demo_candidates_checklist.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["candidate_id", "review_status"])
        writer.writeheader()
        writer.writerow({"candidate_id": "cand_001", "review_status": ""})

    try:
        build_flat_current_handoff.build_package(
            tmp_path,
            date_label="2026-06-23",
            packets=[build_flat_current_handoff.PacketSpec("bad", packet.relative_to(tmp_path).as_posix(), 1, "Bad packet.")],
            output_dir=tmp_path / "out",
            zip_path=tmp_path / "out.zip",
        )
    except ValueError as exc:
        assert "HUMAN_REVIEW_STEPS.md" in str(exc)
    else:
        raise AssertionError("packet without review instructions should be rejected")


def test_flat_handoff_removes_later_duplicate_candidate_ids(tmp_path: Path) -> None:
    first = write_packet(tmp_path, "2026-06-16_first_review", ["shared", "first_only"])
    second = write_packet(tmp_path, "2026-06-16_second_review", ["shared", "second_only"])
    output_dir = tmp_path / "flat"
    zip_path = tmp_path / "flat.zip"

    report = build_flat_current_handoff.build_package(
        tmp_path,
        date_label="2026-06-23",
        packets=[
            build_flat_current_handoff.PacketSpec("first", first.relative_to(tmp_path).as_posix(), 1, "First packet."),
            build_flat_current_handoff.PacketSpec("second", second.relative_to(tmp_path).as_posix(), 2, "Second packet."),
        ],
        output_dir=output_dir,
        zip_path=zip_path,
    )

    with (output_dir / "P" / "p02" / "demo_candidates_checklist.csv").open(encoding="utf-8-sig", newline="") as f:
        second_rows = list(csv.DictReader(f))
    assert [row["candidate_id"] for row in second_rows] == ["second_only"]
    assert report["totals"]["checklist_rows"] == 3
    assert report["totals"]["deduplicated_rows_removed"] == 1


def test_flat_handoff_cli_help_runs_from_repo_root() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "tools" / "build_flat_current_handoff.py"), "--help"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Build a flat, self-contained Eng_Bench human review handoff ZIP." in result.stdout
