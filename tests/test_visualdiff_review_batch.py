import csv
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class VisualdiffReviewBatchTests(unittest.TestCase):
    def test_build_verify_and_process_visualdiff_standalone_batch(self):
        builder = load_module(
            "build_next_review_batch_visualdiff",
            ROOT / "tools" / "build_next_review_batch.py",
        )
        verifier = load_module(
            "verify_review_batch_visualdiff",
            ROOT / "tools" / "verify_review_batch_package.py",
        )
        processor = load_module(
            "process_next_review_batch_visualdiff",
            ROOT / "tools" / "process_next_review_batch_return.py",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_page = root / "derived" / "pages_300dpi" / "old_doc" / "page_000.png"
            new_page = root / "derived" / "pages_300dpi" / "new_doc" / "page_000.png"
            old_page.parent.mkdir(parents=True)
            new_page.parent.mkdir(parents=True)
            Image.new("RGB", (120, 100), "white").save(old_page)
            Image.new("RGB", (120, 100), "gray").save(new_page)
            source = root / "visualdiff" / "annotations" / "visualdiff_review_demo.jsonl"
            write_jsonl(
                source,
                [
                    {
                        "pair_id": "p0",
                        "project_id": "family_demo",
                        "split": "provisional_review",
                        "review_bucket": "source_expansion_unverified",
                        "bbox_old": [10, 20, 40, 50],
                        "bbox_new": [12, 22, 42, 52],
                        "image_old": "derived/pages_300dpi/old_doc/page_000.png",
                        "image_new": "derived/pages_300dpi/new_doc/page_000.png",
                        "description": "CHANGE_DESC_GT_TODO",
                        "review_status": "needs_review",
                    }
                ],
            )

            report = builder.build_next_batch(
                root=root,
                queues=[
                    builder.QueueSpec(
                        "visualdiff/annotations/visualdiff_review_demo.jsonl",
                        "visualdiff_demo",
                        "Visualdiff Demo",
                    )
                ],
                batch_dir=Path("derived/human_adjudication/batch"),
                zip_output=Path("derived/human_adjudication/batch.zip"),
                date_label="2026-06-05",
                pad_px=8,
            )

            batch = root / "derived" / "human_adjudication" / "batch"
            folder_report = verifier.verify_batch_dir(batch)
            zip_report = verifier.verify_batch_zip(root / "derived/human_adjudication/batch.zip")
            checklist_path = (
                batch / "review_packs" / "visualdiff_demo" / "visualdiff_demo_validation_checklist.csv"
            )
            with checklist_path.open(encoding="utf-8", newline="") as f:
                checklist_rows = list(csv.DictReader(f))

            self.assertTrue(report["valid"])
            self.assertEqual(report["packs"][0]["kind"], "visualdiff")
            self.assertTrue(folder_report["valid"])
            self.assertTrue(zip_report["valid"])
            self.assertEqual(checklist_rows[0]["panel_path"], "panels/p0.png")
            self.assertTrue(
                (batch / "review_packs/visualdiff_demo/pages_old/old_doc__page_000.png").exists()
            )
            self.assertTrue(
                (batch / "review_packs/visualdiff_demo/pages_new/new_doc__page_000.png").exists()
            )

            checklist_rows[0]["human_status"] = "edit"
            checklist_rows[0]["human_description"] = "The visible component value changed."
            with checklist_path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(checklist_rows[0]))
                writer.writeheader()
                writer.writerows(checklist_rows)

            processing, errors = processor.process_batch(
                root,
                batch,
                root / "derived/human_adjudication/processed",
            )
            staged = read_jsonl(root / "derived/human_adjudication/processed/visualdiff_demo_reviewed.jsonl")

            self.assertFalse(errors)
            self.assertEqual(processing["totals"]["mergeable_rows"], 1)
            self.assertEqual(staged[0]["description"], "The visible component value changed.")


if __name__ == "__main__":
    unittest.main()
