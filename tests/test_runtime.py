from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


V2_ROOT = Path(__file__).resolve().parents[1]
if str(V2_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(V2_ROOT / "src"))

from simplis_automation_v2.catalog import load_catalog
from simplis_automation_v2.runtime import resolve_runtime


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
            self.assertEqual(result["completion_contract"], "actual_task_evidence")
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

    def test_83_is_supported_and_84_is_preferred_in_discovery_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable84, symbols84 = self._runtime_paths(root)
            executable83 = root / "SIMetrix830" / "bin64" / "SIMetrix.exe"
            executable83.parent.mkdir(parents=True)
            executable83.write_text("stub", encoding="utf-8")
            (executable83.parent / "SxCommand.exe").write_text("stub", encoding="utf-8")
            with patch("simplis_automation_v2.runtime._COMMON_EXECUTABLES", (executable84, executable83)):
                preferred = resolve_runtime(symbol_library_dir=symbols84)
            self.assertEqual(preferred["version"], "8.4")
            self.assertTrue(preferred["preferred_version"])
            result83 = resolve_runtime(simetrix_exe=executable83, symbol_library_dir=symbols84)
            self.assertTrue(result83["ready"])
            self.assertEqual(result83["version"], "8.3")
            self.assertFalse(result83["preferred_version"])
