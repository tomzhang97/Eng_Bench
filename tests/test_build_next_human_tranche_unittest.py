import csv
import json
import tempfile
import unittest
from pathlib import Path

from tools.build_next_human_tranche import build_tranche, write_outputs


class BuildNextHumanTrancheTest(unittest.TestCase):
    def write_index(self, path: Path, packs: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"date_label": "test", "packs": packs, "totals": {}}, indent=2),
            encoding="utf-8",
        )

    def test_selects_ready_incomplete_diverse_packs_with_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = root / "index.json"
            self.write_index(
                index,
                [
                    {
                        "packet_id": "visualdiff_fdot_small",
                        "kind": "visualdiff",
                        "folder_path": "derived/review_packs/visualdiff_fdot_small",
                        "manifest_rows": 80,
                        "blank_rows": 80,
                        "invalid_rows": 0,
                        "missing_evidence_refs": 0,
                        "ready_to_send": True,
                        "human_complete": False,
                        "issues": [],
                    },
                    {
                        "packet_id": "microtext_pid_large",
                        "kind": "microtext",
                        "folder_path": "derived/review_packs/microtext_pid_large",
                        "manifest_rows": 500,
                        "blank_rows": 500,
                        "invalid_rows": 0,
                        "missing_evidence_refs": 0,
                        "ready_to_send": True,
                        "human_complete": False,
                        "issues": [],
                    },
                    {
                        "packet_id": "microtext_done",
                        "kind": "microtext",
                        "folder_path": "derived/review_packs/microtext_done",
                        "manifest_rows": 20,
                        "blank_rows": 0,
                        "invalid_rows": 0,
                        "missing_evidence_refs": 0,
                        "ready_to_send": True,
                        "human_complete": True,
                        "issues": [],
                    },
                    {
                        "packet_id": "visualdiff_bad_refs",
                        "kind": "visualdiff",
                        "folder_path": "derived/review_packs/visualdiff_bad_refs",
                        "manifest_rows": 10,
                        "blank_rows": 10,
                        "invalid_rows": 0,
                        "missing_evidence_refs": 1,
                        "ready_to_send": False,
                        "human_complete": False,
                        "issues": ["missing_evidence_refs"],
                    },
                ],
            )

            report = build_tranche(root=root, index_path=index, row_budget=150, max_rows_per_pack=100)

            selected = report["selected_packs"]
            self.assertEqual(["visualdiff_fdot_small", "microtext_pid_large"], [row["packet_id"] for row in selected])
            self.assertEqual(75, selected[0]["suggested_rows"])
            self.assertEqual(75, selected[1]["suggested_rows"])
            self.assertEqual(150, report["totals"]["selected_rows"])
            self.assertEqual(2, report["totals"]["selected_packs"])
            self.assertEqual(2, report["totals"]["excluded_packs"])

    def test_writes_json_markdown_and_csv_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = root / "index.json"
            self.write_index(
                index,
                [
                    {
                        "packet_id": "microtext_mechanical_pack",
                        "kind": "microtext",
                        "folder_path": "derived/review_packs/microtext_mechanical_pack",
                        "manifest_rows": 12,
                        "blank_rows": 12,
                        "invalid_rows": 0,
                        "missing_evidence_refs": 0,
                        "ready_to_send": True,
                        "human_complete": False,
                        "issues": [],
                    }
                ],
            )
            report = build_tranche(root=root, index_path=index, row_budget=50, max_rows_per_pack=25)

            json_path = root / "out.json"
            md_path = root / "out.md"
            csv_path = root / "out.csv"
            write_outputs(report, output_json=json_path, output_md=md_path, output_csv=csv_path)

            self.assertTrue(json_path.exists())
            self.assertIn("Next Human Review Tranche", md_path.read_text(encoding="utf-8"))
            with csv_path.open("r", encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(1, len(rows))
            self.assertEqual("microtext_mechanical_pack", rows[0]["packet_id"])

    def test_round_robins_domains_instead_of_exhausting_visualdiff_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = root / "index.json"
            packs = []
            for idx in range(5):
                packs.append(
                    {
                        "packet_id": f"visualdiff_pack_{idx}",
                        "kind": "visualdiff",
                        "folder_path": f"derived/review_packs/visualdiff_pack_{idx}",
                        "manifest_rows": 10,
                        "blank_rows": 10,
                        "invalid_rows": 0,
                        "missing_evidence_refs": 0,
                        "ready_to_send": True,
                        "human_complete": False,
                        "issues": [],
                    }
                )
            packs.append(
                {
                    "packet_id": "microtext_pid_pack",
                    "kind": "microtext",
                    "folder_path": "derived/review_packs/microtext_pid_pack",
                    "manifest_rows": 10,
                    "blank_rows": 10,
                    "invalid_rows": 0,
                    "missing_evidence_refs": 0,
                    "ready_to_send": True,
                    "human_complete": False,
                    "issues": [],
                }
            )
            self.write_index(index, packs)

            report = build_tranche(root=root, index_path=index, row_budget=30, max_rows_per_pack=10)

            domains = [row["domain"] for row in report["selected_packs"]]
            self.assertIn("pid", domains)
            self.assertLess(domains.count("visualdiff"), len(domains))
            self.assertEqual({"pid": 10, "visualdiff": 20}, report["totals"]["selected_rows_by_domain"])


if __name__ == "__main__":
    unittest.main()
