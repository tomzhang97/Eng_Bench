import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "preflight_human_return_bundle.py"


def load_module():
    tools_path = str(ROOT / "tools")
    if tools_path not in sys.path:
        sys.path.insert(0, tools_path)
    spec = importlib.util.spec_from_file_location("preflight_human_return_bundle", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PreflightHumanReturnBundleUnittest(unittest.TestCase):
    def test_preflight_marks_complete_returned_packet_processable(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            returned_root = root / "returned"
            packet_root = returned_root / "packet_demo"
            pack_dir = packet_root / "review_packs" / "micro_pack"
            pack_dir.mkdir(parents=True)
            (packet_root / "NEXT_REVIEW_BATCH_MANIFEST.csv").write_text(
                "pack_name,checklist,source_jsonl,rows,kind\n"
                "micro_pack,micro_validation_checklist.csv,microtext/review.jsonl,2,microtext\n",
                encoding="utf-8",
            )
            (pack_dir / "micro_validation_checklist.csv").write_text(
                "candidate_id,review_status,proposed_text,corrected_text\n"
                "mt_001,accepted,P-101,\n"
                "mt_002,edited,P-10Z,P-102\n",
                encoding="utf-8",
            )
            manifest_path = root / "delivery_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "packets": [
                            {
                                "packet_id": "packet_demo",
                                "folder_path": "derived/human_adjudication/packet_demo",
                                "zip_path": "derived/human_adjudication/packet_demo.zip",
                                "review_rows": 2,
                                "deliverable": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = module.preflight_bundle(
                root=root,
                delivery_manifest_path=manifest_path,
                returned_root=returned_root,
                return_label="return_demo",
            )

        self.assertEqual(report["totals"]["expected_packets"], 1)
        self.assertEqual(report["totals"]["found_packets"], 1)
        self.assertEqual(report["totals"]["processable_packets"], 1)
        self.assertEqual(report["totals"]["review_rows"], 2)
        self.assertEqual(report["totals"]["blank_rows"], 0)
        self.assertEqual(report["totals"]["issues"], 0)
        packet = report["packets"][0]
        self.assertTrue(packet["processable"])
        self.assertIn("process_next_review_batch_return.py", packet["process_command"])
        self.assertIn("returned", packet["process_command"])
        self.assertEqual(packet["issues"], [])


if __name__ == "__main__":
    unittest.main()
