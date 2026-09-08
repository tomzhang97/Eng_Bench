import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "build_handoff_delivery_manifest.py"


def load_module():
    spec = importlib.util.spec_from_file_location("build_handoff_delivery_manifest", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildHandoffDeliveryManifestUnittest(unittest.TestCase):
    def test_manifest_records_zip_hashes_and_visualdiff_instruction_status(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            zip_path = root / "derived" / "human_adjudication" / "packet_a.zip"
            zip_path.parent.mkdir(parents=True)
            steps = "\n".join(
                [
                    "# Human Review Steps",
                    "- If old/new crops are visually identical, use `reject_unclear`.",
                    "- If the only difference is a whole-crop/page alignment shift, use `reject_unclear`.",
                    "- Use `edit`, not `layout`, when a specific object, label, symbol, wire, table cell, or other drawing element moved.",
                    "",
                ]
            )
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("packet_a/README.md", "read me\n")
                zf.writestr("packet_a/HUMAN_REVIEW_STEPS.md", steps)
                zf.writestr("packet_a/review_packs/visualdiff_demo/manifest.jsonl", "{}\n")

            index_path = root / "derived" / "quality" / "human_packet_index.json"
            index_path.parent.mkdir(parents=True)
            index_path.write_text(
                json.dumps(
                    {
                        "date_label": "demo",
                        "packets": [
                            {
                                "packet_id": "packet_a",
                                "kind": "standalone_review_batch",
                                "zip_path": "derived/human_adjudication/packet_a.zip",
                                "folder_path": "derived/human_adjudication/packet_a",
                                "ready_to_send": True,
                                "verified": True,
                                "review_rows": 3,
                            },
                            {
                                "packet_id": "packet_b",
                                "zip_path": "derived/human_adjudication/packet_b.zip",
                                "ready_to_send": False,
                                "review_rows": 99,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            manifest = module.build_manifest(root=root, index_path=index_path, date_label="demo")

        self.assertEqual(manifest["totals"]["packets"], 1)
        self.assertEqual(manifest["totals"]["skipped_unready_packets"], 1)
        self.assertEqual(manifest["totals"]["review_rows"], 3)
        self.assertEqual(manifest["totals"]["issues"], 0)
        packet = manifest["packets"][0]
        self.assertEqual(packet["packet_id"], "packet_a")
        self.assertEqual(len(packet["sha256"]), 64)
        self.assertGreater(packet["zip_bytes"], 0)
        self.assertEqual(packet["entry_count"], 3)
        self.assertEqual(packet["nested_zip_entries"], 0)
        self.assertTrue(packet["archived_readme"])
        self.assertTrue(packet["archived_human_steps"])
        self.assertTrue(packet["visualdiff_content"])
        self.assertTrue(packet["visualdiff_instruction_rule_ok"])
        self.assertEqual(packet["issues"], [])


if __name__ == "__main__":
    unittest.main()
