from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from simplis_automation_v2.finalization import (
    FINALIZATION_SCHEMA,
    REVIEW_CHECKS,
    REVIEW_SCHEMA,
    build_finalize_script,
    finalize_review,
)


class FinalizationTests(unittest.TestCase):
    def test_finalize_script_cleanly_reopens_and_waits_for_native_capture(self) -> None:
        script = build_finalize_script(
            Path("buck.sxsch"),
            Path("buck.reopen.net"),
            Path("status.txt"),
            Path("messages.log"),
        )
        self.assertIn("OpenSchem /cd /readonly", script)
        self.assertIn("Netlist /simplis", script)
        self.assertIn("Focus schem", script)
        self.assertIn("Zoom full", script)
        self.assertIn("Echo screenshot_ready=true", script)
        self.assertLess(script.index("Echo screenshot_ready=true"), script.index("Sleep(15)"))

    def test_codex_review_is_hash_bound_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "buck.png"
            Image.new("RGB", (640, 480), "white").save(image)
            request = root / "finalization-request.json"
            request.write_text(
                json.dumps(
                    {
                        "schema_version": FINALIZATION_SCHEMA,
                        "capture": {"path": str(image), "sha256": __import__("hashlib").sha256(image.read_bytes()).hexdigest()},
                        "clean_reopen": {"passed": True},
                    }
                ),
                encoding="utf-8",
            )
            review = root / "review.json"
            request_digest = __import__("hashlib").sha256(request.read_bytes()).hexdigest()
            screenshot_digest = __import__("hashlib").sha256(image.read_bytes()).hexdigest()
            review.write_text(
                json.dumps(
                    {
                        "schema_version": REVIEW_SCHEMA,
                        "reviewer": "codex_multimodal",
                        "checklist_version": 1,
                        "finalization_request_sha256": request_digest,
                        "screenshot_sha256": screenshot_digest,
                        "verdict": "pass",
                        "checks": {name: "pass" for name in REVIEW_CHECKS},
                        "findings": [],
                    }
                ),
                encoding="utf-8",
            )
            record = finalize_review(request, review)
            self.assertTrue(record["ok"])
            image.write_bytes(b"changed")
            with self.assertRaises(Exception):
                finalize_review(request, review)

    def test_review_failure_cannot_become_deliverable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "buck.png"
            Image.new("RGB", (640, 480), "white").save(image)
            digest = __import__("hashlib").sha256(image.read_bytes()).hexdigest()
            request = root / "request.json"
            request.write_text(json.dumps({"schema_version": FINALIZATION_SCHEMA, "capture": {"path": str(image), "sha256": digest}, "clean_reopen": {"passed": True}}), encoding="utf-8")
            review = root / "review.json"
            checks = {name: "pass" for name in REVIEW_CHECKS}
            checks["readable_text"] = "fail"
            review.write_text(json.dumps({
                "schema_version": REVIEW_SCHEMA,
                "reviewer": "codex_multimodal",
                "checklist_version": 1,
                "finalization_request_sha256": __import__("hashlib").sha256(request.read_bytes()).hexdigest(),
                "screenshot_sha256": digest,
                "verdict": "pass",
                "checks": checks,
            }), encoding="utf-8")
            record = finalize_review(request, review)
            self.assertFalse(record["ok"])


if __name__ == "__main__":
    unittest.main()
