from __future__ import annotations

import unittest
from unittest.mock import patch

from simplis_automation_v2.cli import build_parser, command_doctor


class CliTests(unittest.TestCase):
    def test_doctor_is_static_and_never_starts_a_process(self) -> None:
        args = build_parser().parse_args(["doctor"])
        with patch(
            "simplis_automation_v2.cli._runtime_from_args",
            return_value={"ready": True, "watchdog_ready": True},
        ), patch("subprocess.Popen") as popen:
            result = command_doctor(args)
        popen.assert_not_called()
        self.assertTrue(result["ready"])
        self.assertEqual(result["process_launches"], 0)
        self.assertEqual(result["completion_contract"], "actual_task_evidence")

    def test_default_cli_name_and_finalize_commands(self) -> None:
        parser = build_parser()
        self.assertEqual(parser.prog, "simplis")
        self.assertEqual(parser.parse_args(["finalize", "result.json", "--out-dir", "out"]).command, "finalize")
        self.assertEqual(parser.parse_args(["finalize-review", "request.json", "--review", "review.json"]).command, "finalize-review")


if __name__ == "__main__":
    unittest.main()
