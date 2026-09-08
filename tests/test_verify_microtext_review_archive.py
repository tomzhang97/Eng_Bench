import csv
import io
import json
import zipfile
from pathlib import Path

from PIL import Image

from tools.verify_microtext_review_archive import audit_archive, valid_png


def png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(output, format="PNG")
    return output.getvalue()


def xlsx_bytes() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as workbook:
        workbook.writestr("[Content_Types].xml", "<Types/>")
        workbook.writestr("xl/workbook.xml", "<workbook/>")
    return output.getvalue()


def build_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    pack = tmp_path / "pack"
    (pack / "crops").mkdir(parents=True)
    (pack / "pages").mkdir()
    (pack / "crops" / "row1.png").write_bytes(png_bytes())
    (pack / "pages" / "doc__p0000.png").write_bytes(png_bytes())
    row = {
        "candidate_id": "candidate-1",
        "doc_id": "doc",
        "version_id": "v1",
        "page_index": 0,
        "bbox": [1, 1, 4, 4],
        "crop_path": "crops/row1.png",
        "page_path": "pages/doc__p0000.png",
    }
    manifest_text = json.dumps(row) + "\n"
    (pack / "manifest.jsonl").write_text(manifest_text, encoding="utf-8")
    queue = tmp_path / "queue.jsonl"
    queue.write_text(manifest_text, encoding="utf-8")
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["candidate_id", "review_status"])
    writer.writeheader()
    writer.writerow({"candidate_id": "candidate-1", "review_status": ""})
    (pack / "microtext_validation_checklist.csv").write_text(output.getvalue(), encoding="utf-8")
    (pack / "microtext_validation_checklist.xlsx").write_bytes(xlsx_bytes())
    (pack / "index.html").write_text('<img src="crops/row1.png">', encoding="utf-8")
    (pack / "README.md").write_text("readme\n", encoding="utf-8")
    (pack / "INTERN_INSTRUCTIONS_ZH.md").write_text("instructions\n", encoding="utf-8")
    (pack / "RETURN_ONLY_THIS_XLSX.txt").write_text("return xlsx\n", encoding="utf-8")
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(pack.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(pack).as_posix())
    return pack, queue, archive


def test_valid_archive_passes(tmp_path: Path) -> None:
    pack, queue, archive = build_fixture(tmp_path)
    report = audit_archive(tmp_path, archive, pack, queue)
    assert report["valid"] is True
    assert report["counts"]["queue_rows"] == 1
    assert report["counts"]["crop_files"] == 1
    assert report["counts"]["page_files"] == 1
    assert report["counts"]["nested_zips"] == 0
    assert report["xlsx_valid"] is True


def test_archive_rejects_nested_zip_and_missing_evidence(tmp_path: Path) -> None:
    pack, queue, archive = build_fixture(tmp_path)
    (pack / "nested.zip").write_bytes(b"not a zip")
    (pack / "crops" / "row1.png").unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(pack.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(pack).as_posix())
    report = audit_archive(tmp_path, archive, pack, queue)
    assert report["valid"] is False
    assert report["counts"]["nested_zips"] == 1
    assert report["details"]["missing_crop_members"] == ["crops/row1.png"]


def test_valid_png_honors_explicit_audited_pixel_limit() -> None:
    output = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(output, format="PNG")
    data = output.getvalue()

    assert valid_png(data, max_image_pixels=1024) is True
    assert valid_png(data, max_image_pixels=100) is False
