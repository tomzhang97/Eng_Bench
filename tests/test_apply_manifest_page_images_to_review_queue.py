import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "apply_manifest_page_images_to_review_queue.py"
SPEC = importlib.util.spec_from_file_location("apply_manifest_page_images_to_review_queue", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class ApplyManifestPageImagesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        source = self.root / "docs" / "source.pdf"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"verified source")
        self.source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        pages = self.root / "derived" / "pages" / "drawing"
        pages.mkdir(parents=True)
        (pages / "page_007.png").write_bytes(b"page seven")
        self.manifest = [
            {
                "type": "doc",
                "doc_id": "drawing",
                "path": "docs/source.pdf",
                "sha256": self.source_hash,
                "pages": 10,
                "derived": {"pages_dir": "derived/pages/drawing"},
            }
        ]

    def tearDown(self):
        self.temp.cleanup()

    def test_recovers_unique_zero_based_page_image(self):
        rows = [{"candidate_id": "a", "doc_id": "drawing", "page_index": 7}]

        passing, held, report = MODULE.enrich_rows(self.root, rows, self.manifest)

        self.assertEqual(held, [])
        self.assertEqual(passing[0]["image_path"], "derived/pages/drawing/page_007.png")
        self.assertTrue(passing[0]["machine_page_image_enriched"])
        self.assertFalse(passing[0]["safe_to_merge_gold"])
        self.assertTrue(report["valid"])

    def test_hash_mismatch_holds_row(self):
        manifest = [dict(self.manifest[0], sha256="0" * 64)]

        passing, held, report = MODULE.enrich_rows(
            self.root,
            [{"candidate_id": "a", "doc_id": "drawing", "page_index": 7}],
            manifest,
        )

        self.assertEqual(passing, [])
        self.assertEqual(held[0]["machine_hold_reason"], "manifest_source_sha256_mismatch")
        self.assertFalse(report["valid"])

    def test_out_of_range_page_index_holds_row(self):
        passing, held, _ = MODULE.enrich_rows(
            self.root,
            [{"candidate_id": "a", "doc_id": "drawing", "page_index": 10}],
            self.manifest,
        )

        self.assertEqual(passing, [])
        self.assertEqual(held[0]["machine_hold_reason"], "page_index_out_of_manifest_range")

    def test_ambiguous_page_image_holds_row(self):
        pages = self.root / "derived" / "pages" / "drawing"
        (pages / "page_7.jpg").write_bytes(b"duplicate")

        passing, held, _ = MODULE.enrich_rows(
            self.root,
            [{"candidate_id": "a", "doc_id": "drawing", "page_index": 7}],
            self.manifest,
        )

        self.assertEqual(passing, [])
        self.assertEqual(
            held[0]["machine_hold_reason"],
            "missing_or_ambiguous_manifest_page_image",
        )


if __name__ == "__main__":
    unittest.main()
