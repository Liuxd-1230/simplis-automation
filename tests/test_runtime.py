from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simplis_automation_v2.catalog import catalog_runtime_evidence
from simplis_automation_v2.runtime import installed_source_path, resolve_runtime, startup_command


class RuntimeTests(unittest.TestCase):
    def test_startup_path_with_spaces_uses_documented_execute_function(self):
        path = Path("custom 中文") / "space folder" / "job.sxscr"
        self.assertEqual(startup_command("SIMetrix.exe", path), ["SIMetrix.exe", "/s", f"Let sa_startup_result = Execute('{path.as_posix()}')"])
        self.assertEqual(startup_command("SIMetrix.exe", Path("job.sxscr")), ["SIMetrix.exe", "/s", "job.sxscr"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.versions = {}
        # 模拟安装发现及 Windows PE 元数据，不模拟电路仿真结果
        for name, value in (("_registry_locations", []), ("_common_locations", [])):
            item = patch("simplis_automation_v2.runtime." + name, return_value=value)
            item.start()
            self.addCleanup(item.stop)
        item = patch("simplis_automation_v2.runtime.shutil.which", return_value=None)
        item.start()
        self.addCleanup(item.stop)
        item = patch.dict("os.environ", {}, clear=True)
        item.start()
        self.addCleanup(item.stop)
        item = patch("simplis_automation_v2.runtime._file_version", side_effect=lambda p: self.versions.get(str(p)))
        item.start()
        self.addCleanup(item.stop)

    def installation(self, name, version="8.40.0.1"):
        root = self.root / name
        for relative in ("bin64/SIMetrix.exe", "bin64/SxCommand.exe", "bin/simplis.exe", "support/symbollibs/basic.sxslb"):
            file = root / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b"runtime fixture")
            if file.suffix == ".exe" and version:
                self.versions[str(file.resolve())] = version
        return root

    def test_custom_locations_and_metadata_override_misleading_directory(self):
        for name in ("C-drive/Program Files/SIMetrix830", "D-drive/custom", "E-drive/用户安装/电路 软件"):
            with self.subTest(name=name):
                root = self.installation(name)
                result = resolve_runtime(install=root)
                self.assertTrue(result["watchdog_ready"], result["errors"])
                self.assertEqual(result["version"], "8.4")
                self.assertEqual(result["version_status"], "confirmed")
                for key in ("executable", "simplis_exe", "sxcommand_exe", "symbol_library", "models", "docs", "examples"):
                    self.assertTrue(Path(result[key]).is_relative_to(root))

    def test_directory_or_config_version_does_not_confirm_version(self):
        root = self.installation("SIMetrix840", version=None)
        result = resolve_runtime(install=root, version="8.40")
        self.assertFalse(result["ready"])
        self.assertEqual(result["version_status"], "inferred")
        self.assertIsNone(result["version"])

    @unittest.skipUnless(os.name == "nt", "Windows drive-letter paths")
    def test_simulated_c_d_e_absolute_installation_roots(self):
        for drive in ("C", "D", "E"):
            root = Path(f"{drive}:/自定义目录/Simulation Tools")
            files = {root / "bin64/SIMetrix.exe", root / "bin64/SxCommand.exe", root / "bin/simplis.exe"}
            for file in files:
                self.versions[str(file)] = "8.40.0.1"
            symbols = root / "support/symbollibs"
            with patch.object(Path, "is_file", lambda p: p in files), patch.object(Path, "is_dir", lambda p: p == symbols), \
                 patch.object(Path, "glob", lambda p, pattern: iter([symbols / "basic.sxslb"]) if p == symbols else iter([])):
                result = resolve_runtime(install=root)
            self.assertTrue(result["ready"], result["errors"])
            self.assertEqual(result["install_root"], str(root))
            self.assertEqual(Path(result["models"]), root / "support/models")

    def test_multiversion_selection_and_no_silent_switch(self):
        older = self.installation("old", "8.30.0.1")
        newer = self.installation("new")
        with patch("simplis_automation_v2.runtime._registry_locations", return_value=[older, newer]):
            self.assertEqual(resolve_runtime()["version"], "8.4")
            self.assertEqual(resolve_runtime(version="8.30")["install_root"], str(older))
            mismatch = resolve_runtime(install=older, version="8.40")
            self.assertFalse(mismatch["ready"])
            self.assertEqual(mismatch["install_root"], str(older))
            unavailable = resolve_runtime(version="9.10")
            self.assertFalse(unavailable["ready"])

    def test_verified_coverage_precedes_newer_version(self):
        older = self.installation("old", "8.30.0.1")
        newer = self.installation("new")
        file = older / "support/symbollibs/basic.sxslb"
        records = {"8.3": dict(libraries={"support/symbollibs/basic.sxslb": hashlib.sha256(file.read_bytes()).hexdigest()}, verified_features=["test_feature"])}
        with patch("simplis_automation_v2.runtime._registry_locations", return_value=[newer, older]), patch("simplis_automation_v2.runtime.json.loads", return_value=records):
            self.assertEqual(resolve_runtime()["version"], "8.3")
            file.write_bytes(b"changed model")
            self.assertEqual(resolve_runtime()["version"], "8.4")

    def test_task_catalog_constrains_automatic_selection_without_overriding_user(self):
        older = self.installation("older", "8.30.0.1")
        newer = self.installation("newer")
        with patch("simplis_automation_v2.runtime._registry_locations", return_value=[older, newer]):
            automatic = resolve_runtime()
            self.assertEqual(resolve_runtime(automatic, target_version="8.30")["install_root"], str(older))
            explicit = resolve_runtime(install=newer)
            self.assertEqual(resolve_runtime(explicit, target_version="8.30")["install_root"], str(newer))

    def test_priority_explicit_project_environment(self):
        env = self.installation("env", "8.30.0.1")
        project = self.installation("project")
        explicit = self.installation("explicit", "8.30.0.1")
        (self.root / "simplis-runtime.json").write_text(json.dumps({"install": "project"}))
        with patch.dict("os.environ", {"SIMPLIS_INSTALL": str(env)}):
            self.assertEqual(resolve_runtime(project_dir=self.root)["install_root"], str(project))
            self.assertEqual(resolve_runtime(project_dir=self.root, install=explicit)["install_root"], str(explicit))
            self.assertEqual(resolve_runtime(project_dir=env)["install_root"], str(project))
        (self.root / "simplis-runtime.json").unlink()
        with patch.dict("os.environ", {"SIMETRIX_EXE": str(env / "bin64/SIMetrix.exe")}):
            self.assertEqual(resolve_runtime()["install_root"], str(env))

    def test_conflicting_program_versions_and_cross_install_symbols_rejected(self):
        root = self.installation("first")
        other = self.installation("second", "8.30.0.1")
        result = resolve_runtime(install=root, symbol_library_dir=other / "support/symbollibs")
        self.assertIn("mixed_installation_resources", {e["code"] for e in result["errors"]})
        self.versions[str(root / "bin/simplis.exe")] = "8.30.0.1"
        self.assertFalse(resolve_runtime(install=root)["ready"])

    def test_missing_explicit_installation_preserved(self):
        with patch("simplis_automation_v2.runtime._registry_locations", return_value=[self.installation("valid")]):
            result = resolve_runtime(install=self.root / "missing")
        self.assertFalse(result["ready"])
        self.assertEqual(result["install_root"], str(self.root / "missing"))

    def test_old_library_paths_relocate_and_content_changes_invalidate_evidence(self):
        root = self.installation("relocated")
        symbols = root / "support/symbollibs"
        file = symbols / "basic.sxslb"
        models = root / "support/models/custom.lb"
        models.parent.mkdir()
        models.write_bytes(b"model")
        catalog = dict(runtime=dict(symbol_libraries=[dict(path=r"D:\SIMetrix830\support\symbollibs\basic.sxslb", sha256=hashlib.sha256(file.read_bytes()).hexdigest())],
                                    model_libraries=[dict(path="support/models/custom.lb", sha256=hashlib.sha256(models.read_bytes()).hexdigest())]))
        self.assertTrue(catalog_runtime_evidence(catalog, symbols)["ready"])
        self.assertEqual(installed_source_path(r"D:\SIMetrix830\support\models\custom.lb", symbols), models)
        models.write_bytes(b"different model")
        self.assertFalse(catalog_runtime_evidence(catalog, symbols)["ready"])

    def test_compile_resolves_a_missing_historical_catalog_directory(self):
        from simplis_automation_v2.compiler import _runtime_symbol_dir
        relocated = self.installation("relocated-install")
        catalog = dict(runtime=dict(version="8.4", symbol_library_dir=str(self.root / "obsolete")))
        with patch("simplis_automation_v2.runtime._registry_locations", return_value=[relocated]):
            self.assertEqual(_runtime_symbol_dir(catalog), relocated / "support/symbollibs")


if __name__ == "__main__":
    unittest.main()
