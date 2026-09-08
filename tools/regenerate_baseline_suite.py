#!/usr/bin/env python3
"""Regenerate and score the counted Eng_Bench baseline suite."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BaselineSpec:
    name: str
    group: str
    script: str
    generator_args: tuple[str, ...]
    task: str
    split: str


def baseline_specs() -> list[BaselineSpec]:
    specs = [
        BaselineSpec(
            "weak_center_test",
            "quick",
            "baselines/weak_heuristic_baselines.py",
            ("--mode", "center", "--split", "test", "--task", "all"),
            "all",
            "test",
        ),
        BaselineSpec(
            "weak_full_page_test",
            "quick",
            "baselines/weak_heuristic_baselines.py",
            ("--mode", "full_page", "--split", "test", "--task", "all"),
            "all",
            "test",
        ),
        BaselineSpec(
            "metadata_template_test",
            "quick",
            "baselines/weak_heuristic_baselines.py",
            ("--mode", "metadata_template", "--split", "test", "--task", "all"),
            "all",
            "test",
        ),
        BaselineSpec(
            "train_prior_microtext_test",
            "quick",
            "baselines/weak_heuristic_baselines.py",
            (
                "--mode",
                "label_prior",
                "--split",
                "test",
                "--task",
                "microtext",
                "--calibration-splits",
                "train",
            ),
            "microtext",
            "test",
        ),
        BaselineSpec(
            "dev_prior_visualdiff_test",
            "quick",
            "baselines/weak_heuristic_baselines.py",
            (
                "--mode",
                "label_prior",
                "--split",
                "test",
                "--task",
                "visualdiff",
                "--calibration-splits",
                "dev",
            ),
            "visualdiff",
            "test",
        ),
        BaselineSpec(
            "train_prior_all_test",
            "quick",
            "baselines/weak_heuristic_baselines.py",
            (
                "--mode",
                "label_prior",
                "--split",
                "test",
                "--task",
                "all",
                "--calibration-splits",
                "train",
            ),
            "all",
            "test",
        ),
        BaselineSpec(
            "dev_prior_all_test",
            "quick",
            "baselines/weak_heuristic_baselines.py",
            (
                "--mode",
                "label_prior",
                "--split",
                "test",
                "--task",
                "all",
                "--calibration-splits",
                "dev",
            ),
            "all",
            "test",
        ),
        BaselineSpec(
            "train_dev_prior_all_test",
            "quick",
            "baselines/weak_heuristic_baselines.py",
            (
                "--mode",
                "label_prior",
                "--split",
                "test",
                "--task",
                "all",
                "--calibration-splits",
                "train,dev",
            ),
            "all",
            "test",
        ),
    ]
    for cell in range(9):
        specs.append(
            BaselineSpec(
                f"weak_grid_cell_{cell}_test",
                "quick",
                "baselines/weak_heuristic_baselines.py",
                (
                    "--mode",
                    "grid",
                    "--split",
                    "test",
                    "--task",
                    "all",
                    "--grid-cell",
                    str(cell),
                ),
                "all",
                "test",
            )
        )
    for mode, name in (
        ("category_heuristic", "textlayer_heuristic_microtext_test"),
        ("smallest_span", "textlayer_smallest_span_microtext_test"),
        ("center_span", "textlayer_center_span_microtext_test"),
        ("page_frequency", "textlayer_page_frequency_microtext_test"),
    ):
        specs.append(
            BaselineSpec(
                name,
                "textlayer",
                "baselines/textlayer_microtext_baseline.py",
                ("--mode", mode, "--split", "test"),
                "microtext",
                "test",
            )
        )
    for mode, name, split in (
        ("absolute", "simple_diff_visualdiff_all", "all"),
        ("edge", "edge_diff_visualdiff_test", "test"),
        ("ssim", "ssim_diff_visualdiff_test", "test"),
        ("phase", "phase_diff_visualdiff_test", "test"),
        ("orb_residual", "orb_residual_diff_visualdiff_test", "test"),
        ("largest_cc", "largest_cc_diff_visualdiff_test", "test"),
        ("tile_zncc", "tile_zncc_diff_visualdiff_test", "test"),
    ):
        specs.append(
            BaselineSpec(
                name,
                "pixel",
                "baselines/simple_diff_baseline.py",
                ("--mode", mode, "--split", split),
                "visualdiff",
                split,
            )
        )
    specs.append(
        BaselineSpec(
            "textlayer_diff_visualdiff_test",
            "textlayer",
            "baselines/textlayer_diff_visualdiff_baseline.py",
            ("--split", "test"),
            "visualdiff",
            "test",
        )
    )
    return specs


def command_for_generator(root: Path, spec: BaselineSpec) -> list[str]:
    predictions = f"results/baselines/{spec.name}_predictions.jsonl"
    return [
        sys.executable,
        str(root / spec.script),
        "--root",
        str(root),
        "--input",
        "eng_bench.jsonl",
        "--output",
        predictions,
        *spec.generator_args,
    ]


def command_for_scorer(
    root: Path,
    spec: BaselineSpec,
    bootstrap_samples: int,
) -> list[str]:
    return [
        sys.executable,
        str(root / "tools" / "benchmark_runner.py"),
        "--gt",
        str(root / "eng_bench.jsonl"),
        "--pred",
        str(root / "results" / "baselines" / f"{spec.name}_predictions.jsonl"),
        "--task",
        spec.task,
        "--split",
        spec.split,
        "--model-name",
        spec.name,
        "--report-json",
        str(root / "results" / "baselines" / f"{spec.name}_report.json"),
        "--report-md",
        str(root / "results" / "baselines" / f"{spec.name}_report.md"),
        "--bootstrap-samples",
        str(bootstrap_samples),
    ]


def run_command(command: list[str], root: Path) -> tuple[int, float]:
    started = time.monotonic()
    completed = subprocess.run(command, cwd=root, check=False)
    return completed.returncode, round(time.monotonic() - started, 3)


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument(
        "--groups",
        default="quick,textlayer,pixel",
        help="Comma-separated groups: quick,textlayer,pixel",
    )
    parser.add_argument("--bootstrap-samples", type=int, default=200)
    parser.add_argument(
        "--report",
        default="results/baselines/baseline_regeneration_report.json",
    )
    parser.add_argument("--list", action="store_true", help="List selected baselines only.")
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    groups = {part.strip() for part in args.groups.split(",") if part.strip()}
    unknown = groups - {"quick", "textlayer", "pixel"}
    if unknown:
        raise ValueError(f"unknown baseline groups: {sorted(unknown)}")
    selected = [spec for spec in baseline_specs() if spec.group in groups]
    if args.list:
        for spec in selected:
            print(f"{spec.group}\t{spec.name}\t{spec.task}\t{spec.split}")
        return 0

    results: list[dict[str, Any]] = []
    failed = False
    for index, spec in enumerate(selected, start=1):
        print(f"[{index}/{len(selected)}] Generating {spec.name}", flush=True)
        generator_code, generator_seconds = run_command(
            command_for_generator(root, spec), root
        )
        scorer_code = -1
        scorer_seconds = 0.0
        if generator_code == 0:
            print(f"[{index}/{len(selected)}] Scoring {spec.name}", flush=True)
            scorer_code, scorer_seconds = run_command(
                command_for_scorer(root, spec, args.bootstrap_samples), root
            )
        passed = generator_code == 0 and scorer_code == 0
        failed = failed or not passed
        results.append(
            {
                **asdict(spec),
                "generator_returncode": generator_code,
                "generator_seconds": generator_seconds,
                "scorer_returncode": scorer_code,
                "scorer_seconds": scorer_seconds,
                "passed": passed,
            }
        )
        if not passed and not args.continue_on_error:
            break

    payload = {
        "groups": sorted(groups),
        "bootstrap_samples": args.bootstrap_samples,
        "selected_count": len(selected),
        "completed_count": len(results),
        "passed_count": sum(bool(row["passed"]) for row in results),
        "failed_count": sum(not row["passed"] for row in results),
        "results": results,
    }
    report_path = Path(args.report)
    if not report_path.is_absolute():
        report_path = root / report_path
    write_report(report_path, payload)
    print(f"[OK] Wrote {report_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
