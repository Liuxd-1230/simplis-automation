from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from simplis_automation_v2.optimization import run_optimization


class OptimizationTests(unittest.TestCase):
    def test_failed_promotion_corner_blocks_derived_best_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "circuit.yaml").write_text("schema_version: simplis-automation/v2\n", encoding="utf-8")
            experiment = {
                "schema_version": "simplis-automation/v2/experiment",
                "circuit": "circuit.yaml",
                "optimize": {
                    "max_evaluations": 3,
                    "parameters": {"ton": {"min": 1.0, "max": 3.0, "initial": 2.0}},
                    "objective": {"error": {"target": 0, "weight": 1}},
                    "esr_continuation": {"parameter": "esr", "target": 0.005, "multipliers": [1]},
                    "corners": [{"name": "bad", "parameters": {"vin": 16}}],
                },
            }
            path = root / "experiment.yaml"
            path.write_text(yaml.safe_dump(experiment, sort_keys=False), encoding="utf-8")

            def evaluator(candidate, directory, context):
                if context["stage"] == "promotion":
                    return {"ok": False, "classification": "electrical_behavior_failed"}
                return {"ok": True, "classification": "complete", "validation": [{"metrics": {"error": 0.0}}]}

            result = run_optimization(path, root / "out", evaluator=evaluator)
            self.assertFalse(result["ok"])
            self.assertEqual(result["worst_corner"]["name"], "bad")
            self.assertNotIn("derived_output", result)

    def test_failures_do_not_stop_budget_and_esr_finishes_at_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "circuit.yaml").write_text("schema_version: simplis-automation/v2\n", encoding="utf-8")
            experiment = {
                "schema_version": "simplis-automation/v2/experiment",
                "name": "opt",
                "circuit": "circuit.yaml",
                "optimize": {
                    "max_evaluations": 9,
                    "parameters": {"ton": {"min": 1.0, "max": 3.0, "initial": 2.0}},
                    "objective": {"vout_dc_error_pct": {"target": 0, "weight": 1}},
                    "esr_continuation": {"parameter": "esr", "target": 0.005, "multipliers": [4, 2, 1]},
                },
            }
            path = root / "experiment.yaml"
            path.write_text(yaml.safe_dump(experiment, sort_keys=False), encoding="utf-8")
            calls = []

            def evaluator(candidate, directory, context):
                calls.append(context["esr_multiplier"])
                if len(calls) % 3:
                    return {"ok": False, "classification": "simulation_failed"}
                return {"ok": True, "classification": "complete", "validation": [{"metrics": {"vout_dc_error_pct": abs(candidate["ton"] - 2)}}]}

            result = run_optimization(path, root / "out", evaluator=evaluator)
        self.assertEqual(result["evaluations"], 9)
        self.assertTrue(result["budget_exhausted"])
        self.assertEqual(calls, [4, 4, 4, 2, 2, 2, 1, 1, 1])
        self.assertEqual(result["counts"]["simulation_failed"], 6)


if __name__ == "__main__":
    unittest.main()
