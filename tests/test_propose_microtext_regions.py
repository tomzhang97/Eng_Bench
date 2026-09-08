from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw
import cv2
import numpy as np
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from tools import propose_microtext_regions  # noqa: E402


def test_resolve_pages_clips_range_to_available_rendered_pages(tmp_path: Path) -> None:
    page_dir = tmp_path / "derived" / "pages_300dpi" / "short_doc"
    page_dir.mkdir(parents=True)
    Image.new("RGB", (20, 20), "white").save(page_dir / "page_000.png")
    Image.new("RGB", (20, 20), "white").save(page_dir / "page_001.png")

    assert propose_microtext_regions.resolve_pages(tmp_path, "short_doc", "2-5", dpi=300) == [1]


def test_resolve_pages_still_rejects_invalid_ranges(tmp_path: Path) -> None:
    page_dir = tmp_path / "derived" / "pages_300dpi" / "short_doc"
    page_dir.mkdir(parents=True)
    Image.new("RGB", (20, 20), "white").save(page_dir / "page_000.png")

    try:
        propose_microtext_regions.resolve_pages(tmp_path, "short_doc", "0", dpi=300)
    except ValueError as exc:
        assert "1-based" in str(exc)
    else:
        raise AssertionError("zero page selection should stay invalid")


def test_proposes_regions_on_small_line_drawing_sheet(tmp_path: Path) -> None:
    image_path = tmp_path / "small_sheet.png"
    image = Image.new("RGB", (640, 420), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([20, 20, 620, 400], outline="black", width=2)
    draw.line([80, 100, 560, 100], fill="black", width=2)
    draw.line([80, 220, 560, 220], fill="black", width=2)
    draw.text((110, 76), "SITE PLAN", fill="black")
    draw.text((330, 118), "TYP WINDOW DETAIL", fill="black")
    draw.text((180, 238), "STAIR SECTION", fill="black")
    image.save(image_path)

    proposals = propose_microtext_regions.propose_regions_for_image(image_path, limit=20)

    assert len(proposals) >= 3
    assert any(row["width"] >= 40 and row["height"] >= 8 for row in proposals)


def test_component_text_metrics_separate_clean_text_from_point_noise(tmp_path: Path) -> None:
    clean_path = tmp_path / "clean.png"
    clean = Image.new("L", (360, 90), "white")
    ImageDraw.Draw(clean).text((20, 25), "GAME ROOM", fill="black")
    clean.save(clean_path)
    clean_region = cv2.imread(str(clean_path), cv2.IMREAD_GRAYSCALE)

    noisy = np.full((90, 360), 255, dtype=np.uint8)
    random = np.random.default_rng(7)
    for x, y in random.integers([0, 0], [360, 90], size=(450, 2)):
        noisy[y, x] = 0

    clean_metrics = propose_microtext_regions.component_text_metrics(clean_region)
    noisy_metrics = propose_microtext_regions.component_text_metrics(noisy)
    assert clean_metrics["glyph_components"] >= 2
    assert clean_metrics["tiny_component_ratio"] < 0.5
    assert noisy_metrics["tiny_component_ratio"] > 0.8


def test_spatial_diversity_caps_each_page_tile() -> None:
    proposals = [
        {"bbox": [10, 10, 20, 20], "score": 10},
        {"bbox": [30, 30, 40, 40], "score": 9},
        {"bbox": [70, 10, 80, 20], "score": 8},
        {"bbox": [70, 70, 80, 80], "score": 7},
    ]
    selected = propose_microtext_regions.select_spatially_diverse(
        proposals,
        width=100,
        height=100,
        limit=4,
        tile_columns=2,
        tile_rows=2,
        max_per_tile=1,
    )
    assert len(selected) == 3
    assert {tuple(row["tile"]) for row in selected} == {(0, 0), (1, 0), (1, 1)}


class ProposeMicrotextRegionQualityTest(unittest.TestCase):
    def test_build_review_rows_forwards_opt_in_large_region_limits(self) -> None:
        with self.subTest("large archival region controls"):
            from tempfile import TemporaryDirectory

            with TemporaryDirectory() as directory:
                root = Path(directory)
                page_dir = root / "derived" / "pages_300dpi" / "large_doc"
                page_dir.mkdir(parents=True)
                Image.new("RGB", (100, 100), "white").save(page_dir / "page_000.png")

                with patch.object(
                    propose_microtext_regions,
                    "propose_regions_for_image",
                    return_value=[],
                ) as detector:
                    propose_microtext_regions.build_review_rows(
                        root,
                        "large_doc",
                        "v1",
                        [0],
                        max_region_width=2400,
                        max_region_height=360,
                        max_region_area=500_000,
                    )

                self.assertEqual(detector.call_args.kwargs["max_width"], 2400)
                self.assertEqual(detector.call_args.kwargs["max_height"], 360)
                self.assertEqual(detector.call_args.kwargs["max_area"], 500_000)

    def test_component_metrics_separate_clean_text_from_point_noise(self) -> None:
        clean = np.full((90, 360), 255, dtype=np.uint8)
        cv2.putText(clean, "GAME ROOM", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, 0, 2)
        noisy = np.full((90, 360), 255, dtype=np.uint8)
        random = np.random.default_rng(7)
        for x, y in random.integers([0, 0], [360, 90], size=(450, 2)):
            noisy[y, x] = 0

        clean_metrics = propose_microtext_regions.component_text_metrics(clean)
        noisy_metrics = propose_microtext_regions.component_text_metrics(noisy)
        self.assertGreaterEqual(clean_metrics["glyph_components"], 2)
        self.assertLess(clean_metrics["tiny_component_ratio"], 0.5)
        self.assertGreater(clean_metrics["glyph_center_span_ratio"], 0.1)
        self.assertGreater(clean_metrics["wide_glyph_ratio"], 0.5)
        self.assertGreater(noisy_metrics["tiny_component_ratio"], 0.8)

    def test_spatial_diversity_caps_each_page_tile(self) -> None:
        proposals = [
            {"bbox": [10, 10, 20, 20], "score": 10},
            {"bbox": [30, 30, 40, 40], "score": 9},
            {"bbox": [70, 10, 80, 20], "score": 8},
            {"bbox": [70, 70, 80, 80], "score": 7},
        ]
        selected = propose_microtext_regions.select_spatially_diverse(
            proposals,
            width=100,
            height=100,
            limit=4,
            tile_columns=2,
            tile_rows=2,
            max_per_tile=1,
        )
        self.assertEqual(3, len(selected))
        self.assertEqual({(0, 0), (1, 0), (1, 1)}, {tuple(row["tile"]) for row in selected})
