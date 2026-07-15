from __future__ import annotations

import csv
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from closed_loop_optimize import read_metrics, write_optimizer_outputs  # noqa: E402


class ClosedLoopOptimizeTests(unittest.TestCase):
    def write_mock_spec(self, root: Path, *, max_evals: int = 2) -> Path:
        template = root / "candidate_template.sxscr"
        template.write_text(
            "Echo candidate={{gain}} {{cap}}\n"
            "Echo result={{RESULT_JSON}}\n"
            "Echo candidate_json={{CANDIDATE_JSON}}\n"
            "Quit\n",
            encoding="utf-8",
        )
        spec = {
            "strategy": "grid",
            "max_evals": max_evals,
            "script_template": str(template),
            "parameters": {
                "gain": [1.0, 2.0, 3.0],
                "cap": [1e-9],
            },
            "weights": {"objective": 1.0},
            "mock_target": {"gain": 2.0, "cap": 1e-9},
            "report": {
                "title": "Buck 补偿器优化报告",
                "metric_labels_zh": {
                    "objective": "目标函数",
                    "vout_dc_error_mv": "输出直流误差",
                },
                "chart_metrics": ["objective", "vout_dc_error_mv"],
            },
        }
        spec_path = root / "optimizer_spec.json"
        spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
        return spec_path

    def run_cli(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        cli = Path(__file__).resolve().parents[1] / "scripts" / "simplis_cli.py"
        return subprocess.run(
            [sys.executable, str(cli), *args],
            check=False,
            text=True,
            capture_output=True,
        )

    def test_cli_optimize_mock_writes_resume_outputs_reports_and_charts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = self.write_mock_spec(root)
            work = root / "work"

            proc = self.run_cli(["optimize", "--spec", str(spec), "--work-dir", str(work), "--mock", "--batch"])

            self.assertEqual(proc.returncode, 0, proc.stderr)
            history = json.loads((work / "optimization_history.json").read_text(encoding="utf-8"))
            best = json.loads((work / "best_candidate.json").read_text(encoding="utf-8"))
            self.assertEqual(len(history), 2)
            self.assertEqual(best["candidate"]["gain"], 2.0)

            with (work / "summary.csv").open(encoding="utf-8") as handle:
                summary_rows = list(csv.DictReader(handle))
            self.assertEqual(len(summary_rows), 2)
            self.assertIn("candidate_gain", summary_rows[0])
            self.assertIn("metric_objective", summary_rows[0])

            report = (work / "report.zh-CN.md").read_text(encoding="utf-8")
            self.assertIn("# Buck 补偿器优化报告", report)
            self.assertIn("最佳参数", report)
            self.assertIn("失败候选", report)
            self.assertIn("charts/score_convergence.png", report)

            for name in ("score_convergence.png", "key_metrics.png", "parameter_trace.png"):
                chart = work / "charts" / name
                self.assertTrue(chart.exists(), name)
                self.assertGreater(chart.stat().st_size, 100, name)
                self.assertEqual(chart.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_cli_optimize_resume_skips_existing_candidates_and_fresh_resets_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = self.write_mock_spec(root, max_evals=3)
            work = root / "work"

            first = self.run_cli([
                "optimize",
                "--spec",
                str(spec),
                "--work-dir",
                str(work),
                "--mock",
                "--max-evals",
                "1",
            ])
            self.assertEqual(first.returncode, 0, first.stderr)
            first_script = work / "candidate_0000.sxscr"
            first_script.write_text(first_script.read_text(encoding="utf-8") + "; keep marker\n", encoding="utf-8")

            second = self.run_cli([
                "optimize",
                "--spec",
                str(spec),
                "--work-dir",
                str(work),
                "--mock",
                "--max-evals",
                "2",
            ])
            self.assertEqual(second.returncode, 0, second.stderr)
            history = json.loads((work / "optimization_history.json").read_text(encoding="utf-8"))
            self.assertEqual(len(history), 2)
            self.assertEqual(len({json.dumps(row["candidate"], sort_keys=True) for row in history}), 2)
            self.assertIn("; keep marker", first_script.read_text(encoding="utf-8"))

            fresh = self.run_cli([
                "optimize",
                "--spec",
                str(spec),
                "--work-dir",
                str(work),
                "--mock",
                "--max-evals",
                "1",
                "--fresh",
            ])
            self.assertEqual(fresh.returncode, 0, fresh.stderr)
            fresh_history = json.loads((work / "optimization_history.json").read_text(encoding="utf-8"))
            self.assertEqual(len(fresh_history), 1)
            self.assertNotIn("; keep marker", first_script.read_text(encoding="utf-8"))

    def test_read_metrics_accepts_key_value_direct_json_and_wrapped_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key_value = root / "metrics.txt"
            direct_json = root / "direct.json"
            wrapped_json = root / "wrapped.json"
            key_value.write_text("failed=false\nobjective=1.25\nlabel=steady\n", encoding="utf-8")
            direct_json.write_text(json.dumps({"failed": False, "objective": 2.5}), encoding="utf-8")
            wrapped_json.write_text(
                json.dumps({"candidate": {"gain": 1.0}, "metrics": {"objective": 3.5}}),
                encoding="utf-8",
            )

            self.assertEqual(read_metrics(key_value, 0, dry_run=False)["objective"], 1.25)
            self.assertEqual(read_metrics(direct_json, 0, dry_run=False)["objective"], 2.5)
            wrapped = read_metrics(wrapped_json, 0, dry_run=False)
            self.assertEqual(wrapped["objective"], 3.5)
            self.assertFalse(wrapped["failed"])

    def test_write_optimizer_outputs_serializes_failed_candidates_and_reports_them_last(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            history = [
                {
                    "index": 0,
                    "candidate": {"gain": 1.0},
                    "metrics": {"failed": True, "reason": "missing_metrics"},
                    "score": math.inf,
                    "returncode": 2,
                },
                {
                    "index": 1,
                    "candidate": {"gain": 2.0},
                    "metrics": {"failed": False, "objective": 0.0},
                    "score": 0.0,
                    "returncode": 0,
                },
            ]
            spec = {
                "parameters": {"gain": [1.0, 2.0]},
                "report": {
                    "title": "测试报告",
                    "metric_labels_zh": {"objective": "目标函数"},
                    "chart_metrics": ["objective"],
                },
            }

            write_optimizer_outputs(work, history, spec)

            saved_history = json.loads((work / "optimization_history.json").read_text(encoding="utf-8"))
            self.assertIsNone(saved_history[0]["score"])
            best = json.loads((work / "best_candidate.json").read_text(encoding="utf-8"))
            self.assertEqual(best["candidate"]["gain"], 2.0)
            with (work / "summary.csv").open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["candidate_gain"], "2")
            self.assertEqual(rows[-1]["failed"], "true")
            report = (work / "report.zh-CN.md").read_text(encoding="utf-8")
            self.assertIn("失败候选：1 / 2", report)


if __name__ == "__main__":
    unittest.main()
