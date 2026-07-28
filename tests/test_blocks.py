from __future__ import annotations

import unittest

from simplis_automation_v2.blocks import expand_blocks


class BlockExpansionTests(unittest.TestCase):
    def test_all_functional_blocks_expand_and_manifest_maps_leaves(self) -> None:
        circuit = {
            "components": [],
            "blocks": [
                {"id": "power", "type": "half_bridge", "ports": {"VIN": "VIN", "SW": "SW", "PWM_HS": "HS", "PWM_LS": "LS", "RTN": "0"}},
                {"id": "fb", "type": "feedback", "ports": {"VOUT": "VOUT", "FB": "FB", "RTN": "0"}},
                {"id": "ripple", "type": "synthetic_ripple", "ports": {"SW": "SW", "VOUT": "VOUT", "FB": "FB", "RIPPLE": "RIPPLE", "RTN": "0"}},
                {"id": "ton", "type": "adaptive_ton", "ports": {"TRIG": "TRIG", "VIN": "VIN", "TON_OUT": "TON", "TON_DONE": "TON_DONE", "RTN": "0"}},
                {"id": "control", "type": "comparator_latch", "ports": {"REF": "REF", "SENSE": "RIPPLE", "RESET": "TON_DONE", "Q": "PWM", "QN": "PWMN", "RTN": "0"}},
                {"id": "dead", "type": "deadtime_driver", "ports": {"PWM": "PWM", "PWM_HS": "HS", "PWM_LS": "LS", "RTN": "0"}},
                {"id": "amp", "type": "behavioral_voltage_amplifier", "ports": {"INP": "AP", "INN": "AN", "OUT": "AO", "RTN": "0"}, "parameters": {"gain": 250}},
                {"id": "ota", "type": "behavioral_ota", "ports": {"INP": "GP", "INN": "GN", "OUT": "GO", "RTN": "0"}, "parameters": {"gm": "2mS"}},
            ],
        }
        expanded, manifest = expand_blocks(circuit)
        self.assertEqual(set(manifest), {"power", "fb", "ripple", "ton", "control", "dead", "amp", "ota"})
        self.assertEqual(len(expanded["components"]), 24)
        self.assertIn("CTRL_SET", expanded["nets"])
        self.assertEqual(manifest["control"]["internal_networks"], ["CTRL_SET"])
        self.assertEqual(manifest["power"]["leaf_components"], ["power__hs", "power__hs_diode", "power__ls", "power__ls_diode"])
        self.assertEqual(manifest["ton"]["parameters"], {})

        by_id = {item["id"]: item for item in expanded["components"]}
        self.assertEqual(by_id["power__hs_diode"]["pins"], {"P": "SW", "N": "VIN"})
        self.assertEqual(by_id["power__ls_diode"]["pins"], {"P": "0", "N": "SW"})
        self.assertEqual(by_id["power__hs_diode"]["ref"], "POWER_D_HS_BODY")
        self.assertEqual(by_id["power__ls_diode"]["ref"], "POWER_D_LS_BODY")
        self.assertEqual(by_id["ripple__center"]["pins"], {"P": "RIP_BASE", "N": "FB"})
        self.assertEqual(by_id["ripple__center"]["properties"], {"VALUE": "0V"})
        self.assertEqual(by_id["ripple__scale"]["kind"], "vcvs")
        self.assertEqual(by_id["ripple__scale"]["pins"], {"P": "RIP_DRV", "N": "RIP_BASE", "CP": "SW", "CN": "VOUT"})
        self.assertEqual(by_id["ripple__filter_r"]["pins"], {"P": "RIP_DRV", "N": "RIPPLE"})
        self.assertEqual(by_id["ripple__filter_r"]["properties"]["VALUE"], "100kohm")
        self.assertEqual(by_id["ripple__filter_c"]["pins"], {"P": "RIPPLE", "N": "RIP_BASE"})
        self.assertEqual(by_id["ripple__filter_c"]["properties"], {"VALUE": "220pF", "IC": "0V"})
        self.assertEqual(by_id["ripple__restore_r"]["pins"], {"P": "RIPPLE", "N": "RIP_BASE"})
        self.assertEqual(by_id["ripple__restore_r"]["properties"], {"VALUE": "100kohm"})
        self.assertEqual(by_id["fb__bode"]["properties"], {})
        self.assertEqual(by_id["ton__fall_delay"]["properties"]["FALL_DELAY"], "5ns")
        self.assertEqual(by_id["ton__edge_inverter"]["unconnected_pins"], ["OUT"])
        self.assertEqual(by_id["ton__falling_edge_and"]["pins"]["OUT"], "TON_DONE")
        self.assertEqual(by_id["ton__falling_edge_and"]["unconnected_pins"], ["OUT_BAR"])
        self.assertEqual(by_id["control__comparator"]["unconnected_pins"], ["OUT_BAR"])
        self.assertEqual(by_id["control__latch"]["properties"]["IC"], "0")
        self.assertEqual(by_id["control__latch"]["properties"]["DOM"], "R")
        self.assertNotIn("CTRL_SET_BAR", expanded["nets"])
        self.assertNotIn("TON_DONE_BAR", expanded["nets"])
        self.assertEqual(by_id["amp__gain"]["pins"], {"P": "AO", "N": "0", "CP": "AP", "CN": "AN"})
        self.assertEqual(by_id["amp__gain"]["properties"], {"VALUE": 250})
        self.assertEqual(by_id["ota__gm"]["properties"], {"VALUE": "2mS"})


if __name__ == "__main__":
    unittest.main()
