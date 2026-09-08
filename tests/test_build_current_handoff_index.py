from __future__ import annotations

import csv
import zipfile
from pathlib import Path

from tools import build_current_handoff_index


def write_checklist(path: Path, rows: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["candidate_id", "review_status"])
        writer.writeheader()
        for index in range(rows):
            writer.writerow({"candidate_id": f"cand_{index:03d}", "review_status": ""})


def make_packet(root: Path, name: str, zip_name: str, checklist_rows: tuple[int, int]) -> None:
    packet = root / "derived" / "human_adjudication" / name
    write_checklist(packet / "first_checklist.csv", checklist_rows[0])
    write_checklist(packet / "second_checklist.csv", checklist_rows[1])
    (packet / "HUMAN_REVIEW_STEPS.md").write_text("# Steps\n", encoding="utf-8")
    zip_path = root / "derived" / "human_adjudication" / zip_name
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(packet / "HUMAN_REVIEW_STEPS.md", f"{name}/HUMAN_REVIEW_STEPS.md")
        z.write(packet / "first_checklist.csv", f"{name}/first_checklist.csv")
        z.write(packet / "second_checklist.csv", f"{name}/second_checklist.csv")


def test_index_summarizes_packet_roots_and_zips(tmp_path: Path) -> None:
    make_packet(
        tmp_path,
        "2026-06-16_demo_review",
        "Eng_Bench_v2_0_demo_review_2026-06-16.zip",
        (3, 4),
    )

    report = build_current_handoff_index.build_report(
        tmp_path,
        date_label="2026-06-16",
        packets=[
            build_current_handoff_index.PacketSpec(
                packet_id="demo",
                packet_root="derived/human_adjudication/2026-06-16_demo_review",
                zip_path="derived/human_adjudication/Eng_Bench_v2_0_demo_review_2026-06-16.zip",
                priority=1,
                notes="Demo packet.",
            )
        ],
    )

    assert report["totals"]["packets"] == 1
    assert report["totals"]["checklist_rows"] == 7
    assert report["totals"]["zip_count"] == 1
    assert report["totals"]["nested_zip_entries"] == 0
    assert report["packets"][0]["ready_to_send"] is True
    assert report["packets"][0]["checklist_files"] == 2
    assert report["packets"][0]["checklist_rows"] == 7


def test_cli_writes_index_outputs(tmp_path: Path) -> None:
    make_packet(
        tmp_path,
        "2026-06-16_demo_review",
        "Eng_Bench_v2_0_demo_review_2026-06-16.zip",
        (1, 2),
    )
    out_dir = tmp_path / "index"
    out_json = tmp_path / "index.json"
    out_md = tmp_path / "index.md"
    out_csv = tmp_path / "index.csv"

    exit_code = build_current_handoff_index.main(
        [
            "--root",
            str(tmp_path),
            "--date-label",
            "2026-06-16",
            "--packet",
            "demo=derived/human_adjudication/2026-06-16_demo_review=derived/human_adjudication/Eng_Bench_v2_0_demo_review_2026-06-16.zip",
            "--output-dir",
            str(out_dir),
            "--output-json",
            str(out_json),
            "--output-md",
            str(out_md),
            "--output-csv",
            str(out_csv),
        ]
    )

    assert exit_code == 0
    assert out_json.exists()
    assert out_md.exists()
    assert out_csv.exists()
    assert (out_dir / "README_FIRST.md").exists()
    assert "Give the intern these separate packet ZIPs" in (out_dir / "README_FIRST.md").read_text(
        encoding="utf-8"
    )


def test_index_instructions_use_actual_packet_count(tmp_path: Path) -> None:
    for index in range(4):
        make_packet(
            tmp_path,
            f"2026-06-16_demo_review_{index}",
            f"Eng_Bench_v2_0_demo_review_{index}_2026-06-16.zip",
            (1, 1),
        )
    specs = [
        build_current_handoff_index.PacketSpec(
            packet_id=f"demo_{index}",
            packet_root=f"derived/human_adjudication/2026-06-16_demo_review_{index}",
            zip_path=f"derived/human_adjudication/Eng_Bench_v2_0_demo_review_{index}_2026-06-16.zip",
            priority=index + 1,
            notes="Demo packet.",
        )
        for index in range(4)
    ]

    report = build_current_handoff_index.build_report(
        tmp_path,
        date_label="2026-06-16",
        packets=specs,
    )
    markdown = build_current_handoff_index.render_index_markdown(report)

    assert "Give the intern the 4 packet ZIPs listed above" in markdown
    assert "three packet ZIPs" not in markdown
