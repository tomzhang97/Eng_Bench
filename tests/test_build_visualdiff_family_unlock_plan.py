import csv
import json
import tempfile
import unittest
from pathlib import Path

from tools.build_visualdiff_family_unlock_plan import build_report, write_outputs


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


class BuildVisualdiffFamilyUnlockPlanTest(unittest.TestCase):
    def write_index(self, path: Path, packs: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"packs": packs}, indent=2), encoding="utf-8")

    def test_reports_new_ready_families_and_marks_next_tranche_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_jsonl(
                root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
                [
                    {"pair_id": "vdiff__bbb__C__to__C3__000"},
                ],
            )
            active_pack = root / "derived" / "review_packs" / "active_family"
            new_pack = root / "derived" / "review_packs" / "fdot_110"
            second_new_pack = root / "derived" / "review_packs" / "fdot_120"
            write_jsonl(
                active_pack / "manifest.jsonl",
                [{"pair_id": "vdiff__bbb__C__to__C3__review"}],
            )
            write_jsonl(
                new_pack / "manifest.jsonl",
                [
                    {
                        "pair_id": "vdiff__fdot_sp_110_100__fy2023__to__fy2024__p0000__txt000",
                        "project_id": "vdiff__fdot_sp_110_100__fy2023__to__fy2024",
                        "source_candidate_id": "civil_012",
                    },
                    {
                        "pair_id": "vdiff__fdot_sp_110_100__fy2023__to__fy2024__p0001__txt000",
                        "project_id": "vdiff__fdot_sp_110_100__fy2023__to__fy2024",
                        "source_candidate_id": "civil_012",
                    },
                ],
            )
            write_jsonl(
                second_new_pack / "manifest.jsonl",
                [
                    {
                        "pair_id": "vdiff__fdot_sp_120_002__fy2024__to__fy2025__p0000__txt000",
                        "project_id": "vdiff__fdot_sp_120_002__fy2024__to__fy2025",
                        "source_candidate_id": "civil_012",
                    }
                ],
            )
            index = root / "index.json"
            self.write_index(
                index,
                [
                    {
                        "packet_id": "active_family",
                        "kind": "visualdiff",
                        "folder_path": "derived/review_packs/active_family",
                        "ready_to_send": True,
                        "human_complete": False,
                        "blank_rows": 1,
                        "invalid_rows": 0,
                        "missing_evidence_refs": 0,
                    },
                    {
                        "packet_id": "fdot_110",
                        "kind": "visualdiff",
                        "folder_path": "derived/review_packs/fdot_110",
                        "ready_to_send": True,
                        "human_complete": False,
                        "blank_rows": 2,
                        "invalid_rows": 0,
                        "missing_evidence_refs": 0,
                    },
                    {
                        "packet_id": "fdot_120",
                        "kind": "visualdiff",
                        "folder_path": "derived/review_packs/fdot_120",
                        "ready_to_send": True,
                        "human_complete": False,
                        "blank_rows": 1,
                        "invalid_rows": 0,
                        "missing_evidence_refs": 0,
                    },
                    {
                        "packet_id": "microtext_ignored",
                        "kind": "microtext",
                        "folder_path": "derived/review_packs/microtext_ignored",
                        "ready_to_send": True,
                        "human_complete": False,
                        "blank_rows": 10,
                        "invalid_rows": 0,
                        "missing_evidence_refs": 0,
                    },
                ],
            )
            tranche = root / "tranche.csv"
            with tranche.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["packet_id", "suggested_rows"])
                writer.writeheader()
                writer.writerow({"packet_id": "fdot_110", "suggested_rows": "2"})

            report = build_report(
                root=root,
                packet_index=index,
                tranche_csv=tranche,
                date_label="test",
                target_families=30,
            )

            self.assertEqual(1, report["totals"]["active_family_count"])
            self.assertEqual(2, report["totals"]["new_ready_candidate_family_count"])
            self.assertEqual(1, report["totals"]["selected_new_candidate_family_count"])
            self.assertEqual(2, report["totals"]["projected_family_count_if_selected_new_families_promoted"])
            families = {row["family"]: row for row in report["candidate_families"]}
            self.assertTrue(families["vdiff__bbb__C"]["active_gold"])
            self.assertFalse(families["vdiff__fdot_sp_110_100__fy2023"]["active_gold"])
            self.assertTrue(families["vdiff__fdot_sp_110_100__fy2023"]["selected_by_next_tranche"])
            self.assertEqual(2, families["vdiff__fdot_sp_110_100__fy2023"]["candidate_rows"])
            self.assertEqual(2, families["vdiff__fdot_sp_110_100__fy2023"]["suggested_rows_in_next_tranche"])
            self.assertFalse(families["vdiff__fdot_sp_120_002__fy2024"]["selected_by_next_tranche"])

    def test_writes_json_markdown_and_csv_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "derived" / "review_packs" / "fdot_110"
            write_jsonl(
                pack / "manifest.jsonl",
                [{"pair_id": "vdiff__fdot_sp_110_100__fy2023__to__fy2024__p0000__txt000"}],
            )
            index = root / "index.json"
            self.write_index(
                index,
                [
                    {
                        "packet_id": "fdot_110",
                        "kind": "visualdiff",
                        "folder_path": "derived/review_packs/fdot_110",
                        "ready_to_send": True,
                        "human_complete": False,
                        "blank_rows": 1,
                        "invalid_rows": 0,
                        "missing_evidence_refs": 0,
                    }
                ],
            )
            report = build_report(root=root, packet_index=index, date_label="test")
            json_path = root / "out.json"
            md_path = root / "out.md"
            csv_path = root / "out.csv"

            write_outputs(report, output_json=json_path, output_md=md_path, output_csv=csv_path)

            self.assertTrue(json_path.exists())
            self.assertIn("VisualDiff Family Unlock Plan", md_path.read_text(encoding="utf-8"))
            with csv_path.open("r", newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual("vdiff__fdot_sp_110_100__fy2023", rows[0]["family"])


if __name__ == "__main__":
    unittest.main()
