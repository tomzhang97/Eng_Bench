#!/usr/bin/env python3
"""Render ASCII or binary STL meshes to deterministic engineering previews."""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from mpl_toolkits.mplot3d.art3d import Poly3DCollection


def _load_binary(payload: bytes) -> np.ndarray | None:
    if len(payload) < 84:
        return None
    triangle_count = struct.unpack_from("<I", payload, 80)[0]
    if 84 + triangle_count * 50 != len(payload):
        return None
    dtype = np.dtype(
        [
            ("normal", "<f4", (3,)),
            ("vertices", "<f4", (3, 3)),
            ("attribute", "<u2"),
        ]
    )
    records = np.frombuffer(payload, dtype=dtype, count=triangle_count, offset=84)
    return np.asarray(records["vertices"], dtype=np.float64)


def _load_ascii(payload: bytes) -> np.ndarray:
    vertices: list[list[float]] = []
    for raw_line in payload.decode("utf-8", errors="replace").splitlines():
        parts = raw_line.strip().split()
        if len(parts) != 4 or parts[0].casefold() != "vertex":
            continue
        try:
            vertices.append([float(parts[1]), float(parts[2]), float(parts[3])])
        except ValueError as exc:
            raise ValueError(f"invalid STL vertex line: {raw_line!r}") from exc
    if not vertices or len(vertices) % 3:
        raise ValueError("ASCII STL must contain a non-zero multiple of three vertices")
    return np.asarray(vertices, dtype=np.float64).reshape((-1, 3, 3))


def load_triangles(path: Path) -> np.ndarray:
    payload = path.read_bytes()
    triangles = _load_binary(payload)
    if triangles is None:
        triangles = _load_ascii(payload)
    if not np.isfinite(triangles).all():
        raise ValueError(f"STL contains non-finite coordinates: {path}")
    return triangles


def mesh_stats(triangles: np.ndarray) -> dict[str, Any]:
    vertices = triangles.reshape((-1, 3))
    minimum = vertices.min(axis=0)
    maximum = vertices.max(axis=0)
    return {
        "triangles": int(len(triangles)),
        "bounds_min": [round(float(value), 6) for value in minimum],
        "bounds_max": [round(float(value), 6) for value in maximum],
        "extent": [round(float(value), 6) for value in maximum - minimum],
    }


def _deterministic_sample(triangles: np.ndarray, maximum: int) -> np.ndarray:
    if len(triangles) <= maximum:
        return triangles
    indices = np.linspace(0, len(triangles) - 1, num=maximum, dtype=np.int64)
    return triangles[indices]


def render_stl(
    source: Path,
    output: Path,
    *,
    width: int = 1200,
    height: int = 1200,
    elevation: float = 24.0,
    azimuth: float = -52.0,
    maximum_triangles: int = 45_000,
) -> dict[str, Any]:
    triangles = load_triangles(source)
    stats = mesh_stats(triangles)
    sampled = _deterministic_sample(triangles, maximum_triangles)

    edges_a = sampled[:, 1] - sampled[:, 0]
    edges_b = sampled[:, 2] - sampled[:, 0]
    normals = np.cross(edges_a, edges_b)
    lengths = np.linalg.norm(normals, axis=1)
    valid = lengths > 1e-12
    sampled = sampled[valid]
    normals = normals[valid]
    lengths = lengths[valid]
    if not len(sampled):
        raise ValueError(f"STL has no renderable triangles: {source}")
    normals = normals / lengths[:, None]
    light = np.asarray([0.35, -0.45, 0.82], dtype=np.float64)
    light /= np.linalg.norm(light)
    intensity = 0.56 + 0.34 * np.abs(normals @ light)
    face_colors = np.column_stack(
        [0.46 * intensity, 0.62 * intensity, 0.72 * intensity, np.ones(len(intensity))]
    )

    dpi = 120
    figure = plt.figure(figsize=(width / dpi, height / dpi), dpi=dpi, facecolor="white")
    axis = figure.add_subplot(111, projection="3d")
    axis.set_facecolor("white")
    axis.set_proj_type("ortho")
    mesh = Poly3DCollection(
        sampled,
        facecolors=face_colors,
        edgecolors=(0.13, 0.18, 0.21, 0.12),
        linewidths=0.08,
        antialiased=False,
    )
    mesh.set_zsort("average")
    axis.add_collection3d(mesh)

    vertices = triangles.reshape((-1, 3))
    minimum = vertices.min(axis=0)
    maximum = vertices.max(axis=0)
    center = (minimum + maximum) / 2
    radius = max(float((maximum - minimum).max()) / 2, 1e-6) * 1.08
    axis.set_xlim(center[0] - radius, center[0] + radius)
    axis.set_ylim(center[1] - radius, center[1] + radius)
    axis.set_zlim(center[2] - radius, center[2] + radius)
    axis.set_box_aspect((1, 1, 1))
    axis.view_init(elev=elevation, azim=azimuth)
    axis.set_axis_off()
    figure.subplots_adjust(left=0, right=1, bottom=0, top=1)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=dpi, facecolor="white", pad_inches=0)
    plt.close(figure)

    stats.update(
        {
            "rendered_triangles": int(len(sampled)),
            "width": width,
            "height": height,
            "elevation": elevation,
            "azimuth": azimuth,
        }
    )
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, default=1200)
    parser.add_argument("--height", type=int, default=1200)
    parser.add_argument("--elevation", type=float, default=24.0)
    parser.add_argument("--azimuth", type=float, default=-52.0)
    parser.add_argument("--maximum-triangles", type=int, default=45_000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = render_stl(
        args.input,
        args.output,
        width=args.width,
        height=args.height,
        elevation=args.elevation,
        azimuth=args.azimuth,
        maximum_triangles=args.maximum_triangles,
    )
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
