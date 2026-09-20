from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("v3_tools", Path(__file__).parents[1] / "scripts/simplis_tools.py")
tools = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tools)


def t2(rows: str, count: int = 4) -> str:
    return f"$$$ 1 {count} 1 simplis DATA\nINPUT FILE: synthetic\nTransient Analysis\n{count} DATA PTS\n1 VARIABLES\n0 TIME\n1 V(2)\n\n" + rows


class V3ToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def data(self, text):
        path = self.root / "wave.t2"
        path.write_text(text, encoding="ascii")
        return path

    def test_nonuniform_points_and_duplicate_jump_limits(self):
        p = self.data(t2("0 0\n1 0\n1 4\n3 4\n"))
        result = tools.waveform(p, "V(2)")
        self.assertAlmostEqual(result["measurement"]["time_mean"], 8 / 3)
        self.assertAlmostEqual(tools.waveform(p, "V(2)", 1, 3)["measurement"]["time_mean"], 4)
        self.assertEqual(tools.waveform(p, "V(2)", 0, 1)["measurement"]["time_mean"], 0)

    def test_clips_and_interpolates_boundaries(self):
        p = self.data(t2("0 0\n2 4\n", 2))
        self.assertAlmostEqual(tools.waveform(p, "V(2)", .25, 1.25)["measurement"]["time_mean"], 1.5)

    def test_rejects_extrapolation_or_unknown_column(self):
        p = self.data(t2("0 0\n2 4\n", 2))
        for column, start, end in [("V(2)", -1, 1), ("V(2)", 1, 3), ("missing", 0, 1), ("V(2)", 1, 1)]:
            with self.subTest(column=column, start=start, end=end), self.assertRaises(ValueError):
                tools.waveform(p, column, start, end)

    def test_rejects_bad_or_incomplete_waveforms(self):
        for text in ["state snapshot", t2("0 0\n", 2), t2("0 0\n1 nan\n", 2),
                     t2("1 0\n0 1\n", 2), t2("0 0 4\n1 1\n", 2), t2("0 0\n0 1\n", 2)]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                tools.read_t2(self.data(text))

    def test_symbol_definition_and_pin_order_from_library(self):
        p = self.root / "test.sxslb"
        p.write_text('.Symbol\nAttributes name="switch" description="Test switch"\nPin name="N" order=2 x=0 y=360\nPin name="P" order=1 x=0 y=0\nProperty name="VALUE" value="a b"\n.EndSymbol\n', encoding="utf-8")
        result = tools.symbols(p, name="switch")
        self.assertEqual([v["name"] for v in result["symbols"][0]["pins"]], ["P", "N"])
        self.assertEqual(len(result["sha256"]), 64)
        self.assertEqual(len(tools.symbols(p, query="test")["symbols"]), 1)
        p.write_text(p.read_text() * 2)
        with self.assertRaises(ValueError):
            tools.symbols(p, name="switch")

    def inputs(self):
        exe, deck = self.root / "fake.exe", self.root / "source.ckt"
        exe.write_bytes(b"test")
        deck.write_text("* synthetic\nV1 1 0 1\n.END\n")
        return exe, deck, self.root / "run"

    def test_runner_records_fresh_data_but_not_behavior_validation(self):
        exe, deck, out = self.inputs()
        def solve(*args, **kwargs):
            (kwargs["cwd"] / "input.ckt.t2").write_text(t2("0 0\n1 1\n", 2))
            return subprocess.CompletedProcess(args[0], 0)
        with patch.object(tools.subprocess, "run", side_effect=solve):
            result = tools.run_deck(exe, deck, out)
        self.assertTrue(result["completed"])
        self.assertFalse(result["behavior_validated"])
        self.assertEqual((out / "input.ckt").read_bytes(), deck.read_bytes())

    def test_zero_exit_without_waveform_is_not_success(self):
        exe, deck, out = self.inputs()
        with patch.object(tools.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            result = tools.run_deck(exe, deck, out)
        self.assertFalse(result["completed"])
        self.assertIn("error", result)

    def test_solver_error_file_even_with_zero_exit(self):
        exe, deck, out = self.inputs()
        def solve(*args, **kwargs):
            (out / "input.ckt.err").write_text("bad model")
            return subprocess.CompletedProcess([], 0)
        with patch.object(tools.subprocess, "run", side_effect=solve):
            result = tools.run_deck(exe, deck, out)
        self.assertFalse(result["completed"])
        self.assertEqual(result["error"], "bad model")

    def test_preserves_existing_directory_and_rejects_external_dependencies(self):
        exe, deck, out = self.inputs()
        out.mkdir(); (out / "keep.txt").write_text("existing")
        with self.assertRaises(ValueError):
            tools.run_deck(exe, deck, out)
        self.assertEqual((out / "keep.txt").read_text(), "existing")
        deck.write_text('.INCLUDE "outside.lib"\n')
        with self.assertRaises(ValueError):
            tools.run_deck(exe, deck, self.root / "another")
        self.assertFalse((self.root / "another").exists())

    def test_timeout_is_reported_and_never_retried(self):
        exe, deck, out = self.inputs()
        with patch.object(tools.subprocess, "run", side_effect=subprocess.TimeoutExpired("solver", 1)) as run:
            result = tools.run_deck(exe, deck, out, 1)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(result["exit_code"], 124)
        self.assertFalse(json.loads((out / "run.json").read_text())["completed"])


if __name__ == "__main__":
    unittest.main()
