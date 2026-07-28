from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


V2_ROOT = Path(__file__).resolve().parents[1]
if str(V2_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(V2_ROOT / "src"))

from simplis_automation_v2.errors import ImportBlockedError
from simplis_automation_v2.importer import import_schematic, promote_parameter
from simplis_automation_v2.io import load_yaml
from simplis_automation_v2.schema import validate_circuit


CATALOG = {
    "catalog_lock": {"path": "catalog.lock.yaml", "fingerprint": "approved-catalog"},
    "devices": [
        {
            "kind": "resistor",
            "approval": "approved",
            "symbol": {"library": "Passives", "name": "res"},
            "pins": [{"name": "P", "index": 1, "domain": "analog"}, {"name": "N", "index": 2, "domain": "analog"}],
            "properties": {
                "VALUE": {"dimension": "resistance", "minimum": "1", "maximum": "1Meg"},
            },
            "ideality": "fully_ideal",
        },
        {
            "kind": "terminal",
            "approval": "approved",
            "symbol": {"library": "Drawing", "name": "term"},
            "pins": [{"name": "P", "index": 1, "domain": "analog"}],
            "properties": {"VALUE": {"dimension": "net_name"}},
            "ideality": "fully_ideal",
        },
        {
            "kind": "opaque_module",
            "approval": "approved",
            "symbol": {"name": "vendor_controller"},
            "path": "Blocks/vendor_controller.sxcmp",
            "pins": [],
            "properties": {},
            "ideality": "boundary_only",
        },
    ],
}


SIMPLE_SXSCH = """
.Instance
Attributes type=symbol name="res" selected=0 protected=0 x=120 y=240 orient=N90
Property name="REF" value="R1" autopos=1 normal=Right rotated=Bottom font=Default order=-1
Property name="VALUE" value="10k" autopos=1 normal=Right rotated=Top font=Default order=-1
.EndInstance
.Instance
Attributes type=symbol name="term" selected=0 protected=0 x=360 y=240 orient=N0
Property name="VALUE" value="VOUT" autopos=1 normal=Right rotated=Bottom font=Default order=-1
.EndInstance
Wire x1=120 y1=240 x2=360 y2=240 net="VOUT" branch="-:R1#P"
Wire x1=120 y1=300 x2=0 y2=300 net="0" branch="-:R1#N"
"""


class ImporterTests(unittest.TestCase):
    def _write(self, root: Path, name: str, text: str) -> Path:
        path = root / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_import_preserves_components_layout_properties_and_pin_networks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self._write(root, "buck.sxsch", SIMPLE_SXSCH)
            out = root / "circuit.yaml"

            result = import_schematic(source, CATALOG, out)
            written = load_yaml(out)

        self.assertEqual(result, written)
        self.assertEqual(result["schema_version"], "simplis-automation/v2")
        self.assertEqual(result["catalog_lock"], CATALOG["catalog_lock"])
        resistor = next(component for component in result["components"] if component["id"] == "R1")
        self.assertEqual(resistor["native"], {"symbol": "res", "library": "Passives"})
        self.assertEqual(resistor["ref"], "R1")
        self.assertEqual(resistor["properties"], {"VALUE": "10k"})
        self.assertEqual(resistor["layout"], {"x": 120, "y": 240, "orientation": "N90"})
        self.assertEqual(resistor["pins"], {"P": "VOUT", "N": "0"})
        self.assertEqual(result["nets"]["VOUT"]["endpoints"], ["R1.P", "term.P"])
        self.assertEqual(result["nets"]["0"]["endpoints"], ["R1.N"])
        self.assertEqual(result["metadata"]["import_status"], "static_valid")
        self.assertEqual(result["metadata"]["diagnostics"]["catalog_candidates"], [])
        self.assertEqual(validate_circuit(result)["design"], {"name": "buck"})

    def test_import_emits_catalog_candidates_for_unknown_symbol_and_property(self) -> None:
        source_text = SIMPLE_SXSCH.replace(
            'Property name="VALUE" value="10k"',
            'Property name="VALUE" value="10k"\nProperty name="UNSUPPORTED" value="yes"',
        ).replace('name="term"', 'name="mystery"')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = import_schematic(self._write(root, "unknown.sxsch", source_text), CATALOG, root / "draft.yaml")

        candidates = result["metadata"]["diagnostics"]["catalog_candidates"]
        self.assertEqual(result["metadata"]["import_status"], "catalog_pending")
        self.assertTrue(any(item["code"] == "unknown_property" and item["property"] == "UNSUPPORTED" for item in candidates))
        self.assertTrue(any(item["code"] == "unknown_symbol" and item["symbol"] == "mystery" for item in candidates))

    def test_shared_symbol_without_a_disambiguating_property_stays_catalog_pending(self) -> None:
        catalog = dict(CATALOG)
        catalog["devices"] = list(CATALOG["devices"]) + [
            {
                "kind": "pulse_source",
                "approval": "approved",
                "symbol": {"name": "vwave_v2"},
                "pins": [],
                "properties": {"SOURCE_MODEL": {"enum": ["PULSE"]}},
                "ideality": "fully_ideal",
            },
            {
                "kind": "ramp_source",
                "approval": "approved",
                "symbol": {"name": "vwave_v2"},
                "pins": [],
                "properties": {"SOURCE_MODEL": {"enum": ["RAMP"]}},
                "ideality": "fully_ideal",
            },
        ]
        text = """
.Instance
Attributes type=symbol name="vwave_v2" x=0 y=0 orient=N0
Property name="REF" value="V1"
.EndInstance
"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = import_schematic(self._write(root, "ambiguous.sxsch", text), catalog, root / "draft.yaml")

        candidates = result["metadata"]["diagnostics"]["catalog_candidates"]
        self.assertEqual(result["metadata"]["import_status"], "catalog_pending")
        self.assertTrue(any(item["code"] == "ambiguous_symbol_mapping" for item in candidates))

    def test_approved_component_module_is_opaque_boundary_only(self) -> None:
        source_text = """
.Instance
Attributes type=component path="Blocks/vendor_controller.sxcmp" selected=0 protected=0 x=600 y=720 orient=M0
Property name="REF" value="XCTRL" autopos=1 normal=Right rotated=Bottom font=Default order=-1
Property name="GAIN" value="4" autopos=1 normal=Right rotated=Bottom font=Default order=-1
Netnames pin1="VIN" pin2="VOUT"
.EndInstance
"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = import_schematic(self._write(root, "module.sxsch", source_text), CATALOG, root / "draft.yaml")

        component = result["components"][0]
        self.assertEqual(result["metadata"]["import_status"], "boundary_verified")
        self.assertEqual(component["kind"], "opaque_module")
        self.assertTrue(component["module"]["boundary_only"])
        self.assertEqual(component["layout"], {"x": 600, "y": 720, "orientation": "M0"})
        self.assertEqual(component["properties"]["GAIN"], "4")
        self.assertEqual(component["module"]["details"]["Netnames"], {"pin1": "VIN", "pin2": "VOUT"})
        self.assertEqual(component["pins"], {"pin1": "VIN", "pin2": "VOUT"})
        self.assertEqual(result["nets"]["VIN"]["endpoints"], ["XCTRL.pin1"])
        self.assertEqual(result["metadata"]["diagnostics"]["catalog_candidates"], [])

    def test_direct_sxcmp_is_one_opaque_module_not_unfolded_circuit(self) -> None:
        text = """
.Instance
Attributes type=symbol name="res" selected=0 protected=0 x=10 y=20 orient=N0
Property name="REF" value="R_INTERNAL" autopos=1 normal=Right rotated=Bottom font=Default order=-1
.EndInstance
.Instance
Attributes type=symbol name="modport" selected=0 protected=0 x=40 y=20 orient=N0
Property name="netname" value="OUT" autopos=1 normal=Right rotated=Bottom font=Default order=-1
.EndInstance
"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = import_schematic(self._write(root, "vendor_controller.sxcmp", text), CATALOG, root / "module.yaml")

        self.assertEqual(result["metadata"]["import_status"], "boundary_verified")
        self.assertEqual(len(result["components"]), 1)
        self.assertEqual(result["components"][0]["kind"], "opaque_module")
        self.assertEqual(result["components"][0]["module"]["ports"][0]["netname"], "OUT")

    def test_binary_input_is_rejected_without_writing_a_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "binary.sxsch"
            out = root / "should-not-exist.yaml"
            source.write_bytes(b"\x12\x00SIMetrix Component\x03\x00")

            with self.assertRaises(ImportBlockedError):
                import_schematic(source, CATALOG, out)

            self.assertFalse(out.exists())

    def test_promote_parameter_replaces_literal_and_records_parameter_card(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            imported = root / "imported.yaml"
            promoted = root / "promoted.yaml"
            import_schematic(self._write(root, "buck.sxsch", SIMPLE_SXSCH), CATALOG, imported)

            result = promote_parameter(imported, "R1", "VALUE", "r_load", "resistance", "1k", "100k", promoted)

        resistor = next(component for component in result["components"] if component["id"] == "R1")
        self.assertEqual(resistor["properties"]["VALUE"], "${r_load}")
        self.assertEqual(
            result["parameters"]["r_load"],
            {
                "default": "10k",
                "dimension": "resistance",
                "min": "1k",
                "max": "100k",
                "component_id": "R1",
                "property": "VALUE",
            },
        )

    def test_promote_parameter_blocks_non_whitelisted_property(self) -> None:
        source_text = SIMPLE_SXSCH.replace(
            'Property name="VALUE" value="10k"',
            'Property name="VALUE" value="10k"\nProperty name="UNSUPPORTED" value="yes"',
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            imported = root / "imported.yaml"
            import_schematic(self._write(root, "buck.sxsch", source_text), CATALOG, imported)

            with self.assertRaises(ImportBlockedError):
                promote_parameter(imported, "R1", "UNSUPPORTED", "bad", "dimensionless", "0", "1", root / "blocked.yaml")

    def test_import_converts_supported_f11_directives_to_experiment(self) -> None:
        text = SIMPLE_SXSCH + '\nText value=".simulator SIMPLIS\\n.pop TRIG_GATE={TRIG_GATE}\\n.tran 10u 0\\n.simulator DEFAULT\\n.VAR TON=200n"\n'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            out = root / "draft.yaml"
            result = import_schematic(self._write(root, "analysis.sxsch", text), CATALOG, out)
            experiment = load_yaml(root / "draft.experiment.yaml")
        self.assertIn("experiment", result["metadata"])
        self.assertEqual({item["type"] for item in experiment["analyses"]}, {"pop_ac", "startup"})
        self.assertEqual(experiment["imported_variables"]["TON"], "200n")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
