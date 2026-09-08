import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from unwrap_ntrs_pdf_payload import unwrap_payload


def linearized_pdf(body: bytes = b"content") -> bytes:
    template = b"%PDF-1.6\n1 0 obj\n<</Linearized 1/L LENGTH>>\nendobj\n" + body + b"\n%%EOF\n"
    length = len(template.replace(b"LENGTH", b"00000000"))
    return template.replace(b"LENGTH", f"{length:08d}".encode("ascii"))


class UnwrapNtrsPdfPayloadTest(unittest.TestCase):
    def test_extracts_valid_linearized_pdf_after_legacy_prefix(self) -> None:
        pdf = linearized_pdf()
        raw = b"legacy-envelope\x00" * 10 + pdf
        extracted, report = unwrap_payload(raw)
        self.assertEqual(extracted, pdf)
        self.assertEqual(report["prefix_bytes_removed"], len(raw) - len(pdf))
        self.assertTrue(report["linearized_length_matches"])

    def test_rejects_payload_without_pdf_signature(self) -> None:
        with self.assertRaisesRegex(ValueError, "signature not found"):
            unwrap_payload(b"not a pdf")

    def test_rejects_declared_length_mismatch(self) -> None:
        pdf = linearized_pdf().replace(b"/L 000000", b"/L 999999")
        with self.assertRaisesRegex(ValueError, "length mismatch"):
            unwrap_payload(pdf)

    def test_rejects_excessive_prefix(self) -> None:
        pdf = linearized_pdf()
        with self.assertRaisesRegex(ValueError, "exceeds maximum"):
            unwrap_payload(b"x" * 20 + pdf, max_prefix_bytes=10)


if __name__ == "__main__":
    unittest.main()
