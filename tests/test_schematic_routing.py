from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from schematic_generator import (  # noqa: E402
    Pin,
    Symbol,
    build_create_script,
    compute_placements,
    get_design_paths,
)


def symbols() -> dict[str, Symbol]:
    return {
        "dc_source": Symbol("dc_source", Path("test"), {"P": Pin("P", 1, 0, 0), "N": Pin("N", 2, 0, 480)}, {}),
        "res": Symbol("res", Path("test"), {"P": Pin("P", 1, 0, 0), "N": Pin("N", 2, 0, 360)}, {}),
        "cap": Symbol("cap", Path("test"), {"P": Pin("P", 1, 0, 0), "N": Pin("N", 2, 0, 240)}, {}),
        "gnd": Symbol("gnd", Path("test"), {"A": Pin("A", 1, 0, 0)}, {}),
        "term": Symbol("term", Path("test"), {"A": Pin("A", 1, 0, 0)}, {}),
    }


def feedback_config(mode: str) -> dict:
    return {
        "design": {
            "name": f"feedback_{mode}",
            "schematic": f"feedback_{mode}.sxsch",
            "netlist": f"feedback_{mode}.net",
            "script": f"feedback_{mode}.sxscr",
        },
        "routing": {
            "mode": mode,
            "max_wire_length": 1440,
            "max_component_span": 2160,
        },
        "layout": {"grid": 120, "groups": ["feedback"]},
        "devices": [
            {"ref": "V1", "symbol": "dc_source", "group": "feedback", "x": 600, "y": 600, "props": {"VALUE": 12}},
            {"ref": "RTOP", "symbol": "res", "group": "feedback", "x": 1800, "y": 600, "props": {"VALUE": "100k"}},
            {"ref": "RBOT", "symbol": "res", "group": "feedback", "x": 1800, "y": 1320, "props": {"VALUE": "20k"}},
            {"ref": "CFB", "symbol": "cap", "group": "feedback", "x": 2400, "y": 960, "props": {"VALUE": "10p"}},
        ],
        "nets": {
            "V1": {"P": "VOUT", "N": "0"},
            "RTOP": {"P": "VOUT", "N": "VFB"},
            "RBOT": {"P": "VFB", "N": "0"},
            "CFB": {"P": "VFB", "N": "0"},
        },
    }


def pin_collision_config() -> dict:
    return {
        "design": {
            "name": "hybrid_pin_collision",
            "schematic": "hybrid_pin_collision.sxsch",
            "netlist": "hybrid_pin_collision.net",
            "script": "hybrid_pin_collision.sxscr",
        },
        "routing": {
            "mode": "hybrid",
            "max_wire_length": 1440,
            "max_component_span": 2160,
        },
        "layout": {"grid": 120, "groups": ["feedback"]},
        "devices": [
            {"ref": "V1", "symbol": "dc_source", "group": "feedback", "x": 600, "y": 600, "props": {"VALUE": 12}},
            {"ref": "RTOP", "symbol": "res", "group": "feedback", "x": 1800, "y": 600, "props": {"VALUE": "100k"}},
            {"ref": "CSENSE", "symbol": "cap", "group": "feedback", "x": 1200, "y": 600, "props": {"VALUE": "10p"}},
        ],
        "nets": {
            "V1": {"P": "VOUT", "N": "0"},
            "RTOP": {"P": "VOUT", "N": "0"},
            "CSENSE": {"P": "SENSE", "N": "0"},
        },
    }


def wire_collision_config() -> dict:
    return {
        "design": {
            "name": "hybrid_wire_collision",
            "schematic": "hybrid_wire_collision.sxsch",
            "netlist": "hybrid_wire_collision.net",
            "script": "hybrid_wire_collision.sxscr",
        },
        "routing": {
            "mode": "hybrid",
            "max_wire_length": 1440,
            "max_component_span": 2160,
        },
        "layout": {"grid": 120, "groups": ["feedback"]},
        "devices": [
            {"ref": "VOUT_L", "symbol": "term", "group": "feedback", "x": 600, "y": 600},
            {"ref": "VOUT_R", "symbol": "term", "group": "feedback", "x": 1800, "y": 600},
            {"ref": "SENSE_T", "symbol": "term", "group": "feedback", "x": 1200, "y": 240},
            {"ref": "SENSE_B", "symbol": "term", "group": "feedback", "x": 1200, "y": 960},
        ],
        "nets": {
            "VOUT_L": {"A": "VOUT"},
            "VOUT_R": {"A": "VOUT"},
            "SENSE_T": {"A": "SENSE"},
            "SENSE_B": {"A": "SENSE"},
        },
    }


class SchematicRoutingTests(unittest.TestCase):
    def test_default_routing_still_labels_every_connected_pin(self) -> None:
        config = feedback_config("labels")
        placements = compute_placements(config)
        script = build_create_script(config, symbols(), placements, get_design_paths(config, Path("out")), run_netlist=True)

        self.assertNotIn("Wire /loc", script)
        self.assertEqual(script.count(" term VALUE VOUT"), 2)
        self.assertEqual(script.count(" term VALUE VFB"), 3)
        self.assertEqual(script.count(" gnd"), 3)

    def test_hybrid_routing_uses_short_local_wires_and_fewer_labels(self) -> None:
        config = feedback_config("hybrid")
        placements = compute_placements(config)
        script = build_create_script(config, symbols(), placements, get_design_paths(config, Path("out")), run_netlist=True)

        self.assertIn("Wire /loc 600 600 1800 600", script)
        self.assertIn("Wire /loc 1800 960 2400 960", script)
        self.assertEqual(script.count(" term VALUE VOUT"), 1)
        self.assertEqual(script.count(" term VALUE VFB"), 1)
        self.assertEqual(script.count(" gnd"), 2)

    def test_hybrid_routing_does_not_cross_foreign_net_pins(self) -> None:
        config = pin_collision_config()
        placements = compute_placements(config)
        script = build_create_script(config, symbols(), placements, get_design_paths(config, Path("out")), run_netlist=True)

        self.assertNotIn("Wire /loc 600 600 1800 600", script)
        self.assertEqual(script.count(" term VALUE VOUT"), 2)

    def test_hybrid_routing_does_not_cross_foreign_net_wires(self) -> None:
        config = wire_collision_config()
        placements = compute_placements(config)
        script = build_create_script(config, symbols(), placements, get_design_paths(config, Path("out")), run_netlist=True)

        self.assertIn("Wire /loc 600 600 1800 600", script)
        self.assertNotIn("Wire /loc 1200 240 1200 960", script)
        self.assertEqual(script.count(" term VALUE SENSE"), 2)


if __name__ == "__main__":
    unittest.main()
