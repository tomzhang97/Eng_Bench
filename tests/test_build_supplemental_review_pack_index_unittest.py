import csv
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools import build_supplemental_review_pack_index


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class SupplementalReviewPackIndexUnittest(unittest.TestCase):
    def test_index_summarizes_pack_readiness_and_missing_refs(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            good = root / "derived" / "review_packs" / "good_microtext"
            crop = good / "crops" / "row1.png"
            page = good / "pages" / "row1.png"
            source = root / "derived" / "pages_300dpi" / "doc_a" / "page_000.png"
            crop.parent.mkdir(parents=True)
            page.parent.mkdir(parents=True)
            source.parent.mkdir(parents=True)
            crop.write_bytes(b"crop")
            page.write_bytes(b"page")
            source.write_bytes(b"source")
            (good / "index.html").write_text("<html></html>", encoding="utf-8")
            (good / "HUMAN_REVIEW_STEPS.md").write_text("# steps\n", encoding="utf-8")
            write_jsonl(
                good / "manifest.jsonl",
                [
                    {
                        "candidate_id": "mt1",
                        "crop_path": "derived/review_packs/good_microtext/crops/row1.png",
                        "page_path": "derived/review_packs/good_microtext/pages/row1.png",
                        "image_path": "derived/pages_300dpi/doc_a/page_000.png",
                    }
                ],
            )
            write_csv(
                good / "good_microtext_checklist.csv",
                [
                    {
                        "candidate_id": "mt1",
                        "review_status": "",
                        "proposed_text": "7 bar",
                        "corrected_text": "",
                    }
                ],
            )

            broken = root / "derived" / "review_packs" / "broken_pack"
            write_jsonl(
                broken / "manifest.jsonl",
                [
                    {
                        "candidate_id": "bad1",
                        "crop_path": "derived/review_packs/broken_pack/crops/missing.png",
                    }
                ],
            )

            report = build_supplemental_review_pack_index.build_report(root)

        by_id = {row["packet_id"]: row for row in report["packs"]}
        self.assertEqual(report["totals"]["packs"], 2)
        self.assertEqual(report["totals"]["manifest_rows"], 2)
        self.assertEqual(report["totals"]["checklist_rows"], 1)
        self.assertEqual(report["totals"]["blank_rows"], 1)
        self.assertEqual(report["totals"]["missing_evidence_refs"], 1)
        self.assertTrue(by_id["good_microtext"]["ready_to_send"])
        self.assertFalse(by_id["good_microtext"]["human_complete"])
        self.assertEqual(by_id["good_microtext"]["blank_rows"], 1)
        self.assertFalse(by_id["broken_pack"]["ready_to_send"])
        self.assertEqual(by_id["broken_pack"]["missing_evidence_refs"], 1)
        self.assertIn("missing_checklist", by_id["broken_pack"]["issues"])

    def test_cli_writes_json_markdown_and_csv(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pack = root / "derived" / "review_packs" / "empty"
            write_jsonl(pack / "manifest.jsonl", [{"candidate_id": "mt1"}])
            out_json = root / "out.json"
            out_md = root / "out.md"
            out_csv = root / "out.csv"

            exit_code = build_supplemental_review_pack_index.main(
                [
                    "--root",
                    str(root),
                    "--output-json",
                    str(out_json),
                    "--output-md",
                    str(out_md),
                    "--output-csv",
                    str(out_csv),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(out_json.exists())
            self.assertTrue(out_md.exists())
            self.assertTrue(out_csv.exists())
            self.assertEqual(json.loads(out_json.read_text(encoding="utf-8"))["totals"]["packs"], 1)

    def test_index_falls_back_for_legacy_windows_encoded_checklists(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pack = root / "derived" / "review_packs" / "legacy"
            write_jsonl(pack / "manifest.jsonl", [{"candidate_id": "mt1"}])
            (pack / "index.html").write_text("<html></html>", encoding="utf-8")
            (pack / "README.md").write_text("# legacy\n", encoding="utf-8")
            (pack / "legacy_checklist.csv").write_bytes(
                "candidate_id,review_status,proposed_text\nmt1,accepted,7 bar ¿legacy?\n".encode(
                    "cp1252"
                )
            )

            report = build_supplemental_review_pack_index.build_report(root)

        row = report["packs"][0]
        self.assertEqual(row["checklist_rows"], 1)
        self.assertEqual(row["invalid_rows"], 0)
        self.assertIn("non_utf8_csv:legacy_checklist.csv", row["issues"])


if __name__ == "__main__":
    unittest.main()
