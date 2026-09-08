from pathlib import Path
import tempfile
import unittest

from PIL import Image

from tools import import_pipettin_mechanical_drawings_wave_2026_08_17 as importer


class PipettinMechanicalImportTests(unittest.TestCase):
    def test_wave_candidates_are_unique_and_review_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ids: set[str] = set()
            total = 0
            for spec in importer.DOCS:
                image_path = root / f"{spec['doc_id']}.png"
                Image.new("RGB", (1684, 1191), "white").save(image_path)
                rows = importer.build_review_rows(root, spec, image_path)
                total += len(rows)
                for row in rows:
                    self.assertNotIn(row["candidate_id"], ids)
                    ids.add(row["candidate_id"])
                    self.assertEqual(row["category"], "dimension_value")
                    self.assertEqual(row["review_status"], "needs_review")
                    self.assertEqual(row["promotion_state"], "unreviewed_candidate")
                    self.assertIs(row["safe_to_merge_gold"], False)
                    self.assertEqual(row["reserved_split"], "test")
                    self.assertTrue((root / row["crop_path"]).is_file())
            self.assertEqual(total, 11)

    def test_rights_evidence_and_conflicting_pdfs_are_hash_pinned(self) -> None:
        self.assertEqual(importer.COMMIT, "b3f0c20615c7595e6b2b22d72bb7132a9f409158")
        self.assertEqual(len(importer.DOCS), 2)
        self.assertEqual(len(importer.HELD_PDFS), 2)
        for expected in importer.EVIDENCE_FILES.values():
            self.assertEqual(len(expected), 64)
        for spec in importer.DOCS:
            self.assertEqual(len(spec["sha256"]), 64)
        for held in importer.HELD_PDFS:
            self.assertEqual(len(held["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
