from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from simplis_automation_v2.experiment_runner import (
    _build_fast_job_script,
    _align_transient_vectors,
    _analysis_contract,
    _catalog_proof_eligibility,
    _complex_response_ratio,
    _f11_lines,
    _partition_analysis_experiments,
    _prepare_analysis_schematic,
    run_experiment,
    _script_string_expression,
    _simulation_info_matches_current_run,
)


class ExperimentRunnerTests(unittest.TestCase):
    def test_fast_job_contains_all_runtime_stages_in_one_script(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            schematic = root / "buck.sxsch"
            netlist = root / "buck.net"
            status = root / "status.txt"
            message = root / "message.log"
            create = root / "create.sxscr"
            create.write_text(
                "\n".join(
                    (
                        "Set EchoOn",
                        f"RedirectMessages dup \"{message}\"",
                        f"Let v2_status = OpenEchoFile('{status}', 'w')",
                        "Echo script_started=true",
                        "Let v2_close = CloseEchoFile()",
                        "NewSchem /simulator SIMPLIS buck",
                        f'SaveAs /force "{schematic}"',
                        f'Netlist /simplis "{netlist}"',
                        "Quit",
                    )
                ),
                encoding="utf-8",
            )
            script = _build_fast_job_script(
                create_script=create,
                experiment={"analyses": [{"directives": [".TRAN 10u 0"]}]},
                schematic=schematic,
                netlist=netlist,
                status_file=status,
                message_log=message,
                required_vectors={"simplis_tran1": ["#VOUT"]},
                vector_dir=root / "vectors",
            )
        self.assertEqual(script.count("NewSchem /simulator SIMPLIS"), 1)
        self.assertNotIn("OpenSchem /cd /readonly", script)
        self.assertLess(script.index("WriteF11Lines"), script.index("Netlist /simplis"))
        self.assertLess(script.index("Netlist /simplis"), script.index("simplis_run"))
        self.assertLess(script.index("simplis_run"), script.index("Echo vector_export_done=true"))
    def test_catalog_proof_accepts_only_the_documented_none_status_limitation(self) -> None:
        accepted = _catalog_proof_eligibility(
            trust={
                "trusted": False,
                "simulator_status": "None",
                "errors": [{"code": "simulator_status_incomplete"}],
                "warnings": [],
            },
            stage_tokens_complete=True,
            group_evidence_valid=True,
            vectors_fresh_complete=True,
            validations=[{"valid": True}],
            charts=["proof.png"],
        )
        self.assertTrue(accepted["eligible"], accepted)
        self.assertFalse(accepted["scoring_eligible"])
        self.assertIn("simulator_status_none", accepted["limitations"])

        rejected = _catalog_proof_eligibility(
            trust={
                "trusted": False,
                "simulator_status": "None",
                "errors": [{"code": "simplis_exit_nonzero"}],
                "warnings": [],
            },
            stage_tokens_complete=True,
            group_evidence_valid=True,
            vectors_fresh_complete=True,
            validations=[{"valid": True}],
            charts=["proof.png"],
        )
        self.assertFalse(rejected["eligible"])

    def test_transient_analysis_injects_sampling_points_when_unspecified(self) -> None:
        lines = _f11_lines({"analyses": [{"directives": [".TRAN 10u 0"]}]})
        self.assertEqual(lines[:3], [".simulator SIMPLIS", ".OPTIONS", "+ PSP_NPT=10001"])

    def test_explicit_transient_sampling_points_are_not_duplicated(self) -> None:
        lines = _f11_lines({"analyses": [{"directives": [".OPTIONS PSP_NPT=2001", ".TRAN 10u 0"]}]})
        self.assertEqual(sum("PSP_NPT" in line for line in lines), 1)

    def test_f11_literal_braces_are_encoded_without_script_evaluation(self) -> None:
        expression = _script_string_expression("+ TRIG_GATE={TRIG_GATE}")
        self.assertNotIn("{TRIG_GATE}", expression)
        self.assertIn("Chr(123)", expression)
        self.assertIn("Chr(125)", expression)

    def test_reused_transient_group_exports_each_vector_once(self) -> None:
        experiment = {
            "analyses": [
                {"type": "startup", "analysis": "tran", "group": "simplis_tran1", "vectors": {"VOUT": "#VOUT"}},
                {"type": "load_step", "analysis": "tran", "group": "simplis_tran1", "vectors": {"VOUT": "#VOUT"}},
            ]
        }
        groups, exports = _analysis_contract(experiment)
        self.assertEqual(groups, {"simplis_tran1": ["#VOUT"]})
        self.assertEqual(len(exports), 1)

    def test_partitions_tran_checks_from_pop_ac_but_keeps_shared_tran_group(self) -> None:
        experiment = {
            "name": "gold",
            "validation": {"thresholds": {"phase_margin_deg": {"min": 45}}},
            "analyses": [
                {"type": "startup", "analysis": "tran", "group": "simplis_tran1", "directives": [".TRAN 10u 0"], "vectors": {"VOUT": "#VOUT"}},
                {"type": "load_step", "analysis": "tran", "group": "simplis_tran1", "vectors": {"VOUT": "#VOUT"}},
                {"type": "pop_ac", "analysis": "ac", "group": "simplis_ac1", "parameters": {"soft_start_time": "100ns"}, "directives": [".POP", ".AC DEC 10 1k 1Meg"], "vectors": {"RETURN": "#FB"}},
            ],
        }
        circuit = Path("C:/work/gold.yaml")
        partitions = _partition_analysis_experiments(experiment, circuit_path=circuit)
        self.assertEqual(len(partitions), 2)
        self.assertEqual([item["type"] for item in partitions[0]["experiment"]["analyses"]], ["startup", "load_step"])
        self.assertEqual([item["type"] for item in partitions[1]["experiment"]["analyses"]], ["pop_ac"])
        self.assertEqual(partitions[0]["experiment"]["circuit"], str(circuit))
        self.assertEqual(partitions[1]["parameter_overrides"], {"soft_start_time": "100ns"})
        self.assertEqual(partitions[0]["experiment"]["validation"], {"thresholds": {}})

    def test_partitioned_run_aggregates_results_and_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            experiment_path = root / "experiment.json"
            experiment_path.write_text(
                json.dumps(
                    {
                        "schema_version": "simplis-automation/v2/experiment",
                        "name": "gold",
                        "circuit": "gold.yaml",
                        "analyses": [
                            {"type": "startup", "analysis": "tran", "group": "simplis_tran1", "directives": [".TRAN 10u 0"], "vectors": {"VOUT": "#VOUT"}},
                            {"type": "pop_ac", "analysis": "ac", "group": "simplis_ac1", "parameters": {"soft_start_time": "100ns"}, "directives": [".POP", ".AC DEC 10 1k 1Meg"], "vectors": {"RETURN": "#FB", "INJECT": "#INJ"}, "response": {"numerator": "RETURN", "denominator": "INJECT", "sign": -1}},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "simplis_automation_v2.experiment_runner._run_single_experiment",
                side_effect=[
                    {
                        "ok": True,
                        "classification": "complete",
                        "scoring_eligible": True,
                        "validation": [{"type": "startup", "valid": True, "metrics": {"vout_dc_error_pct": 0.5}}],
                        "charts": ["startup.png"],
                        "errors": [],
                        "threshold_errors": [],
                        "runtime": {"ready": True},
                    },
                    {
                        "ok": False,
                        "classification": "simulation_failed",
                        "scoring_eligible": False,
                        "validation": [{"type": "ac", "valid": False, "metrics": {}}],
                        "charts": [],
                        "errors": [{"code": "pop_convergence_failed"}],
                        "threshold_errors": [],
                    },
                ],
            ) as single:
                result = run_experiment(experiment_path, root / "out")
            self.assertEqual(single.call_count, 2)
            self.assertFalse(result["ok"])
            self.assertFalse(result["scoring_eligible"])
            self.assertEqual(result["classification"], "simulation_failed")
            self.assertEqual(result["analysis_isolation"]["run_count"], 2)
            self.assertEqual(len(result["validation"]), 2)
            self.assertEqual(result["errors"][0]["analysis_run"], "02-ac-simplis_ac1")
            self.assertEqual(result["analysis_runs"][1]["effective_parameters"], {"soft_start_time": "100ns"})

    def test_partitioned_run_applies_cross_analysis_thresholds_after_all_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            experiment_path = root / "experiment.json"
            experiment_path.write_text(
                json.dumps(
                    {
                        "schema_version": "simplis-automation/v2/experiment",
                        "circuit": "gold.yaml",
                        "analyses": [
                            {"type": "startup", "analysis": "tran", "group": "tran", "directives": [".TRAN 10u 0"], "vectors": {"VOUT": "#VOUT"}},
                            {"type": "pop_ac", "analysis": "ac", "group": "ac", "directives": [".POP", ".AC DEC 10 1k 1Meg"], "vectors": {"RETURN": "#FB", "INJECT": "#INJ"}, "response": {"numerator": "RETURN", "denominator": "INJECT", "sign": -1}},
                        ],
                        "validation": {"thresholds": {"phase_margin_deg": {"min": 45}}},
                    }
                ),
                encoding="utf-8",
            )
            completed = [
                {"ok": True, "classification": "complete", "scoring_eligible": True, "validation": [{"valid": True, "metrics": {"vout_dc_error_pct": 0.5}}], "charts": ["tran.png"], "errors": []},
                {"ok": True, "classification": "complete", "scoring_eligible": True, "validation": [{"valid": True, "metrics": {"phase_margin_deg": 40.0}}], "charts": ["ac.png"], "errors": []},
            ]
            with patch("simplis_automation_v2.experiment_runner._run_single_experiment", side_effect=completed):
                result = run_experiment(experiment_path, root / "out")
            self.assertFalse(result["ok"])
            self.assertEqual(result["classification"], "electrical_behavior_failed")
            self.assertEqual(result["threshold_errors"][0]["metric"], "phase_margin_deg")
            self.assertEqual(result["errors"][0]["code"], "aggregate_thresholds_failed")

    def test_aligns_independent_event_axes_without_zipping_unrelated_samples(self) -> None:
        parsed = {
            "S": {"x": [0.0, 1.0, 2.0], "y": [0.0, 5.0, 0.0]},
            "Q": {"x": [0.0, 1.1, 2.0], "y": [0.0, 5.0, 5.0]},
        }
        aligned = _align_transient_vectors(parsed, {"S": "#SET", "Q": "#Q"}, step_roles={"S", "Q"})
        self.assertEqual(aligned["time"], [0.0, 1.0, 1.1, 2.0])
        self.assertEqual(len(aligned["S"]), len(aligned["Q"]))
        self.assertEqual(aligned["S"][2], 5.0)
        self.assertEqual(aligned["Q"][1], 0.0)

    def test_analysis_prepare_uses_task_watchdog_for_timeout_and_process_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            schematic = root / "input.sxsch"
            schematic.write_text("schematic", encoding="utf-8")
            with patch(
                "simplis_automation_v2.experiment_runner.run_script_with_watchdog",
                return_value={"ok": False, "classification": "hard_timeout", "owned_pids": [4321]},
            ) as watchdog:
                prepared, result = _prepare_analysis_schematic(
                    schematic,
                    {"analyses": [{"directives": [".TRAN 1u 0"]}]},
                    root,
                    {"executable": "SIMetrix.exe"},
                    1,
                )
            self.assertIsNone(prepared)
            self.assertEqual(result["process"]["classification"], "hard_timeout")
            self.assertTrue(watchdog.call_args.kwargs["assess_completion"] is False)

    def test_ac_response_is_explicit_signed_complex_ratio(self) -> None:
        parsed = {
            "RETURN": {
                "x": [1.0, 10.0, 100.0],
                "y": [{"real": 2.0, "imag": 2.0}, {"real": 1.0, "imag": 0.0}, {"real": 0.2, "imag": -0.1}],
            },
            "INJECT": {
                "x": [1.0, 10.0, 100.0],
                "y": [{"real": 1.0, "imag": 1.0}, {"real": 0.5, "imag": 0.0}, {"real": 0.1, "imag": 0.0}],
            },
        }
        ratio = _complex_response_ratio(parsed, {"numerator": "RETURN", "denominator": "INJECT", "sign": -1})
        self.assertTrue(ratio["valid"], ratio)
        self.assertAlmostEqual(ratio["response"][0]["real"], -2.0)
        self.assertAlmostEqual(ratio["response"][0]["imag"], 0.0)
        self.assertEqual(ratio["provenance"], {"numerator": "RETURN", "denominator": "INJECT", "sign": -1})
        zero = parsed | {"INJECT": {"x": [1.0, 10.0, 100.0], "y": [{"real": 0.0, "imag": 0.0}] * 3}}
        self.assertFalse(_complex_response_ratio(zero, {"numerator": "RETURN", "denominator": "INJECT", "sign": 1})["valid"])

    def test_simulation_info_must_point_to_fresh_current_run_netlist(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            schematic = root / "buck.sxsch"
            netlist = root / "buck.net"
            schematic.write_text("schematic", encoding="utf-8")
            netlist.write_text("netlist", encoding="utf-8")
            started = netlist.stat().st_mtime - 0.1
            matched = _simulation_info_matches_current_run(
                {"simulation_info.netlist_path": str(netlist), "simulation_info.analysis": "TRAN"},
                schematic=schematic,
                run_root=root,
                run_started_at=started,
            )
            self.assertTrue(matched["matched"], matched)
            wrong = root / "old.net"
            wrong.write_text("old", encoding="utf-8")
            rejected = _simulation_info_matches_current_run(
                {"simulation_info.netlist_path": str(wrong), "simulation_info.analysis": "TRAN"},
                schematic=schematic,
                run_root=root,
                run_started_at=started,
            )
            self.assertFalse(rejected["matched"])


if __name__ == "__main__":
    unittest.main()
