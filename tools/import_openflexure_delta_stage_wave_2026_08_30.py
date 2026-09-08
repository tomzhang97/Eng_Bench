#!/usr/bin/env python3
"""Import pinned OpenFlexure Delta Stage meshes as review-only VisualDiff."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageChops

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.render_stl import render_stl


IMPORT_DATE = "2026-08-30"
WAVE = "wave1245"
SOURCE_CANDIDATE_ID = "mech_018"
REPO_URL = "https://gitlab.com/openflexure/openflexure-delta-stage"
PUBLIC_STATUS = "cern_ohl_1_2_open_hardware_candidate"
COMMITS = {
    "v1.1.0": "f2a0764aaf69ab29036093c1e52e17f939ba7b41",
    "v1.2.0": "2f4d7c97f1483d101fcead1d9b5b93ea00524152",
}
EVIDENCE = {
    "v1.1.0": {
        "LICENSE": "94e4835c8760715a365cf7ec0c182d97d4d9b0b3538cff1b48eb6401c2888cff",
        "README.md": "9b4866e8b607f59e8cdfe86022d58606aad43bb799d309e2563f170b34d86152",
        "okh-OpenFlexureDeltaStage.yml": "dd499690f0df645391ce9b85dcc9dc8a984503c4e9bce4b88391cbe275e63386",
    },
    "v1.2.0": {
        "LICENSE": "94e4835c8760715a365cf7ec0c182d97d4d9b0b3538cff1b48eb6401c2888cff",
        "README.md": "9b4866e8b607f59e8cdfe86022d58606aad43bb799d309e2563f170b34d86152",
        "okh-OpenFlexureDeltaStage.yml": "24b93b8b0268fbc1b39addab21b2e89b579284a1bfc65607bd16491678600e26",
    },
}
MODELS = [
    {
        "model_id": "35mm_petri_dish_holder",
        "old": {
            "tag": "v1.1.0",
            "build_name": "35mm_petri_dish_holder",
            "asset_name": "1_1_0__35mm_petri_dish_holder.stl",
            "doc_id": "openflexure_delta_stage_35mm_holder_v1_1_0",
            "scad_path": "openscad/35mm_petri_dish_holder.scad",
            "scad_sha256": "6d0cd9b185baabd2ec7ca49ed87c70c5a499e498ff9d49a7892d44ea7d3363e2",
            "stl_sha256": "46d0708b18deba42d58768011d9b86c124895aeebc4d9ead4edcf4394a40922e",
        },
        "new": {
            "tag": "v1.2.0",
            "build_name": "35mm_petri_dish_holder",
            "asset_name": "1_2_0__35mm_petri_dish_holder.stl",
            "doc_id": "openflexure_delta_stage_35mm_holder_v1_2_0",
            "scad_path": "openscad/35mm_petri_dish_holder.scad",
            "scad_sha256": "5d263217bd682f878566e9a798eb78679655ce5de961962ac554b44169ce3f34",
            "stl_sha256": "ea114f13fb0267bab5b01535f671ba33f4257715cbb5cd92c5dcb5c698324a9c",
        },
        "description": "The v1.2.0 holder adds and repositions circular mounting holes around the 35 mm dish opening and reshapes the upper mounting surface.",
        "expected_disposition": "review",
    },
    {
        "model_id": "simple_base",
        "old": {
            "tag": "v1.1.0",
            "build_name": "base",
            "asset_name": "1_1_0__base.stl",
            "doc_id": "openflexure_delta_stage_simple_base_v1_1_0",
            "scad_path": "openscad/base.scad",
            "scad_sha256": "2b69aed0128285b30368c7761751b5a3686cac6d305decab89acbcd6109c6209",
            "stl_sha256": "93825202cc489634fff0f3d9acd7c5aa24a5927e16cee084fa7e5373f43f59d8",
        },
        "new": {
            "tag": "v1.2.0",
            "build_name": "simple_base",
            "asset_name": "1_2_0__simple_base.stl",
            "doc_id": "openflexure_delta_stage_simple_base_v1_2_0",
            "scad_path": "openscad/simple_base.scad",
            "scad_sha256": "0e89794fe9000ab12d245f32c0d84412047940c76e99b621fd41f0f5dc40e17c",
            "stl_sha256": "fe05a54e8039034ef0e20d3a780d3561da31f358ca42a232c4cfbc3149785ae0",
        },
        "description": "The v1.2.0 simple base has a larger perimeter and adds visible mounting bores and support pads compared with the v1.1.0 base.",
        "expected_disposition": "review",
    },
    {
        "model_id": "base_raspi_sangaboard",
        "old": {
            "tag": "v1.1.0",
            "build_name": "base_raspi_sangaboard",
            "asset_name": "1_1_0__base_raspi_sangaboard.stl",
            "doc_id": "openflexure_delta_stage_raspi_base_v1_1_0",
            "scad_path": "openscad/base_raspi_sangaboard.scad",
            "scad_sha256": "47d4845d0c75701b0b55f9587cf9e46fcefb54c2bfb97dd2a1cff9dac80d3e26",
            "stl_sha256": "cc188cc5805d803aa913c81d7d3e86ddd325f36b3e3807fac477b5e708bd8b7f",
        },
        "new": {
            "tag": "v1.2.0",
            "build_name": "base_raspi_sangaboard",
            "asset_name": "1_2_0__base_raspi_sangaboard.stl",
            "doc_id": "openflexure_delta_stage_raspi_base_v1_2_0",
            "scad_path": "openscad/base_raspi_sangaboard.scad",
            "scad_sha256": "4e43812489e59b584594b8787536077851af1b5215304f3d61f28193dd22cbe5",
            "stl_sha256": "35ce1621b0a871051581695c6ffd6957bd75aeb924f656d3fd3a7a9cebd7f4d2",
        },
        "description": "The source payload changed, but the controlled render exposes only a tiny local delta.",
        "expected_disposition": "hold",
    },
    {
        "model_id": "delta_stage",
        "old": {
            "tag": "v1.1.0",
            "build_name": "delta_stage",
            "asset_name": "1_1_0__delta_stage.stl",
            "doc_id": "openflexure_delta_stage_body_v1_1_0",
            "scad_path": "openscad/delta_stage.scad",
            "scad_sha256": "8853340e6ef496ee027ab3be8506f93dff666b379700fa693d3d515e1a196a18",
            "stl_sha256": "439afdd69bcbd62921966897f67869d1561348888d630ffffa4875ff3f681659",
        },
        "new": {
            "tag": "v1.2.0",
            "build_name": "delta_stage",
            "asset_name": "1_2_0__delta_stage.stl",
            "doc_id": "openflexure_delta_stage_body_v1_2_0",
            "scad_path": "openscad/delta_stage.scad",
            "scad_sha256": "09af91a5a69a315a2fa0d82da12d757d4dd8ca5384d9dc09ef6d310e5a63c557",
            "stl_sha256": "89cc27893f7ae845489125e1809d38fa4866283a897996fcef8dbe157a734481",
        },
        "description": "The source payload changed, but the controlled render is effectively unchanged.",
        "expected_disposition": "hold",
    },
]


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def assert_hash(path: Path, expected: str) -> None:
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f"hash mismatch for {path}: expected {expected}, got {actual}")


def git(repo: Path, *args: str, binary: bool = False) -> bytes | str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={repo.resolve().as_posix()}", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=not binary,
    )
    return result.stdout


def git_blob(repo: Path, tag: str, path: str) -> bytes:
    payload = git(repo, "show", f"{tag}:{path}", binary=True)
    assert isinstance(payload, bytes)
    return payload


def verify_inputs(repo: Path, asset_dir: Path) -> None:
    for tag, commit in COMMITS.items():
        resolved = git(repo, "rev-parse", "--verify", f"{tag}^{{commit}}")
        assert isinstance(resolved, str)
        if resolved.strip() != commit:
            raise ValueError(f"{tag} resolved to {resolved.strip()}, expected {commit}")
        for path, expected in EVIDENCE[tag].items():
            payload = git_blob(repo, tag, path)
            if sha256_bytes(payload) != expected:
                raise ValueError(f"evidence hash mismatch for {tag}:{path}")
        license_text = git_blob(repo, tag, "LICENSE").decode("utf-8", errors="replace")
        if "CERN Open Hardware Licence v1.2" not in license_text:
            raise ValueError(f"missing CERN-OHL-1.2 marker at {tag}")
    for model in MODELS:
        for side in ("old", "new"):
            revision = model[side]
            assert_hash(asset_dir / revision["asset_name"], revision["stl_sha256"])
            source = git_blob(repo, revision["tag"], revision["scad_path"])
            if sha256_bytes(source) != revision["scad_sha256"]:
                raise ValueError(f"SCAD hash mismatch for {revision['tag']}:{revision['scad_path']}")


def copy_verified(source: Path, destination: Path, expected: str) -> None:
    assert_hash(source, expected)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        assert_hash(destination, expected)
        return
    shutil.copy2(source, destination)
    assert_hash(destination, expected)


def write_bytes_verified(destination: Path, payload: bytes, expected: str) -> None:
    if sha256_bytes(payload) != expected:
        raise ValueError(f"payload hash mismatch for {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        assert_hash(destination, expected)
        return
    destination.write_bytes(payload)
    assert_hash(destination, expected)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def pixel_metrics(old_path: Path, new_path: Path) -> dict[str, Any]:
    with Image.open(old_path) as old_image, Image.open(new_path) as new_image:
        old = np.asarray(old_image.convert("RGB"), dtype=np.int16)
        new = np.asarray(new_image.convert("RGB"), dtype=np.int16)
        if old.shape != new.shape:
            raise ValueError(f"render dimensions differ: {old.shape} vs {new.shape}")
        delta = np.abs(old - new)
        bbox = ImageChops.difference(old_image.convert("RGB"), new_image.convert("RGB")).getbbox()
        return {
            "pixel_exact_match": bool(np.array_equal(old, new)),
            "mean_absolute_delta": round(float(delta.mean()), 6),
            "changed_pixel_ratio_gt16": round(float(np.any(delta > 16, axis=2).mean()), 8),
            "difference_bbox": list(bbox) if bbox else None,
        }


def visual_signal_disposition(metrics: dict[str, Any]) -> str:
    if metrics["changed_pixel_ratio_gt16"] >= 0.005 and metrics["mean_absolute_delta"] >= 0.25:
        return "review"
    return "hold"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def append_manifest(path: Path, additions: list[dict[str, Any]]) -> int:
    rows = read_jsonl(path)
    key_to_index = {
        (str(row.get("type") or ""), str(row.get("doc_id") or row.get("pair_id") or "")): index
        for index, row in enumerate(rows)
    }
    added = 0
    changed = False
    for row in additions:
        key = (str(row.get("type") or ""), str(row.get("doc_id") or row.get("pair_id") or ""))
        index = key_to_index.get(key)
        if index is not None:
            existing = rows[index]
            if existing == row:
                continue
            license_only_enrichment = dict(existing)
            if "license_evidence" in row and "license_evidence" not in existing:
                license_only_enrichment["license_evidence"] = row["license_evidence"]
            if license_only_enrichment != row:
                raise ValueError(f"manifest conflict for {key}")
            rows[index] = row
            changed = True
            continue
        key_to_index[key] = len(rows)
        rows.append(row)
        added += 1
        changed = True
    if changed:
        temporary = path.with_name(f".{path.name}.wave1245.tmp")
        write_jsonl(temporary, rows)
        temporary.replace(path)
    return added


def append_inventory(path: Path, additions: list[dict[str, str]]) -> int:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = list(reader.fieldnames or [])
        existing = {str(row.get("doc_id") or ""): row for row in reader}
    added = 0
    with path.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        for row in additions:
            doc_id = row["doc_id"]
            if doc_id in existing:
                if existing[doc_id].get("source_url") != row.get("source_url"):
                    raise ValueError(f"inventory conflict for {doc_id}")
                continue
            writer.writerow({field: row.get(field, "") for field in fields})
            existing[doc_id] = row
            added += 1
    return added


def attribution_text() -> str:
    return f"""# OpenFlexure Delta Stage attribution

- Upstream repository: {REPO_URL}
- Imported releases: v1.1.0 (`{COMMITS['v1.1.0']}`) and v1.2.0 (`{COMMITS['v1.2.0']}`)
- Built mesh origin: `https://build.openflexure.org/openflexure-delta-stage/<tag>/models/<model>.stl`
- License at both pinned tags: CERN Open Hardware Licence v1.2
- Source candidate: `{SOURCE_CANDIDATE_ID}`

Each local STL is tied to its exact build URL and SHA-256. The corresponding
OpenSCAD source, README, OKH manifest, and full license text are preserved from
the pinned Git commits. Only visually meaningful revision pairs are staged for
human review; weak rendered deltas remain machine holds. No row is active Gold.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--source-repo",
        type=Path,
        default=Path(".codex_work/versioned_git_sources/openflexure_delta_stage"),
    )
    parser.add_argument(
        "--asset-dir",
        type=Path,
        default=Path(".codex_tmp/openflexure_delta_stage_wave1245"),
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    repo = args.source_repo if args.source_repo.is_absolute() else root / args.source_repo
    asset_dir = args.asset_dir if args.asset_dir.is_absolute() else root / args.asset_dir
    verify_inputs(repo.resolve(), asset_dir.resolve())
    if not args.apply:
        print("[READY] docs=8 review_pairs=2 held_pairs=2; rerun with --apply")
        return 0

    manifest_path = root / "manifest.jsonl"
    inventory_path = root / "SOURCE_INVENTORY.csv"
    snapshot_dir = root / "derived/snapshots/2026-08-30-wave1245-openflexure-delta-stage"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for source in (manifest_path, inventory_path):
        snapshot = snapshot_dir / source.name
        if not snapshot.exists():
            shutil.copy2(source, snapshot)

    source_root = root / "visualdiff/docs/openflexure_delta_stage"
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "ATTRIBUTION.md").write_text(attribution_text(), encoding="utf-8")
    for tag, files in EVIDENCE.items():
        for path, expected in files.items():
            write_bytes_verified(source_root / "evidence" / tag / path, git_blob(repo, tag, path), expected)

    docs: list[dict[str, Any]] = []
    inventory_rows: list[dict[str, str]] = []
    render_receipts: dict[str, dict[str, Any]] = {}
    local_by_doc: dict[str, dict[str, Any]] = {}
    for model in MODELS:
        for side in ("old", "new"):
            revision = model[side]
            tag = revision["tag"]
            commit = COMMITS[tag]
            doc_id = revision["doc_id"]
            stl_rel = Path("visualdiff/docs/openflexure_delta_stage/revisions") / f"{doc_id}.stl"
            scad_rel = Path("visualdiff/docs/openflexure_delta_stage/sources") / tag / revision["scad_path"]
            page_rel = Path("derived/pages_300dpi") / doc_id / "page_000.png"
            textlayer_rel = Path("derived/textlayer") / f"{doc_id}.jsonl"
            copy_verified(asset_dir / revision["asset_name"], root / stl_rel, revision["stl_sha256"])
            write_bytes_verified(
                root / scad_rel,
                git_blob(repo, tag, revision["scad_path"]),
                revision["scad_sha256"],
            )
            receipt = render_stl(root / stl_rel, root / page_rel, width=1000, height=1000)
            receipt.update(
                {
                    "doc_id": doc_id,
                    "source_path": stl_rel.as_posix(),
                    "source_sha256": revision["stl_sha256"],
                    "renderer": "tools/render_stl.py",
                }
            )
            render_receipts[doc_id] = receipt
            (root / textlayer_rel).parent.mkdir(parents=True, exist_ok=True)
            (root / textlayer_rel).write_text("", encoding="utf-8")
            build_url = (
                f"https://build.openflexure.org/openflexure-delta-stage/{tag}/models/"
                f"{revision['build_name']}.stl"
            )
            doc = {
                "type": "doc",
                "doc_id": doc_id,
                "task": "visualdiff",
                "source_candidate_id": SOURCE_CANDIDATE_ID,
                "same_model_id": f"openflexure_delta_stage_{model['model_id']}",
                "domain": "mechanical_cad",
                "doc_type": "stl_mechanical_model",
                "version": {"revision": tag, "git_commit": commit, "imported": IMPORT_DATE},
                "path": stl_rel.as_posix(),
                "sha256": revision["stl_sha256"],
                "pages": 1,
                "render": {
                    "renderer": "tools/render_stl.py",
                    "width": 1000,
                    "height": 1000,
                    "elevation": 24.0,
                    "azimuth": -52.0,
                    "colorspace": "rgb",
                },
                "derived": {
                    "pages_dir": f"derived/pages_300dpi/{doc_id}",
                    "textlayer_jsonl": textlayer_rel.as_posix(),
                    "source_scad_path": scad_rel.as_posix(),
                },
                "source_url": f"{REPO_URL}/-/releases/{tag}",
                "direct_source_url": build_url,
                "public_status": PUBLIC_STATUS,
                "license_note": "The pinned v1.1.0 and v1.2.0 tags contain the CERN Open Hardware Licence v1.2; preserve the full license, source commit, generated-mesh URL, and exact hashes.",
                "license_evidence": {
                    "path": f"visualdiff/docs/openflexure_delta_stage/evidence/{tag}/LICENSE",
                    "sha256": EVIDENCE[tag]["LICENSE"],
                },
                "attribution_path": "visualdiff/docs/openflexure_delta_stage/ATTRIBUTION.md",
                "provenance": {
                    "git_commit": commit,
                    "git_path": revision["scad_path"],
                    "git_source_sha256": revision["scad_sha256"],
                    "built_mesh_url": build_url,
                },
                "notes": "Wave1245 source import; review-only VisualDiff capacity; no unreviewed row is active Gold.",
            }
            docs.append(doc)
            local_by_doc[doc_id] = {"page": page_rel, "doc": doc}
            is_new_review_doc = side == "new" and model["expected_disposition"] == "review"
            inventory_rows.append(
                {
                    "doc_id": doc_id,
                    "domain": "mechanical_cad",
                    "task": "visualdiff",
                    "public_status": PUBLIC_STATUS,
                    "source_path": stl_rel.as_posix(),
                    "rendered_pages": "1",
                    "textlayer_spans": "0",
                    "mineable_candidates": "1" if is_new_review_doc else "0",
                    "review_rows": "1" if is_new_review_doc else "0",
                    "open_review_rows": "1" if is_new_review_doc else "0",
                    "unpacketed_open_review_rows": "1" if is_new_review_doc else "0",
                    "fresh_open_review_rows": "1" if is_new_review_doc else "0",
                    "unique_open_review_rows": "1" if is_new_review_doc else "0",
                    "unique_fresh_open_review_rows": "1" if is_new_review_doc else "0",
                    "duplicate_payload_alias": "False",
                    "next_step": (
                        "packet_fresh_open_review_rows"
                        if is_new_review_doc
                        else "paired_source_doc_or_machine_hold"
                    ),
                    "priority_score": "40" if is_new_review_doc else "0",
                    "source_url": f"{REPO_URL}/-/releases/{tag}",
                }
            )

    review_rows: list[dict[str, Any]] = []
    hold_rows: list[dict[str, Any]] = []
    pair_rows: list[dict[str, Any]] = []
    pair_reports: list[dict[str, Any]] = []
    for model in MODELS:
        old_doc = model["old"]["doc_id"]
        new_doc = model["new"]["doc_id"]
        pair_id = f"vdiff__openflexure_delta_stage_{model['model_id']}__v1_1_0__to__v1_2_0"
        metrics = pixel_metrics(root / local_by_doc[old_doc]["page"], root / local_by_doc[new_doc]["page"])
        disposition = visual_signal_disposition(metrics)
        if disposition != model["expected_disposition"]:
            raise ValueError(
                f"unexpected signal disposition for {model['model_id']}: "
                f"expected {model['expected_disposition']}, got {disposition}"
            )
        base_row = {
            "pair_id": f"{pair_id}__p0000__full_model",
            "project_id": pair_id,
            "source_candidate_id": SOURCE_CANDIDATE_ID,
            "task": "visualdiff",
            "old_doc_id": old_doc,
            "new_doc_id": new_doc,
            "page_old": 0,
            "page_new": 0,
            "bbox_old": [0, 0, 1000, 1000],
            "bbox_new": [0, 0, 1000, 1000],
            "image_old": local_by_doc[old_doc]["page"].as_posix(),
            "image_new": local_by_doc[new_doc]["page"].as_posix(),
            "change_type": "geometry",
            "description": model["description"],
            "description_source": "machine_source_diff_and_visual_qa",
            "source": "pinned_generated_stl_revision_pair",
            "confidence": 0.9 if disposition == "review" else 0.1,
            "review_status": "needs_review" if disposition == "review" else "machine_hold",
            "promotion_state": "unreviewed_candidate" if disposition == "review" else "machine_hold",
            "safe_to_merge_gold": False,
            "reserved_split": "test",
            "split": "provisional_review",
            "review_bucket": "v2_0_source_expansion",
            "machine_qa_status": (
                "selected_for_human_review" if disposition == "review" else "held_weak_visual_delta"
            ),
            "machine_visual_qa_status": (
                "selected_for_human_review" if disposition == "review" else "held_weak_visual_delta"
            ),
            "machine_audit": metrics,
            "notes": "Pinned OpenFlexure release meshes rendered with identical deterministic camera settings; human confirmation is required before Gold promotion.",
        }
        if disposition == "review":
            review_rows.append(base_row)
            pair_rows.append(
                {
                    "type": "pair",
                    "pair_id": pair_id,
                    "task": "visualdiff",
                    "pair_type": "same_model_revision",
                    "source_candidate_id": SOURCE_CANDIDATE_ID,
                    "from_doc_id": old_doc,
                    "to_doc_id": new_doc,
                    "page_mapping": {"type": "by_index", "pages_A": "0", "pages_B": "0"},
                    "derived": {"align_dir": f"derived/align/{pair_id}"},
                    "notes": "Wave1245 review-only mechanical revision pair; deterministic STL render; no active Gold row.",
                }
            )
        else:
            base_row["hold_reason"] = "source_payload_changed_but_controlled_render_delta_is_not_review_worthy"
            hold_rows.append(base_row)
        pair_reports.append(
            {
                "project_id": pair_id,
                "model_id": model["model_id"],
                "disposition": disposition,
                "metrics": metrics,
            }
        )

    manifest_additions_rel = Path(
        "derived/quality/openflexure_delta_stage_manifest_additions_2026-08-30-wave1245.jsonl"
    )
    write_jsonl(root / manifest_additions_rel, docs + pair_rows)
    manifest_added = append_manifest(manifest_path, docs + pair_rows)
    inventory_added = append_inventory(inventory_path, inventory_rows)
    queue_rel = Path("derived/review_queues/visualdiff_openflexure_delta_stage_2026-08-30-wave1245.jsonl")
    annotation_rel = Path("visualdiff/annotations/visualdiff_review_openflexure_delta_stage_2026-08-30-wave1245.jsonl")
    holds_rel = Path("derived/quality/openflexure_delta_stage_visual_holds_2026-08-30-wave1245.jsonl")
    write_jsonl(root / queue_rel, review_rows)
    write_jsonl(root / annotation_rel, review_rows)
    write_jsonl(root / holds_rel, hold_rows)
    hold_doc_ids = sorted(
        {
            str(row["old_doc_id"])
            for row in hold_rows
        }
        | {
            str(row["new_doc_id"])
            for row in hold_rows
        }
    )
    exhaustion_plan_rel = Path(
        "derived/quality/openflexure_delta_stage_hold_exhaustion_plan_2026-08-30-wave1245.json"
    )
    exhaustion_novelty_rel = Path(
        "derived/quality/openflexure_delta_stage_hold_novelty_2026-08-30-wave1245.json"
    )
    (root / exhaustion_plan_rel).write_text(
        json.dumps(
            {
                "goal": "Gold v2.0 Global",
                "wave": WAVE,
                "selected_sources": [{"doc_id": doc_id} for doc_id in hold_doc_ids],
                "method": "deterministic_stl_visual_signal_gate",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / exhaustion_novelty_rel).write_text(
        json.dumps(
            {
                "goal": "Gold v2.0 Global",
                "wave": WAVE,
                "totals": {"input_pairs": len(hold_rows), "net_new_rows": 0},
                "held_pairs": hold_rows,
                "interpretation": "The source payloads differ, but controlled renders do not expose a review-worthy engineering change.",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    bundle = {
        "goal": "Gold v2.0 Global",
        "wave": WAVE,
        "source_candidate_id": SOURCE_CANDIDATE_ID,
        "repo_url": REPO_URL,
        "commits": COMMITS,
        "public_status": PUBLIC_STATUS,
        "docs": docs,
        "pairs": pair_reports,
        "render_receipts": render_receipts,
        "review_queue": queue_rel.as_posix(),
        "holds": holds_rel.as_posix(),
        "manifest_additions": manifest_additions_rel.as_posix(),
        "hold_exhaustion_plan": exhaustion_plan_rel.as_posix(),
        "hold_novelty_report": exhaustion_novelty_rel.as_posix(),
        "active_gold_rows_modified": 0,
        "safe_to_merge_gold": False,
    }
    bundle_path = source_root / "source_bundle.json"
    bundle_path.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = {
        "goal": "Gold v2.0 Global",
        "wave": WAVE,
        "docs": len(docs),
        "manifest_rows_added": manifest_added,
        "inventory_rows_added": inventory_added,
        "review_pairs": len(review_rows),
        "held_pairs": len(hold_rows),
        "review_queue": queue_rel.as_posix(),
        "annotation_queue": annotation_rel.as_posix(),
        "holds": holds_rel.as_posix(),
        "source_bundle": bundle_path.relative_to(root).as_posix(),
        "manifest_additions": manifest_additions_rel.as_posix(),
        "hold_exhaustion_plan": exhaustion_plan_rel.as_posix(),
        "hold_novelty_report": exhaustion_novelty_rel.as_posix(),
        "active_gold_rows_modified": 0,
        "safe_to_merge_gold": False,
        "valid": True,
    }
    report_path = root / "derived/quality/openflexure_delta_stage_import_2026-08-30-wave1245.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
