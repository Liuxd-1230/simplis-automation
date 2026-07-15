from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import yaml

from simplis_automation_v2.catalog import parse_symbol_libraries
from simplis_automation_v2.compiler import _component_layout, _pin_term_orientation, _routing_plan, _script_orientation, _segments_intersect, _term_orientation, _transform_pin, compile_circuit
from simplis_automation_v2.errors import CatalogError, ValidationError
from simplis_automation_v2.schema import load_circuit, resolve_parameters
from simplis_automation_v2.io import sha256_file
from simplis_automation_v2.units import evaluate_expression


def write_yaml(path: Path, value: dict) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


class CoreV2Tests(unittest.TestCase):
    def test_terminal_labels_point_outward_from_left_and_right_pins(self) -> None:
        self.assertEqual(_term_orientation(-120, 0), "N180")
        self.assertEqual(_term_orientation(120, 0), "N0")
        self.assertEqual(_term_orientation(0, -120), "N270")
        self.assertEqual(_term_orientation(0, 120), "N90")

    def test_offset_left_input_uses_body_side_instead_of_instance_origin(self) -> None:
        symbol = {
            "pins": [{"name": "IN", "x": 0, "y": -240}, {"name": "OUT", "x": 1200, "y": 0}],
            "drawing_bbox": {"min_x": 120, "min_y": -480, "max_x": 1080, "max_y": 720},
            "visible_properties": [
                {"name": "NAME", "value": "SIMPLIS One Shot", "autopos": 1, "normal": "Top", "rotated": "Top"}
            ],
        }
        component = {
            "id": "timer",
            "symbol_record": symbol,
            "properties": {},
            "pins": {"IN": "PWM_RAW", "OUT": "TON_OUT"},
            "preferred_orientation": "N0",
        }
        self.assertEqual(_pin_term_orientation(component, "N0", "IN"), "N180")

        circuit = {
            "layout": {"mode": "hybrid"},
            "components": [
                {"id": "timer", "group": "timing", "pins": {"IN": "PWM_RAW", "OUT": "TON_OUT"}, "layout": {"x": 0, "y": 0}}
            ],
        }
        placements, _summary = _component_layout(circuit, [component])
        footprint = placements["timer"]["footprint"]
        terminal = next(item for item in footprint["terminal_annotations"] if item["name"] == "pin:IN")
        self.assertEqual(terminal["placement"], "terminal:N180")
        self.assertLessEqual(terminal["bbox"]["max_x"], footprint["body_bbox"]["min_x"])

    def test_script_orientation_uses_simetrix_zero_to_seven_codes(self) -> None:
        orientations = ("N0", "N90", "N180", "N270", "M0", "M90", "M180", "M270")
        self.assertEqual([_script_orientation(item) for item in orientations], [str(index) for index in range(8)])

    def test_all_native_rotations_and_mirrors_transform_pins(self) -> None:
        expected = {
            "N0": (10, 20),
            "N90": (-20, 10),
            "N180": (-10, -20),
            "N270": (20, -10),
            "M0": (-10, 20),
            "M90": (-20, -10),
            "M180": (10, -20),
            "M270": (20, 10),
        }
        self.assertEqual({name: _transform_pin(10, 20, name) for name in expected}, expected)

    def test_symbol_parser_uses_arcs_polygons_pins_and_visible_labels_for_bbox(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "shape.sxslb").write_text(
                """.Symbol
Attributes name=\"shape\"
Segment x1=-20 y1=-10 x2=40 y2=30
Arc l=-100 t=-80 r=120 b=140 x1=0 y1=0 x2=20 y2=20
Poly x=\"-150 10 90\" y=\"0 200 -30\"
Pin name=\"IN\" order=1 x=-180 y=0
Pin name=\"OUT\" order=2 x=240 y=0
Property name=\"VISIBLE\" value=\"gain\" autopos=0 x=300 y=260
Property name=\"HIDDEN\" value=\"ignore\" autopos=0 x=900 y=900 visible=0
.EndSymbol
""",
                encoding="utf-8",
            )
            bbox = parse_symbol_libraries(root)["shape"]["bbox"]
        self.assertEqual(bbox, {"min_x": -180, "min_y": -80, "max_x": 420, "max_y": 320, "width": 600, "height": 400})

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
Attributes format=1.0 revision=8 name=\"cap\"
Pin name=\"P\" order=1 x=-120 y=0
Pin name=\"N\" order=2 x=120 y=0
Property name=\"VALUE\" value=\"1u\"
.EndSymbol
.Symbol
Attributes format=1.0 revision=8 name=\"ind\"
Pin name=\"P\" order=1 x=-120 y=0
Pin name=\"N\" order=2 x=120 y=0
Property name=\"VALUE\" value=\"1u\"
.EndSymbol
.Symbol
Attributes format=1.0 revision=8 name=\"digital\"
Pin name=\"IN\" order=1 x=-120 y=0
Pin name=\"OUT\" order=2 x=120 y=0
Property name=\"VALUE\" value=\"1\"
.EndSymbol
.Symbol
Attributes format=1.0 revision=8 name=\"display_dynamic\"
Pin name=\"IN\" order=1 x=-120 y=0
Pin name=\"OUT\" order=2 x=120 y=0
.EndSymbol
.Symbol
Attributes format=1.0 revision=8 name=\"vc_switch\"
Pin name=\"P\" order=1 x=0 y=0
Pin name=\"N\" order=2 x=0 y=360
Pin name=\"CP\" order=3 x=-240 y=120
Pin name=\"CN\" order=4 x=-240 y=240
Property name=\"VALUE\" value=\"\"
Property name=\"RON\" value=\"1\"
Property name=\"ROFF\" value=\"1MEG\"
Property name=\"THRESHOLD\" value=\"2\"
Property name=\"HYSTWD\" value=\"0.1\"
Property name=\"IC\" value=\"Open\"
Property name=\"LOGIC\" value=\"POS\"
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
                    "kind": "capacitor",
                    "approval": "approved",
                    "ideality": "ideal",
                    "symbol": {"name": "cap"},
                    "property_encoder": "reactive_value_ic",
                    "pins": [{"name": "P", "index": 1, "domain": "analog"}, {"name": "N", "index": 2, "domain": "analog"}],
                    "properties": {
                        "VALUE": {"dimension": "capacitance", "required": True},
                        "IC": {"dimension": "voltage", "virtual": True},
                    },
                },
                {
                    "kind": "inductor",
                    "approval": "approved",
                    "ideality": "ideal",
                    "symbol": {"name": "ind"},
                    "property_encoder": "reactive_value_ic",
                    "pins": [{"name": "P", "index": 1, "domain": "analog"}, {"name": "N", "index": 2, "domain": "analog"}],
                    "properties": {
                        "VALUE": {"dimension": "inductance", "required": True},
                        "IC": {"dimension": "current", "virtual": True},
                    },
                },
                {
                    "kind": "logic.buffer",
                    "approval": "approved",
                    "ideality": "ideal",
                    "symbol": {"name": "digital"},
                    "pins": [{"name": "IN", "index": 1, "domain": "digital"}, {"name": "OUT", "index": 2, "domain": "digital"}],
                    "properties": {"value": {"native": "VALUE", "dimension": "dimensionless", "required": True}},
                },
                {
                    "kind": "logic.dynamic",
                    "approval": "approved",
                    "ideality": "ideal",
                    "placement_adapter": "display_symbol_binding",
                    "symbol": {"name": "display_dynamic"},
                    "pins": [{"name": "IN", "index": 1, "domain": "analog"}, {"name": "OUT", "index": 2, "domain": "analog"}],
                    "properties": {},
                    "placement_properties": {"SIMPLIS_TEMPLATE": "<ref> <nodelist> DYNAMIC"},
                },
                {
                    "kind": "switch.vc",
                    "approval": "approved",
                    "ideality": "ideal",
                    "symbol": {"name": "vc_switch"},
                    "property_encoder": "simplis_vc_switch_vars",
                    "pins": [
                        {"name": "P", "index": 1, "domain": "analog"},
                        {"name": "N", "index": 2, "domain": "analog"},
                        {"name": "CP", "index": 3, "domain": "analog"},
                        {"name": "CN", "index": 4, "domain": "analog"},
                    ],
                    "properties": {
                        "RON": {"dimension": "resistance", "default": "1mohm", "required": True},
                        "ROFF": {"dimension": "resistance", "default": "100Mohm", "required": True},
                        "THRESHOLD": {"dimension": "voltage", "default": "2.5V"},
                        "HYSTWD": {"dimension": "voltage", "default": "10mV"},
                        "IC": {"value_type": "enum", "enum": ["Open", "Closed"], "default": "Open"},
                        "LOGIC": {"value_type": "enum", "enum": ["POS", "NEG"], "default": "POS"},
                    },
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
        self.assertAlmostEqual(evaluate_expression("2mS", expected_dimension="conductance").value, 0.002)

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
        self.assertIn("NewSchem /simulator SIMPLIS", script)
        self.assertNotIn("NewSchem /newWindow", script)
        self.assertIn("Echo script_started=true", script)
        self.assertLess(script.index("Echo script_started=true"), script.index("NewSchem /simulator SIMPLIS"))
        self.assertLess(script.index("Echo netlist_completed=true"), script.index("Echo completion_token=true"))

    def test_unknown_property_fails_closed(self) -> None:
        root, _, _ = self.make_workspace()
        circuit = self.circuit()
        circuit["components"][0]["properties"]["wrong"] = "1"
        source = root / "circuit.yaml"
        write_yaml(source, circuit)
        with self.assertRaises(ValidationError):
            compile_circuit(source, root / "output")

    def test_display_symbol_binding_always_receives_required_ref(self) -> None:
        root, _, _ = self.make_workspace()
        circuit = self.circuit()
        circuit["components"].append(
            {"id": "dynamic", "ref": "U7", "kind": "logic.dynamic", "pins": {"IN": "VIN", "OUT": "0"}, "group": "logic"}
        )
        circuit["nets"]["VIN"]["endpoints"].append("dynamic.IN")
        circuit["nets"]["0"]["endpoints"].append("dynamic.OUT")
        source = root / "circuit.yaml"
        write_yaml(source, circuit)
        compile_circuit(source, root / "output")
        script = (root / "output" / "create_and_netlist.sxscr").read_text(encoding="utf-8")
        self.assertIn("display_dynamic", script)
        self.assertIn("Prop /hideNew REF U7", script)

    def test_simplis_vc_switch_encoder_writes_the_composite_value_used_by_the_netlist(self) -> None:
        root, _, _ = self.make_workspace()
        circuit = self.circuit()
        circuit["components"].append(
            {
                "id": "switch",
                "ref": "S1",
                "kind": "switch.vc",
                "properties": {
                    "RON": "2mohm",
                    "ROFF": "50Mohm",
                    "THRESHOLD": "3V",
                    "HYSTWD": "20mV",
                    "IC": "Closed",
                    "LOGIC": "NEG",
                },
                "pins": {"P": "VIN", "N": "0", "CP": "VIN", "CN": "0"},
                "group": "power",
            }
        )
        circuit["nets"]["VIN"]["endpoints"].extend(["switch.P", "switch.CP"])
        circuit["nets"]["0"]["endpoints"].extend(["switch.N", "switch.CN"])
        source = root / "circuit.yaml"
        write_yaml(source, circuit)
        compile_circuit(source, root / "output")
        script = (root / "output" / "create_and_netlist.sxscr").read_text(encoding="utf-8")
        self.assertIn(
            'Prop /hideNew VALUE "ROFF=50000000 RON=0.002 THRESHOLD=3 HYSTWD=0.02 IC=\'CLOSE\' LOGIC=\'NEG\'"',
            script,
        )

    def test_reactive_value_encoder_combines_value_and_optional_initial_condition(self) -> None:
        root, _, _ = self.make_workspace()
        circuit = self.circuit()
        circuit["components"].extend(
            [
                {
                    "id": "cout",
                    "ref": "COUT",
                    "kind": "capacitor",
                    "properties": {"VALUE": "100uF", "IC": "1.2V"},
                    "pins": {"P": "VIN", "N": "0"},
                    "group": "output",
                },
                {
                    "id": "lout",
                    "ref": "L1",
                    "kind": "inductor",
                    "properties": {"VALUE": "1uH", "IC": "4A"},
                    "pins": {"P": "VIN", "N": "0"},
                    "group": "power",
                },
                {
                    "id": "plain_cap",
                    "ref": "C1",
                    "kind": "capacitor",
                    "properties": {"VALUE": "220pF"},
                    "pins": {"P": "VIN", "N": "0"},
                    "group": "output",
                },
            ]
        )
        circuit["nets"]["VIN"]["endpoints"].extend(["cout.P", "lout.P", "plain_cap.P"])
        circuit["nets"]["0"]["endpoints"].extend(["cout.N", "lout.N", "plain_cap.N"])
        source = root / "reactive-ic.yaml"
        write_yaml(source, circuit)
        manifest = compile_circuit(source, root / "output")
        script = (root / "output" / "create_and_netlist.sxscr").read_text(encoding="utf-8")
        self.assertIn('Prop /hideNew VALUE "0.0001 IC=1.2"', script)
        self.assertIn('Prop /hideNew VALUE "1e-06 IC=4"', script)
        self.assertIn("Prop /hideNew VALUE 2.2e-10", script)
        by_id = {item["id"]: item for item in manifest["expected"]["components"]}
        self.assertEqual(by_id["cout"]["properties"], {"VALUE": "0.0001 IC=1.2"})
        self.assertEqual(by_id["lout"]["properties"], {"VALUE": "1e-06 IC=4"})
        self.assertEqual(by_id["plain_cap"]["properties"], {"VALUE": "2.2e-10"})

    def test_explicit_unconnected_pin_is_preserved_without_a_fake_net(self) -> None:
        root, _, _ = self.make_workspace()
        circuit = self.circuit()
        circuit["components"].append(
            {
                "id": "dynamic",
                "ref": "U7",
                "kind": "logic.dynamic",
                "pins": {"IN": "VIN"},
                "unconnected_pins": ["OUT"],
                "group": "logic",
            }
        )
        circuit["nets"]["VIN"]["endpoints"].append("dynamic.IN")
        source = root / "circuit.yaml"
        write_yaml(source, circuit)
        manifest = compile_circuit(source, root / "output")
        dynamic = next(item for item in manifest["expected"]["components"] if item["id"] == "dynamic")
        self.assertEqual(dynamic["unconnected_pins"], ["OUT"])
        self.assertNotIn("OUT", dynamic["pins"])
        self.assertFalse(any("NC" in name for name in manifest["expected"]["nets"]))

    def test_catalog_proof_compile_is_explicit_and_never_claims_ideal(self) -> None:
        root, _, catalog_path = self.make_workspace()
        catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
        dynamic = next(item for item in catalog["devices"] if item["kind"] == "logic.dynamic")
        dynamic["approval"] = "pending"
        dynamic["ideality"] = "pending_review"
        write_yaml(catalog_path, catalog)
        circuit = self.circuit()
        circuit["components"].append(
            {
                "id": "dynamic",
                "ref": "U7",
                "kind": "logic.dynamic",
                "pins": {"IN": "VIN"},
                "unconnected_pins": ["OUT"],
                "group": "logic",
            }
        )
        circuit["nets"]["VIN"]["endpoints"].append("dynamic.IN")
        source = root / "circuit.yaml"
        write_yaml(source, circuit)
        with self.assertRaises(CatalogError):
            compile_circuit(source, root / "normal")
        proof = compile_circuit(source, root / "proof", catalog_proof_kinds=["logic.dynamic"])
        self.assertTrue(proof["classification"]["catalog_proof_only"])
        self.assertEqual(proof["classification"]["pending_devices"], ["logic.dynamic"])
        self.assertFalse(proof["classification"]["fully_ideal"])
        with self.assertRaises(CatalogError):
            compile_circuit(source, root / "wrong-proof", catalog_proof_kinds=["source.dc"])

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

    def test_layout_modes_move_only_when_the_mode_allows_it(self) -> None:
        root, _, _ = self.make_workspace()

        fixed = self.circuit()
        fixed["layout"] = {"mode": "hybrid"}
        fixed["components"][0]["layout"] = {"x": 0, "y": 0}
        fixed["components"][1]["layout"] = {"x": 0, "y": 0}
        fixed_path = root / "fixed.yaml"
        write_yaml(fixed_path, fixed)
        with self.assertRaises(ValidationError):
            compile_circuit(fixed_path, root / "fixed-out")

        automatic = self.circuit()
        automatic["layout"] = {"mode": "auto"}
        automatic["components"][0]["layout"] = {"x": 0, "y": 0}
        automatic["components"][1]["layout"] = {"x": 0, "y": 0}
        auto_path = root / "auto.yaml"
        write_yaml(auto_path, automatic)
        auto_manifest = compile_circuit(auto_path, root / "auto-out")
        placed = {item["id"]: item["layout"] for item in auto_manifest["expected"]["components"]}
        self.assertTrue(any(item["moved"] for item in placed.values()))
        self.assertTrue(auto_manifest["expected"]["layout"]["collision_check"]["passed"])

        manual = self.circuit()
        manual["layout"] = {"mode": "manual"}
        manual["components"][0]["layout"] = {"x": 0, "y": 0, "orientation": "N0"}
        manual["components"][1]["layout"] = {"x": 3600, "y": 0, "orientation": "N0"}
        manual_path = root / "manual.yaml"
        write_yaml(manual_path, manual)
        manual_manifest = compile_circuit(manual_path, root / "manual-out")
        for item in manual_manifest["expected"]["components"]:
            self.assertFalse(item["layout"]["moved"])
            self.assertEqual(item["layout"]["requested_position"], item["layout"]["final_position"])

    def test_block_footprints_pack_by_cell_and_reject_duplicate_cells(self) -> None:
        symbol = {
            "pins": [{"name": "P", "x": 0, "y": 0}, {"name": "N", "x": 360, "y": 0}],
            "bbox": {"min_x": 0, "min_y": -60, "max_x": 360, "max_y": 60},
        }
        resolved = [
            {"id": "a", "symbol_record": symbol, "pins": {"P": "A", "N": "B"}, "preferred_orientation": "N0"},
            {"id": "b", "symbol_record": symbol, "pins": {"P": "B", "N": "C"}, "preferred_orientation": "N0"},
        ]
        circuit = {
            "layout": {"mode": "hybrid", "clearance": {"horizontal": 480, "vertical": 360}},
            "components": [
                {"id": "a", "group": "control", "block": "blk", "pins": {"P": "A", "N": "B"}, "layout": {"row": 0, "col": 0}},
                {"id": "b", "group": "control", "block": "blk", "pins": {"P": "B", "N": "C"}, "layout": {"row": 0, "col": 1}},
            ],
            "_block_manifest": {"blk": {"leaf_components": ["a", "b"], "layout": {"x": 0, "y": 0}}},
        }
        placements, summary = _component_layout(circuit, resolved)
        self.assertTrue(summary["collision_check"]["passed"])
        self.assertGreaterEqual(placements["b"]["bbox"]["min_x"] - placements["a"]["bbox"]["max_x"], 480)
        self.assertEqual(placements["a"]["placement_reason"], "block_footprint_pack")

        circuit["components"][1]["layout"] = {"row": 0, "col": 0}
        with self.assertRaises(ValidationError):
            _component_layout(circuit, resolved)

    def test_block_packing_reserves_actual_autopositioned_reference_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "labels.sxslb").write_text(
                """.Symbol
Attributes name=\"labeled\"
Segment x1=0 y1=0 x2=360 y2=0
Pin name=\"P\" order=1 x=0 y=0
Pin name=\"N\" order=2 x=360 y=0
Property name=\"REF\" value=\"R?\" autopos=1 normal=Right rotated=Bottom font=Default order=1
.EndSymbol
""",
                encoding="utf-8",
            )
            symbol = parse_symbol_libraries(root)["labeled"]
        resolved = [
            {"id": "short", "symbol_record": symbol, "properties": {"REF": "R1"}, "pins": {"P": "A", "N": "B"}, "preferred_orientation": "N0"},
            {
                "id": "long",
                "symbol_record": symbol,
                "properties": {"REF": "READABLE_LONG_REFERENCE"},
                "pins": {"P": "B", "N": "C"},
                "preferred_orientation": "N0",
            },
        ]
        circuit = {
            "layout": {"mode": "hybrid", "clearance": {"horizontal": 480, "vertical": 360}},
            "components": [
                {"id": "short", "group": "control", "block": "blk", "pins": {"P": "A", "N": "B"}, "layout": {"row": 0, "col": 0}},
                {"id": "long", "group": "control", "block": "blk", "pins": {"P": "B", "N": "C"}, "layout": {"row": 0, "col": 1}},
            ],
            "_block_manifest": {"blk": {"leaf_components": ["short", "long"], "layout": {"x": 0, "y": 0}}},
        }
        placements, _summary = _component_layout(circuit, resolved)
        self.assertGreater(placements["long"]["footprint"]["width"], placements["short"]["footprint"]["width"])
        self.assertEqual(
            placements["long"]["footprint"]["visible_annotations"][0]["text"],
            "READABLE_LONG_REFERENCE",
        )
        self.assertGreaterEqual(placements["long"]["bbox"]["min_x"] - placements["short"]["bbox"]["max_x"], 480)

    def test_component_footprint_reserves_outward_terminal_and_ground_geometry(self) -> None:
        symbol = {
            "pins": [{"name": "IN", "x": -120, "y": 0}, {"name": "OUT", "x": 120, "y": 0}, {"name": "RTN", "x": 0, "y": 120}],
            "bbox": {"min_x": -60, "min_y": -60, "max_x": 60, "max_y": 60},
        }
        resolved = [
            {
                "id": "amp",
                "symbol_record": symbol,
                "pins": {"IN": "LONG_INPUT_NET", "OUT": "LONG_OUTPUT_NET", "RTN": "0"},
                "preferred_orientation": "N0",
            }
        ]
        circuit = {
            "layout": {"mode": "hybrid"},
            "components": [
                {
                    "id": "amp",
                    "group": "control",
                    "pins": {"IN": "LONG_INPUT_NET", "OUT": "LONG_OUTPUT_NET", "RTN": "0"},
                    "layout": {"x": 0, "y": 0},
                }
            ],
        }
        placements, _summary = _component_layout(circuit, resolved)
        footprint = placements["amp"]["footprint"]
        by_name = {item["name"]: item for item in footprint["terminal_annotations"]}
        self.assertEqual(by_name["pin:IN"]["placement"], "terminal:N180")
        self.assertEqual(by_name["pin:OUT"]["placement"], "terminal:N0")
        self.assertEqual(by_name["pin:RTN"]["placement"], "ground:N0")
        self.assertLess(by_name["pin:IN"]["bbox"]["min_x"], footprint["body_bbox"]["min_x"])
        self.assertGreater(by_name["pin:OUT"]["bbox"]["max_x"], footprint["body_bbox"]["max_x"])
        self.assertGreater(by_name["pin:RTN"]["bbox"]["max_y"], footprint["body_bbox"]["max_y"])

    def test_automatic_blocks_keep_declaration_order_instead_of_alphabetical_order(self) -> None:
        symbol = {
            "pins": [{"name": "P", "x": 0, "y": 0}, {"name": "N", "x": 360, "y": 0}],
            "bbox": {"min_x": 0, "min_y": -60, "max_x": 360, "max_y": 60},
        }
        resolved = [
            {"id": "first_leaf", "symbol_record": symbol, "pins": {"P": "A", "N": "B"}, "preferred_orientation": "N0"},
            {"id": "second_leaf", "symbol_record": symbol, "pins": {"P": "B", "N": "C"}, "preferred_orientation": "N0"},
        ]
        circuit = {
            "layout": {"mode": "hybrid", "clearance": {"horizontal": 480, "vertical": 360}},
            "components": [
                {"id": "first_leaf", "group": "control", "block": "z_first", "pins": {"P": "A", "N": "B"}},
                {"id": "second_leaf", "group": "control", "block": "a_second", "pins": {"P": "B", "N": "C"}},
            ],
            "_block_manifest": {
                "z_first": {"leaf_components": ["first_leaf"], "layout": {"x": 0, "y": 0}},
                "a_second": {"leaf_components": ["second_leaf"], "layout": {"x": 0, "y": 0}},
            },
        }
        placements, _summary = _component_layout(circuit, resolved)
        self.assertLess(placements["first_leaf"]["x"], placements["second_leaf"]["x"])

    def test_nearby_noncollinear_pins_inside_one_block_use_short_manhattan_routing(self) -> None:
        symbol = {"pins": [{"name": "P", "x": 0, "y": 0}, {"name": "N", "x": 360, "y": 0}]}
        components = [
            {"id": "left", "symbol_record": symbol, "pins": {"N": "__blk__internal"}},
            {"id": "right", "symbol_record": symbol, "pins": {"P": "__blk__internal"}},
        ]
        placements = {
            "left": {"x": 0, "y": 0, "orientation": "N0", "group": "control", "block": "blk"},
            "right": {"x": 1440, "y": 360, "orientation": "N0", "group": "control", "block": "blk"},
        }
        routing = _routing_plan(components, placements)
        self.assertIn("__blk__internal", routing["local_wires"])
        self.assertNotIn("__blk__internal", routing["labeled"])

    def test_manhattan_router_does_not_join_two_diagonal_block_nets(self) -> None:
        symbol = {"pins": [{"name": "P", "x": 0, "y": 0}]}
        components = [
            {"id": "a_start", "symbol_record": symbol, "pins": {"P": "NET_A"}},
            {"id": "a_end", "symbol_record": symbol, "pins": {"P": "NET_A"}},
            {"id": "b_start", "symbol_record": symbol, "pins": {"P": "NET_B"}},
            {"id": "b_end", "symbol_record": symbol, "pins": {"P": "NET_B"}},
        ]
        placements = {
            "a_start": {"x": 0, "y": 0, "orientation": "N0", "group": "timing", "block": "ton"},
            "a_end": {"x": 600, "y": 2160, "orientation": "N0", "group": "timing", "block": "ton"},
            "b_start": {"x": 0, "y": 2160, "orientation": "N0", "group": "timing", "block": "ton"},
            "b_end": {"x": 600, "y": 2400, "orientation": "N0", "group": "timing", "block": "ton"},
        }
        routing = _routing_plan(components, placements)
        self.assertEqual(set(routing["local_wires"]), {"NET_A", "NET_B"})
        a_segments = routing["local_wire_segments"]["NET_A"]
        b_segments = routing["local_wire_segments"]["NET_B"]
        self.assertFalse(any(_segments_intersect(a, b) for a in a_segments for b in b_segments))

    def test_embedded_clone_compiles_through_a_minimal_host_schematic(self) -> None:
        root, _, catalog_path = self.make_workspace()
        source = root / "official.sxcmp"
        source.write_text(
            """.Symbol
Attributes name=\"embedded_two\"
Segment x1=0 y1=0 x2=360 y2=0
Pin name=\"P\" order=1 x=0 y=0
Pin name=\"N\" order=2 x=360 y=0
Property name=\"REF\" value=\"X?\"
Property name=\"SIMPLIS_TEMPLATE\" value=\"<ref> <nodelist> EMBEDDED\"
.EndSymbol
""",
            encoding="utf-8",
        )
        catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
        catalog["devices"].append(
            {
                "kind": "embedded.two",
                "approval": "approved",
                "ideality": "ideal",
                "placement_adapter": "embedded_clone",
                "symbol": {"name": "embedded_two", "source": str(source), "source_sha256": sha256_file(source)},
                "pins": [{"name": "P", "index": 1, "domain": "analog"}, {"name": "N", "index": 2, "domain": "analog"}],
                "properties": {},
            }
        )
        write_yaml(catalog_path, catalog)
        circuit = self.circuit()
        circuit["components"].append({"id": "embedded", "ref": "X1", "kind": "embedded.two", "pins": {"P": "VIN", "N": "0"}, "group": "control"})
        circuit["nets"]["VIN"]["endpoints"].append("embedded.P")
        circuit["nets"]["0"]["endpoints"].append("embedded.N")
        circuit_path = root / "embedded.yaml"
        write_yaml(circuit_path, circuit)
        manifest = compile_circuit(circuit_path, root / "embedded-out")
        host = Path(manifest["artifacts"]["embedded_symbol_host"])
        self.assertTrue(host.is_file())
        self.assertIn("embedded_two", host.read_text(encoding="utf-8"))
        script = Path(manifest["artifacts"]["script"]).read_text(encoding="utf-8")
        self.assertIn("OpenSchem /cd", script)
        self.assertNotIn("NewSchem /newWindow", script)
        self.assertEqual(manifest["expected"]["embedded_symbols"][0]["name"], "embedded_two")
