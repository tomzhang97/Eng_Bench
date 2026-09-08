import csv
import json
import tempfile
import unittest
from pathlib import Path

from tools.preflight_visualdiff_return import build_report, write_outputs


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"png")


class PreflightVisualdiffReturnTest(unittest.TestCase):
    def test_stages_mergeable_rows_and_holds_duplicates_without_mutating_gold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            handoff = root / "handoff"
            pack = handoff / "review_packs" / "visualdiff_demo"
            manifest = [
                {
                    "pair_id": "p_accept",
                    "description": "CHANGE_DESC_GT_TODO",
                    "old_crop_path": "old/p_accept.png",
                    "new_crop_path": "new/p_accept.png",
                    "panel_path": "panels/p_accept.png",
                    "old_page_path": "pages_old/old.png",
                    "new_page_path": "pages_new/new.png",
                },
                {
                    "pair_id": "p_reject",
                    "description": "CHANGE_DESC_GT_TODO",
                    "old_crop_path": "old/p_reject.png",
                    "new_crop_path": "new/p_reject.png",
                    "panel_path": "panels/p_reject.png",
                    "old_page_path": "pages_old/old.png",
                    "new_page_path": "pages_new/new.png",
                },
                {
                    "pair_id": "p_dup",
                    "description": "CHANGE_DESC_GT_TODO",
                    "old_crop_path": "old/p_dup.png",
                    "new_crop_path": "new/p_dup.png",
                    "panel_path": "panels/p_dup.png",
                    "old_page_path": "pages_old/old.png",
                    "new_page_path": "pages_new/new.png",
                },
                {
                    "pair_id": "p_blank",
                    "description": "CHANGE_DESC_GT_TODO",
                    "old_crop_path": "old/p_blank.png",
                    "new_crop_path": "new/p_blank.png",
                    "panel_path": "panels/p_blank.png",
                    "old_page_path": "pages_old/old.png",
                    "new_page_path": "pages_new/new.png",
                },
            ]
            write_jsonl(pack / "manifest.jsonl", manifest)
            for row in manifest:
                for field in ("old_crop_path", "new_crop_path", "panel_path", "old_page_path", "new_page_path"):
                    touch(pack / row[field])
            write_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [{"pair_id": "p_dup"}])
            write_csv(
                handoff / "visualdiff_selected_rows_validation_checklist.csv",
                [
                    {
                        "packet_id": "visualdiff_demo",
                        "review_pack": "visualdiff_demo",
                        "pair_id": "p_accept",
                        "human_status": "accepted",
                        "human_description": "The revision date changed from 2023-24 to 2024-25.",
                        "human_notes": "",
                    },
                    {
                        "packet_id": "visualdiff_demo",
                        "review_pack": "visualdiff_demo",
                        "pair_id": "p_reject",
                        "human_status": "rejected",
                        "human_description": "",
                        "human_notes": "old/new identical",
                    },
                    {
                        "packet_id": "visualdiff_demo",
                        "review_pack": "visualdiff_demo",
                        "pair_id": "p_dup",
                        "human_status": "edited",
                        "human_description": "A label moved relative to the nearby table.",
                        "human_notes": "",
                    },
                    {
                        "packet_id": "visualdiff_demo",
                        "review_pack": "visualdiff_demo",
                        "pair_id": "p_blank",
                        "human_status": "",
                        "human_description": "",
                        "human_notes": "",
                    },
                ],
            )

            report = build_report(root=root, handoff_dir=handoff)

            self.assertFalse(report["ready_to_merge"])
            self.assertEqual(4, report["totals"]["manifest_rows"])
            self.assertEqual(4, report["totals"]["checklist_rows"])
            self.assertEqual(1, report["totals"]["mergeable_rows"])
            self.assertEqual(1, report["totals"]["duplicate_active_rows"])
            self.assertEqual(1, report["totals"]["rejected_rows"])
            self.assertEqual(1, report["totals"]["blank_rows"])
            self.assertEqual("p_accept", report["mergeable_rows"][0]["pair_id"])
            self.assertEqual("p_dup", report["hold_rows"][0]["pair_id"])

    def test_writes_report_markdown_and_jsonl_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            handoff = root / "handoff"
            pack = handoff / "review_packs" / "visualdiff_demo"
            row = {
                "pair_id": "p_accept",
                "description": "CHANGE_DESC_GT_TODO",
                "old_crop_path": "old/p_accept.png",
                "new_crop_path": "new/p_accept.png",
                "panel_path": "panels/p_accept.png",
                "old_page_path": "pages_old/old.png",
                "new_page_path": "pages_new/new.png",
            }
            write_jsonl(pack / "manifest.jsonl", [row])
            for field in ("old_crop_path", "new_crop_path", "panel_path", "old_page_path", "new_page_path"):
                touch(pack / row[field])
            write_csv(
                handoff / "visualdiff_selected_rows_validation_checklist.csv",
                [
                    {
                        "packet_id": "visualdiff_demo",
                        "review_pack": "visualdiff_demo",
                        "pair_id": "p_accept",
                        "human_status": "edited",
                        "human_description": "A connector label changed.",
                        "human_notes": "",
                    }
                ],
            )
            report = build_report(root=root, handoff_dir=handoff)
            output_json = root / "report.json"
            output_md = root / "report.md"
            mergeable = root / "mergeable.jsonl"
            hold = root / "hold.jsonl"

            write_outputs(
                report,
                output_json=output_json,
                output_md=output_md,
                mergeable_jsonl=mergeable,
                hold_jsonl=hold,
            )

            self.assertTrue(report["ready_to_merge"])
            self.assertIn("VisualDiff Return Preflight", output_md.read_text(encoding="utf-8"))
            mergeable_rows = [json.loads(line) for line in mergeable.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(1, len(mergeable_rows))
            self.assertEqual("A connector label changed.", mergeable_rows[0]["description"])
            self.assertEqual("", hold.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
