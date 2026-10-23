from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from simplis_tools import ac_waveform, waveform
from simplis_automation_v2.execution import build_simulation_script
from simplis_automation_v2.watchdog import solver_diagnostics


class PopAcTests(unittest.TestCase):
    def test_real_trigger_failure_preserves_number_and_explanation(self):
        result = solver_diagnostics([ROOT / "tests/fixtures/pop_ac/trigger_5020.err"])
        self.assertEqual(result["messages"][0]["error_id"], "5020")
        self.assertIn("without registering", result["messages"][0]["text"])
        self.assertIn("triggering condition", result["messages"][0]["text"])

    def test_real_buck_complex_table_is_not_a_time_table(self):
        source = ROOT / "tests/fixtures/pop_ac/native_buck.f2"
        data = ac_waveform(source)
        self.assertEqual(data["points"], 186)
        self.assertEqual((data["start"], data["end"]), (1000, 5000000))
        self.assertIn("V(XBUCK.XSENSE.#FB_DIV).REAL", data["columns"])
        with self.assertRaises(ValueError):
            waveform(source)

    def test_script_records_return_before_queries_and_guards_missing_group(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            script = build_simulation_script(schematic=root/"buck.sxsch", status_file=root/"status.txt",
                         message_log=root/"messages.log", required_vectors={"simplis_ac1": ["#FB"]})
        self.assertLess(script.index("Echo simulation_returned=true"), script.index("GetSIMPLISExitCode()"))
        self.assertIn("Set /temp SimplisSimulationHealthReportGlobalEnable=FALSE", script)
        self.assertLess(script.index("IF Search(v2_groups, 'simplis_ac1') >= 0 THEN"), script.index("SetGroup simplis_ac1"))


if __name__ == "__main__":
    unittest.main()
