from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np
from PIL import Image

from tools import propose_microtext_ocr_regions as ocr_regions


class ProposeMicrotextOcrRegionsTest(unittest.TestCase):
    def test_restore_rotated_bbox_to_original_coordinates(self) -> None:
        bbox = [10, 20, 30, 40]
        self.assertEqual(
            [20, 170, 40, 190],
            ocr_regions.restore_rotated_bbox(
                bbox,
                original_width=100,
                original_height=200,
                rotate_cw_degrees=90,
            ),
        )
        self.assertEqual(
            [60, 10, 80, 30],
            ocr_regions.restore_rotated_bbox(
                bbox,
                original_width=100,
                original_height=200,
                rotate_cw_degrees=270,
            ),
        )
    def test_tile_starts_cover_final_edge_without_duplicates(self) -> None:
        self.assertEqual([0], ocr_regions.tile_starts(3000, 5000, 512))
        starts = ocr_regions.tile_starts(13600, 5000, 512)
        self.assertEqual(0, starts[0])
        self.assertEqual(8600, starts[-1])
        self.assertEqual(starts, sorted(set(starts)))

    def test_select_page_paths_is_numeric_and_rejects_missing_pages(self) -> None:
        paths = [Path("page_100.png"), Path("page_023.png"), Path("page_076.png")]
        self.assertEqual(
            [Path("page_023.png"), Path("page_100.png")],
            ocr_regions.select_page_paths(paths, [100, 23]),
        )
        self.assertEqual(
            [Path("page_023.png"), Path("page_076.png"), Path("page_100.png")],
            ocr_regions.select_page_paths(paths, []),
        )
        with self.assertRaisesRegex(ValueError, "unavailable: \\[999\\]"):
            ocr_regions.select_page_paths(paths, [999])

    def test_classifies_only_supported_engineering_text(self) -> None:
        self.assertEqual("equipment_tag", ocr_regions.classify_engineering_text("P-101"))
        self.assertEqual("instrument_tag", ocr_regions.classify_engineering_text("PIT-204"))
        self.assertEqual("instrument_tag", ocr_regions.classify_engineering_text("FT10"))
        self.assertEqual("instrument_tag", ocr_regions.classify_engineering_text("PS 8"))
        for process_value in ("GPM: 40-72", "PSI: ATM", "SETPOINT 1000 mg/L", "1 MGD"):
            with self.subTest(process_value=process_value):
                self.assertEqual(
                    "process_value",
                    ocr_regions.classify_engineering_text(process_value),
                )

        for process_range in (
            "0 to 500 psia",
            "0 to 10 psid",
            "0 to 3,000 psig",
            "0 to 302.4 g/s",
            "0 to 30.15 slpm",
            "0 to 100%",
        ):
            with self.subTest(process_range=process_range):
                self.assertEqual(
                    "process_value",
                    ocr_regions.classify_engineering_text(process_range),
                )
        for process_value in (
            "5 DEG C",
            "18 DEG F",
            "5℃",
            "18℉",
            "270 ℃",
            "20 mA",
            "24 VDC",
        ):
            with self.subTest(hvac_process_value=process_value):
                self.assertEqual(
                    "process_value",
                    ocr_regions.classify_engineering_text(process_value),
                )
        for tolerance_value in (
            "±.005",
            "+/- 0.1",
            "+.004/-.002",
            "+ .004 / - .002",
            "+0.2 -0.1 mm",
            "0.500 +.005/-.000",
            "H7/g6",
            "1.5% max",
            "9.5 % MAXIMUM",
            "4% min.",
        ):
            with self.subTest(tolerance_value=tolerance_value):
                self.assertEqual(
                    "tolerance_value",
                    ocr_regions.classify_engineering_text(tolerance_value),
                )
        for tolerance_false_positive in (
            "+0.5",
            "1+2",
            "10/20",
            "2026-08-16",
            "1.5%",
            "0 to 100%",
        ):
            with self.subTest(tolerance_false_positive=tolerance_false_positive):
                self.assertNotEqual(
                    "tolerance_value",
                    ocr_regions.classify_engineering_text(tolerance_false_positive),
                )
        self.assertEqual("", ocr_regions.classify_engineering_text("1.5%"))
        self.assertEqual(
            "tolerance_value",
            ocr_regions.classify_engineering_text(
                "1.5%",
                include_civil_slope_values=True,
            ),
        )
        for component_label in ("C10", "R12", "P4", "T13", "U1"):
            with self.subTest(component_label=component_label):
                self.assertEqual(
                    "pin_label",
                    ocr_regions.classify_engineering_text(component_label),
                )
        self.assertEqual("dimension_value", ocr_regions.classify_engineering_text("10 in."))
        for dimension in ('30"', "6'", '2-3/8"', '1 1/2"', '12" O.C.'):
            with self.subTest(dimension=dimension):
                self.assertEqual(
                    "dimension_value",
                    ocr_regions.classify_engineering_text(dimension),
                )
        for dimension in (
            '12"MIN',
            '2" COVER',
            "2'min",
            '#4 @ 12"o.c.',
            '@ 10"0.c.',
            '5"0.c.',
        ):
            with self.subTest(annotated_dimension=dimension):
                self.assertEqual(
                    "dimension_value",
                    ocr_regions.classify_engineering_text(dimension),
                )
        self.assertEqual("equipment_tag", ocr_regions.classify_engineering_text("Donkey boiler"))
        self.assertEqual("equipment_tag", ocr_regions.classify_engineering_text("CENTRIFUGAL PUMP"))
        self.assertEqual(
            "equipment_tag",
            ocr_regions.classify_engineering_text("STEAM GENERATOR FEED PUMPS"),
        )
        self.assertEqual("room_label", ocr_regions.classify_engineering_text("MILL ROOM"))
        self.assertEqual("pin_label", ocr_regions.classify_engineering_text("VERTICAL POST"))
        for false_positive in (
            "this vessel",
            "TO CONDENSER",
            "deliver fuel to boilers",
            "ENGINEERING RECORD",
            "ENGINE ROOM PII",
            "deck machinery and",
            "compressor for on-",
            "ll evaporator",
            "(IPS) will pump into the sewer system",
            "1. Pumps",
            "Start lead pump",
            "Stop all pumps",
            "pump start sequence manual select",
            "PUMP DATA SHEET",
            "PUMP TOTAL DYNAMIC HEAD",
            "MOTOR RATING",
            "Pump Impellers (Typ.)",
            "Valve with Motor",
            "Stormwater Pump Station",
            "1SUCTION BELL BEARING ASSEMBLY",
            "Motor Overload",
            "Pump station Auto operating mode selected",
        ):
            with self.subTest(false_positive=false_positive):
                self.assertEqual("", ocr_regions.classify_engineering_text(false_positive))
        self.assertEqual(
            "",
            ocr_regions.classify_engineering_text(
                "This paragraph explains the entire drawing in detail"
            ),
        )

    def test_opt_in_profiles_cover_pinout_and_mechanical_labels(self) -> None:
        for pin_label in (
            "GP0",
            "GPIO29",
            "PIN40",
            "VBUS",
            "3V3(OUT)",
            "ADC_VREF",
            "I2C0 SDA",
            "SPI1 RX",
            "UART0 TX",
        ):
            with self.subTest(pin_label=pin_label):
                self.assertEqual(
                    "pin_label",
                    ocr_regions.classify_engineering_text(
                        pin_label,
                        include_pcb_pin_signals=True,
                    ),
                )
        for dimension in ("17.78", "1.3(typ)", "R0.8"):
            with self.subTest(dimension=dimension):
                self.assertEqual(
                    "dimension_value",
                    ocr_regions.classify_engineering_text(
                        dimension,
                        include_unitless_dimensions=True,
                    ),
                )
        self.assertEqual("", ocr_regions.classify_engineering_text("17.78"))
        for dimension in ("3", "22", "999", "R10", "Ø26"):
            with self.subTest(integer_dimension=dimension):
                self.assertEqual(
                    "dimension_value",
                    ocr_regions.classify_engineering_text(
                        dimension,
                        include_unitless_integer_dimensions=True,
                    ),
                )
        for integer_false_positive in ("0", "1000", "2026"):
            with self.subTest(integer_false_positive=integer_false_positive):
                self.assertEqual(
                    "",
                    ocr_regions.classify_engineering_text(
                        integer_false_positive,
                        include_unitless_integer_dimensions=True,
                    ),
                )
        self.assertEqual("", ocr_regions.classify_engineering_text("22"))
        self.assertEqual("", ocr_regions.classify_engineering_text("GP0"))
        self.assertEqual(
            "",
            ocr_regions.classify_engineering_text(
                "Figure 2. The pinout",
                include_unitless_dimensions=True,
                include_pcb_pin_signals=True,
            ),
        )

    def test_architectural_room_labels_are_opt_in_and_atomic(self) -> None:
        for room_label in (
            "KITCHEN",
            "PANTRY",
            "HALL",
            "PORCH",
            "BATH",
            "LINEN CLOS",
            "LIVING RM",
        ):
            with self.subTest(room_label=room_label):
                self.assertEqual(
                    "room_label",
                    ocr_regions.classify_engineering_text(
                        room_label,
                        include_architectural_room_labels=True,
                    ),
                )
                self.assertEqual("", ocr_regions.classify_engineering_text(room_label))
        self.assertEqual(
            "",
            ocr_regions.classify_engineering_text(
                "the kitchen is convenient",
                include_architectural_room_labels=True,
            ),
        )

    def test_pid_designators_are_opt_in_and_keep_specific_categories(self) -> None:
        examples = {
            'PG05001-6"-AAAA2-ST-SR': "pipe_line_tag",
            "PG05001-6\u2033-AAAA2-ST-SR": "pipe_line_tag",
            "10-LCV-11010-20000-NPH-250-W10CB01-HC-50": "pipe_line_tag",
            "18-30003-STM-150-W40CB01-HC-100": "pipe_line_tag",
            "01-623-AIR-50-W16CB01": "pipe_line_tag",
            "316-2-G": "pipe_line_tag",
            "301-1/2-Q": "pipe_line_tag",
            "301-Y2-Q": "pipe_line_tag",
            "325-2-0": "pipe_line_tag",
            "VT-05C205-AAAA2-I": "equipment_tag",
            "Flow Sensor": "equipment_tag",
            "Lower Explosive Limit Sensor": "equipment_tag",
            "Trash Gate": "equipment_tag",
            "Diversion Structure": "equipment_tag",
            "Sanitary Sewer Manhole": "equipment_tag",
            "Upstream Storm Drain": "pipe_line_tag",
        }
        for label, category in examples.items():
            with self.subTest(label=label):
                self.assertEqual("", ocr_regions.classify_engineering_text(label))
                self.assertEqual(
                    category,
                    ocr_regions.classify_engineering_text(
                        label,
                        include_pid_labels=True,
                    ),
                )
        self.assertEqual(
            "",
            ocr_regions.classify_engineering_text(
                "EQUIPMENT AND LINE DESIGNATORS",
                include_pid_labels=True,
            ),
        )
        self.assertEqual(
            "",
            ocr_regions.classify_engineering_text(
                "sensor operation manual",
                include_pid_labels=True,
            ),
        )
        for false_positive in (
            "2026-08-16",
            "13-1",
            "2-23",
            "2-6-1",
            "10-SECTION-2",
        ):
            with self.subTest(false_positive=false_positive):
                self.assertEqual(
                    "",
                    ocr_regions.classify_engineering_text(
                        false_positive,
                        include_pid_labels=True,
                    ),
                )

    def test_hvac_control_labels_are_opt_in_and_reject_template_tokens(self) -> None:
        examples = {
            "SA-T-SP": "instrument_tag",
            "MINOA-D-C": "instrument_tag",
            "DTWR-T-HL": "instrument_tag",
            "SEC-PMP-C": "instrument_tag",
            "D-1": "equipment_tag",
            "V-3": "equipment_tag",
            "MS-1": "equipment_tag",
            "HWS": "pipe_line_tag",
            "HTHWR": "pipe_line_tag",
            "CWS": "pipe_line_tag",
        }
        for label, category in examples.items():
            with self.subTest(label=label):
                self.assertEqual(
                    category,
                    ocr_regions.classify_engineering_text(
                        label,
                        include_hvac_control_labels=True,
                    ),
                )
        for false_positive in (
            "ALL-AIR",
            "CLOSE-OFF",
            "DUAL-TEMP",
            "M-613c1",
            "W-X-Y-Z",
            "XX-C",
        ):
            with self.subTest(false_positive=false_positive):
                self.assertEqual(
                    "",
                    ocr_regions.classify_engineering_text(
                        false_positive,
                        include_hvac_control_labels=True,
                    ),
                )

    def test_overlap_dedup_keeps_best_confidence_and_distinct_instances(self) -> None:
        base = {
            "doc_id": "doc",
            "page_index": 0,
            "proposed_text": "PUMP",
            "version_id": "v1",
        }
        rows = [
            {**base, "bbox": [10, 10, 110, 50], "ocr_confidence": 0.95},
            {**base, "bbox": [12, 11, 112, 51], "ocr_confidence": 0.99},
            {**base, "bbox": [300, 10, 400, 50], "ocr_confidence": 0.94},
        ]
        selected = ocr_regions.deduplicate_rows(rows)
        self.assertEqual(2, len(selected))
        self.assertTrue(any(row["bbox"] == [12, 11, 112, 51] for row in selected))
        self.assertTrue(any(row["bbox"] == [300, 10, 400, 50] for row in selected))

    def test_joins_only_nearby_stacked_pid_instrument_tokens(self) -> None:
        detections = [
            {"text": "PI", "score": 0.98, "bbox": [10, 10, 34, 28]},
            {"text": "8", "score": 0.99, "bbox": [15, 31, 29, 48]},
            {"text": "PS", "score": 0.97, "bbox": [110, 10, 136, 28]},
            {"text": "9", "score": 0.99, "bbox": [300, 31, 314, 48]},
        ]

        joined = ocr_regions.join_stacked_pid_instruments(detections)

        self.assertEqual(1, len(joined))
        self.assertEqual("PI 8", joined[0]["text"])
        self.assertEqual([10, 10, 34, 48], joined[0]["bbox"])
        self.assertEqual(0.98, joined[0]["score"])

    def test_pid_only_instrument_prefixes_are_opt_in(self) -> None:
        for label in ("HV 7", "TV 42", "LAHH 91", "LY 91", "HMY 91", "HMS 91", "LV 41"):
            with self.subTest(label=label):
                self.assertEqual("", ocr_regions.classify_engineering_text(label))
                self.assertEqual(
                    "instrument_tag",
                    ocr_regions.classify_engineering_text(label, include_pid_labels=True),
                )

    def test_joins_extended_stacked_pid_instrument_tokens(self) -> None:
        detections = [
            {"text": "LAHH", "score": 0.97, "bbox": [10, 10, 54, 28]},
            {"text": "91", "score": 0.99, "bbox": [22, 31, 42, 48]},
            {"text": "HV", "score": 0.98, "bbox": [110, 10, 136, 28]},
            {"text": "7", "score": 0.96, "bbox": [118, 31, 128, 48]},
        ]

        joined = ocr_regions.join_stacked_pid_instruments(detections)

        self.assertEqual(["LAHH 91", "HV 7"], [row["text"] for row in joined])

    def test_page_proposal_adds_stacked_pid_candidate_only_when_opted_in(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            page = root / "derived" / "pages_300dpi" / "doc" / "page_000.png"
            page.parent.mkdir(parents=True)
            Image.new("RGB", (300, 200), "white").save(page)

            class FakeEngine:
                def __call__(self, _image: np.ndarray) -> SimpleNamespace:
                    return SimpleNamespace(
                        boxes=np.array(
                            [
                                [[10, 20], [34, 20], [34, 38], [10, 38]],
                                [[15, 41], [29, 41], [29, 58], [15, 58]],
                            ],
                            dtype=float,
                        ),
                        txts=("PI", "8"),
                        scores=(0.98, 0.99),
                    )

            rows, _ = ocr_regions.propose_page_rows(
                root=root,
                image_path=page,
                doc_id="doc",
                version_id="v1",
                page_index=0,
                engine=FakeEngine(),
                np_module=np,
                tile_size=500,
                overlap=50,
                min_confidence=0.9,
                padding=5,
                limit_per_page=20,
                max_image_pixels=1_000_000,
                include_pid_labels=True,
            )

            self.assertEqual(1, len(rows))
            self.assertEqual("PI 8", rows[0]["proposed_text"])
            self.assertEqual("instrument_tag", rows[0]["category"])
            self.assertEqual([5, 15, 39, 63], rows[0]["bbox"])

    def test_page_proposal_preserves_geometry_and_never_marks_gold(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            page = root / "derived" / "pages_300dpi" / "doc" / "page_000.png"
            page.parent.mkdir(parents=True)
            Image.new("RGB", (300, 200), "white").save(page)

            class FakeEngine:
                def __call__(self, _image: np.ndarray) -> SimpleNamespace:
                    return SimpleNamespace(
                        boxes=np.array(
                            [[[10, 20], [110, 20], [110, 60], [10, 60]]], dtype=float
                        ),
                        txts=("Donkey boiler",),
                        scores=(0.98,),
                    )

            rows, report = ocr_regions.propose_page_rows(
                root=root,
                image_path=page,
                doc_id="doc",
                version_id="v1",
                page_index=0,
                engine=FakeEngine(),
                np_module=np,
                tile_size=500,
                overlap=50,
                min_confidence=0.9,
                padding=5,
                limit_per_page=20,
                max_image_pixels=1_000_000,
            )

            self.assertEqual(1, report["tiles"])
            self.assertEqual(1, len(rows))
            self.assertEqual([5, 15, 115, 65], rows[0]["bbox"])
            self.assertEqual("equipment_tag", rows[0]["category"])
            self.assertEqual("needs_review", rows[0]["review_status"])
            self.assertEqual("", rows[0]["target_text"])
            self.assertEqual(rows[0]["candidate_id"], ocr_regions.candidate_id(rows[0]))

    def test_page_proposal_can_capture_unclassified_text_only_when_opted_in(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            page = root / "derived" / "pages_300dpi" / "doc" / "page_000.png"
            page.parent.mkdir(parents=True)
            Image.new("RGB", (300, 200), "white").save(page)

            class FakeEngine:
                def __call__(self, _image: np.ndarray) -> SimpleNamespace:
                    return SimpleNamespace(
                        boxes=np.array(
                            [[[10, 20], [110, 20], [110, 60], [10, 60]]], dtype=float
                        ),
                        txts=("LN2 Dump Tanker",),
                        scores=(0.98,),
                    )

            common = {
                "root": root,
                "image_path": page,
                "doc_id": "doc",
                "version_id": "v1",
                "page_index": 0,
                "engine": FakeEngine(),
                "np_module": np,
                "tile_size": 500,
                "overlap": 50,
                "min_confidence": 0.9,
                "padding": 5,
                "limit_per_page": 20,
                "max_image_pixels": 1_000_000,
            }
            rows, _ = ocr_regions.propose_page_rows(**common)
            self.assertEqual([], rows)

            rows, _ = ocr_regions.propose_page_rows(
                **common,
                include_unclassified=True,
            )
            self.assertEqual(1, len(rows))
            self.assertEqual("unknown_microtext", rows[0]["category"])

    def test_page_proposal_restores_downscaled_detector_geometry(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            page = root / "derived" / "pages_300dpi" / "doc" / "page_000.png"
            page.parent.mkdir(parents=True)
            Image.new("RGB", (300, 200), "white").save(page)

            class FakeEngine:
                def __call__(self, image: np.ndarray) -> SimpleNamespace:
                    self.assertEqual((100, 150), image.shape[:2])
                    return SimpleNamespace(
                        boxes=np.array(
                            [[[5, 10], [55, 10], [55, 30], [5, 30]]], dtype=float
                        ),
                        txts=("Donkey boiler",),
                        scores=(0.98,),
                    )

                def assertEqual(self, expected: object, actual: object) -> None:
                    if expected != actual:
                        raise AssertionError(f"{expected!r} != {actual!r}")

            rows, report = ocr_regions.propose_page_rows(
                root=root,
                image_path=page,
                doc_id="doc",
                version_id="v1",
                page_index=0,
                engine=FakeEngine(),
                np_module=np,
                tile_size=500,
                overlap=50,
                min_confidence=0.9,
                padding=5,
                limit_per_page=20,
                max_image_pixels=1_000_000,
                ocr_scale=0.5,
            )

            self.assertEqual([5, 15, 115, 65], rows[0]["bbox"])
            self.assertEqual(0.5, report["ocr_scale"])
            self.assertIn("ocr_scale=0.5", rows[0]["review_notes"])

    def test_page_proposal_restores_upscaled_detector_geometry(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            page = root / "derived" / "pages_300dpi" / "doc" / "page_000.png"
            page.parent.mkdir(parents=True)
            Image.new("RGB", (300, 200), "white").save(page)

            class FakeEngine:
                def __call__(self, image: np.ndarray) -> SimpleNamespace:
                    if image.shape[:2] != (400, 600):
                        raise AssertionError(f"unexpected analysis size: {image.shape[:2]}")
                    return SimpleNamespace(
                        boxes=np.array(
                            [[[20, 40], [220, 40], [220, 120], [20, 120]]], dtype=float
                        ),
                        txts=("Donkey boiler",),
                        scores=(0.98,),
                    )

            rows, report = ocr_regions.propose_page_rows(
                root=root,
                image_path=page,
                doc_id="doc",
                version_id="v1",
                page_index=0,
                engine=FakeEngine(),
                np_module=np,
                tile_size=500,
                overlap=50,
                min_confidence=0.9,
                padding=5,
                limit_per_page=20,
                max_image_pixels=1_000_000,
                ocr_scale=2.0,
            )

            self.assertEqual([5, 15, 115, 65], rows[0]["bbox"])
            self.assertEqual(2.0, report["ocr_scale"])
            self.assertIn("ocr_scale=2.0", rows[0]["review_notes"])
