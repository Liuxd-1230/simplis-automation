from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


V2_ROOT = Path(__file__).resolve().parents[1]
if str(V2_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(V2_ROOT / "src"))

from simplis_automation_v2.sweep import enumerate_cases, run_sweep


class SweepTests(unittest.TestCase):
    def test_enumerates_linear_and_explicit_grid_in_stable_order(self) -> None:
        cases = enumerate_cases(
            {
                "grid": {
                    "rload": {"values": [1, 2]},
                    "lout": {"linear": {"start": "1uH", "stop": "2uH", "count": 2}},
                }
            }
        )
        self.assertEqual(
            cases,
            [
                {"lout": "1uH", "rload": 1},
                {"lout": "1uH", "rload": 2},
                {"lout": "2uH", "rload": 1},
                {"lout": "2uH", "rload": 2},
            ],
        )

    def test_log_grid_is_deterministic(self) -> None:
        cases = enumerate_cases({"grid": {"gain": {"log": {"start": 1, "stop": 100, "count": 3}}}})
        self.assertEqual([case["gain"] for case in cases], [1.0, 10.0, 100.0])

    def test_sweep_uses_isolated_case_directories_and_injected_workers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            circuit = root / "circuit.yaml"
            circuit.write_text("schema_version: simplis-automation/v2/circuit\n", encoding="utf-8")
            experiment = root / "experiment.yaml"
            experiment.write_text(
                "\n".join(
                    (
                        "schema_version: simplis-automation/v2/experiment",
                        "circuit: circuit.yaml",
                        "grid:",
                        "  rload:",
                        "    values: [1, 2]",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            seen: list[tuple[Path, dict[str, object]]] = []

            def compiler(circuit_path, case_dir, parameter_overrides, **_kwargs):
                seen.append((case_dir, dict(parameter_overrides)))
                manifest = case_dir / "build-manifest.json"
                manifest.write_text("{}", encoding="utf-8")
                return {"ok": True, "manifest_path": str(manifest)}

            def verifier(manifest_path, runtime=None):
                return {"ok": True, "status": "passed", "classification": "netlisted", "manifest_path": str(manifest_path)}

            result = run_sweep(experiment, root / "out", compiler=compiler, verifier=verifier)
            self.assertTrue(result["ok"])
            self.assertEqual([item[1] for item in seen], [{"rload": 1}, {"rload": 2}])
            self.assertEqual([path.name for path, _values in seen], ["case-000", "case-001"])
            self.assertTrue((root / "out" / "sweep-result.json").is_file())

    def test_case_limit_fails_before_compilation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            circuit = root / "circuit.yaml"
            circuit.write_text("x: y\n", encoding="utf-8")
            experiment = root / "experiment.yaml"
            experiment.write_text("circuit: circuit.yaml\ngrid:\n  x: {values: [1, 2, 3]}\n", encoding="utf-8")
            result = run_sweep(experiment, root / "out", max_cases=2)
            self.assertFalse(result["ok"])
            self.assertEqual(result["errors"][0]["code"], "grid_invalid")
