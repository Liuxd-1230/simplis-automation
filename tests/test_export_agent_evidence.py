from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts.export_agent_evidence import export_evidence, main, parse_key_value_metrics, redact_path


class ExportAgentEvidenceTests(unittest.TestCase):
    def assert_no_path_fragment(self, value: Any, fragment: str) -> None:
        if isinstance(value, str):
            self.assertNotIn(fragment, value)
            self.assertNotIn(fragment.replace("\\", "/"), value)
            self.assertNotIn(fragment.replace("/", "\\"), value)
            return
        if isinstance(value, dict):
            for key, item in value.items():
                self.assert_no_path_fragment(key, fragment)
                self.assert_no_path_fragment(item, fragment)
            return
        if isinstance(value, list):
            for item in value:
                self.assert_no_path_fragment(item, fragment)

    def test_parse_key_value_metrics_converts_scalars_and_keeps_embedded_equals(self) -> None:
        metrics = parse_key_value_metrics(
            """
# comment
failed=false
passes=3
vout_avg=1.201
ripple=-2.5e-3
reason=missing_curve=VOUT
label=steady state
ignored line
"""
        )

        self.assertEqual(metrics["failed"], False)
        self.assertEqual(metrics["passes"], 3)
        self.assertEqual(metrics["vout_avg"], 1.201)
        self.assertEqual(metrics["ripple"], -2.5e-3)
        self.assertEqual(metrics["reason"], "missing_curve=VOUT")
        self.assertEqual(metrics["label"], "steady state")
        self.assertNotIn("ignored line", metrics)

    def test_export_evidence_collects_netlist_deck_logs_metrics_and_vectors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "design.net").write_text(
                ".node_map VIN 1\n.node_map VOUT 2\nV1 1 0 12\nR1 2 0 1\n",
                encoding="utf-8",
            )
            (root / "design.deck").write_text(
                ".POP\n"
                "+ TRIG_GATE=X1.!D_CYCLE\n"
                ".TRAN 60u 0\n"
                ".AC DEC 10 1 1MEG\n"
                ".PRINT V(VOUT)\n"
                ".GRAPH \":VOUT\"\n",
                encoding="utf-8",
            )
            (root / "design.deck.err").write_text(
                "*** Warning ***: test warning\n*** ERROR ***: test error\n",
                encoding="utf-8",
            )
            (root / "metrics.txt").write_text(
                "failed=true\nreason=missing_curve=VOUT\nvout_avg=0\n",
                encoding="utf-8",
            )
            (root / "metrics.json").write_text(
                json.dumps({"efficiency": 0.91, "samples": 42}),
                encoding="utf-8",
            )
            (root / "vectors").mkdir()
            (root / "vectors" / "VOUT.txt").write_text(
                "time value\n0 0\n1e-6 1.2\n2e-6 1.1\n",
                encoding="utf-8",
            )

            report = export_evidence(root)

        self.assertIn("VIN", report["netlist"]["node_map"])
        self.assertIn("VOUT", report["netlist"]["node_map"])
        self.assertEqual(report["netlist"]["device_lines"], 2)
        self.assertEqual(report["deck"]["pop"], [".POP"])
        self.assertTrue(report["deck"]["has_pop"])
        self.assertTrue(report["deck"]["has_tran"])
        self.assertTrue(report["deck"]["has_ac"])
        self.assertEqual(report["deck"]["print_lines"], [".PRINT V(VOUT)"])
        self.assertEqual(report["deck"]["graph_lines"], ['.GRAPH ":VOUT"'])
        self.assertIn("X1.!D_CYCLE", report["deck"]["trigger_gates"])
        self.assertEqual(report["metrics"]["values"]["failed"], True)
        self.assertEqual(report["metrics"]["values"]["reason"], "missing_curve=VOUT")
        self.assertEqual(report["metrics"]["values"]["efficiency"], 0.91)
        self.assertEqual(report["waveforms"]["vectors"]["VOUT.txt"]["samples"], 3)
        self.assertEqual(report["waveforms"]["vectors"]["VOUT.txt"]["min"], 0.0)
        self.assertEqual(report["waveforms"]["vectors"]["VOUT.txt"]["max"], 1.2)
        self.assertAlmostEqual(report["waveforms"]["vectors"]["VOUT.txt"]["mean"], 0.7666666666666666)
        self.assertEqual(report["simulator_logs"]["errors"], ["*** ERROR ***: test error"])
        self.assertEqual(report["simulator_logs"]["warnings"], ["*** Warning ***: test warning"])

    def test_export_evidence_summarizes_simetrix_ac_complex_vectors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ac_n46.txt").write_text(
                "freq\tN46\n"
                "1000\t(-0.004922329565 ,-0.001443921967 )\n"
                "1096.4\t(-0.005050021956 ,0.001142594522 )\n",
                encoding="utf-8",
            )

            report = export_evidence(root)

        vector = report["waveforms"]["vectors"]["ac_n46.txt"]
        self.assertEqual(vector["samples"], 2)
        self.assertEqual(vector["x_name"], "freq")
        self.assertEqual(vector["y_name"], "N46")
        self.assertEqual(
            vector["first_complex"],
            {"real": -0.004922329565, "imag": -0.001443921967},
        )
        self.assertEqual(
            vector["last_complex"],
            {"real": -0.005050021956, "imag": 0.001142594522},
        )
        self.assertNotIn("first", vector)
        self.assertAlmostEqual(vector["real_mean"], -0.0049861757605)
        self.assertAlmostEqual(vector["imag_mean"], -0.0001506637225)
        self.assertAlmostEqual(vector["magnitude_first"], 0.005129760266716133)
        self.assertAlmostEqual(
            vector["phase_deg_first"],
            math.degrees(math.atan2(-0.001443921967, -0.004922329565)),
        )

    def test_export_evidence_preserves_headerless_numeric_vectors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "raw_vector.txt").write_text(
                "0 0\n"
                "1e-6 1.2\n"
                "2e-6 1.1\n",
                encoding="utf-8",
            )

            report = export_evidence(root)

        vector = report["waveforms"]["vectors"]["raw_vector.txt"]
        self.assertEqual(vector["samples"], 3)
        self.assertEqual(vector["first"], 0.0)
        self.assertEqual(vector["last"], 1.1)
        self.assertEqual(vector["x_min"], 0.0)
        self.assertEqual(vector["x_max"], 2e-6)
        self.assertNotIn("x_name", vector)

    def test_export_evidence_ignores_vector_export_status_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "status").mkdir()
            (root / "status" / "vector_export_status.txt").write_text(
                "start_vector_export=1\n"
                "simplis_exit_code= 0\n"
                "vector_export_done=1\n",
                encoding="utf-8",
            )
            (root / "vectors").mkdir()
            (root / "vectors" / "pop_vout.txt").write_text(
                "time\tVOUT\n"
                "0\t0\n"
                "1e-6\t1.2\n",
                encoding="utf-8",
            )

            report = export_evidence(root)

        self.assertEqual(report["artifacts"]["waveforms"], [str(root / "vectors" / "pop_vout.txt")])
        self.assertEqual(set(report["waveforms"]["vectors"]), {"pop_vout.txt"})
        self.assertNotIn("vector_export_status.txt", report["waveforms"]["vectors"])

    def test_redact_path_removes_absolute_prefixes_with_windows_or_posix_separators(self) -> None:
        roots = [Path(r"C:\projects\simplis_runs")]

        self.assertEqual(
            redact_path(r"C:\projects\simplis_runs\outputs\run_001\design.deck.err", roots=roots),
            r"<redacted>\outputs\run_001\design.deck.err",
        )
        self.assertEqual(
            redact_path("C:/projects/simplis_runs/outputs/run_001/design.deck.err", roots=roots),
            "<redacted>/outputs/run_001/design.deck.err",
        )

    def test_export_evidence_redacts_report_paths_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "run_001"
            root.mkdir()
            (root / "design.deck.err").write_text(
                f"ERROR: failed while reading {root / 'design.deck'}\n",
                encoding="utf-8",
            )

            report = export_evidence(root, redact_paths=True)

        encoded = json.dumps(report, sort_keys=True)
        self.assertNotIn(str(root), encoded)
        self.assertNotIn(str(root.parent), encoded)
        self.assert_no_path_fragment(report, str(root))
        self.assert_no_path_fragment(report, str(root.parent))
        self.assertIn("<redacted>", encoded)

    def test_export_evidence_reports_missing_root_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"

            report = export_evidence(missing)

        self.assertEqual(report["root"], str(missing))
        self.assertEqual(report["artifacts"]["netlists"], [])
        self.assertIn("work directory does not exist", report["warnings"][0])

    def test_export_evidence_handles_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            report = export_evidence(root)

        self.assertEqual(report["netlist"]["node_map"], {})
        self.assertEqual(report["netlist"]["device_lines"], 0)
        self.assertEqual(report["deck"]["trigger_gates"], [])
        self.assertEqual(report["metrics"]["values"], {})
        self.assertEqual(report["waveforms"]["vectors"], {})
        self.assertEqual(report["simulator_logs"]["errors"], [])

    def test_deck_trigger_extraction_handles_periodic_op_and_generic_assignments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "run.deck").write_text(
                "XPOP PERIODIC_OP_V8 TRIG_GATE={TRIG_GATE}\n"
                "+ TRIG_GATE = XSW.!D_CYCLE\n"
                "* TRIG_GATE=COMMENTED.!OUT\n"
                "; PERIODIC_OP_V8 TRIG_GATE=COMMENTED_TOO.!OUT\n"
                ".POP TRIG_GATE=XCTRL.!GATE\n",
                encoding="utf-8",
            )

            report = export_evidence(root)

        self.assertEqual(report["deck"]["trigger_gates"], ["{TRIG_GATE}", "XSW.!D_CYCLE", "XCTRL.!GATE"])
        self.assertEqual(report["deck"]["unresolved_trigger_gates"], ["{TRIG_GATE}"])
        self.assertNotIn("COMMENTED.!OUT", report["deck"]["trigger_gates"])

    def test_log_warning_and_error_extraction_includes_file_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sim.lst").write_text(
                "Info only\n"
                "0 errors, 0 warnings\n"
                "no error on previous run\n"
                "WARNING: convergence soft limit\n"
                "Error: timestep too small\n",
                encoding="utf-8",
            )

            report = export_evidence(root)

        self.assertEqual(report["simulator_logs"]["warning_entries"][0]["line"], "WARNING: convergence soft limit")
        self.assertEqual(report["simulator_logs"]["error_entries"][0]["line"], "Error: timestep too small")
        self.assertEqual(report["simulator_logs"]["warning_entries"][0]["file"], str(root / "sim.lst"))
        self.assertEqual(report["simulator_logs"]["error_entries"][0]["line_number"], 5)

    def test_metric_key_collisions_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "metrics_a.txt").write_text("failed=false\nvout_avg=1.2\n", encoding="utf-8")
            (root / "metrics_b.txt").write_text("failed=true\nreason=late file\n", encoding="utf-8")

            report = export_evidence(root)

        self.assertEqual(report["metrics"]["values"]["failed"], True)
        self.assertEqual(len(report["metrics"]["warnings"]), 1)
        self.assertIn("overwrites previous value", report["metrics"]["warnings"][0])

    def test_main_writes_json_and_markdown_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "design.net").write_text(".node_map VOUT 1\nR1 1 0 1k\n", encoding="utf-8")
            out = root / "evidence.json"
            summary = root / "evidence.md"

            rc = main(["--work-dir", str(root), "--out", str(out), "--summary-md", str(summary)])

            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["netlist"]["device_lines"], 1)
            self.assertIn("SIMPLIS Agent Evidence", summary.read_text(encoding="utf-8"))

    def test_main_redacts_paths_in_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "run.err").write_text(f"ERROR in {root / 'run.deck'}\n", encoding="utf-8")
            out = root / "evidence.json"
            summary = root / "evidence.md"

            rc = main(
                [
                    "--work-dir",
                    str(root),
                    "--out",
                    str(out),
                    "--summary-md",
                    str(summary),
                    "--redact-paths",
                ]
            )

            self.assertEqual(rc, 0)
            self.assertNotIn(str(root), out.read_text(encoding="utf-8"))
            self.assertNotIn(str(root), summary.read_text(encoding="utf-8"))

    def test_simplis_cli_export_agent_evidence_subcommand_writes_outputs(self) -> None:
        from scripts.simplis_cli import main as simplis_main

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "design.net").write_text(".node_map VOUT 1\nR1 1 0 1k\n", encoding="utf-8")
            out = root / "cli-evidence.json"
            summary = root / "cli-evidence.md"

            rc = simplis_main(
                [
                    "export-agent-evidence",
                    "--work-dir",
                    str(root),
                    "--out",
                    str(out),
                    "--summary-md",
                    str(summary),
                    "--redact-paths",
                ]
            )

            self.assertEqual(rc, 0)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["netlist"]["device_lines"], 1)
            self.assertNotIn(str(root), out.read_text(encoding="utf-8"))
            self.assertIn("SIMPLIS Agent Evidence", summary.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
