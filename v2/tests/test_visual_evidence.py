from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


V2_ROOT = Path(__file__).resolve().parents[1]
if str(V2_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(V2_ROOT / "src"))

from simplis_automation_v2.errors import ValidationError
from simplis_automation_v2.visual_evidence import VISUAL_CHECKS, parse_visual_checks, record_visual_evidence


class VisualEvidenceTests(unittest.TestCase):
    def _workspace(self, root: Path) -> tuple[Path, dict[str, str]]:
        schematic = root / "example.sxsch"
        netlist = root / "example.net"
        schematic.write_text("schematic\n", encoding="utf-8")
        netlist.write_text("R1 1 0 1k\n", encoding="utf-8")
        manifest = root / "build-manifest.json"
        manifest.write_text(json.dumps({"artifacts": {"schematic": str(schematic), "netlist": str(netlist)}}), encoding="utf-8")
        checks = {name: "pass" for name in VISUAL_CHECKS}
        checks["functional_bands"] = "not_applicable"
        checks["analysis_gutter"] = "not_applicable"
        return manifest, checks

    def test_computer_use_capture_records_hashes_checks_and_limitations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, checks = self._workspace(root)
            output = root / "visual-evidence.json"
            record = record_visual_evidence(
                manifest,
                output,
                capture_method="computer_use",
                capture_id="screenshot-0",
                image_path=None,
                window_title="SIMetrix/SIMPLIS Elite Main Window",
                active_document=str(root / "example.sxsch"),
                width=1920,
                height=1032,
                role="whole_sheet",
                clean_reopen_token="netlist-status:completion_token=true",
                responsive=True,
                modal_free=True,
                canvas_nonblank=True,
                verdict="pass",
                checks=checks,
                findings=["Series resistor is horizontal."],
            )
            self.assertTrue(output.is_file())
            self.assertEqual(record["capture"]["id"], "screenshot-0")
            self.assertFalse(record["capture"]["persistent_image"])
            self.assertEqual(len(record["schematic"]["sha256"]), 64)
            self.assertFalse(record["scoring_eligible"])
            self.assertEqual(len(record["limitations"]), 2)

    def test_passing_record_rejects_wrong_document_or_failed_precondition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, checks = self._workspace(root)
            common = {
                "capture_method": "computer_use",
                "capture_id": "screenshot-0",
                "image_path": None,
                "window_title": "SIMetrix",
                "width": 800,
                "height": 600,
                "role": "whole_sheet",
                "clean_reopen_token": "complete",
                "responsive": True,
                "modal_free": True,
                "canvas_nonblank": True,
                "verdict": "pass",
                "checks": checks,
            }
            with self.assertRaises(ValidationError):
                record_visual_evidence(manifest, root / "wrong.json", active_document="other.sxsch", **common)
            with self.assertRaises(ValidationError):
                record_visual_evidence(
                    manifest,
                    root / "blank.json",
                    active_document="example.sxsch",
                    **(common | {"canvas_nonblank": False}),
                )

    def test_all_named_checks_are_required(self) -> None:
        with self.assertRaises(ValidationError):
            parse_visual_checks(["readable_text=pass"])
        with self.assertRaises(ValidationError):
            parse_visual_checks([f"{name}=pass" for name in VISUAL_CHECKS[:-1]] + ["unknown=pass"])


if __name__ == "__main__":
    unittest.main()
