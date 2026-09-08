from __future__ import annotations

import json
from pathlib import Path

from tools import run_compatible_return_plan


def write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def safe_plan(command: str) -> dict[str, object]:
    return {
        "totals": {"uncovered_checklists": 0},
        "steps": [{"kind": "preflight", "packet": "all", "command": command}],
    }


def test_runner_dry_run_verifies_plan_without_executing(tmp_path: Path) -> None:
    marker = tmp_path / "derived" / "quality" / "marker.txt"
    write(
        tmp_path / "tools" / "make_marker.py",
        f"from pathlib import Path\np=Path({str(marker)!r})\np.parent.mkdir(parents=True, exist_ok=True)\np.write_text('ran')\n",
    )
    write(tmp_path / "derived" / "human_adjudication" / "returned" / "PACKET_WORKLIST.csv")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            safe_plan(
                'python tools\\make_marker.py --handoff-root "derived\\human_adjudication\\returned" '
                '--output-json "derived\\quality\\marker_report.json"'
            )
        ),
        encoding="utf-8",
    )

    report = run_compatible_return_plan.run_plan(tmp_path, plan_path, execute=False)

    assert report["valid"]
    assert report["executed"] is False
    assert report["steps"][0]["status"] == "planned"
    assert not marker.exists()


def test_runner_refuses_invalid_plan(tmp_path: Path) -> None:
    write(tmp_path / "tools" / "microtext_merge.py")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "totals": {"uncovered_checklists": 1},
                "steps": [
                    {
                        "kind": "unsafe",
                        "packet": "p01",
                        "command": 'python tools\\microtext_merge.py --output "microtext\\annotations\\gold.jsonl"',
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = run_compatible_return_plan.run_plan(tmp_path, plan_path, execute=False)

    assert not report["valid"]
    assert report["steps"] == []
    assert report["verification"]["issues"]


def test_runner_execute_runs_safe_step_and_writes_report(tmp_path: Path) -> None:
    marker = tmp_path / "derived" / "quality" / "marker.txt"
    write(
        tmp_path / "tools" / "make_marker.py",
        f"from pathlib import Path\np=Path({str(marker)!r})\np.parent.mkdir(parents=True, exist_ok=True)\np.write_text('ran')\n",
    )
    write(tmp_path / "derived" / "human_adjudication" / "returned" / "PACKET_WORKLIST.csv")
    plan_path = tmp_path / "plan.json"
    out_json = tmp_path / "run_report.json"
    plan_path.write_text(
        json.dumps(
            safe_plan(
                'python tools\\make_marker.py --handoff-root "derived\\human_adjudication\\returned" '
                '--output-json "derived\\quality\\marker_report.json"'
            )
        ),
        encoding="utf-8",
    )

    exit_code = run_compatible_return_plan.main(
        ["--root", str(tmp_path), "--plan", str(plan_path), "--execute", "--output-json", str(out_json)]
    )

    assert exit_code == 0
    assert marker.read_text(encoding="utf-8") == "ran"
    saved = json.loads(out_json.read_text(encoding="utf-8"))
    assert saved["steps"][0]["status"] == "passed"
