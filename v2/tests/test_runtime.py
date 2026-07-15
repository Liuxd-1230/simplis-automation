from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


V2_ROOT = Path(__file__).resolve().parents[1]
if str(V2_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(V2_ROOT / "src"))

from simplis_automation_v2.catalog import load_catalog
from simplis_automation_v2.runtime import _completion_probe_script, completion_capability_from_evidence, resolve_runtime


class RuntimeTests(unittest.TestCase):
    def _runtime_paths(self, root: Path) -> tuple[Path, Path]:
        executable = root / "SIMetrix840" / "bin64" / "SIMetrix.exe"
        symbols = root / "SIMetrix840" / "support" / "symbollibs"
        executable.parent.mkdir(parents=True)
        symbols.mkdir(parents=True)
        executable.write_text("stub", encoding="utf-8")
        (executable.parent / "SxCommand.exe").write_text("stub", encoding="utf-8")
        (symbols / "basic.sxslb").write_text("library", encoding="utf-8")
        return executable, symbols

    def test_explicit_runtime_is_ready_and_derives_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable, symbols = self._runtime_paths(Path(temporary))
            result = resolve_runtime(simetrix_exe=executable, symbol_library_dir=symbols)
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["version"], "8.4")
            self.assertTrue(result["version_supported"])
            self.assertTrue(result["watchdog_ready"])
            self.assertEqual(result["completion_capability"], "unavailable")
            self.assertEqual(result["symbol_library_count"], 1)
            self.assertEqual(Path(result["executable"]), executable.resolve())

    def test_missing_paths_are_reported_without_throwing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = resolve_runtime(
                simetrix_exe=root / "missing.exe",
                symbol_library_dir=root / "missing-symbols",
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "invalid")
            self.assertEqual({item["code"] for item in result["errors"]}, {"missing_executable", "missing_symbol_library"})

    def test_catalog_fingerprint_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable, symbols = self._runtime_paths(root)
            catalog = root / "catalog.lock.yaml"
            catalog.write_text(
                "\n".join(
                    (
                        "schema_version: simplis-automation/v2/catalog",
                        "runtime: {}",
                        "devices:",
                        "  - kind: resistor",
                        "    symbol: {name: res}",
                        "    pins: [{name: p}, {name: n}]",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            result = resolve_runtime(
                {"executable": executable, "symbol_library": symbols, "catalog": {"path": catalog, "fingerprint": load_catalog(catalog)["fingerprint"]}}
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["catalog"]["status"], "matched")

    def test_completion_capability_requires_persisted_doctor_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable, symbols = self._runtime_paths(Path(temporary))
            unproven = resolve_runtime(
                {"executable": executable, "symbol_library": symbols, "completion_capability": "calibrated_none_after_return"}
            )
            self.assertEqual(unproven["completion_capability"], "unavailable")
            proven = resolve_runtime(
                {
                    "executable": executable,
                    "symbol_library": symbols,
                    "completion_capability": "calibrated_none_after_return",
                    "completion_capability_evidence": {"passed": True, "capability": "calibrated_none_after_return"},
                }
            )
            self.assertEqual(proven["completion_capability"], "calibrated_none_after_return")

    def test_none_capability_calibration_requires_progress_tokens_info_and_vector(self) -> None:
        values = {
            "script_started": "true",
            "simulation_started": "true",
            "running": "true",
            "simulation_returned": "true",
            "errors_collected": "true",
            "vector_export_done": "true",
            "completion_token": "true",
            "simplis_exit_code": "0",
            "simulation_has_errors": "0",
            "simulation_errors": "<none>",
            "simulator_status": "None",
        }
        evidence = completion_capability_from_evidence(
            values,
            timeline=[{"simulator_status": "InProgress"}],
            vector_fresh_complete=True,
            simulation_info_matches=True,
        )
        self.assertEqual(evidence["capability"], "calibrated_none_after_return")
        no_progress = completion_capability_from_evidence(values, timeline=[], vector_fresh_complete=True, simulation_info_matches=True)
        self.assertEqual(no_progress["capability"], "unavailable")
        valid_vector_but_missing_info = completion_capability_from_evidence(
            values,
            timeline=[{"simulator_status": "InProgress"}],
            vector_fresh_complete=True,
            simulation_info_matches=False,
        )
        self.assertEqual(valid_vector_but_missing_info["capability"], "unavailable")
        self.assertFalse(valid_vector_but_missing_info["passed"])
        self.assertFalse(valid_vector_but_missing_info["simulation_info_matches"])
        explicit = completion_capability_from_evidence(values | {"simulator_status": "Complete"}, timeline=[], vector_fresh_complete=True, simulation_info_matches=True)
        self.assertEqual(explicit["capability"], "explicit_status")

    def test_completion_probe_uses_the_active_group_and_persists_group_evidence_first(self) -> None:
        script = _completion_probe_script(
            Path("completion-probe.sxsch"),
            Path("completion-probe-status.txt"),
            Path("completion-probe-message.log"),
            Path("completion-probe-vector.txt"),
        )
        self.assertNotIn("SetGroup v2_completion_probe", script)
        self.assertNotIn("/label v2_completion_probe", script)
        self.assertNotIn("RunSIMPLIS", script)
        self.assertIn("NewSchem /simulator SIMPLIS", script)
        self.assertNotIn("NewSchem /newWindow", script)
        self.assertIn("WriteF11Lines", script)
        self.assertIn("Inst /select /loc 2160 0 3 res", script)
        self.assertNotIn("CloseSchem /force\nOpenSchem", script)
        self.assertLess(script.rindex("SaveAs /force"), script.index("simplis_run"))
        self.assertLess(script.index("Echo groups={v2_groups_safe}"), script.index("Let v2_length = Length(Vec('#VOUT'))"))
        self.assertLess(script.index("Echo simulation_returned=true"), script.index("Let v2_length = Length(Vec('#VOUT'))"))
        self.assertIn("Echo simulation_info={v2_info_safe}", script)
        self.assertIn("Echo simulation_info.title={v2_info[8]}", script)
        self.assertIn("Echo group.current.vector.1.length={v2_length}", script)
