import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from finalize_all_images import finalize_images  # noqa: E402


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


class FinalizeAllImagesExplicitTest(unittest.TestCase):
    def test_visualdiff_explicit_source_images_fill_canonical_family_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_source = root / "derived" / "pages" / "old.png"
            new_source = root / "derived" / "pages" / "new.png"
            old_source.parent.mkdir(parents=True)
            old_source.write_bytes(b"old")
            new_source.write_bytes(b"new")
            write_jsonl(
                root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
                [
                    {
                        "pair_id": "vd_1",
                        "doc_id": "family_a",
                        "version_id_old": "v1",
                        "version_id_new": "v2",
                        "page_index_old": 0,
                        "page_index_new": 0,
                        "image_old": "derived/pages/old.png",
                        "image_new": "derived/pages/new.png",
                    }
                ],
            )
            write_jsonl(root / "microtext" / "annotations" / "microtext_items.jsonl", [])

            stats = finalize_images(root)

            self.assertEqual(2, stats["copied"])
            self.assertEqual(0, stats["missing"])
            self.assertEqual(
                b"old",
                (root / "images" / "family_a__v1" / "page_0000.png").read_bytes(),
            )
            self.assertEqual(
                b"new",
                (root / "images" / "family_a__v2" / "page_0000.png").read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
