import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.update_manifest_doc_metadata import read_jsonl, update_doc


class UpdateManifestDocMetadataTest(unittest.TestCase):
    def test_updates_only_requested_metadata_after_hash_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = root / "doc.pdf"
            payload.write_bytes(b"payload")
            digest = hashlib.sha256(b"payload").hexdigest()
            manifest = root / "manifest.jsonl"
            manifest.write_text(
                json.dumps(
                    {
                        "type": "doc",
                        "doc_id": "demo",
                        "path": "doc.pdf",
                        "sha256": digest,
                        "version": {"imported": "2026-01-01"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            report = update_doc(
                root,
                manifest,
                "demo",
                {"version": {"revision": "v1.1", "imported": "2026-01-01"}},
                digest,
                apply=True,
            )

            row = read_jsonl(manifest)[0]
            self.assertTrue(report["valid"])
            self.assertTrue(report["applied"])
            self.assertEqual(row["version"]["revision"], "v1.1")
            self.assertEqual(row["sha256"], digest)

    def test_rejects_protected_key_and_hash_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = root / "doc.pdf"
            payload.write_bytes(b"payload")
            digest = hashlib.sha256(b"payload").hexdigest()
            manifest = root / "manifest.jsonl"
            original = {
                "type": "doc",
                "doc_id": "demo",
                "path": "doc.pdf",
                "sha256": digest,
            }
            manifest.write_text(json.dumps(original) + "\n", encoding="utf-8")

            report = update_doc(
                root,
                manifest,
                "demo",
                {"path": "other.pdf"},
                "0" * 64,
                apply=True,
            )

            self.assertFalse(report["valid"])
            self.assertFalse(report["applied"])
            self.assertEqual(read_jsonl(manifest)[0], original)


if __name__ == "__main__":
    unittest.main()
