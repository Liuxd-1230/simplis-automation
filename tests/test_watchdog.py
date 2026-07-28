from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from simplis_automation_v2.watchdog import (
    WatchdogConfig,
    WatchdogMonitor,
    _artifact_signature,
    captured_script_error_lines,
    parse_status_file,
    write_error_bundle,
)


class WatchdogTests(unittest.TestCase):
    def test_task_message_log_growth_is_not_artifact_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            message = root / "create-message.log"
            message.write_text("first", encoding="utf-8")
            first = _artifact_signature(root, excluded_paths=[message])
            message.write_text("first\nremote status echo", encoding="utf-8")
            second = _artifact_signature(root, excluded_paths=[message])
        self.assertEqual(first, second)

    def test_script_expression_error_is_captured_without_waiting_for_stall(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "job-message.log"
            log.write_text(
                "Error : Line 40 of run.sxscr : vector has no data\nThe expression cannot be evaluated\n",
                encoding="utf-8",
            )
            self.assertEqual(len(captured_script_error_lines(log)), 2)

    def test_error_bundle_survives_temporarily_locked_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            locked = root / "locked.log"
            locked.write_text("message", encoding="utf-8")
            with mock.patch("simplis_automation_v2.watchdog.sha256_file", side_effect=PermissionError("locked")):
                output = write_error_bundle(
                    root / "bundle.json",
                    job_id="j",
                    candidate={},
                    classification="script_error_blocked",
                    timeline=[],
                    status_values={},
                    dialogs=[],
                    artifacts=[locked],
                )
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertIn("read_error", payload["artifacts"][0])

    def test_artifact_or_cpu_change_resets_stall_clock(self) -> None:
        monitor = WatchdogMonitor(started_at=0.0, config=WatchdogConfig(stall_timeout=10.0, hard_timeout=100.0))
        first = monitor.observe(now=5.0, process_alive=True, cpu_seconds=1.0, artifact_signature="a", simulator_status="InProgress", dialogs=[])
        self.assertEqual(first["classification"], "running")
        progressed = monitor.observe(now=12.0, process_alive=True, cpu_seconds=2.0, artifact_signature="b", simulator_status="InProgress", dialogs=[])
        self.assertEqual(progressed["classification"], "running")
        stalled = monitor.observe(now=23.0, process_alive=True, cpu_seconds=2.0, artifact_signature="b", simulator_status="InProgress", dialogs=[])
        self.assertEqual(stalled["classification"], "stalled_in_progress")

    def test_idle_gui_cpu_does_not_hide_missing_script_progress(self) -> None:
        monitor = WatchdogMonitor(started_at=0.0, config=WatchdogConfig(stall_timeout=10.0, hard_timeout=100.0))
        monitor.observe(now=1.0, process_alive=True, launch_observed=True, cpu_seconds=1.0, artifact_signature="a", simulator_status="Unavailable", dialogs=[])
        result = monitor.observe(now=12.0, process_alive=True, launch_observed=True, cpu_seconds=8.0, artifact_signature="a", simulator_status="Unavailable", dialogs=[])
        self.assertEqual(result["classification"], "stalled_unresponsive")

    def test_responsive_gui_does_not_require_script_token_at_launch_timeout(self) -> None:
        monitor = WatchdogMonitor(
            started_at=0.0,
            config=WatchdogConfig(launch_timeout=15.0, stall_timeout=60.0, hard_timeout=100.0),
        )
        launched = monitor.observe(
            now=16.0,
            process_alive=True,
            launch_observed=True,
            cpu_seconds=1.0,
            artifact_signature="a",
            simulator_status="Unavailable",
            dialogs=[],
        )
        self.assertEqual(launched["classification"], "running")
        stalled = monitor.observe(
            now=77.0,
            process_alive=True,
            launch_observed=True,
            cpu_seconds=9.0,
            artifact_signature="a",
            simulator_status="Unavailable",
            dialogs=[],
        )
        self.assertEqual(stalled["classification"], "stalled_unresponsive")

    def test_unresponsive_process_still_hits_launch_timeout(self) -> None:
        monitor = WatchdogMonitor(
            started_at=0.0,
            config=WatchdogConfig(launch_timeout=15.0, stall_timeout=60.0, hard_timeout=100.0),
        )
        result = monitor.observe(
            now=16.0,
            process_alive=True,
            launch_observed=False,
            cpu_seconds=1.0,
            artifact_signature="a",
            simulator_status="Unavailable",
            dialogs=[],
        )
        self.assertEqual(result["classification"], "launch_timeout")

    def test_status_timer_repaint_does_not_hide_a_stalled_script(self) -> None:
        monitor = WatchdogMonitor(started_at=0.0, config=WatchdogConfig(stall_timeout=10.0, hard_timeout=100.0))
        monitor.observe(
            now=1.0,
            process_alive=True,
            cpu_seconds=1.0,
            artifact_signature="a",
            simulator_status="Unavailable",
            dialogs=[{"title": "SIMPLIS Status", "text": "Elapsed Time 1.0 sec"}],
        )
        result = monitor.observe(
            now=12.0,
            process_alive=True,
            cpu_seconds=2.0,
            artifact_signature="a",
            simulator_status="Unavailable",
            dialogs=[{"title": "SIMPLIS Status", "text": "Elapsed Time 12.0 sec"}],
        )
        self.assertEqual(result["classification"], "stalled_unresponsive")

    def test_status_parser_preserves_repeated_error_and_warning_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status = Path(tmp) / "status.txt"
            status.write_text(
                "completion_token=true\nsimulation_errors=first\nsimulation_errors=second\nsimulation_warnings=warn\n",
                encoding="utf-8",
            )
            parsed = parse_status_file(status)
        self.assertEqual(parsed["simulation_errors"], ["first", "second"])
        self.assertEqual(parsed["simulation_warnings"], ["warn"])

    def test_status_parser_retries_a_transient_windows_file_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status = Path(tmp) / "status.txt"
            status.write_text("placeholder", encoding="utf-8")
            with mock.patch.object(
                Path,
                "read_text",
                side_effect=[PermissionError("locked"), "script_started=true\ncompletion_token=true\n"],
            ), mock.patch("simplis_automation_v2.watchdog.time.sleep"):
                parsed = parse_status_file(status)
        self.assertEqual(parsed, {"script_started": "true", "completion_token": "true"})

    def test_error_bundle_contains_timeline_dialogs_status_and_log_excerpts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "design.deck.err"
            log.write_text("POP failed to converge", encoding="utf-8")
            bundle_path = write_error_bundle(
                root / "error-bundle.json",
                job_id="job-3",
                candidate={"ripple_gain": 0.4},
                classification="modal_error_blocked",
                timeline=[{"at": 1.0, "status": "SimErrors"}],
                status_values={"simulation_errors": ["POP failed"]},
                dialogs=[{"title": "SIMPLIS Error", "text": "No convergence"}],
                artifacts=[log],
            )
            data = json.loads(bundle_path.read_text(encoding="utf-8"))
        self.assertEqual(data["classification"], "modal_error_blocked")
        self.assertIn("POP failed to converge", data["artifacts"][0]["excerpt"])


if __name__ == "__main__":
    unittest.main()
