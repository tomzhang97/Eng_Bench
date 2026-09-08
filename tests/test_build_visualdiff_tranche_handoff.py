import csv
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.build_visualdiff_tranche_handoff import build_handoff


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"png")


class BuildVisualdiffTrancheHandoffTest(unittest.TestCase):
    def test_filters_selected_visualdiff_rows_and_writes_valid_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_pack = root / "derived" / "review_packs" / "visualdiff_demo"
            rows = []
            checklist_rows = []
            for idx in range(2):
                pair_id = f"vdiff__demo__rev_a__to__rev_b__p0000__{idx:03d}"
                row = {
                    "pair_id": pair_id,
                    "project_id": "vdiff__demo__rev_a__to__rev_b",
                    "split": "provisional_review",
                    "change_type": "text_change_candidate",
                    "description": "CHANGE_DESC_GT_TODO",
                    "old_crop_path": f"derived/review_packs/visualdiff_demo/old/{pair_id}.png",
                    "new_crop_path": f"derived/review_packs/visualdiff_demo/new/{pair_id}.png",
                    "panel_path": f"derived/review_packs/visualdiff_demo/panels/{pair_id}.png",
                    "old_page_path": "derived/review_packs/visualdiff_demo/pages_old/old_page.png",
                    "new_page_path": "derived/review_packs/visualdiff_demo/pages_new/new_page.png",
                }
                rows.append(row)
                checklist_rows.append(
                    {
                        "review_index": str(idx + 1),
                        "pair_id": pair_id,
                        "split": "provisional_review",
                        "project_id": row["project_id"],
                        "change_type": row["change_type"],
                        "current_description": row["description"],
                        "old_crop_path": f"old/{pair_id}.png",
                        "new_crop_path": f"new/{pair_id}.png",
                        "panel_path": f"panels/{pair_id}.png",
                        "old_page_path": "pages_old/old_page.png",
                        "new_page_path": "pages_new/new_page.png",
                        "what_to_validate": "Confirm real visible change.",
                        "human_status": "",
                        "human_description": "",
                        "human_notes": "",
                    }
                )
                for folder in ("old", "new", "panels"):
                    touch(source_pack / folder / f"{pair_id}.png")
            touch(source_pack / "pages_old" / "old_page.png")
            touch(source_pack / "pages_new" / "new_page.png")
            write_jsonl(source_pack / "manifest.jsonl", rows)
            write_csv(source_pack / "visualdiff_demo_validation_checklist.csv", checklist_rows)
            (source_pack / "index.html").write_text("<html>old index</html>", encoding="utf-8")

            tranche_csv = root / "tranche.csv"
            write_csv(
                tranche_csv,
                [
                    {
                        "priority": "1",
                        "packet_id": "visualdiff_demo",
                        "kind": "visualdiff",
                        "domain": "visualdiff",
                        "suggested_rows": "1",
                        "blank_rows": "2",
                        "manifest_rows": "2",
                        "folder_path": "derived/review_packs/visualdiff_demo",
                        "checklist_hint": "",
                        "index_hint": "",
                        "reason": "",
                    },
                    {
                        "priority": "2",
                        "packet_id": "microtext_skip",
                        "kind": "microtext",
                        "domain": "pid",
                        "suggested_rows": "1",
                        "blank_rows": "1",
                        "manifest_rows": "1",
                        "folder_path": "derived/review_packs/microtext_skip",
                        "checklist_hint": "",
                        "index_hint": "",
                        "reason": "",
                    },
                ],
            )
            family_report = root / "family.json"
            family_report.write_text(
                json.dumps(
                    {
                        "totals": {
                            "active_family_count": 3,
                            "target_family_count": 30,
                            "new_ready_candidate_family_count": 1,
                            "selected_new_candidate_family_count": 1,
                            "projected_family_count_if_selected_new_families_promoted": 4,
                        },
                        "candidate_families": [],
                    }
                ),
                encoding="utf-8",
            )

            output_dir = root / "handoff"
            zip_path = root / "handoff.zip"
            report = build_handoff(
                root=root,
                tranche_csv=tranche_csv,
                family_report_json=family_report,
                output_dir=output_dir,
                zip_path=zip_path,
                date_label="2026-07-05test",
            )

            self.assertEqual(1, report["totals"]["packet_count"])
            self.assertEqual(1, report["totals"]["selected_rows"])
            self.assertEqual(0, report["totals"]["missing_evidence_refs"])
            filtered_manifest = [
                json.loads(line)
                for line in (output_dir / "review_packs" / "visualdiff_demo" / "manifest.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            ]
            self.assertEqual(1, len(filtered_manifest))
            self.assertEqual("old/vdiff__demo__rev_a__to__rev_b__p0000__000.png", filtered_manifest[0]["old_crop_path"])
            with (output_dir / "visualdiff_selected_rows_validation_checklist.csv").open(
                newline="", encoding="utf-8"
            ) as f:
                combined_rows = list(csv.DictReader(f))
            self.assertEqual(1, len(combined_rows))
            self.assertEqual("visualdiff_demo", combined_rows[0]["packet_id"])
            self.assertIn("完全一样", (output_dir / "HUMAN_REVIEW_STEPS_ZH.md").read_text(encoding="utf-8"))
            with zipfile.ZipFile(zip_path, "r") as zf:
                self.assertIsNone(zf.testzip())
                names = zf.namelist()
            self.assertTrue(any(name.endswith("HUMAN_REVIEW_STEPS_ZH.md") for name in names))
            self.assertFalse(any(name.endswith(".zip") for name in names))


if __name__ == "__main__":
    unittest.main()
