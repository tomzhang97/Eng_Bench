#!/usr/bin/env python3
"""Import raster source sheets into the derived Eng_Bench page-image layout."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps


SUPPORTED_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def portable_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def normalize_image(source: Path, dest: Path, root: Path, grayscale: bool = False) -> dict[str, Any]:
    if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported raster image type for {source}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        if grayscale:
            image = image.convert("L")
        else:
            image = image.convert("RGB")
        width, height = image.size
        image.save(dest, format="PNG", optimize=True)

    return {
        "source_relpath": portable_path(source, root),
        "output_relpath": portable_path(dest, root),
        "width": width,
        "height": height,
        "sha256": sha256_file(source),
    }


def import_image_pages(
    root: Path,
    doc_id: str,
    image_paths: list[Path],
    dpi: int = 300,
    start_page_index: int = 0,
    grayscale: bool = False,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    if not doc_id.strip():
        raise ValueError("doc_id must be non-empty")
    if not image_paths:
        raise ValueError("at least one input image is required")
    if start_page_index < 0:
        raise ValueError("start_page_index must be non-negative")

    root = root.resolve()
    out_dir = root / "derived" / f"pages_{dpi}dpi" / doc_id
    pages: list[dict[str, Any]] = []

    for offset, image_path in enumerate(image_paths):
        source = image_path if image_path.is_absolute() else root / image_path
        if not source.exists():
            raise FileNotFoundError(source)
        page_index = start_page_index + offset
        output = out_dir / f"page_{page_index:03d}.png"
        page_info = normalize_image(source, output, root=root, grayscale=grayscale)
        page_info.update(
            {
                "doc_id": doc_id,
                "page_index": page_index,
            }
        )
        pages.append(page_info)

    manifest = {
        "doc_id": doc_id,
        "dpi": dpi,
        "page_count": len(pages),
        "pages": pages,
    }
    if manifest_path is not None:
        target = manifest_path if manifest_path.is_absolute() else root / manifest_path
        write_json(target, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Normalize raster source sheets into derived/pages_300dpi/<doc_id>/"
    )
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--doc-id", required=True, help="Document/source id")
    parser.add_argument(
        "--input",
        dest="inputs",
        action="append",
        required=True,
        help="Input image path relative to root, or an absolute path. Repeat for multiple pages.",
    )
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--start-page-index", type=int, default=0)
    parser.add_argument("--grayscale", action="store_true")
    parser.add_argument(
        "--manifest",
        help="Optional manifest JSON path relative to root, e.g. derived/source_imports/doc.json",
    )
    args = parser.parse_args()

    manifest = import_image_pages(
        root=Path(args.root),
        doc_id=args.doc_id,
        image_paths=[Path(value) for value in args.inputs],
        dpi=args.dpi,
        start_page_index=args.start_page_index,
        grayscale=args.grayscale,
        manifest_path=Path(args.manifest) if args.manifest else None,
    )
    print(
        f"[OK] Imported {manifest['page_count']} raster page(s) for {args.doc_id} "
        f"into derived/pages_{args.dpi}dpi/{args.doc_id}/"
    )
    if args.manifest:
        print(f"     Manifest: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
