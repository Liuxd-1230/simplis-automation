from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from run_managed import run_yaml, snapshot_native
from simplis_automation_v2.catalog import load_embedded_symbol
from simplis_automation_v2.compiler import _component_layout


class ManagedSourceTests(unittest.TestCase):
    def test_mixed_real_symbol_heights_stay_in_one_automatic_column(self):
        specs = [("comparator", "SIMPLIS_DIGI1_COMP_Y", {"INP": "REF", "INN": "VOUT", "OUT": "REQ", "RTN": "0"}),
                 ("resistor", "res", {"P": "REQ", "N": "DRIVE"}),
                 ("capacitor", "cap", {"P": "DRIVE", "N": "0"})]
        source, resolved = [], []
        for identity, symbol, pins in specs:
            record = load_embedded_symbol(ROOT / "assets/device-reference/devices.sxsch", symbol)
            source.append(dict(id=identity, pins=pins, group="control"))
            resolved.append(dict(id=identity, symbol_record=record, pins=pins, properties={}, preferred_orientation="N0"))
        placements, _ = _component_layout({"components": source, "layout": {"mode": "auto"}}, resolved)
        ordered = [placements[p[0]] for p in specs]
        self.assertEqual(len({p["x"] for p in ordered}), 1)
        for left, right in zip(ordered, ordered[1:]):
            self.assertGreaterEqual(right["bbox"]["min_y"], left["bbox"]["max_y"] + 360)

    def test_native_snapshot_copies_real_relative_hierarchy_without_editing_sources(self):
        project = ROOT / "examples/native"
        files = ("feedback.sxsch", "controller.sxcmp", "plant.sxcmp")
        originals = {name: (project / name).read_bytes() for name in files}
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "source"
            staged = snapshot_native(project / "feedback.sxsch", project, destination)
            self.assertEqual(staged, destination / "feedback.sxsch")
            for name in files:
                self.assertEqual((destination / name).read_bytes(), originals[name])
                self.assertEqual((project / name).read_bytes(), originals[name])

    def test_yaml_run_rejects_existing_user_directory_before_execution(self):
        source = ROOT / "examples/hysteretic_control_experiment.yaml"
        original = source.read_bytes()
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            user_file = folder / "user_notes.txt"
            user_file.write_text("user file")
            with self.assertRaises(ValueError):
                run_yaml(source, folder)
            self.assertEqual(user_file.read_text(), "user file")
            self.assertEqual(source.read_bytes(), original)
            self.assertFalse((folder / "storage.json").exists())


if __name__ == "__main__":
    unittest.main()
