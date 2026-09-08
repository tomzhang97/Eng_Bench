import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_textlayer(root: Path, doc_id: str, spans: list[dict]) -> None:
    path = root / "derived" / "textlayer" / f"{doc_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for span in spans:
            f.write(json.dumps(span) + "\n")


def write_page(root: Path, doc_id: str, page: int, size: tuple[int, int]) -> None:
    path = root / "derived" / "pages_300dpi" / doc_id / f"page_{page:03d}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "white").save(path)


def span(page: int, text: str, bbox: list[float]) -> dict:
    return {"page": page, "text": text, "bbox_px": bbox}


class BuildVisualdiffTextlayerReviewTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module(
            "build_visualdiff_textlayer_review",
            ROOT / "tools" / "build_visualdiff_textlayer_review.py",
        )

    def test_changed_pair_uses_exact_boxes_via_shared_anchor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_page(root, "demo_a", 0, (1000, 800))
            write_page(root, "demo_b", 0, (1000, 800))
            shared = [
                span(0, "R47", [100, 100, 140, 120]),
                span(0, "VCC_3V3", [500, 500, 560, 520]),
                span(0, "U12", [700, 200, 730, 220]),
            ]
            write_textlayer(root, "demo_a", shared + [span(0, "10K", [150, 100, 180, 120])])
            write_textlayer(root, "demo_b", shared + [span(0, "22K", [150, 100, 182, 120])])

            rows, report = self.mod.build_review_rows(
                root=root,
                pair_family="vdiff__demo__a__to__b",
                source_candidate_id="pcb_999",
                old_doc_id="demo_a",
                new_doc_id="demo_b",
            )

            self.assertEqual(report["changed_pairs"], 1)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["change_type"], "text_change_candidate")
            self.assertEqual(row["old_text"], "10K")
            self.assertEqual(row["new_text"], "22K")
            self.assertEqual(row["bbox_old"], [150, 100, 180, 120])
            self.assertEqual(row["bbox_new"], [150, 100, 182, 120])
            self.assertEqual(row["description"], "CHANGE_DESC_GT_TODO")
            self.assertEqual(row["pair_id"], "vdiff__demo__a__to__b__p0000__txt000")

    def test_page_mapping_resolves_reordered_pages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for page in (0, 1):
                write_page(root, "demo_a", page, (1000, 800))
                write_page(root, "demo_b", page, (1000, 800))
            sheet_one = [
                span(0, "POWER_TREE", [100, 100, 220, 130]),
                span(0, "BUCK_5V", [300, 300, 380, 330]),
                span(0, "L301", [420, 300, 460, 330]),
            ]
            sheet_two = [
                span(1, "ETHERNET_PHY", [100, 100, 240, 130]),
                span(1, "RJ45", [300, 300, 350, 330]),
                span(1, "T201", [420, 300, 470, 330]),
            ]
            write_textlayer(root, "demo_a", sheet_one + sheet_two)
            swapped_one = [dict(item, page=1) for item in sheet_one]
            swapped_two = [dict(item, page=0) for item in sheet_two]
            write_textlayer(
                root,
                "demo_b",
                swapped_two + swapped_one + [span(1, "L302", [500, 300, 540, 330])],
            )

            rows, report = self.mod.build_review_rows(
                root=root,
                pair_family="vdiff__demo__a__to__b",
                source_candidate_id="pcb_999",
                old_doc_id="demo_a",
                new_doc_id="demo_b",
            )

            self.assertEqual(report["mapped_pages"], 2)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["change_type"], "text_added_candidate")
            self.assertEqual(row["new_text"], "L302")
            self.assertEqual(row["page_old"], 0)
            self.assertEqual(row["page_new"], 1)

    def test_explicit_page_mapping_overrides_ambiguous_similarity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for page in (0, 1):
                write_page(root, "demo_a", page, (1000, 800))
                write_page(root, "demo_b", page, (1000, 800))
            old_spans = [
                span(0, "SHARED", [100, 100, 180, 130]),
                span(0, "ADC", [300, 300, 350, 330]),
                span(1, "SHARED", [100, 100, 180, 130]),
                span(1, "POWER", [300, 300, 370, 330]),
            ]
            new_spans = [
                span(0, "SHARED", [100, 100, 180, 130]),
                span(0, "POWER", [300, 300, 370, 330]),
                span(0, "VCC_NEW", [500, 300, 570, 330]),
                span(1, "SHARED", [100, 100, 180, 130]),
                span(1, "ADC", [300, 300, 350, 330]),
                span(1, "ADC_NEW", [500, 300, 580, 330]),
            ]
            write_textlayer(root, "demo_a", old_spans)
            write_textlayer(root, "demo_b", new_spans)

            rows, report = self.mod.build_review_rows(
                root=root,
                pair_family="vdiff__demo__a__to__b",
                source_candidate_id="pcb_999",
                old_doc_id="demo_a",
                new_doc_id="demo_b",
                page_mapping={
                    "type": "explicit",
                    "pairs": [
                        {"page_A": 0, "page_B": 1, "label": "adc"},
                        {"page_A": 1, "page_B": 0, "label": "power"},
                    ],
                },
            )

            self.assertEqual(report["mapping_mode_explicit"], 1)
            self.assertEqual(report["mapped_pages"], 2)
            self.assertEqual({(row["page_old"], row["page_new"]) for row in rows}, {(0, 1), (1, 0)})

    def test_one_sided_rows_skipped_on_orientation_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_page(root, "demo_a", 0, (1000, 800))
            write_page(root, "demo_b", 0, (800, 1000))
            shared = [
                span(0, "PUMP_101", [100, 100, 180, 130]),
                span(0, "VALVE_22", [300, 300, 390, 330]),
                span(0, "TANK_A", [500, 500, 560, 530]),
            ]
            write_textlayer(root, "demo_a", shared)
            write_textlayer(root, "demo_b", shared + [span(0, "NEW_SENSOR", [600, 600, 700, 630])])

            rows, report = self.mod.build_review_rows(
                root=root,
                pair_family="vdiff__demo__a__to__b",
                source_candidate_id="pid_999",
                old_doc_id="demo_a",
                new_doc_id="demo_b",
            )

            self.assertEqual(rows, [])
            self.assertEqual(report["skipped_one_sided_orientation_mismatch"], 1)

    def test_furniture_titleblock_and_caps_are_enforced(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_page(root, "demo_a", 0, (1000, 1000))
            write_page(root, "demo_b", 0, (1000, 1000))
            shared = [
                span(0, "ANCHOR_ONE", [100, 100, 200, 130]),
                span(0, "ANCHOR_TWO", [700, 100, 800, 130]),
                span(0, "ANCHOR_SIX", [100, 700, 200, 730]),
            ]
            old_only = [
                span(0, "Sheet", [40, 40, 80, 60]),
                span(0, "2024-08-26", [40, 80, 120, 100]),
                span(0, "TITLEBLOCK_TEXT", [850, 850, 980, 880]),
                span(0, "x", [40, 120, 50, 140]),
            ]
            added = [
                span(0, "NET_A", [300, 200, 360, 220]),
                span(0, "NET_B", [300, 400, 360, 420]),
                span(0, "NET_C", [300, 600, 360, 620]),
            ]
            write_textlayer(root, "demo_a", shared + old_only)
            write_textlayer(root, "demo_b", shared + added)

            rows, report = self.mod.build_review_rows(
                root=root,
                pair_family="vdiff__demo__a__to__b",
                source_candidate_id="pcb_999",
                old_doc_id="demo_a",
                new_doc_id="demo_b",
                max_per_page=2,
            )

            self.assertEqual(report["old_skipped_furniture_spans"], 3)
            self.assertEqual(report["old_skipped_titleblock_spans"], 1)
            self.assertEqual(report["raw_removed_spans"], 0)
            self.assertEqual(len(rows), 2)
            self.assertEqual(report["skipped_per_page_limit"], 1)
            self.assertTrue(all(row["change_type"] == "text_added_candidate" for row in rows))

    def test_repeated_change_key_capped_across_pages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_spans = []
            new_spans = []
            for page in range(3):
                write_page(root, "demo_a", page, (1000, 800))
                write_page(root, "demo_b", page, (1000, 800))
                sheet_tag = f"SHEET_TAG_{page}"
                shared = [
                    span(page, sheet_tag, [100, 100, 220, 130]),
                    span(page, f"ANCHOR_{page}", [400, 200, 470, 230]),
                ]
                old_spans.extend(shared + [span(page, "DocNo_1001", [500, 700, 600, 730])])
                new_spans.extend(shared + [span(page, "DocNo_1002", [500, 700, 600, 730])])
            write_textlayer(root, "demo_a", old_spans)
            write_textlayer(root, "demo_b", new_spans)

            rows, report = self.mod.build_review_rows(
                root=root,
                pair_family="vdiff__demo__a__to__b",
                source_candidate_id="pcb_999",
                old_doc_id="demo_a",
                new_doc_id="demo_b",
                max_per_change_key=1,
            )

            self.assertEqual(report["changed_pairs"], 3)
            self.assertEqual(len(rows), 1)
            self.assertEqual(report["skipped_repeated_change_key"], 2)
            self.assertEqual(rows[0]["old_text"], "DocNo_1001")
            self.assertEqual(rows[0]["new_text"], "DocNo_1002")

    def test_cluster_one_sided_merges_adjacent_spans(self):
        clusters = self.mod.cluster_one_sided(
            [
                {"text": "WORD_A", "bbox": [100, 100, 150, 120]},
                {"text": "WORD_B", "bbox": [155, 100, 210, 120]},
                {"text": "FAR_AWAY", "bbox": [600, 600, 660, 620]},
            ],
            merge_gap_px=10,
        )
        self.assertEqual(len(clusters), 2)
        self.assertEqual(clusters[0]["bbox"], [100, 100, 210, 120])
        self.assertEqual(clusters[0]["texts"], ["WORD_A", "WORD_B"])


if __name__ == "__main__":
    unittest.main()
