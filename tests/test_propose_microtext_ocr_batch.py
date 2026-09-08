import json
import tempfile
import unittest
from pathlib import Path

from tools.propose_microtext_ocr_batch import load_batch_spec, run_batch


class ProposeMicrotextOcrBatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for doc_id in ("doc_a", "doc_b"):
            page_dir = self.root / "derived" / "pages_300dpi" / doc_id
            page_dir.mkdir(parents=True)
            (page_dir / "page_000.png").write_bytes(b"not-read-by-fake")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_load_batch_spec_validates_profiles_and_duplicates(self) -> None:
        path = self.root / "spec.json"
        path.write_text(
            json.dumps(
                {
                    "documents": [
                        {
                            "doc_id": "doc_a",
                            "version_id": "v1",
                            "profiles": ["pcb_pin_signals", "pcb_pin_signals"],
                            "rotate_cw_degrees": 90,
                            "ocr_scale": 2.0,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(load_batch_spec(path)[0]["profiles"], ["pcb_pin_signals"])
        self.assertEqual(load_batch_spec(path)[0]["rotate_cw_degrees"], 90)
        self.assertEqual(load_batch_spec(path)[0]["ocr_scale"], 2.0)

        path.write_text(
            json.dumps(
                {
                    "documents": [
                        {"doc_id": "doc_a", "version_id": "v1", "rotate_cw_degrees": 45}
                    ]
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "rotate_cw_degrees"):
            load_batch_spec(path)

        path.write_text(
            json.dumps(
                {
                    "documents": [
                        {"doc_id": "doc_a", "version_id": "v1", "ocr_scale": 4.1}
                    ]
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "ocr_scale"):
            load_batch_spec(path)

        path.write_text(
            json.dumps(
                {
                    "documents": [
                        {"doc_id": "doc_a", "version_id": "v1"},
                        {"doc_id": "doc_a", "version_id": "v2"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "Duplicate doc_id"):
            load_batch_spec(path)

    def test_run_batch_preserves_profiles_and_review_only_state(self) -> None:
        calls = []

        def fake_propose(**kwargs):
            calls.append(kwargs)
            row = {
                "candidate_id": f"candidate_{kwargs['doc_id']}",
                "doc_id": kwargs["doc_id"],
                "version_id": kwargs["version_id"],
                "page_index": kwargs["page_index"],
                "bbox": [1, 2, 30, 40],
                "proposed_text": "P-101",
                "category": "equipment_tag",
                "ocr_confidence": 0.99,
                "image_path": f"derived/pages_300dpi/{kwargs['doc_id']}/page_000.png",
            }
            report = {
                "doc_id": kwargs["doc_id"],
                "page_index": kwargs["page_index"],
                "tiles": 1,
                "ocr_detections": 2,
                "review_candidates": 1,
            }
            return [row], report

        rows, report = run_batch(
            self.root,
            [
                {
                    "doc_id": "doc_a",
                    "version_id": "v1",
                    "profiles": ["pid_labels"],
                    "page_indices": [],
                    "ocr_scale": 2.0,
                },
                {
                    "doc_id": "doc_b",
                    "version_id": "v2",
                    "profiles": [
                        "pcb_pin_signals",
                        "civil_slope_values",
                        "unclassified_engineering_text",
                    ],
                    "page_indices": [],
                },
            ],
            engine=object(),
            np_module=object(),
            tile_size=100,
            overlap=10,
            min_confidence=0.9,
            padding=2,
            limit_per_page=5,
            max_image_pixels=1000,
            propose_fn=fake_propose,
        )

        self.assertEqual(len(rows), 2)
        self.assertTrue(calls[0]["include_pid_labels"])
        self.assertEqual(calls[0]["rotate_cw_degrees"], 0)
        self.assertEqual(calls[0]["ocr_scale"], 2.0)
        self.assertFalse(calls[0]["include_pcb_pin_signals"])
        self.assertTrue(calls[1]["include_pcb_pin_signals"])
        self.assertTrue(calls[1]["include_civil_slope_values"])
        self.assertTrue(calls[1]["include_unclassified"])
        self.assertTrue(all(row["safe_to_merge_gold"] is False for row in rows))
        self.assertTrue(all(row["review_status"] == "needs_review" for row in rows))
        self.assertEqual(report["totals"]["documents"], 2)
        self.assertEqual(report["gold_rows_added"], 0)


if __name__ == "__main__":
    unittest.main()
