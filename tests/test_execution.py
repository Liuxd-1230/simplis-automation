from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simplis_automation_v2.execution import (
    JobStateMachine,
    assess_simulation_status,
    build_simulation_script,
    classify_watchdog,
    validate_group_evidence,
)


class ExecutionTests(unittest.TestCase):
    def test_state_machine_requires_every_stage(self) -> None:
        machine = JobStateMachine("job-1")
        for stage in (
            "launched",
            "script_started",
            "simulation_started",
            "running",
            "simulation_returned",
            "errors_collected",
            "vectors_exported",
            "validated",
            "complete",
        ):
            machine.advance(stage, at=1.0)
        self.assertTrue(machine.complete)

        broken = JobStateMachine("job-2")
        broken.advance("launched", at=1.0)
        with self.assertRaises(ValueError):
            broken.advance("simulation_started", at=2.0)

    def test_error_status_makes_existing_vectors_diagnostic_only(self) -> None:
        result = assess_simulation_status(
            {
                "completion_token": "true",
                "simplis_exit_code": "0",
                "simulation_has_errors": "1",
                "simulator_status": "SimErrors",
                "simulation_errors": ["POP failed to converge"],
                "vector_export_done": "1",
            },
            warning_allowlist=[],
            recent_error_files=["design.deck.err"],
        )
        self.assertFalse(result["trusted"])
        self.assertEqual(result["classification"], "diagnostic_only")
        self.assertIn("simulation_reported_errors", {item["code"] for item in result["errors"]})
        self.assertIn("simulator_status_failed", {item["code"] for item in result["errors"]})

    def test_unknown_warning_fails_closed_but_allowlisted_warning_passes(self) -> None:
        values = {
            "completion_token": "true",
            "simplis_exit_code": "0",
            "simulation_has_errors": "0",
            "simulator_status": "Warnings",
            "simulation_errors": [],
            "simulation_warnings": ["minimum topology duration reduced"],
            "vector_export_done": "1",
        }
        blocked = assess_simulation_status(values, warning_allowlist=[], recent_error_files=[])
        self.assertFalse(blocked["trusted"])
        allowed = assess_simulation_status(
            values,
            warning_allowlist=["minimum topology duration reduced"],
            recent_error_files=[],
        )
        self.assertTrue(allowed["trusted"])

    def test_watchdog_distinguishes_modal_error_stall_and_process_death(self) -> None:
        modal = classify_watchdog(
            started_at=0.0,
            now=10.0,
            last_progress_at=9.0,
            process_alive=True,
            simulator_status="SimErrors",
            dialog={"title": "SIMPLIS Error", "text": "No convergence"},
            stall_timeout=60.0,
            hard_timeout=240.0,
        )
        self.assertEqual(modal["classification"], "modal_error_blocked")

        stalled = classify_watchdog(
            started_at=0.0,
            now=70.0,
            last_progress_at=5.0,
            process_alive=True,
            simulator_status="InProgress",
            dialog=None,
            stall_timeout=60.0,
            hard_timeout=240.0,
        )
        self.assertEqual(stalled["classification"], "stalled_in_progress")

        dead = classify_watchdog(
            started_at=0.0,
            now=3.0,
            last_progress_at=2.0,
            process_alive=False,
            simulator_status="None",
            dialog=None,
            stall_timeout=60.0,
            hard_timeout=240.0,
        )
        self.assertEqual(dead["classification"], "process_dead")

    def test_watchdog_stops_on_non_error_modal_prompt(self) -> None:
        blocked = classify_watchdog(
            started_at=0.0,
            now=10.0,
            last_progress_at=9.0,
            process_alive=True,
            simulator_status="None",
            dialog={"title": "Save Schematic?", "text": "Press OK to continue", "is_modal": True},
            stall_timeout=60.0,
            hard_timeout=240.0,
        )
        self.assertEqual(blocked["classification"], "modal_dialog_blocked")

    def test_simulation_script_captures_status_errors_groups_and_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = build_simulation_script(
                schematic=root / "buck.sxsch",
                status_file=root / "status.txt",
                message_log=root / "messages.log",
                required_vectors={"simplis_pop1": ["#VOUT", "#PWM_HS"]},
            )
        self.assertIn("RedirectMessages dup", script)
        self.assertIn("GetSIMPLISExitCode()", script)
        self.assertIn("SimulationHasErrors()", script)
        self.assertIn("GetSimulationErrors()", script)
        self.assertIn("GetSimulatorStatus()", script)
        self.assertIn("simulation_info.netlist_path", script)
        self.assertIn("simulation_info.analysis", script)
        self.assertIn("GetSimulationInfo()", script)
        self.assertIn("SetGroup simplis_pop1", script)
        self.assertIn("group.simplis_pop1.selected=true", script)
        self.assertIn("Show /force /names", script)
        self.assertIn("Vec('#VOUT')", script)
        self.assertIn("Length(Vec('#VOUT'))", script)
        self.assertIn("IF v2_vector_length_0 > 0 THEN", script)
        self.assertIn("Echo errors_collected=true", script)

    def test_stale_or_wrong_group_is_rejected_before_export_scoring(self) -> None:
        result = validate_group_evidence(
            {
                "groups": "simplis_ac1 global",
                "group.simplis_ac1.selected": "true",
                "group.simplis_ac1.analysis": "tran None false",
                "group.simplis_ac1.vectors": "time #VOUT",
            },
            {"simplis_ac1": {"analysis": "ac", "vectors": ["LOOP"]}},
        )
        self.assertFalse(result["valid"])
        self.assertEqual({item["code"] for item in result["errors"]}, {"group_analysis_mismatch", "group_vector_missing"})

    def test_empty_error_sentinel_is_not_a_simulation_error(self) -> None:
        result = assess_simulation_status(
            {
                "completion_token": "true",
                "simplis_exit_code": "0",
                "simulation_has_errors": "0",
                "simulator_status": "Complete",
                "simulation_errors": ["<none>"],
                "vector_export_done": "1",
            },
            warning_allowlist=[],
            recent_error_files=[],
        )
        self.assertTrue(result["trusted"])

    def test_actual_task_none_after_return_requires_every_independent_proof(self) -> None:
        values = {
            "completion_token": "true",
            "simplis_exit_code": "0",
            "simulation_has_errors": "0",
            "simulator_status": "None",
            "simulation_errors": ["<none>"],
            "vector_export_done": "1",
        }
        evidence = {
            "watchdog_progress_observed": True,
            "stage_tokens_complete": True,
            "vectors_fresh_complete": True,
        }
        accepted = assess_simulation_status(
            values,
            warning_allowlist=[],
            recent_error_files=[],
            completion_capability="actual_task_evidence",
            completion_evidence=evidence,
        )
        self.assertTrue(accepted["trusted"], accepted)
        self.assertIn("actual_task_evidence", {item["code"] for item in accepted["warnings"]})

        for missing in evidence:
            rejected = assess_simulation_status(
                values,
                warning_allowlist=[],
                recent_error_files=[],
                completion_capability="actual_task_evidence",
                completion_evidence=evidence | {missing: False},
            )
            self.assertFalse(rejected["trusted"], missing)
            self.assertIn("none_after_return_evidence_incomplete", {item["code"] for item in rejected["errors"]})

        missing = assess_simulation_status(values, warning_allowlist=[], recent_error_files=[])
        self.assertFalse(missing["trusted"])
        self.assertIn("none_after_return_evidence_incomplete", {item["code"] for item in missing["errors"]})


if __name__ == "__main__":
    unittest.main()
