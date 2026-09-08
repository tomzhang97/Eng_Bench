#!/usr/bin/env python3
"""Convert a DWG with LibreDWG and render it as an auditable PNG page."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import ezdxf
from PIL import Image
from ezdxf.addons.drawing import matplotlib
from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration


DWG_VERSION_NAMES = {
    "AC1009": "R12",
    "AC1012": "R13",
    "AC1014": "R14",
    "AC1015": "R2000",
    "AC1018": "R2004",
    "AC1021": "R2007",
    "AC1024": "R2010",
    "AC1027": "R2013",
    "AC1032": "R2018",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_dwg_version(path: Path) -> tuple[str, str]:
    with path.open("rb") as handle:
        code = handle.read(6).decode("ascii", errors="replace")
    if code not in DWG_VERSION_NAMES:
        raise ValueError(f"Unsupported or invalid DWG header {code!r}: {path}")
    return code, DWG_VERSION_NAMES[code]


def converter_version(dwg2dxf: Path) -> str:
    result = subprocess.run(
        [str(dwg2dxf), "--version"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (result.stdout or result.stderr).strip()
    return output.splitlines()[0] if output else "unknown"


def convert_dwg_to_dxf(dwg_path: Path, dxf_path: Path, dwg2dxf: Path) -> list[str]:
    dxf_path.parent.mkdir(parents=True, exist_ok=True)
    command = [str(dwg2dxf), "--overwrite", "--file", str(dxf_path), str(dwg_path)]
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if not dxf_path.is_file() or dxf_path.stat().st_size == 0:
        raise RuntimeError(f"LibreDWG did not create a DXF: {dxf_path}")
    return [line for line in result.stderr.splitlines() if line.strip()]


def render_dxf_to_png(
    dxf_path: Path,
    png_path: Path,
    *,
    dpi: int = 300,
    paper_width_inches: float = 17.0,
    paper_height_inches: float = 11.0,
) -> dict[str, object]:
    doc = ezdxf.readfile(dxf_path)
    modelspace = doc.modelspace()
    entity_counts = Counter(entity.dxftype() for entity in modelspace)
    if not entity_counts:
        raise ValueError(f"DXF modelspace is empty: {dxf_path}")

    png_path.parent.mkdir(parents=True, exist_ok=True)
    config = Configuration(
        color_policy=ColorPolicy.BLACK,
        background_policy=BackgroundPolicy.WHITE,
    )
    matplotlib.qsave(
        modelspace,
        png_path,
        bg="#ffffff",
        fg="#000000",
        dpi=dpi,
        size_inches=(paper_width_inches, paper_height_inches),
        config=config,
    )

    with Image.open(png_path) as image:
        gray = image.convert("L")
        histogram = gray.histogram()
        nonwhite_pixels = sum(histogram[:250])
        width, height = image.size
    if nonwhite_pixels == 0:
        raise RuntimeError(f"Rendered page is blank: {png_path}")

    return {
        "dxf_version": doc.dxfversion,
        "modelspace_entity_counts": dict(sorted(entity_counts.items())),
        "output_width": width,
        "output_height": height,
        "nonwhite_pixels_below_250": nonwhite_pixels,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--doc-id", required=True)
    parser.add_argument("--dwg-relpath", required=True)
    parser.add_argument("--dwg2dxf", required=True, help="Path to GNU LibreDWG dwg2dxf")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument(
        "--page-index",
        type=int,
        default=0,
        help="Zero-based output page index when several DWGs form one document.",
    )
    parser.add_argument("--paper-width-inches", type=float, default=17.0)
    parser.add_argument("--paper-height-inches", type=float, default=11.0)
    parser.add_argument(
        "--keep-dxf",
        action="store_true",
        help="Retain the intermediate DXF under derived/cad_dxf.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(args.root).resolve()
    dwg_path = (root / args.dwg_relpath).resolve()
    dwg2dxf = Path(args.dwg2dxf).resolve()
    if not dwg_path.is_file():
        raise FileNotFoundError(dwg_path)
    if not dwg2dxf.is_file():
        raise FileNotFoundError(dwg2dxf)
    if args.page_index < 0:
        raise ValueError("Page index must be non-negative")
    if args.dpi <= 0 or args.paper_width_inches <= 0 or args.paper_height_inches <= 0:
        raise ValueError("DPI and paper dimensions must be positive")

    dwg_code, dwg_release = read_dwg_version(dwg_path)
    page_stem = f"{args.doc_id}__p{args.page_index:04d}"
    dxf_path = root / "derived" / "cad_dxf" / f"{page_stem}.dxf"
    png_path = (
        root
        / "derived"
        / f"pages_{args.dpi}dpi"
        / args.doc_id
        / f"page_{args.page_index:03d}.png"
    )
    receipt_path = root / "derived" / "cad_render_receipts" / f"{page_stem}.json"

    warnings = convert_dwg_to_dxf(dwg_path, dxf_path, dwg2dxf)
    render_data = render_dxf_to_png(
        dxf_path,
        png_path,
        dpi=args.dpi,
        paper_width_inches=args.paper_width_inches,
        paper_height_inches=args.paper_height_inches,
    )

    receipt = {
        "doc_id": args.doc_id,
        "page_index": args.page_index,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "path": dwg_path.relative_to(root).as_posix(),
            "sha256": sha256_file(dwg_path),
            "dwg_header": dwg_code,
            "dwg_release": dwg_release,
        },
        "converter": {
            "name": "GNU LibreDWG dwg2dxf",
            "version": converter_version(dwg2dxf),
            "path": str(dwg2dxf),
            "sha256": sha256_file(dwg2dxf),
            "warnings": warnings,
        },
        "render": {
            "renderer": "ezdxf.addons.drawing.matplotlib",
            "ezdxf_version": ezdxf.__version__,
            "dpi": args.dpi,
            "paper_width_inches": args.paper_width_inches,
            "paper_height_inches": args.paper_height_inches,
            "path": png_path.relative_to(root).as_posix(),
            "sha256": sha256_file(png_path),
            **render_data,
        },
        "intermediate_dxf": {
            "retained": bool(args.keep_dxf),
            "path": dxf_path.relative_to(root).as_posix() if args.keep_dxf else None,
            "sha256": sha256_file(dxf_path),
        },
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    if not args.keep_dxf:
        dxf_path.unlink()

    print(
        f"[OK] Rendered {dwg_path.relative_to(root)} -> "
        f"{png_path.relative_to(root)} ({render_data['output_width']}x"
        f"{render_data['output_height']})"
    )
    print(f"[OK] Receipt: {receipt_path.relative_to(root)}")


if __name__ == "__main__":
    main()
