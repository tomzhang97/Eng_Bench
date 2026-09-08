import tempfile
import unittest
from pathlib import Path

from tools.build_file_inventory import inventory_rows, write_inventory
from tools.verify_file_inventory import verify_inventory


class VerifyFileInventoryTests(unittest.TestCase):
    def test_accepts_exact_inventory_and_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data.txt"
            data.write_text("original", encoding="utf-8")
            inventory = root / "inventory.csv"
            write_inventory(inventory, inventory_rows(root, inventory))

            self.assertTrue(verify_inventory(root, inventory)["valid"])

            data.write_text("tampered", encoding="utf-8")
            report = verify_inventory(root, inventory)
            self.assertFalse(report["valid"])
            self.assertEqual(report["hash_mismatches"], ["data.txt"])

    def test_detects_unlisted_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "listed.txt").write_text("listed", encoding="utf-8")
            inventory = root / "inventory.csv"
            write_inventory(inventory, inventory_rows(root, inventory))
            (root / "extra.txt").write_text("extra", encoding="utf-8")

            report = verify_inventory(root, inventory)

            self.assertFalse(report["valid"])
            self.assertEqual(report["unlisted_files"], ["extra.txt"])


if __name__ == "__main__":
    unittest.main()
