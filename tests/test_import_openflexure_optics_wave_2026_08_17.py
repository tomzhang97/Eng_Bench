from pathlib import Path
import tempfile
import unittest

from PIL import Image

from tools import import_openflexure_optics_wave_2026_08_17 as importer


class OpenFlexureOpticsImportTests(unittest.TestCase):
    def test_wave_defines_unique_review_only_dimension_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            candidate_ids: set[str] = set()
            total = 0
            for spec in importer.DOCS:
                image_path = root / f"{spec['stem']}.png"
                Image.new("RGB", (1200, 916), "white").save(image_path)
                rows = importer.build_review_rows(root, spec, image_path)
                total += len(rows)
                for row in rows:
                    self.assertNotIn(row["candidate_id"], candidate_ids)
                    candidate_ids.add(row["candidate_id"])
                    self.assertEqual(row["category"], "dimension_value")
                    self.assertEqual(row["review_status"], "needs_review")
                    self.assertEqual(row["promotion_state"], "unreviewed_candidate")
                    self.assertIs(row["safe_to_merge_gold"], False)
                    self.assertEqual(row["reserved_split"], "test")
                    self.assertEqual(row["source_public_status"], importer.PUBLIC_STATUS)
                    self.assertTrue((root / row["crop_path"]).is_file())
            self.assertEqual(total, 5)

    def test_rights_evidence_is_hash_pinned(self) -> None:
        self.assertEqual(importer.COMMIT, "f2298b6803675c166bc753bfbb442bbacb034dd9")
        self.assertEqual(len(importer.EVIDENCE_FILES), 4)
        self.assertEqual(len(importer.DOCS), 3)
        for expected in importer.EVIDENCE_FILES.values():
            self.assertEqual(len(expected), 64)
        for spec in importer.DOCS:
            self.assertEqual(len(spec["svg_sha256"]), 64)
            self.assertEqual(len(spec["png_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
