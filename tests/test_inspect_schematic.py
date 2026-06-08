from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.inspect_schematic import inspect_path, main, parse_schematic_text, truncate_value


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
Wire x1=360 y1=240 x2=360 y2=300 net="VOUT" branch="-:R1#P"
"""


SIMPLE_SXCMP = """
.Instance
Attributes type=symbol name="SIMPLIS_DIGI1_COMP_Y" selected=0 protected=0 x=100 y=200 orient=M0
Property name="REF" value="U1" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="TH" value="1.2" autopos=1 normal=Top rotated=Top font=Default order=-1
.EndInstance
"""


class InspectSchematicTests(unittest.TestCase):
    def test_parse_schematic_instances_wires_and_terms(self) -> None:
        report = parse_schematic_text(SIMPLE_SXSCH, source=Path("fixture.sxsch"))

        self.assertEqual(report["file"]["path"], "fixture.sxsch")
        self.assertEqual(report["symbols"]["res"], 1)
        self.assertEqual(report["symbols"]["term"], 1)
        self.assertEqual(report["instances"][0]["ref"], "R1")
        self.assertEqual(report["instances"][0]["symbol"], "res")
        self.assertEqual(report["instances"][0]["x"], 120)
        self.assertEqual(report["instances"][0]["orient"], "N90")
        self.assertEqual(report["instances"][0]["properties"]["VALUE"], "10k")
        self.assertEqual(report["properties"]["VALUE"]["count"], 2)
        self.assertEqual(report["properties"]["VALUE"]["examples"], ["10k", "VOUT"])
        self.assertEqual(report["wires"]["count"], 2)
        self.assertEqual(report["wires"]["nets"], {"VOUT": 2})
        self.assertEqual(report["wires"]["bbox"], {"min_x": 120, "min_y": 240, "max_x": 360, "max_y": 300})
        self.assertEqual(report["wires"]["horizontal"], 1)
        self.assertEqual(report["wires"]["vertical"], 1)
        self.assertEqual(report["wires"]["diagonal_or_unknown"], 0)
        self.assertEqual(report["wires"]["total_manhattan_length"], 300)
        self.assertEqual(report["terminals"]["terms"], {"VOUT": 1})

    def test_parse_sxcmp_module(self) -> None:
        report = parse_schematic_text(SIMPLE_SXCMP, source=Path("block.sxcmp"))

        self.assertEqual(report["file"]["kind"], "sxcmp")
        self.assertEqual(report["symbols"]["SIMPLIS_DIGI1_COMP_Y"], 1)
        self.assertEqual(report["modules"][0]["path"], "block.sxcmp")
        self.assertIn("TH", report["modules"][0]["tunable_properties"])

    def test_sxcmp_tunable_properties_exclude_metadata_case_insensitively(self) -> None:
        text = """
.Instance
Attributes type=symbol name="TransAmpCore" selected=0 protected=0 x=100 y=200 orient=N0
Property name="Ref" value="U1" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="value" value="metadata" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="ModuleName" value="TransCondAmp" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="MODEL" value="X" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="StyleNormal" value="DefaultInstance" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="StyleSelected" value="DefaultSelected" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="netname" value="OUT" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="scterm" value="1" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="plus" value="INP" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="minus" value="INM" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="INIT_SCRIPT" value="init" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="valuescript" value="value script" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="ParamsScript" value="params" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="EditPropScript" value="edit" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="SIMULATOR" value="SIMPLIS" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="PARAM_MODEL_NAME" value="model" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="GRAPH_NAME" value="graph" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="PERSISTENCE" value="yes" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="PROBE_DISABLED" value="0" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="ICANAL" value="internal" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="USEIC" value="0" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="PORT_A" value="A" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="GAIN_AXIS_LABEL" value="gain" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="PHASE_COLOR" value="red" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="PLOT_GAIN" value="1" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="MEASURE_SPEC_GAIN" value="1" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="CURVE_LOCATION" value="top" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="axisType" value="grid" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="VERTICAL_ORDER" value="1" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="A0" value="100k" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="GBW" value="10Meg" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="RIN" value="1Meg" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="ROUT" value="10" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="IO_SRC_MAX" value="1m" autopos=1 normal=Top rotated=Top font=Default order=-1
Property name="IO_SNK_MAX" value="2m" autopos=1 normal=Top rotated=Top font=Default order=-1
.EndInstance
"""

        report = parse_schematic_text(text, source=Path("TransCondAmp.sxcmp"))

        self.assertEqual(
            report["modules"][0]["tunable_properties"],
            ["A0", "GBW", "IO_SNK_MAX", "IO_SRC_MAX", "RIN", "ROUT"],
        )

    def test_sxcmp_tunable_properties_include_safe_value_placeholders(self) -> None:
        text = """
.Instance
Attributes type=symbol name="cap" selected=0 protected=0 x=10 y=20 orient=N0
Property name="VALUE" value="{C2}" autopos=1 normal=Right rotated=Top font=Default order=-1
.EndInstance
.Instance
Attributes type=symbol name="res" selected=0 protected=0 x=20 y=30 orient=N0
Property name="value" value="{R3}" autopos=1 normal=Right rotated=Top font=Default order=-1
.EndInstance
.Instance
Attributes type=symbol name="probe" selected=0 protected=0 x=30 y=40 orient=N0
Property name="GRAPH_NAME" value="{GRAPH_NAME}" autopos=1 normal=Right rotated=Top font=Default order=-1
Property name="VALUE" value="{PROBE_DISABLED}" autopos=1 normal=Right rotated=Top font=Default order=-1
Property name="value" value="{ICANAL}" autopos=1 normal=Right rotated=Top font=Default order=-1
.EndInstance
"""

        report = parse_schematic_text(text, source=Path("placeholder.sxcmp"))

        self.assertIn("C2", report["modules"][0]["tunable_properties"])
        self.assertIn("R3", report["modules"][0]["tunable_properties"])
        self.assertNotIn("GRAPH_NAME", report["modules"][0]["tunable_properties"])
        self.assertNotIn("PROBE_DISABLED", report["modules"][0]["tunable_properties"])
        self.assertNotIn("ICANAL", report["modules"][0]["tunable_properties"])

    def test_sxcmp_tunable_placeholders_are_found_in_long_property_values(self) -> None:
        prefix = "A" * 180
        text = f"""
.Instance
Attributes type=symbol name="cap" selected=0 protected=0 x=10 y=20 orient=N0
Property name="VALUE" value="{prefix}{{C_LONG}}" autopos=1 normal=Right rotated=Top font=Default order=-1
.EndInstance
"""

        report = parse_schematic_text(text, source=Path("long-placeholder.sxcmp"))

        self.assertIn("C_LONG", report["modules"][0]["tunable_properties"])

    def test_sxcmp_module_reports_modport_exported_pins(self) -> None:
        text = """
.Instance
Attributes type=symbol name="modport" selected=0 protected=0 x=120 y=240 orient=N90
Property name="ref" value="P1" autopos=1 normal=Right rotated=Bottom font=Default order=-1
Property name="Netnames" value="VOUT" autopos=1 normal=Right rotated=Bottom font=Default order=-1
Property name="netname" value="VOUT" autopos=1 normal=Right rotated=Bottom font=Default order=-1
Property name="plus" value=" +" autopos=1 normal=Right rotated=Bottom font=Default order=-1
Netnames pin1="VOUT" pin2="0"
.EndInstance
"""

        report = parse_schematic_text(text, source=Path("ports.sxcmp"))
        port = report["modules"][0]["ports"][0]

        self.assertEqual(port["ref"], "P1")
        self.assertEqual(port["x"], 120)
        self.assertEqual(port["orient"], "N90")
        self.assertEqual(port["properties"]["Netnames"], "VOUT")
        self.assertEqual(port["properties"]["NetnamesDetail"], {"pin1": "VOUT", "pin2": "0"})
        self.assertEqual(port["properties"]["netname"], "VOUT")
        self.assertNotIn("Netnames", report["modules"][0]["tunable_properties"])
        self.assertNotIn("NetnamesDetail", report["modules"][0]["tunable_properties"])
        self.assertEqual(report["modules"][0]["exported_pins"], report["modules"][0]["ports"])

    def test_file_property_summary_merges_case_variants(self) -> None:
        text = """
.Instance
Attributes type=symbol name="res" selected=0 protected=0 x=1 y=2 orient=N0
Property name="Label" value="Upper" autopos=1 normal=Right rotated=Bottom font=Default order=-1
Property name="LABEL" value="AllCaps" autopos=1 normal=Right rotated=Bottom font=Default order=-1
.EndInstance
"""

        report = parse_schematic_text(text, source=Path("case-properties.sxsch"))

        self.assertEqual(set(report["properties"]), {"Label"})
        self.assertEqual(report["properties"]["Label"]["count"], 2)
        self.assertEqual(report["properties"]["Label"]["examples"], ["Upper", "AllCaps"])

    def test_parse_property_value_with_escaped_quotes(self) -> None:
        text = r"""
.Instance
Attributes type=symbol name="GraphProbe" selected=0 protected=0 x=1 y=2 orient=N0
Property name="VALUE" value="axisType=\"grid\" graphName=\"Buck\"" autopos=1 normal=Right rotated=Bottom font=Default order=-1
.EndInstance
"""

        report = parse_schematic_text(text, source=Path("escaped.sxsch"))

        self.assertEqual(
            report["instances"][0]["properties"]["VALUE"],
            'axisType="grid" graphName="Buck"',
        )
        self.assertEqual(report["warnings"], [])

    def test_parse_attributes_and_wire_values_with_escaped_quotes(self) -> None:
        text = r"""
.Instance
Attributes type=symbol name="Graph\"Probe" selected=0 protected=0 x=1 y=2 orient=N0
Property name="REF" value="P1" autopos=1 normal=Right rotated=Bottom font=Default order=-1
.EndInstance
Wire x1=1 y1=2 x2=3 y2=4 net="V\"OUT" branch="-:P1#P"
"""

        report = parse_schematic_text(text, source=Path("escaped-attrs.sxsch"))

        self.assertEqual(report["instances"][0]["symbol"], 'Graph"Probe')
        self.assertEqual(report["instances"][0]["attributes"]["name"], 'Graph"Probe')
        self.assertEqual(report["wires"]["items"][0]["net"], 'V"OUT')
        self.assertEqual(report["wires"]["nets"], {'V"OUT': 1})

    def test_ref_property_is_case_insensitive(self) -> None:
        text = """
.Instance
Attributes type=symbol name="res" selected=0 protected=0 x=1 y=2 orient=N0
Property name="ref" value="RLOW" autopos=1 normal=Right rotated=Bottom font=Default order=-1
.EndInstance
"""

        report = parse_schematic_text(text, source=Path("lower-ref.sxsch"))

        self.assertEqual(report["instances"][0]["ref"], "RLOW")

    def test_symbol_instance_missing_ref_warns_unless_ref_optional(self) -> None:
        text = """
.Instance
Attributes type=symbol name="res" selected=0 protected=0 x=1 y=2 orient=N0
.EndInstance
.Instance
Attributes type=symbol name="gnd" selected=0 protected=0 x=3 y=4 orient=N0
.EndInstance
"""

        report = parse_schematic_text(text, source=Path("missing-ref.sxsch"))

        self.assertEqual(len(report["warnings"]), 1)
        self.assertIn("missing ref", report["warnings"][0])
        self.assertIn("res", report["warnings"][0])

    def test_component_instance_missing_ref_warns(self) -> None:
        text = """
.Instance
Attributes type=component path="Blocks/NoRef.sxcmp" selected=0 protected=0 x=1 y=2 orient=N0
.EndInstance
"""

        report = parse_schematic_text(text, source=Path("component-missing-ref.sxsch"))

        self.assertEqual(len(report["warnings"]), 1)
        self.assertIn("component instance missing ref", report["warnings"][0])

    def test_unsupported_line_inside_instance_warns(self) -> None:
        text = """
.Instance
Attributes type=symbol name="res" selected=0 protected=0 x=1 y=2 orient=N0
Property name="REF" value="R1" autopos=1 normal=Right rotated=Bottom font=Default order=-1
UnsupportedThing foo=bar
.EndInstance
"""

        report = parse_schematic_text(text, source=Path("unsupported.sxsch"))

        self.assertEqual(len(report["warnings"]), 1)
        self.assertIn("unsupported line inside instance", report["warnings"][0])

    def test_global_property_lines_do_not_warn_outside_instance_blocks(self) -> None:
        text = """
.SymbolLibrary
Property name="VALUE" value="axisType=\\"grid\\" graphName=\\"Buck\\"" autopos=1 normal=Right rotated=Bottom font=Default order=-1
.EndSymbolLibrary
.Instance
Property name="VALUE" value="dangling" autopos=1 normal=Right rotated=Bottom font=Default order=-1
.EndInstance
"""

        report = parse_schematic_text(text, source=Path("library.sxsch"))

        self.assertEqual(report["instances"], [])
        self.assertEqual(len(report["warnings"]), 2)
        self.assertIn("property outside symbol instance", report["warnings"][0])
        self.assertIn("instance block ended without symbol attributes", report["warnings"][1])

    def test_component_instance_properties_are_module_references_not_warnings(self) -> None:
        text = """
.Instance
Attributes type=component path="Modeling_Blocks/3p2zcompensator.sxcmp" selected=0 protected=0 x=4320 y=4320 orient=N0
Property name="ModuleName" value="3p2zcompensator" autopos=1 normal=Top rotated=Right font=Default order=-1
Property name="Ref" value="U4" autopos=1 normal=Top rotated=Right font=Default order=-1
.EndInstance
"""

        report = parse_schematic_text(text, source=Path("component.sxsch"))

        self.assertEqual(report["instances"], [])
        self.assertEqual(report["warnings"], [])
        self.assertEqual(report["modules"][0]["path"], "Modeling_Blocks/3p2zcompensator.sxcmp")
        self.assertEqual(report["modules"][0]["ref"], "U4")
        self.assertEqual(report["modules"][0]["properties"]["ModuleName"], "3p2zcompensator")

    def test_truncates_long_values(self) -> None:
        text = "A" * 300
        self.assertEqual(len(truncate_value(text, limit=80)), 83)
        self.assertTrue(truncate_value(text, limit=80).endswith("..."))

    def test_inspect_directory_aggregates_files_and_canonical_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "one.sxsch").write_text(SIMPLE_SXSCH, encoding="utf-8")
            (root / "block.sxcmp").write_text(SIMPLE_SXCMP, encoding="utf-8")

            report = inspect_path(root)

        self.assertEqual(report["totals"]["files"], 2)
        self.assertEqual(report["symbols"]["res"]["count"], 1)
        self.assertEqual(report["canonical_symbols"]["resistor"]["symbol"], "res")
        self.assertEqual(report["canonical_symbols"]["net_terminal"]["symbol"], "term")

    def test_symbol_catalog_metadata_is_reported_and_aggregated(self) -> None:
        text = """
.Symbol
Attributes format=1.0 revision=8 name="res" description="Resistor" catalog="Passives" track=1
.EndSymbol
.Instance
Attributes type=symbol name="res" selected=0 protected=0 x=10 y=20 orient=N0
Property name="REF" value="R1" autopos=1 normal=Right rotated=Bottom font=Default order=-1
.EndInstance
"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root / "catalog.sxsch"
            fixture.write_text(text, encoding="utf-8")

            report = inspect_path(root)

        file_report = report["files"][0]
        self.assertEqual(
            file_report["symbol_catalogs"]["res"],
            {"description": "Resistor", "catalog": "Passives"},
        )
        self.assertEqual(report["symbols"]["res"]["count"], 1)
        self.assertEqual(report["symbols"]["res"]["files"], [str(fixture)])
        self.assertEqual(report["symbols"]["res"]["categories"], ["Passives"])
        self.assertEqual(report["symbols"]["res"]["descriptions"], ["Resistor"])

    def test_inspect_path_raises_for_missing_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"

            with self.assertRaises(FileNotFoundError):
                inspect_path(missing)

    def test_official_alias_only_roles_keep_generator_defaults_with_evidence(self) -> None:
        symbols = [
            "websim_resz1",
            "resz",
            "websim_cap",
            "websim_ind1",
            "websim_pwr_nmos",
        ]
        body = "\n".join(
            f"""
.Instance
Attributes type=symbol name="{symbol}" selected=0 protected=0 x=10 y=20 orient=N0
.EndInstance
"""
            for symbol in symbols
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "official-alias-only.sxsch").write_text(body, encoding="utf-8")

            report = inspect_path(root)

        canonical = report["canonical_symbols"]
        self.assertEqual(canonical["resistor"]["symbol"], "res")
        self.assertFalse(canonical["resistor"]["default_observed"])
        self.assertEqual(canonical["resistor"]["observed"], "websim_resz1")
        self.assertEqual(canonical["resistor"]["observed_count"], 1)
        self.assertEqual(canonical["resistor"]["observed_aliases"], ["websim_resz1", "resz"])
        self.assertEqual(canonical["capacitor"]["symbol"], "cap")
        self.assertEqual(canonical["capacitor"]["observed"], "websim_cap")
        self.assertEqual(canonical["inductor"]["symbol"], "ind")
        self.assertEqual(canonical["inductor"]["observed"], "websim_ind1")
        self.assertEqual(canonical["power_switch"]["symbol"], "simplis_prim_vcswitch")
        self.assertEqual(canonical["power_switch"]["observed"], "websim_pwr_nmos")

    def test_default_and_official_alias_roles_keep_generator_defaults_with_evidence(self) -> None:
        symbols = [
            "websim_resz1",
            "resz",
            "res",
            "websim_cap",
            "websim_ecap1",
            "cap",
            "websim_ind1",
            "websim_cind1",
            "ind",
            "websim_pwr_nmos",
            "PWRNMOS_wCOSS",
            "simplis_prim_vcswitch",
            "probev_new",
            "Differential_Voltage_Probe",
            "Bode_Probe2",
            "Websim_Bode_Probe",
            "InlineCurrentProbe",
            "Power_Probe",
            "gnd2",
            "gnd",
        ]
        body = "\n".join(
            f"""
.Instance
Attributes type=symbol name="{symbol}" selected=0 protected=0 x=10 y=20 orient=N0
.EndInstance
"""
            for symbol in symbols
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "official-style.sxsch").write_text(body, encoding="utf-8")

            report = inspect_path(root)

        canonical = report["canonical_symbols"]
        self.assertEqual(canonical["resistor"]["symbol"], "res")
        self.assertTrue(canonical["resistor"]["default_observed"])
        self.assertEqual(canonical["resistor"]["count"], 1)
        self.assertEqual(canonical["resistor"]["observed"], "res")
        self.assertEqual(canonical["resistor"]["observed_aliases"], ["websim_resz1", "resz"])
        self.assertEqual(canonical["capacitor"]["symbol"], "cap")
        self.assertEqual(canonical["capacitor"]["observed_aliases"], ["websim_cap", "websim_ecap1"])
        self.assertEqual(canonical["inductor"]["symbol"], "ind")
        self.assertEqual(canonical["inductor"]["observed_aliases"], ["websim_ind1", "websim_cind1"])
        self.assertEqual(canonical["power_switch"]["symbol"], "simplis_prim_vcswitch")
        self.assertEqual(canonical["power_switch"]["observed_aliases"], ["websim_pwr_nmos", "PWRNMOS_wCOSS"])
        self.assertEqual(canonical["ground"]["symbol"], "gnd")
        self.assertEqual(canonical["ground"]["observed_aliases"], ["gnd2"])
        self.assertEqual(report["files"][0]["terminals"]["grounds"], 2)
        self.assertEqual(canonical["voltage_probe"]["symbol"], "probev_new")
        self.assertEqual(canonical["voltage_probe"]["observed_aliases"], ["Differential_Voltage_Probe"])
        self.assertEqual(canonical["bode_probe"]["symbol"], "Bode_Probe2")
        self.assertEqual(canonical["bode_probe"]["observed_aliases"], ["Websim_Bode_Probe"])
        self.assertEqual(canonical["current_probe"]["symbol"], "InlineCurrentProbe")
        self.assertEqual(canonical["current_probe"]["observed_aliases"], ["Power_Probe"])

    def test_inspect_directory_maps_required_canonical_symbols(self) -> None:
        required_symbols = {
            "voltage_source": "dc_source",
            "current_source": "dc_isource",
            "resistor": "res",
            "capacitor": "cap",
            "inductor": "ind",
            "ground": "gnd",
            "net_terminal": "term",
            "power_switch": "simplis_prim_vcswitch",
            "body_diode": "dio",
            "voltage_probe": "probev_new",
            "current_probe": "InlineCurrentProbe",
            "pop_trigger": "PERIODIC_OP_V8",
            "pwm_source": "vwave_v2",
            "digital_comparator": "SIMPLIS_DIGI1_COMP_Y",
            "digital_buffer": "SIMPLIS_DIGI1_BUF_Y",
            "digital_latch": "SIMPLIS_DIGI1_SRLATCH_1_NONE_Y",
            "digital_and": "SIMPLIS_DIGI1_AND_2I0_Y",
            "digital_or": "SIMPLIS_DIGI1_OR_2I0_Y",
        }
        body = "\n".join(
            f"""
.Instance
Attributes type=symbol name="{symbol}" selected=0 protected=0 disabled=0 x=10 y=20 orient=N0
Property name="REF" value="{role}" autopos=1 normal=Right rotated=Bottom font=Default order=-1
.EndInstance
"""
            for role, symbol in required_symbols.items()
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "symbols.sxsch").write_text(body, encoding="utf-8")

            report = inspect_path(root)

        self.assertEqual(set(report["canonical_symbols"]), set(required_symbols))
        for role, symbol in required_symbols.items():
            self.assertEqual(report["canonical_symbols"][role]["symbol"], symbol)

    def test_profiles_match_canonical_symbol_defaults(self) -> None:
        root = Path("profiles")
        if not root.exists():
            self.skipTest("profiles are not present")

        profile_roles: dict[str, str] = {}
        for filename in ("power-stage.json", "measurement.json", "digital-control.json"):
            profile = json.loads((root / filename).read_text(encoding="utf-8"))
            for role, spec in profile["roles"].items():
                profile_roles[role] = spec["preferred"]

        required_symbols = {
            "voltage_source": "dc_source",
            "current_source": "dc_isource",
            "resistor": "res",
            "capacitor": "cap",
            "inductor": "ind",
            "ground": "gnd",
            "net_terminal": "term",
            "power_switch": "simplis_prim_vcswitch",
            "body_diode": "dio",
            "voltage_probe": "probev_new",
            "bode_probe": "Bode_Probe2",
            "current_probe": "InlineCurrentProbe",
            "pop_trigger": "PERIODIC_OP_V8",
            "pwm_source": "vwave_v2",
            "digital_comparator": "SIMPLIS_DIGI1_COMP_Y",
            "digital_buffer": "SIMPLIS_DIGI1_BUF_Y",
            "digital_latch": "SIMPLIS_DIGI1_SRLATCH_1_NONE_Y",
            "digital_and": "SIMPLIS_DIGI1_AND_2I0_Y",
            "digital_or": "SIMPLIS_DIGI1_OR_2I0_Y",
        }

        for role, symbol in required_symbols.items():
            self.assertEqual(profile_roles[role], symbol)

    def test_main_writes_json_and_optional_markdown_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "one.sxsch").write_text(SIMPLE_SXSCH, encoding="utf-8")
            out = root / "report.json"
            summary = root / "summary.md"

            rc = main(["--input", str(root), "--out", str(out), "--summary-md", str(summary)])

            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["totals"]["files"], 1)
            self.assertIn("SIMPLIS Inspection Summary", summary.read_text(encoding="utf-8"))

    def test_simplis_cli_inspect_schematic_subcommand_writes_outputs(self) -> None:
        from scripts.simplis_cli import main as simplis_main

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "one.sxsch").write_text(SIMPLE_SXSCH, encoding="utf-8")
            out = root / "cli-report.json"
            summary = root / "cli-summary.md"

            rc = simplis_main(["inspect-schematic", "--input", str(root), "--out", str(out), "--summary-md", str(summary)])

            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["totals"]["files"], 1)
            self.assertIn("SIMPLIS Inspection Summary", summary.read_text(encoding="utf-8"))

    def test_official_examples_smoke_when_present(self) -> None:
        official = Path("examples/official")
        if not official.exists():
            self.skipTest("official examples are not present")

        report = inspect_path(official)

        self.assertEqual(report["warnings"], [])
        self.assertGreater(report["totals"]["files"], 0)
        module_names = {module["name"] for module in report["modules"]}
        self.assertIn("3p2zcompensator", module_names)
        self.assertIn("TransCondAmp", module_names)


if __name__ == "__main__":
    unittest.main()
