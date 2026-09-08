import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.repair_missing_review_pack_checklists import repair_missing_checklists


class RepairMissingReviewPackChecklistsTest(unittest.TestCase):
    def write_jsonl(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")

    def read_csv(self, path: Path) -> list[dict[str, str]]:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))

    def test_creates_missing_microtext_checklist_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "derived" / "review_packs" / "micro_pack"
            self.write_jsonl(
                pack / "manifest.jsonl",
                [
                    {
                        "candidate_id": "mtcand__doc__v1__p0001__000000",
                        "doc_id": "doc",
                        "version_id": "v1",
                        "page_index": 1,
                        "category": "dimension_value",
                        "proposed_text": "12 mm",
                        "crop_path": "derived/review_packs/micro_pack/crops/a.png",
                        "image_path": "derived/pages_300dpi/doc/page_001.png",
                    }
                ],
            )

            summary = repair_missing_checklists(root=root, review_pack_root=pack.parent)

            checklist = pack / "micro_pack_validation_checklist.csv"
            self.assertTrue(checklist.exists())
            rows = self.read_csv(checklist)
            self.assertEqual(1, len(rows))
            self.assertEqual("mtcand__doc__v1__p0001__000000", rows[0]["candidate_id"])
            self.assertEqual("", rows[0]["review_status"])
            self.assertEqual(1, summary["created"])
            self.assertEqual(0, summary["skipped_existing"])

    def test_creates_missing_microtext_index_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "derived" / "review_packs" / "micro_pack"
            self.write_jsonl(
                pack / "manifest.jsonl",
                [
                    {
                        "candidate_id": "mtcand__doc__v1__p0001__000000",
                        "doc_id": "doc",
                        "version_id": "v1",
                        "page_index": 1,
                        "category": "dimension_value",
                        "proposed_text": "12 mm",
                        "crop_path": "derived/review_packs/micro_pack/crops/a.png",
                        "image_path": "derived/pages_300dpi/doc/page_001.png",
                    }
                ],
            )

            summary = repair_missing_checklists(root=root, review_pack_root=pack.parent, create_indexes=True)

            index_path = pack / "index.html"
            self.assertTrue(index_path.exists())
            html = index_path.read_text(encoding="utf-8")
            self.assertIn("Microtext Review Pack", html)
            self.assertIn("mtcand__doc__v1__p0001__000000", html)
            self.assertEqual(1, summary["created_indexes"])

    def test_does_not_overwrite_existing_visualdiff_checklist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "derived" / "review_packs" / "visual_pack"
            self.write_jsonl(
                pack / "manifest.jsonl",
                [
                    {
                        "pair_id": "vdiff__doc__old__to__new__0001",
                        "project_id": "vdiff__doc__old__to__new",
                        "change_desc_gt": "CHANGE_DESC_GT_TODO",
                        "old_crop_path": "derived/review_packs/visual_pack/old/a.png",
                        "new_crop_path": "derived/review_packs/visual_pack/new/a.png",
                        "panel_path": "derived/review_packs/visual_pack/panels/a.png",
                    }
                ],
            )
            existing = pack / "visual_pack_validation_checklist.csv"
            existing.write_text("sentinel\n", encoding="utf-8")

            summary = repair_missing_checklists(root=root, review_pack_root=pack.parent)

            self.assertEqual("sentinel\n", existing.read_text(encoding="utf-8"))
            self.assertEqual(0, summary["created"])
            self.assertEqual(1, summary["skipped_existing"])

    def test_cli_runs_from_script_path(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "tools" / "repair_missing_review_pack_checklists.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "derived" / "review_packs" / "visual_pack"
            self.write_jsonl(
                pack / "manifest.jsonl",
                [
                    {
                        "pair_id": "vdiff__doc__old__to__new__0001",
                        "project_id": "vdiff__doc__old__to__new",
                        "old_crop_path": "derived/review_packs/visual_pack/old/a.png",
                        "new_crop_path": "derived/review_packs/visual_pack/new/a.png",
                    }
                ],
            )
            summary_path = root / "summary.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--root",
                    str(root),
                    "--review-pack-root",
                    "derived/review_packs",
                    "--output-json",
                    str(summary_path),
                ],
                cwd=repo_root,
                text=True,
                capture_output=True,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(1, summary["created"])
            self.assertTrue((pack / "visual_pack_validation_checklist.csv").exists())


if __name__ == "__main__":
    unittest.main()
