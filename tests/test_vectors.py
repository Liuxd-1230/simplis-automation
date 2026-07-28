from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from simplis_automation_v2.vectors import build_vector_manifest


class VectorManifestTests(unittest.TestCase):
    def test_piecewise_transition_may_repeat_time_without_becoming_nonmonotonic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vector = root / "q.txt"
            vector.write_text("time Q\n0 0\n1e-6 0\n1e-6 1\n2e-6 1\n", encoding="utf-8")
            now = time.time()
            vector.touch()
            result = build_vector_manifest(
                run_id="r",
                candidate_hash="c",
                source_hash="s",
                run_started_at=now,
                exports=[{"group": "g", "analysis": "tran", "vector": "Q", "path": vector}],
                output_path=root / "manifest.json",
                trusted=True,
                artifact_hashes={"schematic": "abc", "netlist": "def"},
            )
        self.assertEqual(result["artifact_hashes"]["schematic"], "abc")
        self.assertEqual(result["classification"], "scoring_eligible", result)

    def test_partial_and_stale_vectors_are_diagnostic_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vector = root / "v.txt"
            vector.write_text("time VOUT\n0 0\n1e-6 1\n", encoding="utf-8")
            now = time.time()
            result = build_vector_manifest(run_id="r", candidate_hash="c", source_hash="s", run_started_at=now + 5, exports=[{"group": "g", "analysis": "tran", "vector": "VOUT", "path": vector}], output_path=root / "manifest.json", trusted=True)
        self.assertEqual(result["classification"], "diagnostic_only")
        self.assertTrue(any(item["code"] == "vector_stale" for item in result["errors"]))


if __name__ == "__main__":
    unittest.main()
