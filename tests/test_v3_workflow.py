from __future__ import annotations

import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from device_lookup import find, REFERENCE
from run_storage import cleanup, protect, raw_retention, register_metrics, register_run
from simplis_tools import symbols


class DeviceReferenceTests(unittest.TestCase):
    def test_chinese_cli_output_is_utf8_json(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/simplis_tools.py"),
                                 "find", "--query", "测量支路电流"], check=True,
                                capture_output=True, encoding="utf-8")
        self.assertEqual(json.loads(result.stdout)["matches"][0]["chinese_name"], "串联电流探针")

    def test_branch_current_function_selects_real_current_probe(self):
        result = find("需要测量支路电流")
        self.assertEqual(result["source"], "user-reference")
        part = result["matches"][0]
        self.assertEqual(part["name"], "InlineCurrentProbe")
        self.assertEqual(len(part["pins"]), 2)
        self.assertIn("SIMPLIS_TEMPLATE", {p["name"] for p in part["properties"]})
        template = next(p["value"] for p in part["properties"] if p["name"] == "SIMPLIS_TEMPLATE")
        self.assertIn(".post_process", template)

    def test_all_indexed_pins_match_actual_bundled_symbols(self):
        index = json.loads(REFERENCE.read_text(encoding="utf-8"))
        self.assertEqual(len(index["devices"]), 27)
        for part in index["devices"]:
            actual = symbols(REFERENCE.parent / index["schematic"], name=part["name"])["symbols"][0]
            self.assertEqual(part["pins"], actual["pins"], part["name"])
        self.assertEqual(find("节点电压探针")["matches"][0]["name"], "probev_new")

    def test_updated_ac_and_pop_reference_has_actual_pins_and_model_dependency(self):
        for query, name, pins in (("AC小信号电压源", "ac_source", ["P", "N"]),
                                  ("双节点波特探针", "Bode_Probe2", ["OUT", "IN"]),
                                  ("POP周期触发器", "PERIODIC_OP_V8", ["IN", "OUT"])):
            part = find(query)["matches"][0]
            self.assertEqual(part["name"], name)
            self.assertEqual([p["name"] for p in part["pins"]], pins)
        self.assertEqual(find("POP周期触发器")["matches"][0]["model_dependency"], "support/models/simplis_param.lb")


class RunStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / "run"
        self.run.mkdir()

    def register(self, completed=True, kind="temporary"):
        # 此处检验文件保存规则，电路仿真由独立真实集成验证执行
        generated = {"input.ckt": "input", "solver.log": "log", "run.json": "record", "scratch.txt": "scratch"}
        for name in generated:
            (self.run / name).write_text(name, encoding="utf-8")
        register_run(self.run, generated, kind=kind, completed=completed)

    def test_preview_then_scoped_cleanup_preserves_source_and_manual_files(self):
        self.register()
        source = self.root / "user.sxsch"
        source.write_text("user source")
        manual = self.run / "notes.txt"
        manual.write_text("manual notes")
        before = cleanup(self.run)
        self.assertGreater(before["reclaimable_bytes"], 0)
        self.assertTrue((self.run / "scratch.txt").exists())
        cleanup(self.run, apply=True)
        self.assertFalse((self.run / "scratch.txt").exists())
        self.assertEqual(source.read_text(), "user source")
        self.assertTrue(manual.exists())
        self.assertTrue((self.run / "input.ckt").exists())
        self.assertTrue((self.run / "solver.log").exists())

    def test_changed_generated_file_and_protected_results_survive(self):
        self.register()
        (self.run / "scratch.txt").write_text("manually modified scratch file")
        self.assertEqual(cleanup(self.run)["reclaimable_bytes"], 0)
        protect(self.run, "paper evidence")
        self.assertEqual(cleanup(self.run, apply=True)["reclaimable_bytes"], 0)

    def test_final_kind_is_protected(self):
        self.register(kind="final")
        self.assertEqual(cleanup(self.run, apply=True)["reclaimable_bytes"], 0)

    def test_outside_entry_and_moved_manifest_rejected(self):
        self.register()
        manifest = self.run / "storage.json"
        record = json.loads(manifest.read_text())
        record["files"].insert(0, dict(path="../source.sxsch", role="scratch", bytes=1, mtime_ns=0))
        manifest.write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            cleanup(self.run, apply=True)
        self.assertTrue((self.run / "scratch.txt").exists())
        record["root"] = str(self.root / "another-run")
        manifest.write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            cleanup(self.run)

    def test_metrics_do_not_overwrite_user_file(self):
        self.register()
        path = self.run / "metrics.json"
        path.write_text("user measurement")
        with self.assertRaises(ValueError):
            register_metrics(self.run, {"measurements": []})
        self.assertEqual(path.read_text(), "user measurement")

    def test_one_measurement_never_releases_other_raw_evidence(self):
        # 这里仅测试文件保留策略，文件内容不用于计算电路性能
        for name in ("signal_a.dat", "signal_b.dat"):
            (self.run / name).write_bytes(b"registered data file")
        register_run(self.run, {"signal_a.dat": "raw", "signal_b.dat": "raw"},
                     kind="candidate", completed=True)
        register_metrics(self.run, {"measurements": [{"signal": "a", "time_mean": 1}]})
        self.assertEqual(cleanup(self.run)["reclaimable_bytes"], 0)
        raw_retention(self.run, ["signal_a.dat"], discard=True, reason="Disposable diagnostic signal")
        raw_retention(self.run, ["signal_b.dat"], discard=False, reason="Needed for event timing verification")
        cleanup(self.run, apply=True)
        self.assertFalse((self.run / "signal_a.dat").exists())
        self.assertTrue((self.run / "signal_b.dat").exists())

    def test_raw_selection_rejects_sources_and_modified_files(self):
        self.register()
        with self.assertRaises(ValueError):
            raw_retention(self.run, ["input.ckt"], discard=True, reason="Not a raw file")
        with self.assertRaises(ValueError):
            raw_retention(self.run, ["../user.sxsch"], discard=True, reason="Not owned")

    def test_modified_raw_survives_a_previous_discard_decision(self):
        path = self.run / "signal.dat"
        path.write_bytes(b"registered file")
        register_run(self.run, {path.name: "raw"}, kind="temporary", completed=True)
        raw_retention(self.run, [path.name], discard=True, reason="Disposable test output")
        path.write_bytes(b"manually changed file")
        cleanup(self.run, apply=True)
        self.assertTrue(path.exists())
        with self.assertRaises(ValueError):
            raw_retention(self.run, [path.name], discard=True, reason="Changed after registration")


if __name__ == "__main__":
    unittest.main()
