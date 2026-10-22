from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from simplis_automation_v2.experiment_runner import run_experiment
from simplis_automation_v2.cli import main
from simplis_automation_v2.run_storage import fresh_directory, protect, register_metrics, write_json
from simplis_automation_v2.runtime import normalize_version, resolve_runtime
from simplis_automation_v2.vectors import parse_show_file
from validate_v3_native import validate as validate_native


def validate(versions: list[str], out: Path) -> dict:
    fresh_directory(out)
    report = {"versions": {}, "scope": "opt-in real integration; unit discovery never launches the simulator"}
    for requested in versions:
        active = resolve_runtime(version=requested)
        entry = report["versions"][requested] = dict(runtime=active, status="not_installed_or_unconfirmed", checks={})
        if not active["ready"]:
            continue
        work = out / requested
        work.mkdir()
        try:
            validate_native(Path(active["executable"]), Path(active["simplis_exe"]), work / "native")
            entry["checks"]["native_roundtrip_hierarchy_preprocess_transient"] = "passed"
            native_run = work / "default native 中文"
            with (work / "native-cli.json").open("w", encoding="utf-8") as stream, redirect_stdout(stream):
                status = main(["native", str(work / "native/native/feedback.sxsch"), "--out-dir", str(native_run),
                               "--version", requested, "--vector", "#VOUT"])
            assert status == 0, work / "native-cli.json"
            assert (native_run / "storage.json").is_file()
            entry["checks"]["default_native_cli_storage"] = "passed"
            if requested == "8.4":
                run = work / "v2"
                result = run_experiment(ROOT / "examples/hysteretic_control_experiment.yaml", run, runtime=active)
                assert result["ok"], result.get("errors")
                vector = parse_show_file(run / "vectors/simplis_tran1_vout.txt")
                steady = [v for t, v in zip(vector["x"], vector["y"]) if t >= 60e-6]
                assert 1.79 < min(steady) < 1.81 and 2.19 < max(steady) < 2.21
                register_metrics(run, dict(steady_min_V=min(steady), steady_max_V=max(steady), samples=len(steady)))
                protect(run, "Version compatibility transient and source evidence")
                entry["checks"]["default_v2_yaml_storage_and_control_waveform"] = "passed"
            else:
                entry["checks"]["v2_advanced_behavior"] = "unverified; provide an 8.30-qualified behavioral fixture"
            entry["status"] = "passed"
        except Exception as exc:
            entry.update(status="failed", error=repr(exc))
        finally:
            write_json(out / "compatibility.json", report)
    write_json(out / "compatibility.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Explicit installed-version integration; missing versions remain unverified")
    parser.add_argument("--version", action="append", help="8.30 or 8.40; omit to inspect both")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    versions = [normalize_version(v) for v in args.version] if args.version else ["8.3", "8.4"]
    if any(v not in {"8.3", "8.4"} for v in versions):
        parser.error("Only 8.30 and 8.40 are supported")
    report = validate(versions, args.out_dir.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(1 if any(v["status"] == "failed" for v in report["versions"].values()) else
                     2 if args.version and any(v["status"] != "passed" for v in report["versions"].values()) else 0)
