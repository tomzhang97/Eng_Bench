import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "sync_packet_zip_docs.py"


def load_module():
    spec = importlib.util.spec_from_file_location("sync_packet_zip_docs", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SyncPacketZipDocsUnittest(unittest.TestCase):
    def test_syncs_root_docs_into_ready_packet_zip_preserving_archive_root(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            packet_dir = root / "derived" / "human_adjudication" / "packet_a"
            packet_dir.mkdir(parents=True)
            (packet_dir / "README.md").write_text("fresh readme\n", encoding="utf-8")
            fresh_steps = "\n".join(
                [
                    "# Human Review Steps",
                    "- If old/new crops are visually identical, use `reject_unclear`.",
                    "- If the only difference is a whole-crop/page alignment shift, use `reject_unclear`.",
                    "- Use `layout` only when a specific object, label, symbol, wire, table cell, or other drawing element moved.",
                    "",
                ]
            )
            (packet_dir / "HUMAN_REVIEW_STEPS.md").write_text(fresh_steps, encoding="utf-8")
            (packet_dir / "payload.txt").write_text("keep me\n", encoding="utf-8")

            zip_path = root / "derived" / "human_adjudication" / "packet_a.zip"
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("archive_root/HUMAN_REVIEW_STEPS.md", "stale steps\n")
                zf.writestr("archive_root/README.md", "stale readme\n")
                zf.writestr("archive_root/payload.txt", "keep me\n")

            index_path = root / "derived" / "quality" / "human_packet_index.json"
            index_path.parent.mkdir(parents=True)
            index_path.write_text(
                json.dumps(
                    {
                        "packets": [
                            {
                                "packet_id": "packet_a",
                                "folder_path": "derived/human_adjudication/packet_a",
                                "zip_path": "derived/human_adjudication/packet_a.zip",
                                "ready_to_send": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = module.sync_from_index(root=root, index_path=index_path)

            self.assertEqual(report["totals"]["packets_considered"], 1)
            self.assertEqual(report["totals"]["updated_zips"], 1)
            self.assertEqual(report["totals"]["issues"], 0)
            with zipfile.ZipFile(zip_path) as zf:
                self.assertEqual(
                    zf.read("archive_root/HUMAN_REVIEW_STEPS.md"),
                    (packet_dir / "HUMAN_REVIEW_STEPS.md").read_bytes(),
                )
                self.assertEqual(
                    zf.read("archive_root/README.md"),
                    (packet_dir / "README.md").read_bytes(),
                )
                self.assertEqual(zf.read("archive_root/payload.txt").decode("utf-8"), "keep me\n")


if __name__ == "__main__":
    unittest.main()
