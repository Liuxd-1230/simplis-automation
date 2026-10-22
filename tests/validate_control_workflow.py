from __future__ import annotations

import argparse
from copy import deepcopy
import json
import shlex
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
from build_native_examples import Sheet, interface
from run_managed import run_native, run_yaml
from run_storage import cleanup, load_run, protect, raw_retention, register_metrics, write_json
from simplis_automation_v2.catalog import load_embedded_symbol
from simplis_automation_v2.compiler import _component_layout, _pin_term_orientation, _routing_plan
from simplis_automation_v2.vectors import parse_show_file
from simplis_automation_v2.watchdog import run_script_with_watchdog


def native_batch(gui: Path, root: Path, commands: list[str]):
    root.mkdir(parents=True, exist_ok=False)
    status, log = root / "status.txt", root / "native.log"
    script = root / "commands.sxscr"
    lines = [f'RedirectMessages dup "{log}"', *commands,
             f"Let ff=OpenEchoFile('{status}','w')", 'Echo completion_token=true',
             'Let ff=CloseEchoFile()', 'RedirectMessages flush', 'RedirectMessages off', 'Quit']
    script.write_text("\n".join(lines).replace("\\", "/") + "\n", encoding="utf-8")
    result = run_script_with_watchdog(simetrix_exe=gui, script=script, status_file=status,
                                      message_log=log, work_dir=root, job_id=root.name, assess_completion=False)
    assert result["ok"], result


def make_native(project: Path):
    project.mkdir()
    parts = [
        ("low", "dc_source", "VLOW", {"P": "LOW", "N": "0"}, {"VALUE": "1.8"}, 0, 0),
        ("high", "dc_source", "VHIGH", {"P": "HIGH", "N": "0"}, {"VALUE": "2.2"}, 1, 0),
        ("set", "SIMPLIS_DIGI1_COMP_Y", "USET", {"OUT": "SET", "RTN": "0", "INP": "LOW", "INN": "VOUT"}, {"DELAY": "2n", "HYSTWD": ".002"}, 0, 1),
        ("reset", "SIMPLIS_DIGI1_COMP_Y", "URESET", {"OUT": "RESET", "RTN": "0", "INP": "VOUT", "INN": "HIGH"}, {"DELAY": "2n", "HYSTWD": ".002"}, 1, 1),
        ("latch", "SIMPLIS_DIGI1_SRLATCH_1_NONE_Y", "ULATCH", {"Q": "Q", "RTN": "0", "S": "SET", "R": "RESET"}, {"IC": "1", "OUT_DELAY": "2n"}, 0, 2),
        ("driver", "SIMPLIS_DIGI1_BUF_Y", "UDRIVER", {"OUT": "DRIVE", "RTN": "0", "IN1": "Q"}, {"DELAY": "5n"}, 0, 3),
    ]
    source, resolved = [], []
    for identity, symbol, ref, pins, values, row, column in parts:
        source.append(dict(id=identity, pins=pins, group="control", block="logic", layout=dict(row=row, col=column)))
        record = load_embedded_symbol(ROOT / "assets/device-reference/devices.sxsch", symbol)
        resolved.append(dict(id=identity, kind=symbol, symbol_record=record, pins=pins,
                             properties={"REF": ref, **values}, preferred_orientation="N0"))
    placements, evidence = _component_layout({"components": source, "layout": {"mode": "auto"}}, resolved)
    routing = _routing_plan(resolved, placements)
    control = Sheet()
    resolved_by_id = {part["id"]: part for part in resolved}
    for identity, symbol, ref, pins, values, _, _ in parts:
        position = placements[identity]
        control.instance(symbol, ref, position["x"], position["y"], position["orientation"], **values)
    local = {(p["component"], p["pin"]) for entries in routing["local_wires"].values() for p in entries}
    for segments in routing["local_wire_segments"].values():
        for left, right in segments:
            control.wire(left, right)
    for identity, _, ref, pins, _, _, _ in parts:
        for pin, net in pins.items():
            point = control.pins[ref, pin]
            if net == "0":
                control.ground(*point)
            elif (identity, pin) not in local:
                control.label(net, *point, _pin_term_orientation(resolved_by_id[identity], placements[identity]["orientation"], pin))
    ports = [("VOUT", "USET", "INN", 0, 360), ("DRIVE", "UDRIVER", "OUT", 1680, 360),
             ("SET", "USET", "OUT", 1680, 720), ("RESET", "URESET", "OUT", 1680, 960),
             ("Q", "ULATCH", "Q", 1680, 1200)]
    extent = evidence["objects"][0]["bbox"]
    for index, (net, ref, pin, _, _) in enumerate(ports):
        identity = next(p[0] for p in parts if p[2] == ref)
        if (identity, pin) in local:
            control.label(net, *control.pins[ref, pin], _pin_term_orientation(resolved_by_id[identity], placements[identity]["orientation"], pin))
        x = extent["min_x"] - 960 if index == 0 else extent["max_x"] + 1440
        y = extent["min_y"] + 360 + max(0, index - 1) * 720
        orient = "N0" if index == 0 else "N180"
        control.port(net, x, y, orient)
        terminal = (x - 720, y) if index == 0 else (x + 720, y)
        control.wire((x, y), terminal)
        control.label(net, *terminal, "N180" if index == 0 else "N0")
    control.save(project / "controller.sxcmp", interface("Comparator latch driver", [(p[0], p[3], p[4]) for p in ports]))
    write_json(project / "layout.json", dict(layout=evidence, routing=routing))

    plant = Sheet()
    plant.instance("res", "R1", 720, 360, "N270", VALUE="1k")
    plant.instance("cap", "C1", 1920, 360, VALUE="10n")
    plant.port("DRIVE", 0, 360)
    plant.port("VOUT", 2880, 360, "N180")
    plant.wire((0, 360), plant.pins["R1", "P"])
    plant.wire(plant.pins["R1", "N"], plant.pins["C1", "P"], (2880, 360))
    plant.wire(plant.pins["C1", "N"], (1920, 1080))
    plant.ground(1920, 1080)
    plant.save(project / "plant.sxcmp", interface("RC plant", [("DRIVE", 0, 360), ("VOUT", 1680, 360)]))

    parent = Sheet()
    parent.module("controller.sxcmp", "XCTRL", "Controller", 1920)
    parent.module("plant.sxcmp", "XPLANT", "RC plant", 6000)
    parent.wire((3600, 360), (6000, 360))
    parent.label("DRIVE", 4560, 360)
    parent.wire((7680, 360), (8280, 360))
    parent.label("VOUT", 8280, 360)
    parent.instance("res", "RLOAD", 8640, 720, VALUE="100k")
    parent.wire((8280, 360), (8640, 360), (8640, 720))
    parent.ground(8640, 1080)
    for label, y in (("SET", 720), ("RESET", 960), ("Q", 1200)):
        parent.wire((3600, y), (4320, y))
        parent.label(label, 4320, y)
    crossed = deepcopy(parent)
    parent.wire((8280, 360), (8280, 2160), (1440, 2160), (1440, 360), (1920, 360))
    # 两条不同网络的线在此交叉；交点没有导线端点，用实际网表和运行结果检查是否被误连接
    crossed.wire((8280, 360), (8280, 2160), (4800, 2160), (4800, -600), (1440, -600), (1440, 360), (1920, 360))
    cards = r".OPTIONS PSP_NPT=10001\n.INIT V(XPLANT.C1)=0\n.TRAN 100u 0\n.PRINT V(#VOUT) V(#DRIVE) V(#SET) V(#RESET) V(#Q)"
    parent.save(project / "control.sxsch", analysis=cards)
    crossed.save(project / "crossing.sxsch", analysis=cards)


def metrics(run: Path, *, native: bool):
    vectors = {name: parse_show_file(run / "execution/vectors" / f"simplis_tran1_{name}.txt")
               for name in ("vout", "drive", "q", "set", "reset")}
    output = vectors["vout"]
    steady = [v for t, v in zip(output["x"], output["y"]) if t >= 20e-6]
    drive = vectors["drive"]
    edges = [b for a, b, x, y in zip(drive["x"], drive["x"][1:], drive["y"], drive["y"][1:])
             if x < 2.5 <= y and b > 20e-6]
    assert 1.77 < min(steady) < 1.82 and 2.18 < max(steady) < 2.23, (min(steady), max(steady))
    assert len(edges) > 10, edges
    frequency = (len(edges) - 1) / (edges[-1] - edges[0])
    for name in ("set", "reset", "q"):
        assert max(vectors[name]["y"]) - min(vectors[name]["y"]) >= .9, name
    result = dict(vout_min_V=min(steady), vout_max_V=max(steady), drive_frequency_Hz=frequency,
                  drive_edges=len(edges), workflow="native" if native else "v2-yaml")
    register_metrics(run, result)
    return result


def check_native_ports(path: Path, load: str = "100k"):
    lines = path.read_text().splitlines()
    top = lines[:next(i for i, line in enumerate(lines) if line.startswith(".subckt"))]
    tokens = [shlex.split(line) for line in top if line and not line.startswith("*")]
    aliases = {row[1]: row[2] for row in tokens if row[0] == ".node_map"}
    names = ("VOUT", "DRIVE", "SET", "RESET", "Q")
    assert len({aliases[n] for n in names}) == len(names), aliases
    assert ["XCTRL", *(aliases[n] for n in names), "controller"] in tokens
    assert ["XPLANT", aliases["DRIVE"], aliases["VOUT"], "plant"] in tokens
    assert ["RLOAD", aliases["VOUT"], "0", load] in tokens
    # 子模块固定器件连接在此检查；顶层编号由实际 NODE_MAP 解析，允许正常重编号
    assert all(line in lines for line in (".subckt controller 24 25 26 28 27", "C1 5 0 10n", "R1 4 5 1k")), lines
    assert any(line.startswith("X$ULATCH 27 20 0 26 28 ") for line in lines)
    assert any(line.startswith("X$UDRIVER 25 21 0 27 ") for line in lines)


def validate(gui: Path, install: Path, out: Path):
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    runtime = dict(executable=str(gui), symbol_library=str(install / "support/symbollibs"))
    original = (ROOT / "examples/hysteretic_control.yaml").read_bytes()
    v2 = out / "v2-run"
    result = run_yaml(ROOT / "examples/hysteretic_control_experiment.yaml", v2, runtime=runtime)
    assert result["completed"], result
    assert result["source"]["kind"] == "v2-yaml"
    assert (ROOT / "examples/hysteretic_control.yaml").read_bytes() == original
    # v2 使用 S/R 角色名，文件名由原生网络名称决定，仍为 set/reset
    baseline = metrics(v2, native=False)
    project = out / "native-source"
    make_native(project)
    native_results = {}
    for name in ("control", "crossing"):
        run = out / (name + "-run")
        result = run_native(project / (name + ".sxsch"), run, runtime=runtime,
                            vectors=["#VOUT", "#DRIVE", "#Q", "#SET", "#RESET"])
        assert result["completed"], result
        check_native_ports(run / f"execution/source/SIMPLIS_Data/{name}.net")
        native_results[name] = metrics(run, native=True)
        assert abs(native_results[name]["drive_frequency_Hz"] / baseline["drive_frequency_Hz"] - 1) < .01
    preview = out / "previews"
    preview.mkdir()
    commands = []
    for name, path in (("v2", v2 / "execution/build/hysteretic_control.sxsch"),
                       ("native", project / "control.sxsch"), ("controller", project / "controller.sxcmp")):
        commands += [f'OpenSchem /cd "{path}"', 'Focus schem', 'Zoom full',
                     f'CopyClipSchem /file "{preview / (name + ".png")}" /format png /vp 2600 1800', 'CloseSheet /force']
    commands += [f'OpenSchem /cd "{project / "control.sxsch"}"', 'Unselect', 'Select /prop ref RLOAD',
                 'Prop VALUE 20k', f'SaveAs /force "{project / "control.sxsch"}"',
                 f'Netlist /simplis "{preview / "edited.net"}"', 'CloseSheet /force']
    native_batch(gui, out / "edit-check", commands)
    edited = out / "edited-run"
    result = run_native(project / "control.sxsch", edited, runtime=runtime,
                        vectors=["#VOUT", "#DRIVE", "#Q", "#SET", "#RESET"])
    assert result["completed"], result
    changed = metrics(edited, native=True)
    assert abs(changed["drive_frequency_Hz"] / native_results["control"]["drive_frequency_Hz"] - 1) > .01
    assert (ROOT / "examples/hysteretic_control.yaml").read_bytes() == original
    check_native_ports(preview / "edited.net", "20k")
    bad = project / "invalid_time.sxsch"
    bad.write_text((project / "control.sxsch").read_text().replace(".TRAN 100u 0", ".TRAN -100u 0"), encoding="utf-8")
    failure = out / "failed-native-run"
    failed = run_native(bad, failure, runtime=runtime, vectors=["#VOUT"])
    assert not failed["completed"] and load_run(failure)["kind"] == "diagnostic", failed
    cleanup(failure, apply=True)
    assert (failure / "run.json").exists() and (failure / "execution/native.log").exists()
    for run in (v2, out / "control-run", out / "crossing-run", edited):
        assert not any(f["action"] == "would_delete" and f["path"].startswith("execution/vectors") for f in cleanup(run)["files"])
    raw_retention(v2, ["execution/vectors/simplis_tran1_set.txt"], discard=True,
                  reason="Disposable redundant digital export; native representative SET/Q timing retained")
    cleanup(v2, apply=True)
    assert (v2 / "execution/vectors/simplis_tran1_vout.txt").exists()
    assert not (v2 / "execution/vectors/simplis_tran1_set.txt").exists()
    for run in (out / "control-run", out / "crossing-run", edited):
        protect(run, "Control, hierarchy/crossing or native edit evidence")
    report = dict(v2=baseline, native=native_results, edited=changed,
                  original_yaml_unchanged=True, native_source_modified_by_native_property_editor=True,
                  selective_raw_cleanup=True, comparator_latch_driver_feedback=True,
                  exported_port_mapping_checked=True, real_native_failure_preserved=True)
    write_json(out / "validation.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Opt-in real control layout, hierarchy, editing and storage validation")
    parser.add_argument("--gui", type=Path, required=True)
    parser.add_argument("--install", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    validate(args.gui.resolve(), args.install.resolve(), args.out_dir)
