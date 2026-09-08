import csv
import hashlib
import tempfile
import unittest
from pathlib import Path

from tools.build_file_inventory import inventory_rows, write_inventory


class BuildFileInventoryTests(unittest.TestCase):
    def test_inventory_is_sorted_and_excludes_its_own_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "z.txt").write_text("z", encoding="utf-8")
            (root / "sub").mkdir()
            (root / "sub" / "a.txt").write_text("a", encoding="utf-8")
            output = root / "inventory.csv"
            output.write_text("stale", encoding="utf-8")

            rows = inventory_rows(root, output)
            write_inventory(output, rows)

            self.assertEqual(
                [row["relative_path"] for row in rows],
                ["sub/a.txt", "z.txt"],
            )
            self.assertEqual(rows[0]["sha256"], hashlib.sha256(b"a").hexdigest().upper())
            with output.open(encoding="utf-8", newline="") as handle:
                written = list(csv.DictReader(handle))
            self.assertEqual([row["relative_path"] for row in written], ["sub/a.txt", "z.txt"])

    def test_rejects_output_outside_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            root.mkdir()
            with self.assertRaisesRegex(ValueError, "inside"):
                inventory_rows(root, Path(temporary) / "inventory.csv")


if __name__ == "__main__":
    unittest.main()
