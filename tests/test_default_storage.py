from pathlib import Path
import json
import tempfile
import unittest

from simplis_automation_v2.catalog import load_catalog, validate_catalog
from simplis_automation_v2.experiment_runner import run_experiment
from simplis_automation_v2.io import load_yaml
from simplis_automation_v2.run_storage import cleanup, load_run


ROOT = Path(__file__).resolve().parents[1]


class DefaultStorageTests(unittest.TestCase):
    def test_default_yaml_preflight_failure_keeps_sources_and_diagnostics(self):
        source = ROOT / "examples/hysteretic_control_experiment.yaml"
        original = source.read_bytes()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "run"
            result = run_experiment(source, root, runtime={"executable": str(Path(temp) / "absent.exe")})
            self.assertFalse(result["ok"])
            record = load_run(root)
            self.assertEqual(record["kind"], "diagnostic")
            metadata = json.loads((root / "run.json").read_text())
            self.assertEqual(metadata["source"]["input_consistency"], "verified_unchanged")
            self.assertEqual((root / "inputs/experiment.yaml").read_bytes(), original)
            self.assertTrue((root / "inputs/circuit.yaml").is_file())
            self.assertTrue((root / "inputs/catalog.yaml").is_file())
            self.assertGreater(record["registered_bytes"], 0)
            self.assertEqual(cleanup(root)["reclaimable_bytes"], 0)
        self.assertEqual(source.read_bytes(), original)

    def test_default_yaml_never_adopts_existing_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manual = root / "manual.txt"
            manual.write_text("user work")
            with self.assertRaises(ValueError):
                run_experiment(ROOT / "examples/hysteretic_control_experiment.yaml", root)
            self.assertEqual(manual.read_text(), "user work")
            self.assertFalse((root / "storage.json").exists())

    def test_invalid_input_exception_leaves_registered_diagnostic(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "invalid.yaml"
            source.write_text("invalid: [")
            with self.assertRaises(Exception):
                run_experiment(source, root / "run")
            self.assertEqual(load_run(root / "run")["kind"], "diagnostic")
            self.assertIn("error", json.loads((root / "run/run.json").read_text()))

    def test_path_only_seed_migration_keeps_original_identity(self):
        records = json.loads((ROOT / "src/simplis_automation_v2/compatibility.json").read_text())
        for suffix, version in (("3", "8.3"), ("4", "8.4")):
            path = ROOT / f"catalog/seed_8_{suffix}.yaml"
            self.assertEqual(load_catalog(path)["fingerprint"], records[version]["historical_catalog_fingerprint"])
            modified = load_yaml(path)
            modified["runtime"]["symbol_libraries"][0]["sha256"] = "changed"
            self.assertNotEqual(validate_catalog(modified)["fingerprint"], records[version]["historical_catalog_fingerprint"])


if __name__ == "__main__":
    unittest.main()
