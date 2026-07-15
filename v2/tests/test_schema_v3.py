from __future__ import annotations

import unittest

from simplis_automation_v2.errors import ValidationError
from simplis_automation_v2.schema import migrate_old_v2_document, validate_experiment


class SchemaV3Tests(unittest.TestCase):
    def test_catalog_proof_analysis_is_explicitly_supported(self) -> None:
        experiment = {
            "schema_version": "simplis-automation/v2/experiment",
            "circuit": "proof.yaml",
            "analyses": [
                {
                    "type": "catalog_proof",
                    "analysis": "tran",
                    "behavior": "catalog_and2",
                    "group": "simplis_tran1",
                    "vectors": {"A": "#A", "B": "#B", "OUT": "#OUT", "OUT_BAR": "#OUT_BAR"},
                }
            ],
        }
        validated = validate_experiment(experiment)
        self.assertEqual(validated["analyses"][0]["type"], "catalog_proof")

    def test_ac_response_requires_declared_complex_ratio_roles(self) -> None:
        experiment = {
            "schema_version": "simplis-automation/v2/experiment",
            "circuit": "buck.yaml",
            "analyses": [
                {
                    "type": "pop_ac",
                    "analysis": "ac",
                    "vectors": {"RETURN": "#RETURN", "INJECT": "#INJECT"},
                    "response": {"numerator": "RETURN", "denominator": "INJECT", "sign": -1},
                }
            ],
        }
        validated = validate_experiment(experiment)
        self.assertEqual(validated["analyses"][0]["response"]["sign"], -1)
        broken = experiment | {
            "analyses": [experiment["analyses"][0] | {"response": {"numerator": "LOOP_GAIN", "denominator": "INJECT", "sign": 1}}]
        }
        with self.assertRaises(ValidationError):
            validate_experiment(broken)

    def test_old_double_resistor_ripple_migrates_to_rc(self) -> None:
        migrated = migrate_old_v2_document(
            {
                "schema_version": "simplis-automation/v2",
                "schema_revision": 2,
                "catalog_lock": {"path": "catalog.yaml"},
                "design": {"name": "old"},
                "parameters": {
                    "ripple_inject_r": {"default": "82k", "dimension": "resistance"},
                    "ripple_sense_r": {"default": "100k", "dimension": "resistance"},
                },
                "blocks": [
                    {
                        "id": "ripple",
                        "type": "synthetic_ripple",
                        "ports": {"SW": "SW", "FB": "FB", "RIPPLE": "RIPPLE", "RTN": "0"},
                        "parameters": {"rinject": "${ripple_inject_r}", "rsense": "${ripple_sense_r}"},
                    }
                ],
                "components": [],
            }
        )
        self.assertEqual(migrated["schema_revision"], 3)
        self.assertEqual(migrated["parameters"]["ripple_r"]["default"], "82k")
        self.assertEqual(migrated["parameters"]["ripple_c"]["default"], "220pF")
        self.assertNotIn("ripple_inject_r", migrated["parameters"])
        self.assertEqual(migrated["blocks"][0]["parameters"]["ripple_r"], "${ripple_r}")
        self.assertIn("ripple_c", migrated["blocks"][0]["parameters"])

    def test_old_optimization_ripple_parameters_migrate(self) -> None:
        migrated = migrate_old_v2_document(
            {
                "schema_version": "simplis-automation/v2/experiment",
                "circuit": "buck.yaml",
                "optimize": {
                    "parameters": {
                        "ripple_inject_r": {"min": 1, "max": 2},
                        "ripple_sense_r": {"min": 1, "max": 2},
                    }
                },
            }
        )
        parameters = migrated["optimize"]["parameters"]
        self.assertIn("ripple_r", parameters)
        self.assertIn("ripple_c", parameters)
        self.assertNotIn("ripple_sense_r", parameters)


if __name__ == "__main__":
    unittest.main()
