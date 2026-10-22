from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
from simplis_tools import run_deck, waveform
from build_native_examples import build
from run_storage import cleanup, protect, register_metrics
from simplis_automation_v2.importer import _parse_instances_and_wires
from simplis_automation_v2.runtime import startup_command
from simplis_automation_v2.watchdog import captured_script_error_lines


def validate(gui: Path, solver: Path, out: Path):
    out = out.resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("Use a fresh validation directory")
    native = out / "native"
    build(native)
    for path in native.iterdir():
        assert path.read_bytes() == (ROOT / "examples/native" / path.name).read_bytes(), path.name
    shutil.copyfile(ROOT / "assets/device-reference/devices.sxsch", native / "reference.sxsch")
    script = out / "check.sxscr"
    lines = [f'RedirectMessages dup "{out / "native.log"}"']
    for name in ("reference", "simple_rc", "controller", "plant", "feedback"):
        extension = ".sxcmp" if name in {"controller", "plant"} else ".sxsch"
        path = native / (name + extension)
        lines.extend([f'OpenSchem /cd "{path}"', 'Focus schem', 'Zoom full',
                      f'CopyClipSchem /file "{out / (name + ".png")}" /format png /vp 1800 1000'])
        if extension == ".sxsch":
            lines.append(f'SaveAs /force "{path}"')
        if name in {"simple_rc", "feedback"}:
            lines.append(f'Netlist /simplis "{out / (name + ".net")}"')
            lines.append(f'PreProcessNetlist "{out / (name + ".net")}" "{out / (name + ".deck")}"')
        lines.append('CloseSheet /force')
    lines.extend([f"Let ff=OpenEchoFile('{out / 'complete.txt'}','w')", 'Echo completed',
                  'Let ff=CloseEchoFile()', 'RedirectMessages flush', 'RedirectMessages off', 'Quit'])
    script.write_text("\n".join(lines).replace("\\", "/") + "\n", encoding="utf-8")
    startup = None
    if sys.platform == "win32":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
    # 不传 /i，依照 8.40 UsersManual 第18.3节启动独立实例
    process = subprocess.Popen(startup_command(gui, script), cwd=out, startupinfo=startup)
    deadline = time.monotonic() + 60
    while not (out / "complete.txt").exists() and time.monotonic() < deadline:
        time.sleep(.5)
    if not (out / "complete.txt").exists():
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        raise RuntimeError(f"Native validation timeout; inspect {out / 'native.log'}")
    process.wait(timeout=15)
    errors = captured_script_error_lines(out / "native.log")
    if errors:
        raise RuntimeError(f"Inspect native errors: {errors}")
    for name in ("simple_rc", "feedback"):
        instances, wires, diagnostics = _parse_instances_and_wires((native / (name + ".sxsch")).read_text(encoding="utf-8"))
        assert instances and wires and not diagnostics, diagnostics
    # 针对固定的 8.40 示例检查真实网表中的连接，节点编号变化时需要重新检查
    rc_lines = (out / "simple_rc.net").read_text().splitlines()
    assert all(line in rc_lines for line in ("C1 4 0 1u", "R1 3 4 1k", "V1 3 0 1")), rc_lines
    fb_lines = (out / "feedback.net").read_text().splitlines()
    assert all(line in fb_lines for line in (
        "XCTRL 5 7 6 0 controller", "XPLANT 6 7 0 plant", ".subckt controller 5 7 6 8",
        "E1 6 8 5 7 9", ".subckt plant 4 5 6", "C1 5 6 1u", "R1 4 5 1k")), fb_lines
    results = {}
    for name in ("simple_rc", "feedback"):
        netlist = out / (name + ".deck")
        result = run_deck(solver, netlist, out / (name + "_run"), environment={
            "simulator_version": "8.40", "models": "native R/C/DC/VCVS; no external models",
            "parameters": {"R_ohm": 1000, "C_F": 1e-6, "Vref_V": 1, "controller_gain": 9 if name == "feedback" else None}})
        assert result["completed"], result
        data = waveform(out / (name + "_run/input.ckt.t2"))
        from simplis_tools import read_t2
        columns, rows = read_t2(out / (name + "_run/input.ckt.t2"))
        target = .9 if name == "feedback" else 1
        assert len(columns) == 2, columns
        actual = rows[-1][1]
        assert abs(actual - target) < .008, (name, actual)
        assert abs(rows[0][1]) < 1e-9, rows[0]
        tau = .0001 if name == "feedback" else .001
        tau_point = min(rows, key=lambda row: abs(row[0] - tau))
        assert abs(tau_point[1] - target * (1 - math.exp(-1))) < .003, tau_point
        register_metrics(out / (name + "_run"), dict(final_V=actual, theory_target_V=target, waveform=data))
        saved_instances, _, _ = _parse_instances_and_wires((native / (name + ".sxsch")).read_text(encoding="utf-8"))
        results[name] = dict(final_V=actual, expected_asymptote_V=target, native_instances=len(saved_instances),
                             initial_V=rows[0][1], tau_s=tau, voltage_at_tau_V=tau_point[1], waveform_points=len(rows))
    protect(out / "feedback_run", "Representative hierarchy and feedback evidence")
    assert cleanup(out / "feedback_run")["reclaimable_bytes"] == 0
    from simplis_automation_v2.runtime import resolve_runtime
    report = dict(native_open_save_export=True, exported_topology_checked=True,
                  runtime=resolve_runtime(simetrix_exe=gui), measurements=results)
    (out / "validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Opt-in REAL native integration; never runs under unittest discovery")
    parser.add_argument("--gui", type=Path, required=True)
    parser.add_argument("--solver", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    validate(args.gui.resolve(), args.solver.resolve(), args.out_dir)
