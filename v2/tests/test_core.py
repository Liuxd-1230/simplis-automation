from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import yaml

from simplis_automation_v2.compiler import compile_circuit
from simplis_automation_v2.errors import ValidationError
from simplis_automation_v2.schema import load_circuit, resolve_parameters
from simplis_automation_v2.units import evaluate_expression


def write_yaml(path: Path, value: dict) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


class CoreV2Tests(unittest.TestCase):
    def make_workspace(self) -> tuple[Path, Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        libraries = root / "libraries"
        libraries.mkdir()
        (libraries / "tiny.sxslb").write_text(
            """.Symbol
Attributes format=1.0 revision=8 name=\"dc_source\"
Pin name=\"P\" order=1 x=120 y=0
Pin name=\"N\" order=2 x=-120 y=0
Property name=\"VALUE\" value=\"0\"
.EndSymbol
.Symbol
Attributes format=1.0 revision=8 name=\"res\"
Pin name=\"P\" order=1 x=-120 y=0
Pin name=\"N\" order=2 x=120 y=0
Property name=\"VALUE\" value=\"1\"
.EndSymbol
.Symbol
Attributes format=1.0 revision=8 name=\"digital\"
Pin name=\"IN\" order=1 x=-120 y=0
Pin name=\"OUT\" order=2 x=120 y=0
Property name=\"VALUE\" value=\"1\"
.EndSymbol
""",
            encoding="utf-8",
        )
        catalog = {
            "schema_version": "simplis-automation/v2/catalog",
            "runtime": {"symbol_library_dir": str(libraries)},
            "devices": [
                {
                    "kind": "source.dc",
                    "approval": "approved",
                    "ideality": "ideal",
                    "symbol": {"name": "dc_source"},
                    "pins": [{"name": "P", "index": 1, "domain": "analog"}, {"name": "N", "index": 2, "domain": "analog"}],
                    "properties": {"value": {"native": "VALUE", "dimension": "voltage", "required": True}},
                },
                {
                    "kind": "passive.resistor",
                    "approval": "approved",
                    "ideality": "ideal",
                    "symbol": {"name": "res"},
                    "pins": [{"name": "P", "index": 1, "domain": "analog"}, {"name": "N", "index": 2, "domain": "analog"}],
                    "properties": {"value": {"native": "VALUE", "dimension": "resistance", "required": True, "min": "1 mOhm"}},
                },
                {
                    "kind": "logic.buffer",
                    "approval": "approved",
                    "ideality": "ideal",
                    "symbol": {"name": "digital"},
                    "pins": [{"name": "IN", "index": 1, "domain": "digital"}, {"name": "OUT", "index": 2, "domain": "digital"}],
                    "properties": {"value": {"native": "VALUE", "dimension": "dimensionless", "required": True}},
                },
            ],
        }
        catalog_path = root / "catalog.yaml"
        write_yaml(catalog_path, catalog)
        return root, libraries, catalog_path

    def circuit(self) -> dict:
        return {
            "schema_version": "simplis-automation/v2",
            "catalog_lock": {"path": "catalog.yaml"},
            "design": {"name": "rc"},
            "parameters": {
                "vin": {"dimension": "voltage", "default": "12 V", "min": "6 V", "max": "18 V"},
                "rload": {"dimension": "resistance", "default": "1 kOhm", "min": "1 Ohm", "max": "100 kOhm"},
            },
            "components": [
                {"id": "vin", "ref": "VIN", "kind": "source.dc", "properties": {"value": "${vin}"}, "pins": {"P": "VIN", "N": "0"}, "group": "source"},
                {"id": "load", "ref": "RLOAD", "kind": "passive.resistor", "properties": {"value": "${rload}"}, "pins": {"P": "VIN", "N": "0"}, "group": "load"},
            ],
            "nets": {"VIN": {"endpoints": ["vin.P", "load.P"]}, "0": {"endpoints": ["vin.N", "load.N"]}},
        }

    def test_safe_units_and_parameter_dependencies(self) -> None:
        expression = evaluate_expression("2 * ${period}", {"period": evaluate_expression("10 ns")}, "time")
        self.assertAlmostEqual(expression.value, 20e-9)
        root, _, _ = self.make_workspace()
        source = root / "circuit.yaml"
        write_yaml(source, self.circuit())
        resolved = resolve_parameters(load_circuit(source), {"vin": "10 V"})
        self.assertAlmostEqual(resolved["vin"].value, 10.0)
        self.assertAlmostEqual(resolved["rload"].value, 1000.0)

    def test_compile_writes_deterministic_manifest_and_script(self) -> None:
        root, _, _ = self.make_workspace()
        source = root / "circuit.yaml"
        write_yaml(source, self.circuit())
        manifest = compile_circuit(source, root / "output")
        self.assertTrue(manifest["classification"]["static_valid"])
        self.assertTrue(manifest["classification"]["fully_ideal"])
        self.assertTrue((root / "output" / "create_and_netlist.sxscr").is_file())
        stored = json.loads((root / "output" / "build-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["expected"]["nets"]["VIN"], ["vin.P", "load.P"])
        script = (root / "output" / "create_and_netlist.sxscr").read_text(encoding="utf-8")
        self.assertIn("Netlist /simplis", script)
        self.assertIn("Prop /hideNew VALUE 12", script)

    def test_unknown_property_fails_closed(self) -> None:
        root, _, _ = self.make_workspace()
        circuit = self.circuit()
        circuit["components"][0]["properties"]["wrong"] = "1"
        source = root / "circuit.yaml"
        write_yaml(source, circuit)
        with self.assertRaises(ValidationError):
            compile_circuit(source, root / "output")

    def test_analog_digital_crossing_fails_closed(self) -> None:
        root, _, _ = self.make_workspace()
        circuit = self.circuit()
        circuit["components"].append(
            {"id": "logic", "ref": "U1", "kind": "logic.buffer", "properties": {"value": "1"}, "pins": {"IN": "VIN", "OUT": "VIN"}, "group": "logic"}
        )
        circuit["nets"]["VIN"]["endpoints"].extend(["logic.IN", "logic.OUT"])
        source = root / "circuit.yaml"
        write_yaml(source, circuit)
        with self.assertRaises(ValidationError):
            compile_circuit(source, root / "output")
