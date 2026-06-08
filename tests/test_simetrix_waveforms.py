from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from simetrix_waveforms import (  # noqa: E402
    build_vector_export_script,
    parse_show_file,
    simetrix_vector_expr,
)


class SimetrixWaveformTests(unittest.TestCase):
    def test_vector_expression_uses_vec_for_names_that_are_not_plain_identifiers(self) -> None:
        self.assertEqual(simetrix_vector_expr("time"), "time")
        self.assertEqual(simetrix_vector_expr("VOUT"), "VOUT")
        self.assertEqual(simetrix_vector_expr("#VOUT"), "Vec('#VOUT')")
        self.assertEqual(simetrix_vector_expr("50"), "Vec('50')")
        self.assertEqual(simetrix_vector_expr("IN+"), "Vec('IN+')")

    def test_export_script_sets_groups_and_uses_quoted_paths_and_vec_expressions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = build_vector_export_script(
                schematic=Path(r"E:\work\VRAMPValley.sxsch"),
                output_dir=root / "vectors",
                exports=[
                    ("simplis_pop1", "#VOUT"),
                    ("simplis_pop1", "#V_SERVO"),
                    ("simplis_ac1", "46"),
                ],
                status_file=root / "status.txt",
            )

        self.assertIn('OpenSchem "E:\\work\\VRAMPValley.sxsch"', script)
        self.assertIn("Let echo_file = OpenEchoFile('", script)
        self.assertIn("simplis_run", script)
        self.assertIn("SetGroup simplis_pop1", script)
        self.assertIn("SetGroup simplis_ac1", script)
        self.assertIn("Show /force /names \"VOUT\"", script)
        self.assertIn("Vec('#VOUT')", script)
        self.assertIn("Vec('46')", script)

    def test_parse_show_file_reads_real_and_complex_simetrix_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real_file = Path(tmp) / "pop_vout.txt"
            real_file.write_text(
                "time\tVOUT\n"
                "0\t1.2\n"
                "1e-9\t1.201\n",
                encoding="utf-8",
            )
            complex_file = Path(tmp) / "ac_vout.txt"
            complex_file.write_text(
                "freq\tVOUT\n"
                "1000\t(-1.004922329565 ,-0.001443921967 )\n"
                "1096.4\t(-1.005050021956 ,0.001142594522 )\n",
                encoding="utf-8",
            )

            real_data = parse_show_file(real_file)
            complex_data = parse_show_file(complex_file)

        self.assertEqual(real_data["x_name"], "time")
        self.assertEqual(real_data["y_name"], "VOUT")
        self.assertEqual(real_data["x"], [0.0, 1e-9])
        self.assertEqual(real_data["y"], [1.2, 1.201])

        self.assertEqual(complex_data["x_name"], "freq")
        self.assertEqual(complex_data["y"][0], {"real": -1.004922329565, "imag": -0.001443921967})
        self.assertEqual(complex_data["y"][1], {"real": -1.005050021956, "imag": 0.001142594522})
        json.dumps(complex_data, allow_nan=False)

    def test_cli_can_build_export_script_and_parse_show_files(self) -> None:
        cli = Path(__file__).resolve().parents[1] / "scripts" / "simplis_cli.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "export.sxscr"
            status = root / "nested" / "status" / "vector_export_status.txt"
            pop = root / "pop_vout.txt"
            parsed = root / "parsed.json"
            pop.write_text("time\tVOUT\n0\t1.2\n1e-9\t1.201\n", encoding="utf-8")

            make_proc = subprocess.run(
                [
                    sys.executable,
                    str(cli),
                    "make-vector-export",
                    "--schematic",
                    str(root / "VRAMPValley.sxsch"),
                    "--out-dir",
                    str(root / "vectors"),
                    "--out",
                    str(script),
                    "--status-file",
                    str(status),
                    "--vector",
                    "simplis_pop1:#VOUT",
                    "--vector",
                    "simplis_ac1:46",
                ],
                check=False,
                text=True,
                capture_output=True,
            )
            parse_proc = subprocess.run(
                [
                    sys.executable,
                    str(cli),
                    "parse-show",
                    str(pop),
                    "--out",
                    str(parsed),
                ],
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(make_proc.returncode, 0, make_proc.stderr)
            self.assertIn("SetGroup simplis_pop1", script.read_text(encoding="utf-8"))
            self.assertTrue((root / "vectors").is_dir())
            self.assertTrue(status.parent.is_dir())
            self.assertEqual(parse_proc.returncode, 0, parse_proc.stderr)
            parsed_data = json.loads(parsed.read_text(encoding="utf-8"))
            self.assertEqual(parsed_data[0]["y"], [1.2, 1.201])

    def test_helper_cli_creates_output_directories(self) -> None:
        helper = Path(__file__).resolve().parents[1] / "scripts" / "simetrix_waveforms.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "nested" / "scripts" / "export.sxscr"
            status = root / "nested" / "status" / "vector_export_status.txt"
            out_dir = root / "nested" / "vectors"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(helper),
                    "make-export-script",
                    "--schematic",
                    str(root / "VRAMPValley.sxsch"),
                    "--out-dir",
                    str(out_dir),
                    "--script",
                    str(script),
                    "--status-file",
                    str(status),
                    "--vector",
                    "simplis_pop1:#VOUT",
                ],
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(script.exists())
            self.assertTrue(out_dir.is_dir())
            self.assertTrue(status.parent.is_dir())


if __name__ == "__main__":
    unittest.main()
