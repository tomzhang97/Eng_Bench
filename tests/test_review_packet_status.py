from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from tools import review_packet_status


def write_microtext_checklist(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "candidate_id",
                "review_status",
                "proposed_text",
                "corrected_text",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "candidate_id": "cand_001",
                "review_status": "",
                "proposed_text": "A1",
                "corrected_text": "",
            }
        )


def write_visualdiff_checklist(path: Path, status: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "pair_id",
                "human_status",
                "human_description",
                "human_notes",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "pair_id": "vdiff_001",
                "human_status": status,
                "human_description": "Object moved relative to the nearby reference line.",
                "human_notes": "",
            }
        )


def test_handoff_root_falls_back_to_single_packet_folder(tmp_path: Path) -> None:
    packet_root = tmp_path / "2026-06-16_demo_review"
    write_microtext_checklist(packet_root / "demo_candidates_checklist.csv")

    report = review_packet_status.summarize_handoff(packet_root)

    assert report["packet_roots"] == 1
    assert report["totals"]["files"] == 1
    assert report["totals"]["rows"] == 1
    assert report["totals"]["blank_rows"] == 1
    assert report["totals"]["incomplete_files"] == 1


def test_handoff_root_empty_layout_reports_issue(tmp_path: Path) -> None:
    report = review_packet_status.summarize_handoff(tmp_path)

    assert report["complete"] is False
    assert report["totals"]["files"] == 0
    assert report["totals"]["issues"] == 1
    assert report["issues"][0]["issue"] == "no recognized checklist csv files found"


def test_visualdiff_layout_status_gets_actionable_guidance(tmp_path: Path) -> None:
    checklist = tmp_path / "visualdiff_validation_checklist.csv"
    write_visualdiff_checklist(checklist, "layout")

    report = review_packet_status.summarize_csv(checklist)

    assert report["complete"] is False
    assert report["status_counts"]["invalid_status"] == 1
    assert report["issues"][0]["row_id"] == "vdiff_001"
    assert "Use human_status=edit with human_description" in report["issues"][0]["issue"]
    assert "reject_unclear" in report["issues"][0]["issue"]


class ReviewPacketStatusUnittest(unittest.TestCase):
    def test_visualdiff_layout_status_gets_actionable_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            checklist = Path(temp_dir) / "visualdiff_validation_checklist.csv"
            write_visualdiff_checklist(checklist, "layout")

            report = review_packet_status.summarize_csv(checklist)

        self.assertFalse(report["complete"])
        self.assertEqual(report["status_counts"]["invalid_status"], 1)
        self.assertEqual(report["issues"][0]["row_id"], "vdiff_001")
        self.assertIn(
            "Use human_status=edit with human_description",
            report["issues"][0]["issue"],
        )
        self.assertIn("reject_unclear", report["issues"][0]["issue"])
