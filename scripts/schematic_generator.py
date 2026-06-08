#!/usr/bin/env python3
"""Generate SIMetrix/SIMPLIS schematics from a structured YAML/JSON spec."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime_config import resolve_simetrix_exe, resolve_symbol_lib_dir

GROUND_NETS = {"0", "GND", "gnd", "gnd!", "GROUND"}
SYMBOL_ALLOWLIST = {
    "dc_source",
    "res",
    "cap",
    "ind",
    "gnd",
    "term",
    "probev_new",
    "probei_new",
    "InlineCurrentProbe",
    "nmos",
    "pmos",
    "nmos_a3_vss_new",
    "pmos_a3_vdd_new",
    "nmos_a_new",
    "pmos_a_new",
    "dio",
    "diode_schottky",
    "ADI_Diode",
    "switch_1",
    "simplis_prim_vcswitch",
    "comp",
    "comp_2",
    "vcvs_2",
    "vccs_2",
    "avsrc",
    "aisrc",
    "vwave_v2",
    "and2",
    "and3",
    "or2",
    "or3",
    "inv",
    "inv_d",
    "PERIODIC_OP_V8",
    "SR_FlipFlop_prim",
    "d_latch",
}


@dataclass(frozen=True)
class Pin:
    name: str
    order: int
    x: int
    y: int


@dataclass
class Symbol:
    name: str
    source: Path
    pins: dict[str, Pin]
    properties: dict[str, str]


@dataclass
class DevicePlacement:
    ref: str
    symbol: str
    x: int
    y: int
    orient: str
    props: dict[str, Any]
    group: str


def load_config(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    if suffix == ".json":
        return json.loads(text)
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise SystemExit("YAML config requires PyYAML. Use JSON or install PyYAML.") from exc
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise SystemExit(f"Config root must be a mapping: {path}")
        return data
    raise SystemExit(f"Unsupported config extension {suffix!r}; use .json, .yaml, or .yml")


def normalize_orient(value: Any) -> str:
    if value is None:
        return "0"
    text = str(value)
    if text.startswith(("N", "M")):
        return text
    if text in {"0", "90", "180", "270"}:
        return text
    raise ValueError(f"Unsupported orientation {value!r}; use 0/90/180/270 or N0/N90/N180/N270")


def orient_to_instance(value: str) -> str:
    text = normalize_orient(value)
    if text.startswith("N"):
        return text[1:]
    return text


def orient_to_schematic(value: str) -> str:
    text = normalize_orient(value)
    if text.startswith(("N", "M")):
        return text
    return f"N{text}"


def transform_pin(pin: Pin, orient: str) -> tuple[int, int]:
    text = orient_to_schematic(orient)
    x, y = pin.x, pin.y
    mirror = text.startswith("M")
    angle_text = text[1:] if text.startswith(("N", "M")) else text
    if mirror:
        x = -x
    angle = int(angle_text)
    if angle == 0:
        return x, y
    if angle == 90:
        return -y, x
    if angle == 180:
        return -x, -y
    if angle == 270:
        return y, -x
    raise ValueError(f"Unsupported orientation angle: {orient!r}")


def quote_sxscr(value: Any) -> str:
    text = str(value).replace('"', '\\"')
    return f'"{text}"' if any(ch.isspace() for ch in text) else text


def quote_simetrix_string(value: Any) -> str:
    text = str(value)
    if any(ch in text for ch in ("'", "\r", "\n")):
        raise ValueError(f"SIMetrix script string contains unsupported characters: {text!r}")
    return f"'{text}'"


def parse_properties(lines: list[str]) -> dict[str, str]:
    props: dict[str, str] = {}
    for line in lines:
        match = re.search(r'Property\s+name="([^"]+)"\s+value="([^"]*)"', line)
        if match:
            props[match.group(1)] = match.group(2)
    return props


def parse_pins(lines: list[str]) -> dict[str, Pin]:
    pins: dict[str, Pin] = {}
    for line in lines:
        match = re.search(r'Pin\s+name="([^"]+)"\s+order=([0-9]+)\s+x=(-?[0-9]+)\s+y=(-?[0-9]+)', line)
        if match:
            name = match.group(1)
            pins[name] = Pin(name=name, order=int(match.group(2)), x=int(match.group(3)), y=int(match.group(4)))
    return pins


def parse_symbol_libraries(symbol_dir: Path) -> dict[str, Symbol]:
    symbols: dict[str, Symbol] = {}
    if not symbol_dir.exists():
        raise SystemExit(f"SIMPLIS symbol library directory not found: {symbol_dir}")
    attr_re = re.compile(r'Attributes\b.*\bname="([^"]+)"')
    for lib in sorted(symbol_dir.glob("*.sxslb")):
        current_name: str | None = None
        current_lines: list[str] = []
        in_symbol = False
        for raw in lib.read_text(encoding="utf-8", errors="replace").splitlines():
            if raw.strip() == ".Symbol":
                current_name = None
                current_lines = []
                in_symbol = True
                continue
            if raw.strip() == ".EndSymbol":
                if current_name and current_name not in symbols:
                    pins = parse_pins(current_lines)
                    props = parse_properties(current_lines)
                    symbols[current_name] = Symbol(current_name, lib, pins, props)
                current_name = None
                current_lines = []
                in_symbol = False
                continue
            if in_symbol:
                current_lines.append(raw)
                if current_name is None:
                    match = attr_re.search(raw)
                    if match:
                        current_name = match.group(1)
    return symbols


def get_design_paths(config: dict[str, Any], out_dir: Path) -> dict[str, Path]:
    design = config.get("design", {})
    name = str(design.get("name", "generated_simplis_design"))
    schematic_name = str(design.get("schematic", f"{name}.sxsch"))
    netlist_name = str(design.get("netlist", f"{Path(schematic_name).stem}.net"))
    script_name = str(design.get("script", f"{Path(schematic_name).stem}.sxscr"))
    run_name = str(design.get("run_script", "run.sxscr"))
    metrics_name = str(config.get("metrics", {}).get("output", "metrics.txt"))
    deck_name = f"{Path(netlist_name).stem}.deck"
    return {
        "script": out_dir / script_name,
        "schematic": out_dir / schematic_name,
        "netlist": out_dir / netlist_name,
        "netlist_script": out_dir / "netlist_after_analysis.sxscr",
        "deck": out_dir / deck_name,
        "run_script": out_dir / run_name,
        "metrics": out_dir / metrics_name,
    }


def order_groups(config: dict[str, Any], devices: list[dict[str, Any]]) -> list[str]:
    layout = config.get("layout", {})
    groups = layout.get("groups")
    if groups:
        return [str(item) for item in groups]
    seen: list[str] = []
    for dev in devices:
        group = str(dev.get("group", "default"))
        if group not in seen:
            seen.append(group)
    return seen or ["default"]


def default_orient(symbol: str, role: str | None) -> str:
    if role in {"high_side", "low_side", "switch", "mos"}:
        return "0"
    if symbol in {"res", "cap", "ind", "dc_source"}:
        return "0"
    return "0"


def compute_placements(config: dict[str, Any]) -> dict[str, DevicePlacement]:
    devices = config.get("devices")
    if not isinstance(devices, list) or not devices:
        raise SystemExit("Config must define a non-empty devices list")
    layout = config.get("layout", {})
    grid = int(layout.get("grid", 120))
    group_spacing = int(layout.get("group_spacing", 720))
    row_spacing = int(layout.get("row_spacing", 540))
    col_spacing = int(layout.get("col_spacing", 360))
    origin = layout.get("origin", [-720, -360])
    origin_x, origin_y = int(origin[0]), int(origin[1])
    groups = order_groups(config, devices)
    placements: dict[str, DevicePlacement] = {}
    group_counts = {group: 0 for group in groups}
    for dev in devices:
        if "ref" not in dev or "symbol" not in dev:
            raise SystemExit(f"Each device needs ref and symbol: {dev}")
        ref = str(dev["ref"])
        if ref in placements:
            raise SystemExit(f"Duplicate device ref: {ref}")
        symbol = str(dev["symbol"])
        group = str(dev.get("group", "default"))
        if group not in groups:
            groups.append(group)
            group_counts[group] = 0
        group_index = groups.index(group)
        index = group_counts[group]
        group_counts[group] += 1
        row = int(dev.get("row", index))
        col = int(dev.get("col", 0))
        x = int(dev.get("x", origin_x + group_index * group_spacing + col * col_spacing))
        y = int(dev.get("y", origin_y + row * row_spacing))
        x = int(round(x / grid) * grid)
        y = int(round(y / grid) * grid)
        orient = normalize_orient(dev.get("orient", default_orient(symbol, dev.get("role"))))
        props = dict(dev.get("props", {}))
        if "VALUE" in dev and "VALUE" not in props:
            props["VALUE"] = dev["VALUE"]
        placements[ref] = DevicePlacement(ref=ref, symbol=symbol, x=x, y=y, orient=orient, props=props, group=group)
    return placements


def validate_config(config: dict[str, Any], symbols: dict[str, Symbol], placements: dict[str, DevicePlacement]) -> None:
    nets = config.get("nets", {})
    if not isinstance(nets, dict):
        raise SystemExit("nets must be a mapping from device ref to pin/net mapping")
    errors: list[str] = []
    for placement in placements.values():
        if placement.symbol not in symbols:
            errors.append(f"{placement.ref}: symbol {placement.symbol!r} not found in SIMPLIS libraries")
        elif placement.symbol not in SYMBOL_ALLOWLIST:
            print(f"warning: {placement.ref} uses non-allowlisted symbol {placement.symbol!r}; pin labels will still be generated", file=sys.stderr)
    for ref, pin_map in nets.items():
        if ref not in placements:
            errors.append(f"nets references unknown device {ref!r}")
            continue
        if not isinstance(pin_map, dict):
            errors.append(f"nets.{ref} must be a pin-to-net mapping")
            continue
        symbol = symbols.get(placements[ref].symbol)
        if not symbol:
            continue
        for pin_name in pin_map:
            if str(pin_name) not in symbol.pins:
                known = ", ".join(sorted(symbol.pins))
                errors.append(f"{ref}.{pin_name}: pin not found on {placements[ref].symbol}; known pins: {known}")
    for ref in placements:
        if ref not in nets and placements[ref].symbol not in {"gnd", "term"}:
            errors.append(f"{ref}: no nets mapping; add nets.{ref} or remove the device")
    if errors:
        raise SystemExit("Config validation failed:\n- " + "\n- ".join(errors))


def term_orientation_for_pin(dx: int, dy: int) -> str:
    if abs(dx) >= abs(dy):
        return "180" if dx > 0 else "0"
    return "270" if dy > 0 else "90"


def pin_abs_loc(placement: DevicePlacement, pin: Pin) -> tuple[int, int]:
    dx, dy = transform_pin(pin, placement.orient)
    return placement.x + dx, placement.y + dy


def build_create_script(config: dict[str, Any], symbols: dict[str, Symbol], placements: dict[str, DevicePlacement], paths: dict[str, Path], run_netlist: bool) -> str:
    design = config.get("design", {})
    simulator = str(design.get("simulator", "SIMPLIS"))
    title = str(design.get("name", "generated_simplis_design"))
    nets = config.get("nets", {})
    lines = [f"NewSchem /newWindow /simulator {simulator} {title}"]
    for placement in placements.values():
        args = ["Inst", "/loc", str(placement.x), str(placement.y), orient_to_instance(placement.orient), placement.symbol]
        for name, value in placement.props.items():
            args.extend([str(name), quote_sxscr(value)])
        lines.append(" ".join(args))
    term_count = 0
    gnd_count = 0
    for ref, pin_map in nets.items():
        placement = placements[ref]
        symbol = symbols[placement.symbol]
        for pin_name, net_value in pin_map.items():
            net = str(net_value)
            pin = symbol.pins[str(pin_name)]
            x, y = pin_abs_loc(placement, pin)
            if net in GROUND_NETS:
                gnd_count += 1
                lines.append(f"Inst /loc {x} {y} 0 gnd")
                continue
            dx, dy = transform_pin(pin, placement.orient)
            term_orient = term_orientation_for_pin(dx, dy)
            term_count += 1
            lines.append(f"Inst /loc {x} {y} {term_orient} term VALUE {quote_sxscr(net)}")
    if config.get("visual_stubs", False):
        lines.extend(build_visual_stubs(config, symbols, placements))
    lines.append(f'SaveAs /force "{paths["schematic"]}"')
    if run_netlist:
        lines.append(f'Netlist /simplis "{paths["netlist"]}"')
    if not config.get("keep_open", False):
        lines.append("Quit")
    lines.append("")
    return "\n".join(lines)


def build_netlist_script(config: dict[str, Any], paths: dict[str, Path]) -> str:
    lines = [
        f'OpenSchem "{paths["schematic"]}"',
        f'Netlist /simplis "{paths["netlist"]}"',
    ]
    if not config.get("keep_open", False):
        lines.append("Quit")
    lines.append("")
    return "\n".join(lines)


def build_visual_stubs(config: dict[str, Any], symbols: dict[str, Symbol], placements: dict[str, DevicePlacement]) -> list[str]:
    lines: list[str] = []
    nets = config.get("nets", {})
    stub_len = int(config.get("layout", {}).get("stub_length", 120))
    for ref, pin_map in nets.items():
        placement = placements[ref]
        symbol = symbols[placement.symbol]
        for pin_name in pin_map:
            pin = symbol.pins[str(pin_name)]
            x, y = pin_abs_loc(placement, pin)
            dx, dy = transform_pin(pin, placement.orient)
            if abs(dx) >= abs(dy):
                end_x = x + (stub_len if dx >= 0 else -stub_len)
                end_y = y
            else:
                end_x = x
                end_y = y + (stub_len if dy >= 0 else -stub_len)
            lines.append(f"Wire /loc {x} {y} {end_x} {end_y}")
    return lines


def build_run_script(config: dict[str, Any], paths: dict[str, Path]) -> str:
    metrics = config.get("metrics", {})
    vout = str(metrics.get("vout", "VOUT"))
    sw = str(metrics.get("sw", "SW"))
    target = float(metrics.get("target_vout", 0.0))
    lines = [
        f'PreProcessNetlist "{paths["netlist"]}" "{paths["deck"]}"',
        f'RunSIMPLIS /fresh "{paths["deck"]}"',
        f"Let echo_file = OpenEchoFile({quote_simetrix_string(paths['metrics'])}, {quote_simetrix_string('w')})",
        "Echo failed=false",
        f"Echo metric_source=generated_stub",
        f"Echo vout_net={vout}",
        f"Echo sw_net={sw}",
        f"Echo target_vout={target:.12g}",
        "Echo vout_avg=0",
        "Echo vout_dc_error_mv=0",
        "Echo vout_overshoot_mv=0",
        "Echo vout_undershoot_mv=0",
        "Echo vout_ripple_mv=0",
        "Echo recovery_time_us=0",
        "Echo fsw_mean_khz=0",
        "Echo fsw_jitter_pct=0",
        "Let close_result = CloseEchoFile()",
    ]
    if not config.get("keep_open", False):
        lines.append("Quit")
    lines.append("")
    return "\n".join(lines)


def force_enable_requested_transient(config: dict[str, Any], netlist: Path) -> bool:
    simulation = config.get("simulation", {})
    raw = simulation.get("analysis_text")
    if isinstance(raw, list):
        analysis_text = "\n".join(str(line) for line in raw)
    else:
        analysis_text = str(raw or "")
    if ".POP" not in analysis_text.upper() or ".TRAN" not in analysis_text.upper():
        return False
    if not netlist.exists():
        return False
    content = netlist.read_text(encoding="utf-8", errors="replace")
    updated = re.sub(r"(?im)^\*([ \t]*\.tran\b)", r"\1", content, count=1)
    if updated == content:
        return False
    netlist.write_text(updated, encoding="utf-8")
    return True


def resolve_pop_trigger_gate(netlist: Path) -> str | None:
    if not netlist.exists():
        return None
    content = netlist.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"(?im)^\.node_map\s+TRIG_GATE\s+(\S+)", content)
    if not match:
        return None
    trigger_node = match.group(1)
    instance_re = re.compile(r"(?im)^(X\S*)\s+([^\n]*?)\s+PERIODIC_OP(?:\s|$)")
    for inst_match in instance_re.finditer(content):
        ref = inst_match.group(1)
        nodes = inst_match.group(2).split()
        if trigger_node in nodes:
            trigger_gate = f"{ref}.!D_CYCLE"
            updated = re.sub(r"\{TRIG_GATE\}", trigger_gate, content)
            if updated != content:
                netlist.write_text(updated, encoding="utf-8")
            return trigger_gate
    return None


def run_simetrix(script: Path, timeout: float | None, interactive: bool, simetrix_exe: Path) -> int:
    if not simetrix_exe.exists():
        raise SystemExit(f"SIMetrix executable not found: {simetrix_exe}")
    cmd = [str(simetrix_exe)]
    if interactive:
        cmd.append("/i")
    cmd += ["/s", str(script)]
    try:
        return subprocess.run(cmd, cwd=str(script.parent), check=False, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        return 124


def parse_node_map(netlist: Path) -> set[str]:
    if not netlist.exists():
        return set()
    out = set()
    for line in netlist.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"\.node_map\s+(\S+)\s+", line)
        if match:
            out.add(match.group(1))
    return out


def wait_for_nonempty_file(path: Path, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists() and path.stat().st_size > 0:
            return
        time.sleep(0.1)


def validate_netlist(config: dict[str, Any], netlist: Path) -> dict[str, Any]:
    metrics = config.get("metrics", {})
    required = set(config.get("validation", {}).get("required_nets", []))
    for key in ("vin", "vout", "sw", "fb"):
        if key in metrics:
            required.add(str(metrics[key]))
    required.update(str(item) for item in config.get("validation", {}).get("key_nets", []))
    wait_for_nonempty_file(netlist)
    deadline = time.time() + 5.0
    node_map: set[str] = set()
    missing: list[str] = []
    while True:
        node_map = parse_node_map(netlist)
        missing = sorted(net for net in required if net not in node_map and net not in GROUND_NETS)
        if not missing or time.time() >= deadline:
            break
        time.sleep(0.2)
    return {
        "netlist": str(netlist),
        "node_map": sorted(node_map),
        "required_nets": sorted(required),
        "missing_required_nets": missing,
        "passed": not missing,
    }


def escape_schematic_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\r\n", "\n").replace("\n", "\\n")


def inject_analysis_text(config: dict[str, Any], schematic: Path) -> bool:
    simulation = config.get("simulation", {})
    raw = simulation.get("analysis_text")
    if raw is None:
        return False
    if isinstance(raw, list):
        text = "\n".join(str(line) for line in raw)
    else:
        text = str(raw)
    if not text.strip():
        return False
    wait_for_nonempty_file(schematic)
    if not schematic.exists():
        raise SystemExit(f"Schematic was not written before analysis text injection: {schematic}")
    content = schematic.read_text(encoding="utf-8", errors="replace")
    replacement = f'Text value="{escape_schematic_text(text)}"'
    if 'Text value=""' in content:
        content = content.replace('Text value=""', replacement, 1)
    else:
        content = content.replace(".EndSchematic", replacement + "\n.EndSchematic", 1)
    schematic.write_text(content, encoding="utf-8")
    return True


def generate_from_config(
    config_path: Path,
    out_dir: Path,
    *,
    run: bool = False,
    netlist_check: bool = False,
    metrics: bool = False,
    dry_run: bool = False,
    timeout: float | None = 60.0,
    interactive: bool = False,
    simetrix_exe: Path,
    runtime_config: str | Path | None = None,
    symbol_lib_dir: str | Path | None = None,
) -> dict[str, Any]:
    config = load_config(config_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    symbols = parse_symbol_libraries(resolve_symbol_lib_dir(symbol_lib_dir, config_path=runtime_config, simetrix_exe=simetrix_exe))
    placements = compute_placements(config)
    validate_config(config, symbols, placements)
    paths = get_design_paths(config, out_dir)
    create_script = build_create_script(config, symbols, placements, paths, run_netlist=False)
    paths["script"].write_text(create_script, encoding="utf-8")
    needs_netlist = netlist_check or run or metrics
    if needs_netlist:
        paths["netlist_script"].write_text(build_netlist_script(config, paths), encoding="utf-8")
    run_script_text = None
    if run or metrics:
        run_script_text = build_run_script(config, paths)
        paths["run_script"].write_text(run_script_text, encoding="utf-8")
    result: dict[str, Any] = {
        "config": str(config_path),
        "script": str(paths["script"]),
        "schematic": str(paths["schematic"]),
        "netlist": str(paths["netlist"]),
        "netlist_script": str(paths["netlist_script"]) if needs_netlist else None,
        "deck": str(paths["deck"]) if run_script_text else None,
        "run_script": str(paths["run_script"]) if run_script_text else None,
        "metrics": str(paths["metrics"]) if run_script_text else None,
        "placements": {ref: placement.__dict__ for ref, placement in placements.items()},
    }
    if dry_run:
        result["dry_run"] = True
        return result
    rc = run_simetrix(paths["script"], timeout=timeout, interactive=interactive, simetrix_exe=simetrix_exe)
    result["create_returncode"] = rc
    if rc != 0:
        result["failed"] = True
        return result
    result["analysis_text_injected"] = inject_analysis_text(config, paths["schematic"])
    if needs_netlist:
        netlist_rc = run_simetrix(paths["netlist_script"], timeout=timeout, interactive=interactive, simetrix_exe=simetrix_exe)
        result["netlist_returncode"] = netlist_rc
        if netlist_rc != 0:
            result["failed"] = True
            return result
        result["transient_force_enabled"] = force_enable_requested_transient(config, paths["netlist"])
        result["resolved_pop_trigger_gate"] = resolve_pop_trigger_gate(paths["netlist"])
        result["netlist_validation"] = validate_netlist(config, paths["netlist"])
        if not result["netlist_validation"]["passed"]:
            result["failed"] = True
            return result
    if run or metrics:
        run_rc = run_simetrix(paths["run_script"], timeout=timeout, interactive=interactive, simetrix_exe=simetrix_exe)
        result["run_returncode"] = run_rc
        result["failed"] = run_rc != 0
    else:
        result["failed"] = False
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate SIMPLIS schematics from YAML/JSON specs")
    parser.add_argument("--config", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--netlist-check", action="store_true")
    parser.add_argument("--metrics", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--simetrix-exe", help="Path to SIMetrix.exe; overrides runtime config")
    parser.add_argument("--runtime-config", help="JSON runtime config with simetrix_exe and symbol_lib_dir")
    parser.add_argument("--symbol-lib-dir", help="SIMPLIS symbol library directory; overrides runtime config")
    args = parser.parse_args(argv)
    result = generate_from_config(
        Path(args.config).resolve(),
        Path(args.out_dir).resolve(),
        run=args.run,
        netlist_check=args.netlist_check,
        metrics=args.metrics,
        dry_run=args.dry_run,
        timeout=args.timeout,
        interactive=args.interactive,
        simetrix_exe=resolve_simetrix_exe(args.simetrix_exe, config_path=args.runtime_config),
        runtime_config=args.runtime_config,
        symbol_lib_dir=args.symbol_lib_dir,
    )
    print(json.dumps(result, indent=2))
    return 1 if result.get("failed") else 0


if __name__ == "__main__":
    sys.exit(main())
