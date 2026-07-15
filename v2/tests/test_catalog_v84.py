from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simplis_automation_v2.catalog import discover_catalog_sources, load_catalog, load_embedded_symbol, validate_catalog
from simplis_automation_v2.errors import CatalogError


class CatalogV84Tests(unittest.TestCase):
    def test_discovers_three_evidence_families(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            libs = root / "symbollibs"
            libs.mkdir()
            (libs / "a.sxslb").write_text('.Symbol\nAttributes name="res"\nPin name="P" order=1 x=0 y=0\n.EndSymbol\n', encoding="utf-8")
            models = root / "models"
            models.mkdir()
            (models / "logic.lb").write_text(".SUBCKT DIGI OUT IN RTN\n.ENDS DIGI\n", encoding="utf-8")
            (models / "logic.cat").write_text('model="DIGI"\n', encoding="utf-8")
            examples = root / "examples"
            examples.mkdir()
            (examples / "gate.sxsch").write_text('.Symbol\nAttributes name="embedded"\nPin name="OUT" order=1 x=0 y=0\n.EndSymbol\n', encoding="utf-8")
            inventory = discover_catalog_sources(symbol_library_dir=libs, model_dirs=[models], example_dirs=[examples])
        self.assertIn("res", inventory["symbols"])
        self.assertIn("DIGI", inventory["models"])
        self.assertIn("embedded", inventory["embedded_symbols"])

    def test_ideal_native_approval_requires_all_proofs(self) -> None:
        catalog = {
            "schema_version": "simplis-automation/v2/catalog",
            "runtime": {},
            "devices": [{"kind": "cmp", "approval": "approved", "ideality": "ideal_native", "symbol": {"name": "cmp"}, "pins": [{"name": "OUT"}], "evidence": {"proofs": {}}}],
        }
        with self.assertRaises(CatalogError):
            validate_catalog(catalog)

    def test_embedded_symbol_is_hash_locked_and_retains_definition(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "official.sxcmp"
            source.write_text(
                '.Symbol\nAttributes name="AND2"\nSegment x1=0 y1=0 x2=600 y2=0\nPin name="OUT" order=1 x=600 y=0\nPin name="IN" order=2 x=0 y=0\n.EndSymbol\n',
                encoding="utf-8",
            )
            from simplis_automation_v2.io import sha256_file

            record = load_embedded_symbol(source, "AND2", expected_sha256=sha256_file(source))
            self.assertEqual(record["bbox"]["width"], 600)
            self.assertEqual(record["definition_lines"][0], ".Symbol")
            self.assertEqual(record["definition_lines"][-1], ".EndSymbol")
            with self.assertRaises(CatalogError):
                load_embedded_symbol(source, "AND2", expected_sha256="0" * 64)

    def test_seed_distinguishes_approved_pending_and_import_only(self) -> None:
        catalog = load_catalog(Path(__file__).resolve().parents[1] / "catalog" / "seed_8_4.yaml")
        by_kind = catalog["_by_kind"]
        self.assertEqual(by_kind["resistor"]["approval"], "approved")
        for kind in ("vcvs", "vccs", "idealized_diode", "ac_injection_source", "bode_probe", "digital_and2_grounded"):
            self.assertEqual(by_kind[kind]["approval"], "approved")
        for kind in ("digital_or2", "digital_xor2", "summer2"):
            self.assertEqual(by_kind[kind]["approval"], "pending")
        self.assertEqual(by_kind["bode_probe"]["properties"], {})
        self.assertEqual(by_kind["bode_probe"]["netlist_presence"], "directive")
        self.assertEqual(by_kind["capacitor"]["property_encoder"], "reactive_value_ic")
        self.assertEqual(by_kind["capacitor"]["properties"]["IC"]["dimension"], "voltage")
        self.assertTrue(by_kind["capacitor"]["properties"]["IC"]["virtual"])
        self.assertEqual(by_kind["inductor"]["property_encoder"], "reactive_value_ic")
        self.assertEqual(by_kind["inductor"]["properties"]["IC"]["dimension"], "current")
        self.assertTrue(by_kind["inductor"]["properties"]["IC"]["virtual"])
        self.assertIn("_IDLE_IN_POP=1", by_kind["pulse_current_source"]["properties"]["SIMPLIS_VALUE"]["default"])
        self.assertEqual(by_kind["ccvs_import_boundary"]["approval"], "import_only")
        self.assertEqual(by_kind["cccs_import_boundary"]["approval"], "import_only")


if __name__ == "__main__":
    unittest.main()
