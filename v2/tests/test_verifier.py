from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


V2_ROOT = Path(__file__).resolve().parents[1]
if str(V2_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(V2_ROOT / "src"))

from simplis_automation_v2.catalog import load_catalog
from simplis_automation_v2.io import sha256_file
from simplis_automation_v2.verifier import MANIFEST_SCHEMA, _validate_expected_netlist, verify_manifest


class VerifierTests(unittest.TestCase):
    def test_directive_only_probe_is_verified_by_its_generated_directive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            netlist = Path(temporary) / "probe.net"
            netlist.write_text('.PRINT V(#OUT)\n.GRAPH "db(:#OUT/:#IN)" colourname=PBODE_bode_color\n', encoding="utf-8")
            expected = {
                "components": [
                    {"id": "bode", "ref": "PBODE", "netlist_presence": "directive", "pins": {"OUT": "OUT", "IN": "IN"}}
                ],
                "nets": {"OUT": ["bode.OUT"], "IN": ["bode.IN"]},
            }
            self.assertEqual(_validate_expected_netlist(netlist, expected), [])

    def _fixture(self, root: Path, *, opaque: bool = False) -> tuple[Path, dict[str, object], Path, Path]:
        executable = root / "SIMetrix830" / "bin64" / "SIMetrix.exe"
        symbols = root / "SIMetrix830" / "support" / "symbollibs"
        executable.parent.mkdir(parents=True)
        symbols.mkdir(parents=True)
        executable.write_text("stub", encoding="utf-8")
        (symbols / "ideal.sxslb").write_text("library", encoding="utf-8")
        catalog = root / "catalog.lock.yaml"
        catalog.write_text(
            "\n".join(
                (
                    "schema_version: simplis-automation/v2/catalog",
                    "runtime: {}",
                    "devices:",
                    "  - kind: resistor",
                    "    symbol: {name: res}",
                    "    pins: [{name: p}, {name: n}]",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        build = root / "build"
        build.mkdir()
        script = build / "create.sxscr"
        script.write_text("NewSchem /simulator SIMPLIS test\nQuit\n", encoding="utf-8")
        schematic = build / "test.sxsch"
        netlist = build / "test.net"
        manifest = {
            "schema_version": MANIFEST_SCHEMA,
            "build_dir": str(build),
            "catalog": {"path": str(catalog), "fingerprint": load_catalog(catalog)["fingerprint"]},
            "artifacts": {"script": str(script), "schematic": str(schematic), "netlist": str(netlist)},
            "expected": {
                "components": [{"id": "r1", "ref": "R1", "kind": "resistor", "pins": {"p": "VIN", "n": "0"}, "properties": {}}],
                "nets": {"VIN": ["r1.p"], "0": ["r1.n"]},
            },
            "classification": {"static_valid": True, "fully_ideal": not opaque, "opaque_modules": ["vendor"] if opaque else []},
            "script_sha256": sha256_file(script),
        }
        manifest_path = build / "build-manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        runtime = {"executable": str(executable), "symbol_library": str(symbols), "version": "8.3"}
        return manifest_path, runtime, schematic, netlist

    def _runner(self, schematic: Path, netlist: Path, *, create_netlist: bool = True):
        calls: list[list[str]] = []

        def runner(command, *, cwd, timeout, interactive, stage):
            calls.append(list(command))
            if stage == "create_schematic":
                schematic.write_text("editable schematic", encoding="utf-8")
            elif stage == "netlist" and create_netlist:
                netlist.write_text("R1 VIN 0 1k\n", encoding="utf-8")
                status = Path(cwd) / "netlist-status.txt"
                status.write_text("completion_token=true\nnetlist_completed=true\n", encoding="utf-8")
            return {"returncode": 0, "stdout": "", "stderr": ""}

        return runner, calls

    def test_verify_requires_both_schematic_and_actual_netlist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, runtime, schematic, netlist = self._fixture(Path(temporary))
            runner, calls = self._runner(schematic, netlist)
            result = verify_manifest(manifest, runtime=runtime, runner=runner)
            self.assertTrue(result["ok"])
            self.assertEqual(result["classification"], "fully_ideal")
            self.assertEqual(result["stages"]["create_schematic"]["status"], "passed")
            self.assertEqual(result["stages"]["netlist"]["status"], "passed")
            self.assertEqual(len(calls), 2)
            self.assertTrue(all(call[-1].endswith(".sxscr") for call in calls))
            netlist_script = (manifest.parent / "verification" / "verify-netlist.sxscr").read_text(encoding="utf-8")
            self.assertIn("RedirectMessages dup", netlist_script)
            self.assertIn("Echo script_started=true", netlist_script)
            self.assertLess(netlist_script.index("Echo netlist_completed=true"), netlist_script.index("Echo completion_token=true"))
            self.assertTrue((manifest.parent / "verification-status.json").is_file())
            self.assertTrue((manifest.parent / "verification-evidence.json").is_file())

    def test_opaque_module_caps_classification_at_boundary_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, runtime, schematic, netlist = self._fixture(Path(temporary), opaque=True)
            runner, _calls = self._runner(schematic, netlist)
            result = verify_manifest(manifest, runtime=runtime, runner=runner)
            self.assertTrue(result["ok"])
            self.assertEqual(result["classification"], "boundary_verified")

    def test_missing_netlist_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, runtime, schematic, netlist = self._fixture(Path(temporary))
            runner, _calls = self._runner(schematic, netlist, create_netlist=False)
            result = verify_manifest(manifest, runtime=runtime, runner=runner)
            self.assertFalse(result["ok"])
            self.assertIn("netlist_missing", {item["code"] for item in result["errors"]})

    def test_changed_catalog_blocks_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, runtime, schematic, netlist = self._fixture(Path(temporary))
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            Path(payload["catalog"]["path"]).write_text(
                "\n".join(
                    (
                        "schema_version: simplis-automation/v2/catalog",
                        "runtime: {}",
                        "devices:",
                        "  - kind: resistor",
                        "    symbol: {name: res}",
                        "    pins: [{name: p}, {name: n}]",
                        "    approval: pending",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            runner, calls = self._runner(schematic, netlist)
            result = verify_manifest(manifest, runtime=runtime, runner=runner)
            self.assertFalse(result["ok"])
            self.assertIn("catalog_fingerprint_mismatch", {item["code"] for item in result["errors"]})
            self.assertEqual(calls, [])
