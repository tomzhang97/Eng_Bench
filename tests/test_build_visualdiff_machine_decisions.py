import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.build_visualdiff_machine_decisions import build_decisions


def write_jsonl(path: Path, count: int) -> None:
    path.write_text(
        "".join(json.dumps({"pair_id": f"pair_{index}"}) + "\n" for index in range(count)),
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


class BuildVisualDiffMachineDecisionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input_path = self.root / "input.jsonl"
        self.spec_path = self.root / "holds.json"
        write_jsonl(self.input_path, 8)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_spec(self, payload: dict) -> None:
        self.spec_path.write_text(json.dumps(payload), encoding="utf-8")

    def test_expands_ranges_and_derives_full_keep_coverage(self) -> None:
        self.write_spec(
            {
                "input_sha256": sha256(self.input_path),
                "expected_keep_rows": [2, 4, 5],
                "holds": [
                    {"row_number": 1, "reason": "single"},
                    {"rows": [3, 6], "reason": "list"},
                    {"start": 7, "end": 8, "reason": "range"},
                ],
                "corrections": [
                    {"row_number": 2, "description": "A corrected change."}
                ],
            }
        )
        decisions = build_decisions(self.input_path, self.spec_path)
        self.assertEqual([2, 4, 5], decisions["keep_rows"])
        self.assertEqual([1, 3, 6, 7, 8], [row["row_number"] for row in decisions["holds"]])
        self.assertEqual(sha256(self.input_path), decisions["input_sha256"])

    def test_rejects_stale_optional_sha(self) -> None:
        self.write_spec({"input_sha256": "0" * 64, "holds": []})
        with self.assertRaisesRegex(ValueError, "SHA mismatch"):
            build_decisions(self.input_path, self.spec_path)

    def test_rejects_duplicate_hold_rows_across_selectors(self) -> None:
        self.write_spec(
            {
                "holds": [
                    {"rows": [2, 3], "reason": "first"},
                    {"start": 3, "end": 4, "reason": "second"},
                ]
            }
        )
        with self.assertRaisesRegex(ValueError, "duplicate hold row_number: 3"):
            build_decisions(self.input_path, self.spec_path)

    def test_rejects_out_of_range_hold(self) -> None:
        self.write_spec({"holds": [{"row_number": 9, "reason": "bad"}]})
        with self.assertRaisesRegex(ValueError, "out of range"):
            build_decisions(self.input_path, self.spec_path)

    def test_rejects_correction_for_held_row(self) -> None:
        self.write_spec(
            {
                "holds": [{"row_number": 2, "reason": "hold"}],
                "corrections": [{"row_number": 2, "description": "No."}],
            }
        )
        with self.assertRaisesRegex(ValueError, "held row cannot also be corrected"):
            build_decisions(self.input_path, self.spec_path)

    def test_rejects_unexpected_derived_keep_row(self) -> None:
        self.write_spec(
            {
                "expected_keep_rows": [1, 2],
                "holds": [{"start": 3, "end": 7, "reason": "hold"}],
            }
        )
        with self.assertRaisesRegex(ValueError, r"unexpected=\[8\]"):
            build_decisions(self.input_path, self.spec_path)


if __name__ == "__main__":
    unittest.main()
