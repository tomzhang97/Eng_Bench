import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools.qa_manifest_doc_contact_sheets import page_inventory, read_doc_rows, render_sheets


class ManifestDocContactSheetTests(unittest.TestCase):
    def test_inventory_and_render_include_every_page(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pages_dir = root / "derived" / "pages_300dpi" / "sample_doc"
            pages_dir.mkdir(parents=True)
            Image.new("L", (800, 600), "white").save(pages_dir / "page_000.png")
            Image.new("L", (800, 600), "white").save(pages_dir / "page_001.png")
            manifest = root / "additions.jsonl"
            manifest.write_text(
                json.dumps(
                    {
                        "type": "doc",
                        "doc_id": "sample_doc",
                        "source_candidate_id": "pcb_test",
                        "version": {"revision": "a"},
                        "derived": {"pages_dir": "derived/pages_300dpi/sample_doc"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            docs = read_doc_rows(manifest)
            pages = page_inventory(root, docs)
            sheets = render_sheets(pages, root / "qa", columns=1, rows=1, cell_width=400, cell_height=320)

            self.assertEqual(1, len(docs))
            self.assertEqual(2, len(pages))
            self.assertEqual(2, len(sheets))
            self.assertTrue((root / "qa" / "contact_sheet_01.png").is_file())
            self.assertTrue((root / "qa" / "contact_sheet_02.png").is_file())


if __name__ == "__main__":
    unittest.main()
