import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from tools.filter_textlayer_visualdiff_candidates import filter_rows


def evidence(path: Path) -> None:
    image = Image.new("L", (200, 120), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 80, 50), fill="black")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def row(old_image: str, new_image: str, **overrides):
    value = {
        "pair_id": "vdiff__fixture__p0000__txt000",
        "change_type": "text_change_candidate",
        "old_text": "IC9",
        "new_text": "IC15",
        "page_similarity_jaccard": 0.1,
        "image_old": old_image,
        "image_new": new_image,
        "bbox_old": [20, 20, 80, 50],
        "bbox_new": [20, 20, 80, 50],
    }
    value.update(overrides)
    return value


class FilterTextlayerVisualdiffCandidatesTests(unittest.TestCase):
    def test_keeps_same_designator_family_on_redesigned_page(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence(root / "old.png")
            evidence(root / "new.png")
            passing, held, report = filter_rows(root, [row("old.png", "new.png")])

            self.assertEqual(len(passing), 1)
            self.assertEqual(held, [])
            self.assertEqual(
                passing[0]["machine_qa"]["reason"], "same_reference_designator_family"
            )
            self.assertFalse(report["safe_to_merge_gold"])

    def test_holds_unrelated_pair_on_redesigned_page(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence(root / "old.png")
            evidence(root / "new.png")
            candidate = row(
                "old.png", "new.png", old_text="PGA REF", new_text="THS4631"
            )
            passing, held, _ = filter_rows(root, [candidate])

            self.assertEqual(passing, [])
            self.assertEqual(len(held), 1)
            self.assertEqual(held[0]["machine_qa"]["reason"], "weak_or_incoherent_text_pair")

    def test_keeps_one_sided_text_only_on_stable_page(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence(root / "old.png")
            evidence(root / "new.png")
            candidate = row(
                "old.png",
                "new.png",
                change_type="text_added_candidate",
                old_text="",
                new_text="COJ6B",
                page_similarity_jaccard=0.98,
            )
            passing, held, _ = filter_rows(root, [candidate])

            self.assertEqual(len(passing), 1)
            self.assertEqual(held, [])

    def test_rejects_duplicate_pair_ids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence(root / "old.png")
            evidence(root / "new.png")
            candidate = row("old.png", "new.png")
            with self.assertRaisesRegex(ValueError, "duplicate pair_id"):
                filter_rows(root, [candidate, dict(candidate)])


if __name__ == "__main__":
    unittest.main()
