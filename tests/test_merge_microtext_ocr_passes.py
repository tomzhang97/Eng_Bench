import unittest

from tools.merge_microtext_ocr_passes import merge_rows


def row(text: str, bbox: list[int], confidence: float) -> dict:
    return {
        "doc_id": "doc",
        "page_index": 0,
        "bbox": bbox,
        "category": "pipe_line_tag",
        "proposed_text": text,
        "ocr_confidence": confidence,
        "candidate_id": text,
    }


class MergeMicrotextOcrPassesTests(unittest.TestCase):
    def test_merges_overlapping_passes_and_preserves_alternates(self) -> None:
        merged, report = merge_rows(
            [
                ("coarse.jsonl", row("301-Y2-Q", [100, 100, 200, 130], 0.91)),
                ("fine.jsonl", row("301-1/2-Q", [103, 101, 199, 131], 0.96)),
                ("fine.jsonl", row("316-2-G", [300, 100, 390, 130], 0.99)),
            ],
            category="pipe_line_tag",
        )
        self.assertEqual(2, len(merged))
        corrected = next(item for item in merged if item["proposed_text"] == "301-1/2-Q")
        self.assertEqual(["301-1/2-Q", "301-Y2-Q"], corrected["ocr_alternates"])
        self.assertEqual(2, corrected["ocr_merged_detection_count"])
        self.assertEqual(1, report["collapsed_rows"])

    def test_category_filter_excludes_other_rows(self) -> None:
        other = row("P-1", [0, 0, 20, 20], 1.0)
        other["category"] = "equipment_tag"
        merged, report = merge_rows(
            [("input.jsonl", other)],
            category="pipe_line_tag",
        )
        self.assertEqual([], merged)
        self.assertEqual(0, report["input_rows"])
