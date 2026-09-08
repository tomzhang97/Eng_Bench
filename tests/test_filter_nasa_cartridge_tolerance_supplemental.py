import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools import filter_nasa_cartridge_tolerance_candidates as base
from tools import filter_nasa_cartridge_tolerance_supplemental as module
from tests.test_filter_nasa_cartridge_tolerance_candidates import row, write_png_header


class FilterNasaCartridgeToleranceSupplementalTests(unittest.TestCase):
    def test_recovers_only_unselected_rows_inside_confidence_window(self) -> None:
        rows = [
            row("recover", confidence=0.95),
            row("prior-low", confidence=0.94, bbox=[450, 200, 550, 250]),
            row("prior-high", confidence=0.99, bbox=[450, 300, 550, 350]),
            row("too-low", confidence=0.89, bbox=[450, 400, 550, 450]),
            row("outside", confidence=0.95, bbox=[600, 500, 660, 550]),
        ]
        prior = [rows[1], rows[2]]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_021.png")
            selected, held, report = module.recover_supplemental(
                rows,
                prior,
                root=root,
                min_confidence=0.90,
                upper_confidence_exclusive=0.98,
            )

        self.assertEqual(["recover"], [item["candidate_id"] for item in selected])
        self.assertFalse(selected[0]["safe_to_merge_gold"])
        reasons = {item["candidate_id"]: item["machine_hold_reason"] for item in held}
        self.assertEqual("previously_selected", reasons["prior-low"])
        self.assertEqual("previously_selected", reasons["prior-high"])
        self.assertEqual("below_confidence_floor", reasons["too-low"])
        self.assertEqual("outside_tolerance_columns", reasons["outside"])
        self.assertEqual(0, report["prior_overlap_rows"])

    def test_rejects_prior_ids_missing_from_raw_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_021.png")
            with self.assertRaisesRegex(ValueError, "missing from raw input"):
                module.recover_supplemental(
                    [row("raw")],
                    [row("not-raw")],
                    root=root,
                    min_confidence=0.90,
                    upper_confidence_exclusive=0.98,
                )

    def test_direct_cli_writes_hash_bound_review_only_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_png_header(root / "pages/page_021.png")
            raw_path = root / "raw.jsonl"
            prior_path = root / "prior.jsonl"
            raw_path.write_text(
                "".join(
                    json.dumps(item, ensure_ascii=False) + "\n"
                    for item in (
                        row("recover", confidence=0.95),
                        row("prior", confidence=0.99, bbox=[450, 200, 550, 250]),
                    )
                ),
                encoding="utf-8",
            )
            prior_path.write_text(
                json.dumps(row("prior", confidence=0.99, bbox=[450, 200, 550, 250]))
                + "\n",
                encoding="utf-8",
            )
            selected_path = root / "selected.jsonl"
            held_path = root / "held.jsonl"
            report_path = root / "report.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(module.__file__).resolve()),
                    "--root",
                    str(root),
                    "--input",
                    str(raw_path),
                    "--prior-selected",
                    str(prior_path),
                    "--selected-output",
                    str(selected_path),
                    "--held-output",
                    str(held_path),
                    "--report-json",
                    str(report_path),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(1, report["recovered_rows"])
        self.assertEqual(64, len(report["selected_sha256"]))
        self.assertFalse(report["safe_to_merge_gold"])


if __name__ == "__main__":
    unittest.main()
