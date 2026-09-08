import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class LatestHumanPacketIndexTests(unittest.TestCase):
    def test_same_day_suffixed_index_supersedes_plain_date_index(self):
        mod = load_module(
            "stronger_complete_status_latest_index",
            ROOT / "tools" / "stronger_complete_status.py",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            quality = root / "derived" / "quality"
            quality.mkdir(parents=True)
            (quality / "human_packet_index_2026-06-11.json").write_text(
                json.dumps({"date_label": "2026-06-11", "packets": 9}),
                encoding="utf-8",
            )
            (quality / "human_packet_index_2026-06-11-feather.json").write_text(
                json.dumps({"date_label": "2026-06-11-feather", "packets": 10}),
                encoding="utf-8",
            )
            (quality / "human_packet_index_2026-06-06.json").write_text(
                json.dumps({"date_label": "2026-06-06", "packets": 7}),
                encoding="utf-8",
            )

            latest = mod.latest_human_packet_index(root)

            self.assertEqual(latest["date_label"], "2026-06-11-feather")
            self.assertEqual(latest["packets"], 10)

    def test_verified_compatible_handoff_supersedes_older_packet_index(self):
        mod = load_module(
            "stronger_complete_status_latest_compat",
            ROOT / "tools" / "stronger_complete_status.py",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            quality = root / "derived" / "quality"
            quality.mkdir(parents=True)
            (quality / "human_packet_index_2026-06-11-feather.json").write_text(
                json.dumps(
                    {
                        "date_label": "2026-06-11-feather",
                        "totals": {
                            "review_rows": 2117,
                            "packets": 10,
                            "ready_to_send": 10,
                            "missing_evidence_refs": 0,
                            "overlap_count": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )
            (quality / "compatible_handoff_verification_2026-06-15.json").write_text(
                json.dumps(
                    {
                        "handoff_dir": {
                            "valid": True,
                            "path": "derived/human_adjudication/hv_0615_compat",
                            "packet_folders": 11,
                            "worklist_review_rows": 2158,
                            "totals": {
                                "checklist_rows": 2289,
                                "png_files": 3308,
                            },
                            "packets": [
                                {"packet": "p01", "valid": True},
                                {"packet": "p02", "valid": True},
                            ],
                        },
                        "split_dir": {
                            "valid": True,
                            "zip_count": 5,
                        },
                    }
                ),
                encoding="utf-8",
            )

            latest = mod.latest_human_packet_index(root)

            self.assertEqual(latest["date_label"], "2026-06-15-compatible")
            self.assertEqual(latest["source"], "compatible_handoff_verification")
            self.assertEqual(
                latest["source_report"],
                "derived/quality/compatible_handoff_verification_2026-06-15.json",
            )
            self.assertEqual(latest["totals"]["review_rows"], 2158)
            self.assertEqual(latest["totals"]["packets"], 11)
            self.assertEqual(latest["totals"]["ready_to_send"], 11)
            self.assertEqual(latest["totals"]["missing_evidence_refs"], 0)
            self.assertEqual(latest["totals"]["overlap_count"], 0)


if __name__ == "__main__":
    unittest.main()
