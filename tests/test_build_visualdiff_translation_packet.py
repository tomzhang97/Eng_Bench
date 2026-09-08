import json
import tempfile
import unittest
from pathlib import Path

from tools.build_visualdiff_translation_packet import build_packet


class BuildVisualdiffTranslationPacketTest(unittest.TestCase):
    def test_builds_checklist_and_copies_panel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            panel = root / "derived/review_packs/demo/panels/pair.png"
            panel.parent.mkdir(parents=True)
            panel.write_bytes(b"png")
            source = root / "rows.jsonl"
            source.write_text(
                json.dumps(
                    {
                        "_review_pack": "demo",
                        "panel_path": "panels/pair.png",
                        "pair_id": "pair",
                        "project_id": "project",
                        "old_text": "A",
                        "new_text": "B",
                        "human_description": "中文描述",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            output = root / "packet"
            report = build_packet(root, source, output)

            self.assertEqual(report["rows"], 1)
            self.assertEqual(report["panels_copied"], 1)
            self.assertTrue((output / "evidence/001__pair.png").is_file())
            self.assertIn(
                "english_description",
                (output / "visualdiff_english_confirmation_checklist.csv").read_text(
                    encoding="utf-8-sig"
                ),
            )


if __name__ == "__main__":
    unittest.main()
