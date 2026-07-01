#!/usr/bin/env python3
"""SIMetrix/SIMPLIS buck compensation optimizer scaffold.

This version is deliberately conservative:
- it never rewires the power stage;
- it writes a run-local copy of the schematic and compensator module;
- it injects only whitelisted compensation values and numeric circuit properties;
- it can run in mock mode before real SIMetrix vector export is finalized.
"""

from __future__ import annotations

import argparse
import copy
import csv
import itertools
import json
import math
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parent
REPO_DIR = PROJECT_DIR.parents[1]
SCRIPTS_DIR = REPO_DIR / "scripts"
if SCRIPTS_DIR.exists() and str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    from simetrix_waveforms import build_vector_export_script, export_filename, parse_show_file
except ImportError:  # pragma: no cover - reported by validate/config in real use
    build_vector_export_script = None  # type: ignore[assignment]
    export_filename = None  # type: ignore[assignment]
    parse_show_file = None  # type: ignore[assignment]

DEFAULT_CONFIG = PROJECT_DIR / "optimizer_config.json"
SKILL_DIR = Path.home() / ".codex" / "skills" / "simplis-automation"
COMPENSATION_TARGET = "compensator_alias"
SCHEMATIC_PROPERTY_TARGET = "schematic_property"
SCHEMATIC_VALUE_TOKEN_TARGET = "schematic_value_token"
SCHEMATIC_VAR_TARGET = "schematic_var"
PENALTY_SCORE = 1.0e12
PROVIDED_SOURCE = "provided"
IDEAL_SOURCE = "ideal"
SOURCE_CHOICES = (PROVIDED_SOURCE, IDEAL_SOURCE)
DEFAULT_REQUIRED_METRICS = (
    "fc_hz",
    "phase_margin_deg",
    "gain_margin_db",
    "overshoot_v",
    "undershoot_v",
    "settling_time_s",
    "output_ripple_v",
)
TRANSIENT_REQUIRED_METRICS = (
    "overshoot_v",
    "undershoot_v",
    "settling_time_s",
    "output_ripple_v",
)
DIAG_STAGES = ("open", "netlist", "tran_short", "pop", "ac")


@dataclass(frozen=True)
class RunPaths:
    run_dir: Path
    work_dir: Path
    schematic: Path
    compensator: Path | None
    script: Path
    run_log: Path
    sim_status: Path
    vectors_dir: Path
    screenshots_dir: Path
    metrics_json: Path
    score_json: Path
    params_json: Path
    result_json: Path


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, allow_nan=False), encoding="utf-8")


def project_base_dir(config: dict[str, Any]) -> Path:
    return Path(str(config.get("__config_dir", PROJECT_DIR))).resolve()


def project_path(config: dict[str, Any], key: str) -> Path:
    value = config.get(key)
    if value is None or value == "":
        raise KeyError(f"missing project path config key: {key}")
    path = Path(str(value))
    if path.is_absolute():
        return path.resolve()
    return (project_base_dir(config) / path).resolve()


def work_relative_path(config: dict[str, Any], key: str) -> Path:
    source = Path(str(config[key]))
    if source.is_absolute():
        return Path(source.name)
    return source


def next_run_dir(runs_dir: Path) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    existing = []
    for child in runs_dir.glob("run_*"):
        if child.is_dir():
            match = re.fullmatch(r"run_(\d{4})", child.name)
            if match:
                existing.append(int(match.group(1)))
    return runs_dir / f"run_{(max(existing) + 1) if existing else 1:04d}"


def validate_config(config: dict[str, Any], mode: str) -> list[str]:
    issues: list[str] = []
    schematic = project_path(config, "schematic")
    if not schematic.exists():
        issues.append(f"missing schematic: {schematic}")
    if config.get("compensator"):
        compensator = project_path(config, "compensator")
        if not compensator.exists():
            issues.append(f"missing compensator: {compensator}")
    simetrix_exe = Path(str(config.get("simetrix_exe", "")))
    if mode == "real" and not simetrix_exe.exists():
        issues.append(f"missing SIMetrix.exe: {simetrix_exe}")
    for name in parameter_names(config):
        if name not in config.get("parameters", {}):
            issues.append(f"missing parameter spec: {name}")
            continue
        spec = config["parameters"][name]
        target = spec.get("target", COMPENSATION_TARGET)
        if target == COMPENSATION_TARGET and "maps_to" not in spec:
            issues.append(f"missing maps_to for compensation parameter: {name}")
        if target == COMPENSATION_TARGET and not config.get("compensator"):
            issues.append(f"compensator_alias parameter {name} requires a compensator file; use schematic_var for top-level variables")
        if target == SCHEMATIC_VAR_TARGET and "maps_to" not in spec:
            issues.append(f"missing maps_to for schematic variable parameter: {name}")
        if target == SCHEMATIC_PROPERTY_TARGET:
            for key in ("refdes", "property"):
                if key not in spec:
                    issues.append(f"missing {key} for schematic parameter: {name}")
        if target == SCHEMATIC_VALUE_TOKEN_TARGET:
            for key in ("refdes", "property", "token"):
                if key not in spec:
                    issues.append(f"missing {key} for token parameter: {name}")
    if mode == "real":
        if requires_bode_probe(config) and not has_bode_probe(config):
            issues.append(
                "missing Bode plot probe: place a SIMPLIS Bode Plot Probe in the loop before real AC-loop optimization, "
                "then configure vector_export.ac output/input vectors"
            )
        issues.extend(validate_vector_export_config(config))
    return issues


def requires_bode_probe(config: dict[str, Any]) -> bool:
    setting = config.get("bode_probe", {})
    if isinstance(setting, dict) and "required" in setting:
        return bool(setting["required"])
    loop_metrics = {"fc_hz", "phase_margin_deg", "gain_margin_db"}
    return bool(loop_metrics.intersection(required_metrics(config)))


def has_bode_probe(config: dict[str, Any]) -> bool:
    candidates = [project_path(config, "schematic")]
    if config.get("compensator"):
        candidates.append(project_path(config, "compensator"))
    needles = ('name="Bode_Probe', 'ProbeType" value="Bode"', "edit_bode_plot_probe")
    for path in candidates:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(needle in text for needle in needles):
            return True
    return False


def validate_vector_export_config(config: dict[str, Any]) -> list[str]:
    export = config.get("vector_export", {})
    if not isinstance(export, dict) or not export.get("enabled", False):
        return []
    issues: list[str] = []
    if build_vector_export_script is None or export_filename is None or parse_show_file is None:
        issues.append("vector export helpers are unavailable; expected scripts/simetrix_waveforms.py")
    ac = export.get("ac", {})
    tran = export.get("tran", {})
    if any(name in required_metrics(config) for name in ("fc_hz", "phase_margin_deg", "gain_margin_db")):
        for key in ("group", "output", "input"):
            if not isinstance(ac, dict) or key not in ac:
                issues.append(f"missing vector_export.ac.{key}")
    if any(name in required_metrics(config) for name in TRANSIENT_REQUIRED_METRICS):
        for key in ("group", "vout"):
            if not isinstance(tran, dict) or key not in tran:
                issues.append(f"missing vector_export.tran.{key}")
    return issues


def required_metrics(config: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(name) for name in config.get("required_metrics", DEFAULT_REQUIRED_METRICS))


def parameter_names(config: dict[str, Any]) -> tuple[str, ...]:
    params = config.get("parameters", {})
    return tuple(name for name, spec in params.items() if spec.get("enabled", True))


def parameter_specs(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {name: config["parameters"][name] for name in parameter_names(config)}


def source_config(config: dict[str, Any], source: str) -> dict[str, Any]:
    if source not in SOURCE_CHOICES:
        raise ValueError(f"unknown source: {source}")
    out = copy.deepcopy(config)
    out["source"] = source
    if source == PROVIDED_SOURCE:
        return out

    lout = copy.deepcopy(config["parameters"]["Lout"])
    lout.update({"target": SCHEMATIC_PROPERTY_TARGET, "refdes": "L1", "property": "VALUE", "maps_to": "L"})
    out["schematic"] = "generated/ideal_buck_wide/ideal_buck_wide.sxsch"
    out["compensator"] = None
    out["parameters"] = {"Lout": lout}
    out["required_metrics"] = TRANSIENT_REQUIRED_METRICS
    out["mock_model"] = {
        "center": {"Lout": float(lout["initial"])},
        "nominal_fc_hz": float(config.get("targets", {}).get("fc_hz", 80000.0)),
        "nominal_phase_margin_deg": 65.0,
        "nominal_gain_margin_db": 18.0,
    }
    return out


def compensation_parameter_names(config: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        name
        for name, spec in parameter_specs(config).items()
        if spec.get("target", COMPENSATION_TARGET) == COMPENSATION_TARGET
    )


def mapped_compensator_var_specs(config: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    out: dict[str, tuple[str, dict[str, Any]]] = {}
    for name, spec in parameter_specs(config).items():
        maps_to = spec.get("maps_to")
        if maps_to:
            out[str(maps_to)] = (name, spec)
    return out


def sim_value(value: float) -> str:
    return f"{float(value):.12g}"


def normalize_candidate(config: dict[str, Any], candidate: dict[str, float]) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for name in parameter_names(config):
        spec = config["parameters"][name]
        value = float(candidate.get(name, spec["initial"]))
        lo, hi = map(float, spec["bounds"])
        normalized[name] = min(max(value, lo), hi)
    return normalized


def initial_candidate(config: dict[str, Any]) -> dict[str, float]:
    return {name: float(config["parameters"][name]["initial"]) for name in parameter_names(config)}


def coarse_candidates(config: dict[str, Any]) -> list[dict[str, float]]:
    names = list(parameter_names(config))
    initial = initial_candidate(config)
    unique: dict[str, dict[str, float]] = {candidate_key(initial): initial}
    for name in names:
        spec = config["parameters"][name]
        for value in spec.get("coarse", [spec["initial"]]):
            cand = dict(initial)
            cand[name] = float(value)
            cand = normalize_candidate(config, cand)
            unique[candidate_key(cand)] = cand

    grids = []
    for name in names:
        spec = config["parameters"][name]
        grids.append([float(v) for v in spec.get("coarse", [spec["initial"]])])
    for values in itertools.product(*grids):
        cand = normalize_candidate(config, dict(zip(names, values)))
        unique[candidate_key(cand)] = cand
    return list(unique.values())


def candidate_key(candidate: dict[str, float]) -> str:
    return json.dumps({name: float(candidate[name]) for name in sorted(candidate)}, sort_keys=True)


def coordinate_neighbors(config: dict[str, Any], center: dict[str, float], factor_scale: float) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    for name in parameter_names(config):
        spec = config["parameters"][name]
        base_factor = float(spec.get("step_factor", 1.3))
        factor = max(1.0001, 1.0 + (base_factor - 1.0) * factor_scale)
        for multiplier in (1.0 / factor, factor):
            cand = dict(center)
            cand[name] = float(cand[name]) * multiplier
            out.append(normalize_candidate(config, cand))
    unique: dict[str, dict[str, float]] = {}
    for item in out:
        unique[candidate_key(item)] = item
    return list(unique.values())


def render_template(template: str, values: dict[str, Any]) -> str:
    rendered = template
    for key, value in values.items():
        text = str(value)
        if "'" in text or (key != "PARAM_LOG_LINES" and ("\n" in text or "\r" in text)):
            raise ValueError(f"unsafe SIMetrix template value for {key}: {text!r}")
        rendered = rendered.replace("{{" + key + "}}", text)
    return rendered


def make_run_paths(config: dict[str, Any]) -> RunPaths:
    runs_dir = project_path(config, "runs_dir")
    run_dir = next_run_dir(runs_dir)
    work_dir = run_dir / "work"
    schematic_rel = work_relative_path(config, "schematic")
    comp_rel = work_relative_path(config, "compensator") if config.get("compensator") else None
    return RunPaths(
        run_dir=run_dir,
        work_dir=work_dir,
        schematic=work_dir / schematic_rel,
        compensator=(work_dir / comp_rel) if comp_rel else None,
        script=run_dir / "startup.sxscr",
        run_log=run_dir / "run.log",
        sim_status=run_dir / "sim_status.txt",
        vectors_dir=run_dir / "vectors",
        screenshots_dir=run_dir / "screenshots",
        metrics_json=run_dir / "metrics.json",
        score_json=run_dir / "score.json",
        params_json=run_dir / "params.json",
        result_json=run_dir / "result.json",
    )


def copy_working_schematic(config: dict[str, Any], paths: RunPaths) -> None:
    source_schematic = project_path(config, "schematic")
    paths.schematic.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_schematic, paths.schematic)
    if paths.compensator:
        source_comp = project_path(config, "compensator")
        paths.compensator.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_comp, paths.compensator)


def design_var_override_block(config: dict[str, Any], candidate: dict[str, float]) -> str:
    mapped = mapped_compensator_var_specs(config)
    lines = [
        "",
        "*********************************************************",
        "*** Optimizer circuit/design variable overrides       ***",
        "*********************************************************",
    ]
    added = False
    for var_name, (param_name, spec) in mapped.items():
        if spec.get("target", COMPENSATION_TARGET) == COMPENSATION_TARGET:
            continue
        lines.append(f".VAR {var_name} = {sim_value(candidate[param_name])}")
        added = True
    return "\n".join(lines) + "\n" if added else ""


def schematic_var_parameter_names(config: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        name for name, spec in parameter_specs(config).items() if spec.get("target") == SCHEMATIC_VAR_TARGET
    )


def schematic_var_override_block(config: dict[str, Any], candidate: dict[str, float]) -> str:
    names = schematic_var_parameter_names(config)
    if not names:
        return ""
    lines = [
        "",
        "*********************************************************",
        "*** Optimizer top-level .VAR overrides - run local    ***",
        "*********************************************************",
    ]
    for name in names:
        lines.append(f".VAR {name} = {sim_value(candidate[name])}")
    lines += ["", "*** Map optimizer names to existing schematic variables."]
    for name in names:
        maps_to = str(config["parameters"][name].get("maps_to", name))
        if maps_to != name:
            lines.append(f".VAR {maps_to} = {{{name}}}")
    lines.append("")
    return "\n".join(lines)


def compensation_override_block(config: dict[str, Any], candidate: dict[str, float]) -> str:
    comp_names = compensation_parameter_names(config)
    lines = [
        "",
        "*********************************************************",
        "*** Optimizer compensation overrides - run local copy ***",
        "*********************************************************",
    ]
    for name in comp_names:
        lines.append(f".VAR {name} = {sim_value(candidate[name])}")
    if comp_names:
        lines += ["", "*** Map optimizer names to existing compensator variables."]
    for name in comp_names:
        maps_to = config["parameters"][name]["maps_to"]
        lines.append(f".VAR {maps_to} = {{{name}}}")
    lines.append("")
    for name in parameter_names(config):
        lines.append(f"{{'*'}} {name} : {sim_value(candidate[name])}")
    lines.append("")
    return "\n".join(lines)


def insert_schematic_var_block(schematic: Path, block: str) -> None:
    if not block:
        return
    content = schematic.read_text(encoding="utf-8", errors="replace")
    escaped_block = escape_schematic_text(block)
    text_re = re.compile(r'Text value="(?:\\.|[^"])*"', re.DOTALL)
    for match in text_re.finditer(content):
        text_prop = match.group(0)
        if ".SIMULATOR SIMPLIS" not in text_prop.upper():
            continue
        if "*** Optimizer top-level .VAR overrides" in text_prop:
            return
        marker = ".SIMULATOR SIMPLIS\\n"
        if marker in text_prop:
            updated_prop = text_prop.replace(marker, marker + escaped_block, 1)
        else:
            updated_prop = text_prop[:-1] + escaped_block + '"'
        schematic.write_text(content[: match.start()] + updated_prop + content[match.end() :], encoding="utf-8")
        return
    raise RuntimeError(f"could not find SIMPLIS analysis text for schematic .VAR overrides in {schematic}")


def inject_compensator_params(config: dict[str, Any], paths: RunPaths, candidate: dict[str, float]) -> None:
    if not paths.compensator:
        return
    text = paths.compensator.read_text(encoding="utf-8")
    design_marker = "*** Calculated Parameters - not used in calculations\\n"
    if design_marker not in text:
        raise RuntimeError("could not find compensator calculated-parameter marker for design .VAR overrides")
    design_block = escape_schematic_text(design_var_override_block(config, candidate))
    text = text.replace(design_marker, design_block + design_marker, 1)

    debug_marker = "*****************************************************************\\n*** Debug."
    if debug_marker not in text:
        raise RuntimeError("could not find compensator debug marker for .VAR override insertion")
    comp_block = escape_schematic_text(compensation_override_block(config, candidate))
    text = text.replace(debug_marker, comp_block + debug_marker, 1)
    paths.compensator.write_text(text, encoding="utf-8")


def find_instance_block(text: str, refdes: str) -> re.Match[str]:
    ref_re = re.compile(rf'Property name="(?:REF|Ref)" value="{re.escape(refdes)}"')
    for match in re.finditer(r"(?ms)\.Instance\s.*?\.EndInstance", text):
        if ref_re.search(match.group(0)):
            return match
    raise RuntimeError(f"could not find instance {refdes} for schematic override")


def replace_instance_property(text: str, refdes: str, prop_name: str, value: float) -> str:
    match = find_instance_block(text, refdes)
    block = match.group(0)
    prop_re = re.compile(rf'(Property name="{re.escape(prop_name)}" value=")([^"]*)(")')
    if not prop_re.search(block):
        raise RuntimeError(f"could not find property {prop_name} on instance {refdes}")
    block = prop_re.sub(rf"\g<1>{sim_value(value)}\3", block, count=1)
    return text[: match.start()] + block + text[match.end() :]


def replace_instance_value_token(text: str, refdes: str, prop_name: str, token: str, value: float) -> str:
    match = find_instance_block(text, refdes)
    block = match.group(0)
    prop_re = re.compile(rf'(Property name="{re.escape(prop_name)}" value=")([^"]*)(")')
    prop_match = prop_re.search(block)
    if not prop_match:
        raise RuntimeError(f"could not find property {prop_name} on instance {refdes}")
    prop_value = prop_match.group(2)
    token_re = re.compile(rf"({re.escape(token)}=)([^\s\"]+)")
    if not token_re.search(prop_value):
        raise RuntimeError(f"could not find token {token} in {refdes}.{prop_name}")
    prop_value = token_re.sub(rf"\g<1>{sim_value(value)}", prop_value, count=1)
    block = block[: prop_match.start(2)] + prop_value + block[prop_match.end(2) :]
    return text[: match.start()] + block + text[match.end() :]


def inject_schematic_params(config: dict[str, Any], paths: RunPaths, candidate: dict[str, float]) -> None:
    text = paths.schematic.read_text(encoding="utf-8")
    for name, spec in parameter_specs(config).items():
        target = spec.get("target", COMPENSATION_TARGET)
        if target == SCHEMATIC_PROPERTY_TARGET:
            text = replace_instance_property(text, str(spec["refdes"]), str(spec["property"]), candidate[name])
        elif target == SCHEMATIC_VALUE_TOKEN_TARGET:
            text = replace_instance_value_token(
                text,
                str(spec["refdes"]),
                str(spec["property"]),
                str(spec["token"]),
                candidate[name],
            )
    paths.schematic.write_text(text, encoding="utf-8")
    insert_schematic_var_block(paths.schematic, schematic_var_override_block(config, candidate))


def enable_ac_analysis(paths: RunPaths) -> None:
    text = paths.schematic.read_text(encoding="utf-8")
    text = text.replace("*.AC DEC 25 1k 400k", ".AC DEC 25 1k 400k")
    paths.schematic.write_text(text, encoding="utf-8")


def escape_schematic_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\r\n", "\n").replace("\n", "\\n")


def replace_analysis_text(schematic: Path, analysis_text: str) -> bool:
    content = schematic.read_text(encoding="utf-8", errors="replace")
    replacement = f'Text value="{escape_schematic_text(analysis_text)}"'
    text_re = re.compile(r'Text value="(?:\\.|[^"])*"', re.DOTALL)
    for match in text_re.finditer(content):
        if ".SIMULATOR SIMPLIS" in match.group(0).upper():
            updated = content[: match.start()] + replacement + content[match.end() :]
            schematic.write_text(updated, encoding="utf-8")
            return True
    if 'Text value=""' in content:
        updated = content.replace('Text value=""', replacement, 1)
    elif ".EndSchematic" in content:
        updated = content.replace(".EndSchematic", replacement + "\n.EndSchematic", 1)
    else:
        raise RuntimeError(f"could not find a place to inject analysis text in {schematic}")
    schematic.write_text(updated, encoding="utf-8")
    return False


def diagnostic_analysis_text(config: dict[str, Any], stage: str) -> str:
    source = str(config.get("source", PROVIDED_SOURCE))
    if source == IDEAL_SOURCE:
        trig_cond = "0_TO_1"
        max_period = "5u"
        psp_npt = "4001"
    else:
        trig_cond = "1_TO_0"
        max_period = "2.2u"
        psp_npt = "2001"
    header = [
        ".SIMULATOR SIMPLIS",
        ".PRINT",
        "+ ALL",
        ".OPTIONS",
        f"+ PSP_NPT={psp_npt}",
        "+ POP_ITRMAX=20",
        "+ POP_OUTPUT_CYCLES=3",
        "+ SNAPSHOT_INTVL=0",
        "+ MIN_AVG_TOPOLOGY_DUR=1a",
        "+ AVG_TOPOLOGY_DUR_MEASUREMENT_WINDOW=128",
        "+ NEW_ANALYSIS",
    ]
    pop = [
        ".POP",
        "+ TRIG_GATE={TRIG_GATE}",
        f"+ TRIG_COND={trig_cond}",
        f"+ MAX_PERIOD={max_period}",
        "+ CONVERGENCE=1p",
        "+ CYCLES_BEFORE_LAUNCH=5",
        "+ TD_RUN_AFTER_POP_FAILS=-1",
    ]
    if stage == "tran_short":
        body = [".TRAN 5u 0"]
    elif stage == "pop":
        body = pop
    elif stage == "ac":
        body = [*pop, ".AC DEC 10 1k 400k"]
    else:
        raise ValueError(f"stage {stage!r} does not use diagnostic analysis text")
    return "\n".join([*header, *body, "", ".SIMULATOR DEFAULT", ""])


def write_startup_script(paths: RunPaths, candidate: dict[str, float]) -> None:
    template_path = PROJECT_DIR / "templates" / "run_candidate.sxscr.tpl"
    template = template_path.read_text(encoding="utf-8")
    values: dict[str, Any] = {
        "RUN_ID": paths.run_dir.name,
        "SCHEMATIC": paths.schematic.resolve(),
        "RUN_LOG": paths.run_log.resolve(),
        "SIM_STATUS": paths.sim_status.resolve(),
        "PARAM_LOG_LINES": "\n".join(f"Echo {name}={sim_value(candidate[name])}" for name in sorted(candidate)),
    }
    paths.script.write_text(render_template(template, values), encoding="utf-8")


def prepare_run(config: dict[str, Any], candidate: dict[str, float]) -> RunPaths:
    paths = make_run_paths(config)
    paths.run_dir.mkdir(parents=True, exist_ok=False)
    paths.vectors_dir.mkdir(parents=True, exist_ok=True)
    paths.screenshots_dir.mkdir(parents=True, exist_ok=True)
    copy_working_schematic(config, paths)
    inject_schematic_params(config, paths, candidate)
    inject_compensator_params(config, paths, candidate)
    enable_ac_analysis(paths)
    write_startup_script(paths, candidate)
    write_vector_export_script(config, paths)
    save_json(
        paths.params_json,
        {
            "source": config.get("source", PROVIDED_SOURCE),
            "candidate": candidate,
            "parameter_order": parameter_names(config),
        },
    )
    return paths


def run_simetrix(config: dict[str, Any], paths: RunPaths, mode: str) -> dict[str, Any]:
    if mode == "mock":
        paths.run_log.write_text("mock SIMetrix run\n", encoding="utf-8")
        paths.sim_status.write_text("simplis_exit_code=0\nsimulation_errors=\n", encoding="utf-8")
        return {"mode": mode, "returncode": 0, "timed_out": False, "cmd": []}
    if mode == "dry-run":
        paths.run_log.write_text("dry-run: SIMetrix was not launched\n", encoding="utf-8")
        paths.sim_status.write_text("simplis_exit_code=\nsimulation_errors=dry-run\n", encoding="utf-8")
        return {"mode": mode, "returncode": 0, "timed_out": False, "cmd": []}

    exe = Path(config["simetrix_exe"]).resolve()
    cmd = [str(exe), "/s", str(paths.script.resolve())]
    timeout = float(config.get("timeout_s", 240))
    started = time.time()
    with (paths.run_dir / "simetrix_stdout.txt").open("w", encoding="utf-8") as stdout, (
        paths.run_dir / "simetrix_stderr.txt"
    ).open("w", encoding="utf-8") as stderr:
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(paths.run_dir),
                check=False,
                timeout=timeout,
                stdout=stdout,
                stderr=stderr,
            )
            return {
                "mode": mode,
                "returncode": proc.returncode,
                "timed_out": False,
                "elapsed_s": time.time() - started,
                "cmd": cmd,
            }
        except subprocess.TimeoutExpired:
            return {
                "mode": mode,
                "returncode": 124,
                "timed_out": True,
                "elapsed_s": time.time() - started,
                "cmd": cmd,
            }


def run_simetrix_script(
    exe: Path,
    script: Path,
    timeout_s: float,
    stdout_path: Path,
    stderr_path: Path,
) -> dict[str, Any]:
    cmd = [str(exe.resolve()), "/s", str(script.resolve())]
    started = time.time()
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(script.parent),
                check=False,
                timeout=timeout_s,
                stdout=stdout,
                stderr=stderr,
            )
            return {
                "cmd": cmd,
                "returncode": proc.returncode,
                "timed_out": False,
                "elapsed_s": time.time() - started,
            }
        except subprocess.TimeoutExpired:
            return {
                "cmd": cmd,
                "returncode": 124,
                "timed_out": True,
                "elapsed_s": time.time() - started,
            }


def vector_export_enabled(config: dict[str, Any]) -> bool:
    export = config.get("vector_export", {})
    return isinstance(export, dict) and bool(export.get("enabled", False))


def vector_export_script_path(paths: RunPaths) -> Path:
    return paths.run_dir / "export_vectors.sxscr"


def vector_export_status_path(paths: RunPaths) -> Path:
    return paths.vectors_dir / "vector_export_status.txt"


def vector_export_items(config: dict[str, Any]) -> list[tuple[str, str]]:
    export = config.get("vector_export", {})
    if not isinstance(export, dict) or not export.get("enabled", False):
        return []
    items: list[tuple[str, str]] = []
    ac = export.get("ac", {})
    if isinstance(ac, dict) and {"group", "output", "input"}.issubset(ac):
        items.append((str(ac["group"]), str(ac["output"])))
        items.append((str(ac["group"]), str(ac["input"])))
    tran = export.get("tran", {})
    if isinstance(tran, dict) and {"group", "vout"}.issubset(tran):
        items.append((str(tran["group"]), str(tran["vout"])))
        if tran.get("vc"):
            items.append((str(tran["group"]), str(tran["vc"])))
    unique: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def write_vector_export_script(config: dict[str, Any], paths: RunPaths) -> None:
    if not vector_export_enabled(config):
        return
    if build_vector_export_script is None:
        raise RuntimeError("vector export helper is unavailable")
    script = vector_export_script_path(paths)
    text = build_vector_export_script(
        schematic=paths.schematic.resolve(),
        output_dir=paths.vectors_dir.resolve(),
        exports=vector_export_items(config),
        status_file=vector_export_status_path(paths),
    )
    paths.vectors_dir.mkdir(parents=True, exist_ok=True)
    script.write_text(text, encoding="utf-8")


def run_vector_export(config: dict[str, Any], paths: RunPaths) -> dict[str, Any]:
    if not vector_export_enabled(config):
        return {"enabled": False}
    script = vector_export_script_path(paths)
    if not script.exists():
        write_vector_export_script(config, paths)
    exe = Path(config["simetrix_exe"])
    result = run_simetrix_script(
        exe,
        script,
        float(config.get("timeout_s", 240)),
        paths.run_dir / "vector_export_stdout.txt",
        paths.run_dir / "vector_export_stderr.txt",
    )
    result["enabled"] = True
    result["script"] = str(script.resolve())
    status_path = vector_export_status_path(paths)
    if status_path.exists():
        result["status"] = parse_key_value_text(status_path.read_text(encoding="utf-8", errors="replace"))
    return result


def complex_value(value: Any) -> complex:
    if isinstance(value, dict):
        return complex(float(value.get("real", 0.0)), float(value.get("imag", 0.0)))
    return complex(float(value), 0.0)


def show_vector_path(paths: RunPaths, group: str, vector: str) -> Path:
    if export_filename is None:
        raise RuntimeError("vector export filename helper is unavailable")
    return export_filename(paths.vectors_dir, group, vector)


def convert_exported_vectors(config: dict[str, Any], paths: RunPaths) -> dict[str, Any]:
    if not vector_export_enabled(config):
        return {"enabled": False}
    if parse_show_file is None:
        return {"enabled": True, "failed": True, "failure_reason": "vector parser helper is unavailable"}
    export = config.get("vector_export", {})
    result: dict[str, Any] = {"enabled": True}
    missing: list[str] = []
    ac = export.get("ac", {}) if isinstance(export, dict) else {}
    if isinstance(ac, dict) and {"group", "output", "input"}.issubset(ac):
        out_path = show_vector_path(paths, str(ac["group"]), str(ac["output"]))
        in_path = show_vector_path(paths, str(ac["group"]), str(ac["input"]))
        if out_path.exists() and in_path.exists():
            ac_csv = paths.run_dir / "ac_loop.csv"
            write_ac_loop_csv(out_path, in_path, ac_csv)
            result["ac_loop_csv"] = str(ac_csv.resolve())
            bode_svg = paths.run_dir / "bode.svg"
            write_bode_svg(ac_csv, bode_svg, config)
            result["bode_svg"] = str(bode_svg.resolve())
        else:
            for path in (out_path, in_path):
                if not path.exists():
                    missing.append(str(path.resolve()))
    tran = export.get("tran", {}) if isinstance(export, dict) else {}
    if isinstance(tran, dict) and {"group", "vout"}.issubset(tran):
        vout_path = show_vector_path(paths, str(tran["group"]), str(tran["vout"]))
        vc_path = show_vector_path(paths, str(tran["group"]), str(tran["vc"])) if tran.get("vc") else None
        if vout_path.exists() and (vc_path is None or vc_path.exists()):
            tran_csv = paths.run_dir / "tran.csv"
            write_tran_csv(vout_path, vc_path, tran_csv)
            result["tran_csv"] = str(tran_csv.resolve())
        else:
            for path in (vout_path, vc_path):
                if path is not None and not path.exists():
                    missing.append(str(path.resolve()))
    if missing:
        result["failed"] = True
        result["missing_files"] = missing
    save_json(paths.run_dir / "vector_conversion.json", result)
    return result


def write_ac_loop_csv(output_vector: Path, input_vector: Path, out_csv: Path) -> None:
    if parse_show_file is None:
        raise RuntimeError("vector parser helper is unavailable")
    output = parse_show_file(output_vector)
    input_ = parse_show_file(input_vector)
    rows = []
    for freq, out_value, in_value in zip(output["x"], output["y"], input_["y"]):
        denominator = complex_value(in_value)
        ratio = complex_value(out_value) / denominator if abs(denominator) > 0 else complex(float("nan"), float("nan"))
        magnitude = abs(ratio)
        gain_db = 20.0 * math.log10(magnitude) if magnitude > 0.0 and math.isfinite(magnitude) else float("nan")
        phase_deg = math.degrees(math.atan2(ratio.imag, ratio.real))
        rows.append({"freq_hz": float(freq), "gain_db": gain_db, "phase_deg": phase_deg})
    with out_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["freq_hz", "gain_db", "phase_deg"])
        writer.writeheader()
        writer.writerows(rows)


def write_tran_csv(vout_vector: Path, vc_vector: Path | None, out_csv: Path) -> None:
    if parse_show_file is None:
        raise RuntimeError("vector parser helper is unavailable")
    vout = parse_show_file(vout_vector)
    vc = parse_show_file(vc_vector) if vc_vector is not None else None
    fieldnames = ["time_s", "vout_v"] + (["vc_v"] if vc is not None else [])
    with out_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index, (time_s, vout_value) in enumerate(zip(vout["x"], vout["y"])):
            row: dict[str, float] = {"time_s": float(time_s), "vout_v": float(vout_value)}
            if vc is not None and index < len(vc["y"]):
                row["vc_v"] = float(vc["y"][index])
            writer.writerow(row)


def write_bode_svg(ac_csv: Path, svg_path: Path, config: dict[str, Any]) -> None:
    rows = read_numeric_rows(ac_csv)
    if len(rows) < 2:
        return
    freqs = [row["freq_hz"] for row in rows if row.get("freq_hz", 0.0) > 0.0]
    gains = [row["gain_db"] for row in rows if "gain_db" in row and math.isfinite(row["gain_db"])]
    phases = [row["phase_deg"] for row in rows if "phase_deg" in row and math.isfinite(row["phase_deg"])]
    if not freqs or not gains or not phases:
        return
    width, height = 980, 620
    left, right = 72, 24
    top, mid_gap, bottom = 36, 42, 48
    plot_h = (height - top - mid_gap - bottom) / 2.0
    gain_top = top
    phase_top = top + plot_h + mid_gap
    x_min, x_max = math.log10(min(freqs)), math.log10(max(freqs))
    gain_min, gain_max = padded_range(gains + [0.0])
    phase_min, phase_max = padded_range(phases)

    def x_pos(freq: float) -> float:
        return left + (math.log10(freq) - x_min) / max(1.0e-30, x_max - x_min) * (width - left - right)

    def y_pos(value: float, y_min: float, y_max: float, y_top: float) -> float:
        return y_top + (y_max - value) / max(1.0e-30, y_max - y_min) * plot_h

    gain_points = " ".join(f"{x_pos(row['freq_hz']):.1f},{y_pos(row['gain_db'], gain_min, gain_max, gain_top):.1f}" for row in rows)
    phase_points = " ".join(
        f"{x_pos(row['freq_hz']):.1f},{y_pos(row['phase_deg'], phase_min, phase_max, phase_top):.1f}" for row in rows
    )
    target_fc = float(config.get("targets", {}).get("fc_hz", 0.0) or 0.0)
    target_line = ""
    if target_fc > 0 and min(freqs) <= target_fc <= max(freqs):
        x = x_pos(target_fc)
        target_line = (
            f'<line x1="{x:.1f}" y1="{gain_top}" x2="{x:.1f}" y2="{phase_top + plot_h}" '
            'stroke="#d97706" stroke-dasharray="6 5" stroke-width="1.4"/>'
        )
    zero_y = y_pos(0.0, gain_min, gain_max, gain_top)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="#ffffff"/>
<text x="{left}" y="24" font-family="Arial" font-size="18" fill="#111827">Loop Bode Plot</text>
<rect x="{left}" y="{gain_top}" width="{width-left-right}" height="{plot_h}" fill="#f9fafb" stroke="#d1d5db"/>
<rect x="{left}" y="{phase_top}" width="{width-left-right}" height="{plot_h}" fill="#f9fafb" stroke="#d1d5db"/>
<line x1="{left}" y1="{zero_y:.1f}" x2="{width-right}" y2="{zero_y:.1f}" stroke="#6b7280" stroke-dasharray="4 4"/>
{target_line}
<polyline points="{gain_points}" fill="none" stroke="#2563eb" stroke-width="2"/>
<polyline points="{phase_points}" fill="none" stroke="#dc2626" stroke-width="2"/>
<text x="18" y="{gain_top + 20}" font-family="Arial" font-size="13" fill="#2563eb">Gain dB</text>
<text x="18" y="{phase_top + 20}" font-family="Arial" font-size="13" fill="#dc2626">Phase deg</text>
<text x="{left}" y="{height - 16}" font-family="Arial" font-size="12" fill="#4b5563">Frequency axis is logarithmic. Orange line marks target fc when configured.</text>
</svg>
"""
    svg_path.write_text(svg, encoding="utf-8")


def padded_range(values: list[float]) -> tuple[float, float]:
    low = min(values)
    high = max(values)
    if low == high:
        return low - 1.0, high + 1.0
    pad = 0.08 * (high - low)
    return low - pad, high + pad


def ideal_generator_spec(config: dict[str, Any]) -> dict[str, Any]:
    reference = SKILL_DIR / "references" / "generated_buck_open_loop_tran.json"
    if not reference.exists():
        raise RuntimeError(f"missing generator reference: {reference}")
    spec = load_json(reference)
    spec["design"] = {
        "name": "ideal_buck_wide",
        "simulator": "SIMPLIS",
        "schematic": "ideal_buck_wide.sxsch",
        "netlist": "ideal_buck_wide.net",
        "script": "ideal_buck_wide_create.sxscr",
        "netlist_script": "ideal_buck_wide_netlist.sxscr",
        "deck": "ideal_buck_wide.deck",
        "run_script": "ideal_buck_wide_run.sxscr",
        "metrics": "ideal_buck_wide_metrics.txt",
    }
    layout = spec.setdefault("layout", {})
    layout.update({"grid": 120, "group_spacing": 1920, "row_spacing": 960, "col_spacing": 720, "stub_length": 180})
    initial = initial_candidate(config)
    for device in spec.get("devices", []):
        ref = device.get("ref")
        props = device.setdefault("props", {})
        if ref == "L1":
            props["VALUE"] = sim_value(initial.get("Lout", 6.8e-7))
        elif ref == "COUT":
            props["VALUE"] = sim_value(initial.get("Cout", 2.2e-4))
        elif ref == "RLOAD":
            props["VALUE"] = "1.2"
        elif ref == "VIN12":
            props["VALUE"] = "12"
        elif ref == "VPWM_HS":
            props["SIMPLIS_VALUE"] = (
                "_V1=0 _V2=5 _FREQ=500k _DELAY=0 _DRATIOx100=10 "
                "_T_RISE=5n _T_FALL=5n _DAMP_COEF=0 _PWIDTH=195n "
                "_OFF_UNTIL_DELAY=0 _IDLE_IN_POP=0 _USEPHASE=0 _PHASE=0"
            )
    spec["simulation"] = {
        "analysis_text": diagnostic_analysis_text({"source": IDEAL_SOURCE}, "tran_short").replace(".TRAN 5u 0", ".TRAN 40u 0")
    }
    spec["metrics"] = {"vin": "VIN", "vout": "VOUT", "sw": "SW", "target_vout": 1.2}
    return spec


def generate_ideal(config: dict[str, Any], *, run: bool, netlist_check: bool, dry_run: bool, timeout_s: float) -> dict[str, Any]:
    out_dir = PROJECT_DIR / "generated" / "ideal_buck_wide"
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_path = out_dir / "ideal_buck_wide.json"
    result_path = out_dir / "ideal_generation_result.json"
    save_json(spec_path, ideal_generator_spec(source_config(config, IDEAL_SOURCE)))
    cli = SKILL_DIR / "scripts" / "simplis_cli.py"
    cmd = [
        sys.executable,
        str(cli),
        "--simetrix-exe",
        str(Path(config["simetrix_exe"]).resolve()),
        "generate-schematic",
        "--config",
        str(spec_path.resolve()),
        "--out-dir",
        str(out_dir.resolve()),
        "--timeout",
        str(timeout_s),
        "--batch",
    ]
    if run:
        cmd.append("--run")
    if netlist_check:
        cmd.append("--netlist-check")
    if dry_run:
        cmd.append("--dry-run")
    stdout_path = out_dir / "generate_stdout.txt"
    stderr_path = out_dir / "generate_stderr.txt"
    started = time.time()
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        proc = subprocess.run(cmd, cwd=str(out_dir), check=False, stdout=stdout, stderr=stderr)
    stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace").strip()
    parsed: dict[str, Any] = {}
    if stdout_text:
        try:
            parsed = json.loads(stdout_text)
        except json.JSONDecodeError:
            parsed = {"raw_stdout": stdout_text}
    result = {
        "cmd": cmd,
        "returncode": proc.returncode,
        "elapsed_s": time.time() - started,
        "dry_run": dry_run,
        "stdout": str(stdout_path.resolve()),
        "stderr": str(stderr_path.resolve()),
        "spec": str(spec_path.resolve()),
        "out_dir": str(out_dir.resolve()),
        "generator_result": parsed,
        "failed": proc.returncode != 0 or bool(parsed.get("failed")),
    }
    save_json(result_path, result)
    return result


def write_diagnostic_script(stage: str, schematic: Path, script: Path, status: Path, netlist: Path | None = None) -> None:
    commands = [f'OpenSchem /cd /readonly "{schematic.resolve()}"']
    if stage == "netlist":
        if netlist is None:
            raise ValueError("netlist stage requires a netlist path")
        commands.append(f'Netlist /simplis "{netlist.resolve()}"')
    elif stage in {"tran_short", "pop", "ac"}:
        commands.append("simplis_run")
        commands.append("Let sim_exit_code = GetSIMPLISExitCode()")
    elif stage != "open":
        raise ValueError(f"unknown diagnostic stage: {stage}")
    lines = [
        "Unset EchoOn",
        "ClearMessageWindow",
        f"Let echo_file = OpenEchoFile('{status.resolve()}', 'w')",
        f"Echo stage={stage}",
        "Echo stage_start=true",
        "Let close_result = CloseEchoFile()",
        *commands,
        f"Let echo_file = OpenEchoFile('{status.resolve()}', 'a')",
        f"Echo stage={stage}",
        "Echo stage_done=true",
    ]
    if stage in {"tran_short", "pop", "ac"}:
        lines.extend(
            [
                "Echo simplis_exit_code={sim_exit_code}",
                "Echo simulation_errors=",
            ]
        )
    lines.extend(["Let close_result = CloseEchoFile()", "Quit", ""])
    script.write_text("\n".join(lines), encoding="utf-8")


def prepare_diagnostic_stage(config: dict[str, Any], paths: RunPaths, stage: str) -> dict[str, Path]:
    stage_dir = paths.run_dir / "diagnose" / stage
    stage_work = stage_dir / "work"
    if stage_work.exists():
        shutil.rmtree(stage_work)
    shutil.copytree(paths.work_dir, stage_work)
    schematic = stage_work / Path(config["schematic"])
    if stage in {"tran_short", "pop", "ac"}:
        replace_analysis_text(schematic, diagnostic_analysis_text(config, stage))
    stage_dir.mkdir(parents=True, exist_ok=True)
    return {
        "stage_dir": stage_dir,
        "work_dir": stage_work,
        "schematic": schematic,
        "script": stage_dir / f"{stage}.sxscr",
        "status": stage_dir / "status.txt",
        "netlist": stage_dir / f"{stage}.net",
        "stdout": stage_dir / "simetrix_stdout.txt",
        "stderr": stage_dir / "simetrix_stderr.txt",
    }


def diagnose_real(config: dict[str, Any], mode: str, stage_timeout_s: float) -> dict[str, Any]:
    candidate = initial_candidate(config)
    paths = prepare_run(config, candidate)
    exe = Path(config["simetrix_exe"])
    stages: list[dict[str, Any]] = []
    failed_stage: str | None = None
    for stage in DIAG_STAGES:
        stage_paths = prepare_diagnostic_stage(config, paths, stage)
        write_diagnostic_script(
            stage,
            stage_paths["schematic"],
            stage_paths["script"],
            stage_paths["status"],
            stage_paths["netlist"],
        )
        if mode == "dry-run":
            launch = {"mode": mode, "returncode": 0, "timed_out": False, "cmd": []}
        else:
            launch = run_simetrix_script(
                exe,
                stage_paths["script"],
                stage_timeout_s,
                stage_paths["stdout"],
                stage_paths["stderr"],
            )
            launch["mode"] = mode
        status = {}
        if stage_paths["status"].exists():
            status = parse_key_value_text(stage_paths["status"].read_text(encoding="utf-8", errors="replace"))
        stage_failed = bool(launch.get("timed_out")) or int(launch.get("returncode", 0)) != 0
        if mode != "dry-run" and status.get("stage_done") is not True:
            stage_failed = True
        if status.get("simplis_exit_code") not in (None, "", 0.0):
            stage_failed = True
        reason = ""
        if launch.get("timed_out"):
            reason = f"{stage} timed out after {stage_timeout_s:g}s"
        elif int(launch.get("returncode", 0)) != 0:
            reason = f"{stage} SIMetrix return code {launch.get('returncode')}"
        elif mode != "dry-run" and status.get("stage_done") is not True:
            reason = f"{stage} did not write stage_done"
        elif status.get("simplis_exit_code") not in (None, "", 0.0):
            reason = f"{stage} SIMPLIS exit code {status.get('simplis_exit_code')}"
        row = {
            "stage": stage,
            "failed": stage_failed,
            "failure_reason": reason,
            "script": str(stage_paths["script"].resolve()),
            "schematic": str(stage_paths["schematic"].resolve()),
            "status_file": str(stage_paths["status"].resolve()),
            "netlist": str(stage_paths["netlist"].resolve()) if stage == "netlist" else None,
            "launch": launch,
            "status": status,
        }
        stages.append(row)
        if stage_failed and mode != "dry-run":
            failed_stage = stage
            break
    result = {
        "run_id": paths.run_dir.name,
        "source": config.get("source", PROVIDED_SOURCE),
        "mode": mode,
        "run_dir": str(paths.run_dir.resolve()),
        "candidate": candidate,
        "failed": failed_stage is not None,
        "failed_stage": failed_stage,
        "stages": stages,
    }
    save_json(paths.run_dir / "diagnose_result.json", result)
    return result


def parse_key_value_text(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = coerce_scalar(value)
    return out


def coerce_scalar(value: Any) -> Any:
    text = str(value).strip()
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    try:
        return float(text)
    except ValueError:
        return text


def read_manual_metrics(paths: RunPaths) -> dict[str, Any]:
    for name in ("manual_metrics.csv", "metrics.csv"):
        path = paths.run_dir / name
        if path.exists():
            return parse_metric_csv(path)
    return {}


def read_sim_error_files(paths: RunPaths) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    search_roots = [paths.run_dir, paths.work_dir]
    seen: set[Path] = set()
    for root in search_roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            suffixes = "".join(path.suffixes).lower()
            if not (suffixes.endswith(".err") or suffixes.endswith(".warn")):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if not text:
                continue
            rel = path.relative_to(paths.run_dir) if path.is_relative_to(paths.run_dir) else path
            snippet = " ".join(text.split())[:500]
            item = f"{rel}: {snippet}"
            if suffixes.endswith(".err"):
                errors.append(item)
            else:
                warnings.append(item)
    out: dict[str, Any] = {}
    if errors:
        out["sim_error_files"] = errors
    if warnings:
        out["sim_warning_files"] = warnings
    return out


def parse_metric_csv(path: Path) -> dict[str, Any]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return {}
    if {"metric", "value"}.issubset(rows[0].keys()):
        out: dict[str, Any] = {}
        for row in rows:
            key = str(row["metric"]).strip()
            out[key] = coerce_scalar(row["value"])
        return out
    out = {}
    for key, value in rows[0].items():
        if value is None or value == "":
            continue
        out[key] = coerce_scalar(value)
    return out


def parse_ac_loop_csv(
    path: Path,
    *,
    target_fc_hz: float | None = None,
    crossover_policy: str = "first",
) -> dict[str, Any]:
    rows = read_numeric_rows(path)
    if not rows:
        return {}
    freq_key = first_existing(rows[0], ("freq_hz", "frequency_hz", "frequency", "freq"))
    gain_key = first_existing(rows[0], ("gain_db", "db", "loop_gain_db"))
    phase_key = first_existing(rows[0], ("phase_deg", "phase", "loop_phase_deg"))
    if not (freq_key and gain_key and phase_key):
        return {}
    rows.sort(key=lambda row: row[freq_key])
    crossings = interpolate_crossings(rows, freq_key, gain_key, target=0.0)
    fc = select_crossover(crossings, target_fc_hz, crossover_policy)
    pm = None
    if fc is not None:
        phase_at_fc = interpolate_at_x(rows, freq_key, phase_key, fc)
        if phase_at_fc is not None:
            pm = 180.0 + phase_at_fc
    phase_cross = interpolate_crossing(rows, freq_key, phase_key, target=-180.0)
    gm = None
    gm_unbounded = False
    if phase_cross is not None:
        gain_at_phase = interpolate_at_x(rows, freq_key, gain_key, phase_cross)
        if gain_at_phase is not None:
            gm = -gain_at_phase
    else:
        gm = 999.0
        gm_unbounded = True
    out: dict[str, Any] = {}
    if crossings:
        out["crossovers_hz"] = [item["x"] for item in crossings]
        out["crossover_directions"] = [item["direction"] for item in crossings]
        out["selected_crossover_policy"] = crossover_policy
    if fc is not None:
        out["fc_hz"] = fc
    if pm is not None:
        out["phase_margin_deg"] = pm
    if gm is not None:
        out["gain_margin_db"] = gm
    if gm_unbounded:
        out["gain_margin_unbounded"] = True
    return out


def parse_transient_csv(path: Path, config: dict[str, Any]) -> dict[str, Any]:
    rows = read_numeric_rows(path)
    if not rows:
        return {}
    time_key = first_existing(rows[0], ("time_s", "time", "t"))
    vout_key = first_existing(rows[0], ("vout_v", "vout", "VOUT", "v_out"))
    vc_key = first_existing(rows[0], ("vc_v", "vc", "comp_v", "COMP"))
    if not (time_key and vout_key):
        return {}
    rows.sort(key=lambda row: row[time_key])
    target = float(config.get("nominal_output_v", 1.2))
    step_start = float(config.get("load_step", {}).get("start_s", rows[0][time_key]))
    post = [row for row in rows if row[time_key] >= step_start]
    if not post:
        post = rows
    vmax = max(row[vout_key] for row in post)
    vmin = min(row[vout_key] for row in post)
    band = float(config.get("load_step", {}).get("settling_band_v", 0.01 * target))
    outside = [row[time_key] for row in post if abs(row[vout_key] - target) > band]
    settling = max(0.0, (outside[-1] - step_start) if outside else 0.0)
    ripple_window = float(config.get("load_step", {}).get("ripple_window_s", 1.0e-5))
    end_time = max(row[time_key] for row in rows)
    tail = [row for row in rows if row[time_key] >= end_time - ripple_window] or rows
    ripple = max(row[vout_key] for row in tail) - min(row[vout_key] for row in tail)
    out: dict[str, Any] = {
        "overshoot_v": max(0.0, vmax - target),
        "undershoot_v": max(0.0, target - vmin),
        "settling_time_s": settling,
        "output_ripple_v": ripple,
    }
    if vc_key:
        lo, hi = map(float, config.get("vc_limits", [0.0, 5.0]))
        vc_min = min(row[vc_key] for row in rows)
        vc_max = max(row[vc_key] for row in rows)
        out["vc_min_v"] = vc_min
        out["vc_max_v"] = vc_max
        out["vc_saturated"] = vc_min <= lo or vc_max >= hi
    return out


def read_numeric_rows(path: Path) -> list[dict[str, float]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows: list[dict[str, float]] = []
        for row in reader:
            parsed: dict[str, float] = {}
            for key, value in row.items():
                if value is None or value == "":
                    continue
                try:
                    parsed[key] = float(value)
                except ValueError:
                    pass
            if parsed:
                rows.append(parsed)
    return rows


def first_existing(row: dict[str, float], names: tuple[str, ...]) -> str | None:
    for name in names:
        if name in row:
            return name
    return None


def interpolate_crossing(rows: list[dict[str, float]], x_key: str, y_key: str, target: float) -> float | None:
    last = rows[0]
    for row in rows[1:]:
        y0 = last[y_key] - target
        y1 = row[y_key] - target
        if y0 == 0:
            return last[x_key]
        if y0 * y1 <= 0 and y1 != y0:
            frac = -y0 / (y1 - y0)
            return last[x_key] + frac * (row[x_key] - last[x_key])
        last = row
    return None


def interpolate_crossings(rows: list[dict[str, float]], x_key: str, y_key: str, target: float) -> list[dict[str, Any]]:
    crossings: list[dict[str, Any]] = []
    last = rows[0]
    for row in rows[1:]:
        y0 = last[y_key] - target
        y1 = row[y_key] - target
        if y0 == 0:
            crossings.append({"x": last[x_key], "direction": "flat"})
        elif y0 * y1 <= 0 and y1 != y0:
            frac = -y0 / (y1 - y0)
            x_value = last[x_key] + frac * (row[x_key] - last[x_key])
            crossings.append({"x": x_value, "direction": "down" if y1 < y0 else "up"})
        last = row
    return crossings


def select_crossover(crossings: list[dict[str, Any]], target_fc_hz: float | None, policy: str) -> float | None:
    if not crossings:
        return None
    normalized = policy.lower().replace("-", "_")
    if normalized in {"first", "lowest"}:
        return float(crossings[0]["x"])
    if normalized in {"last", "highest"}:
        return float(crossings[-1]["x"])
    if normalized in {"first_down", "first_falling"}:
        selected = next((item for item in crossings if item["direction"] == "down"), crossings[0])
        return float(selected["x"])
    if normalized in {"last_down", "last_falling"}:
        selected = next((item for item in reversed(crossings) if item["direction"] == "down"), crossings[-1])
        return float(selected["x"])
    if normalized in {"nearest_target", "target_nearest"} and target_fc_hz is not None and target_fc_hz > 0:
        return float(min(crossings, key=lambda item: abs(float(item["x"]) - target_fc_hz))["x"])
    return float(crossings[0]["x"])


def interpolate_at_x(rows: list[dict[str, float]], x_key: str, y_key: str, x_value: float) -> float | None:
    if x_value <= rows[0][x_key]:
        return rows[0][y_key]
    last = rows[0]
    for row in rows[1:]:
        if row[x_key] >= x_value:
            dx = row[x_key] - last[x_key]
            if dx == 0:
                return row[y_key]
            frac = (x_value - last[x_key]) / dx
            return last[y_key] + frac * (row[y_key] - last[y_key])
        last = row
    return rows[-1][y_key]


def mock_metrics(config: dict[str, Any], candidate: dict[str, float]) -> dict[str, Any]:
    model = config.get("mock_model", {})
    center = model.get("center", {})
    err = 0.0
    signed: dict[str, float] = {}
    for name in parameter_names(config):
        c = float(center.get(name, config["parameters"][name]["initial"]))
        ratio = math.log(max(float(candidate[name]), 1.0e-30) / max(c, 1.0e-30))
        signed[name] = ratio
        err += ratio * ratio
    fc = float(model.get("nominal_fc_hz", 80000.0)) * math.exp(
        0.35 * signed.get("Rz1", 0.0)
        - 0.25 * signed.get("Cz1", 0.0)
        + 0.18 * signed.get("VRAMP", 0.0)
        - 0.18 * signed.get("Lout", 0.0)
        - 0.18 * signed.get("Cout", 0.0)
    )
    pm = float(model.get("nominal_phase_margin_deg", 60.0)) - 12.0 * math.sqrt(err)
    gm = float(model.get("nominal_gain_margin_db", 12.0)) - 4.0 * abs(signed.get("Cp1", 0.0))
    return {
        "failed": False,
        "pop_success": True,
        "fc_hz": fc,
        "phase_margin_deg": pm,
        "gain_margin_db": gm,
        "overshoot_v": 0.012 + 0.025 * math.sqrt(err) + 0.006 * max(0.0, -signed.get("Cout", 0.0)),
        "undershoot_v": 0.010 + 0.020 * math.sqrt(err) + 0.005 * max(0.0, signed.get("Lout", 0.0)),
        "settling_time_s": 5.0e-6 + 8.0e-6 * math.sqrt(err),
        "output_ripple_v": 0.006 + 0.006 * abs(signed.get("Cz2", 0.0)) + 0.003 * max(0.0, -signed.get("Cout", 0.0)),
        "vc_saturated": err > 2.5,
        "mock_error": err,
    }


def parse_metrics(config: dict[str, Any], paths: RunPaths, candidate: dict[str, float], launch: dict[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    reasons: list[str] = []
    status = {}
    if paths.sim_status.exists():
        status = parse_key_value_text(paths.sim_status.read_text(encoding="utf-8", errors="replace"))
    if launch.get("mode") == "mock":
        metrics.update(mock_metrics(config, candidate))
    else:
        metrics.update(read_manual_metrics(paths))
        sim_files = read_sim_error_files(paths)
        metrics.update(sim_files)
        if sim_files.get("sim_error_files"):
            metrics["failed"] = True
            reasons.append("SIMPLIS error files: " + " | ".join(sim_files["sim_error_files"]))
        ac_csv = paths.run_dir / "ac_loop.csv"
        tran_csv = paths.run_dir / "tran.csv"
        if ac_csv.exists():
            metrics.update(
                parse_ac_loop_csv(
                    ac_csv,
                    target_fc_hz=float(config.get("targets", {}).get("fc_hz", 0.0) or 0.0),
                    crossover_policy=str(config.get("crossover_policy", "first")),
                )
            )
        if tran_csv.exists():
            metrics.update(parse_transient_csv(tran_csv, config))
    if launch.get("returncode", 0) != 0:
        metrics["failed"] = True
        reasons.append(f"SIMetrix return code {launch.get('returncode')}")
    if launch.get("timed_out"):
        metrics["failed"] = True
        reasons.append("SIMetrix timeout")
    vector_launch = launch.get("vector_export")
    if isinstance(vector_launch, dict):
        if vector_launch.get("returncode", 0) != 0:
            metrics["failed"] = True
            reasons.append(f"vector export SIMetrix return code {vector_launch.get('returncode')}")
        if vector_launch.get("timed_out"):
            metrics["failed"] = True
            reasons.append("vector export timeout")
        vector_status = vector_launch.get("status", {})
        if isinstance(vector_status, dict) and vector_status.get("simplis_exit_code") not in (None, "", 0.0):
            metrics["failed"] = True
            reasons.append(f"vector export SIMPLIS exit code {vector_status.get('simplis_exit_code')}")
    vector_conversion = launch.get("vector_conversion")
    if isinstance(vector_conversion, dict) and vector_conversion.get("failed"):
        metrics["failed"] = True
        if vector_conversion.get("missing_files"):
            reasons.append("missing exported vector files: " + ", ".join(vector_conversion["missing_files"]))
        elif vector_conversion.get("failure_reason"):
            reasons.append(str(vector_conversion["failure_reason"]))
    if status.get("simplis_exit_code") not in (None, "", 0.0):
        metrics["failed"] = True
        reasons.append(f"SIMPLIS exit code {status.get('simplis_exit_code')}")
    if status.get("simulation_errors"):
        text = str(status["simulation_errors"]).strip()
        if text and text.lower() != "dry-run":
            reasons.append(text)
    missing = [name for name in required_metrics(config) if name not in metrics]
    if missing and launch.get("mode") != "dry-run":
        metrics["failed"] = True
        reasons.append("missing metrics: " + ", ".join(missing))
    metrics.setdefault("pop_success", not metrics.get("failed", False))
    metrics.setdefault("vc_saturated", False)
    metrics.setdefault("failed", False)
    if reasons:
        metrics["failure_reason"] = "; ".join(reasons)
    return metrics


def evaluate(config: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    targets = config.get("targets", {})
    weights = config.get("weights", {})
    terms: dict[str, float] = {}
    if metrics.get("failed") or not metrics.get("pop_success", True):
        terms["failed"] = float(weights.get("failed", PENALTY_SCORE))
        return {"score": sum(terms.values()), "terms": terms, "failed": True}
    fc = float(metrics.get("fc_hz", 0.0))
    target_fc = float(targets.get("fc_hz", fc or 1.0))
    fc_min = targets.get("fc_hz_min")
    fc_max = targets.get("fc_hz_max")
    if fc_min is not None and fc_max is not None:
        low = float(fc_min)
        high = float(fc_max)
        distance = 0.0 if low <= fc <= high else min(abs(fc - low), abs(fc - high))
        reference = max(target_fc, (low + high) / 2.0, 1.0)
        terms["fc_rel_error"] = float(weights.get("fc_rel_error", 0.0)) * distance / reference
    else:
        terms["fc_rel_error"] = float(weights.get("fc_rel_error", 0.0)) * abs(fc - target_fc) / target_fc
    pm_value = float(metrics.get("phase_margin_deg", 0.0))
    pm_short = max(0.0, float(targets.get("phase_margin_deg", targets.get("phase_margin_deg_min", 0.0))) - pm_value)
    pm_max = targets.get("phase_margin_deg_max")
    pm_excess = max(0.0, pm_value - float(pm_max)) if pm_max is not None else 0.0
    gm_short = max(0.0, float(targets.get("gain_margin_db", 0.0)) - float(metrics.get("gain_margin_db", 0.0)))
    terms["phase_margin_shortfall"] = float(weights.get("phase_margin_shortfall", 0.0)) * pm_short
    terms["phase_margin_excess"] = float(weights.get("phase_margin_excess", 0.0)) * pm_excess
    terms["gain_margin_shortfall"] = float(weights.get("gain_margin_shortfall", 0.0)) * gm_short
    for name in ("overshoot_v", "undershoot_v", "settling_time_s", "output_ripple_v"):
        value = abs(float(metrics.get(name, 0.0)))
        target = float(targets.get(name, 0.0))
        excess = max(0.0, value - target)
        terms[name] = float(weights.get(name, 0.0)) * excess
    if metrics.get("vc_saturated"):
        terms["vc_saturation"] = float(weights.get("vc_saturation", PENALTY_SCORE))
    score = sum(terms.values())
    return {"score": score, "terms": terms, "failed": False}


def summary_fieldnames(config: dict[str, Any]) -> list[str]:
    return [
        "run_id",
        "source",
        "run_dir",
        "score",
        "failed",
        *parameter_names(config),
        "fc_hz",
        "phase_margin_deg",
        "gain_margin_db",
        "overshoot_v",
        "undershoot_v",
        "settling_time_s",
        "output_ripple_v",
        "vc_saturated",
        "score_terms",
        "failure_reason",
    ]


def summary_row_from_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": result["run_id"],
        "source": result.get("source", PROVIDED_SOURCE),
        "run_dir": result.get("run_dir", ""),
        "score": result["score"],
        "failed": result["failed"],
        "score_terms": json.dumps(result.get("score_terms", {}), sort_keys=True),
        **result.get("candidate", {}),
        **result.get("metrics", {}),
    }


def rebuild_summary(config: dict[str, Any], fieldnames: list[str]) -> None:
    runs_dir = project_path(config, "runs_dir")
    path = runs_dir.parent / "summary.csv"
    rows: list[dict[str, Any]] = []
    for result_path in sorted(runs_dir.glob("run_*/result.json")):
        try:
            rows.append(summary_row_from_result(load_json(result_path)))
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def append_summary(config: dict[str, Any], row: dict[str, Any]) -> None:
    path = project_path(config, "runs_dir").parent / "summary.csv"
    fieldnames = summary_fieldnames(config)
    exists = path.exists()
    if exists:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader, [])
        if header != fieldnames:
            rebuild_summary(config, fieldnames)
            return
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def save_result(
    config: dict[str, Any],
    paths: RunPaths,
    candidate: dict[str, float],
    launch: dict[str, Any],
    metrics: dict[str, Any],
    score: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "run_id": paths.run_dir.name,
        "source": config.get("source", PROVIDED_SOURCE),
        "run_dir": str(paths.run_dir.resolve()),
        "candidate": candidate,
        "launch": launch,
        "metrics": metrics,
        "score": score["score"],
        "score_terms": score["terms"],
        "failed": bool(metrics.get("failed", False) or score.get("failed", False)),
    }
    save_json(paths.metrics_json, metrics)
    save_json(paths.score_json, score)
    save_json(paths.result_json, result)
    append_summary(config, summary_row_from_result(result))
    return result


def run_candidate(config: dict[str, Any], candidate: dict[str, float], mode: str) -> dict[str, Any]:
    candidate = normalize_candidate(config, candidate)
    paths = prepare_run(config, candidate)
    launch = run_simetrix(config, paths, mode)
    if mode == "real" and not launch.get("timed_out") and int(launch.get("returncode", 0)) == 0:
        if vector_export_enabled(config):
            launch["vector_export"] = run_vector_export(config, paths)
            launch["vector_conversion"] = convert_exported_vectors(config, paths)
    metrics = parse_metrics(config, paths, candidate, launch)
    score = evaluate(config, metrics)
    return save_result(config, paths, candidate, launch, metrics, score)


def write_best(project_dir: Path, best: dict[str, Any]) -> None:
    save_json(
        project_dir / "best_params.json",
        {
            "run_id": best["run_id"],
            "source": best.get("source", PROVIDED_SOURCE),
            "score": best["score"],
            "params": best["candidate"],
            "metrics": best["metrics"],
            "score_terms": best.get("score_terms", {}),
            "run_dir": best["run_dir"],
        },
    )


def optimize(config: dict[str, Any], mode: str, max_evals: int, coordinate_rounds: int) -> dict[str, Any]:
    history: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cand in coarse_candidates(config):
        if len(history) >= max_evals:
            break
        cand = normalize_candidate(config, cand)
        key = candidate_key(cand)
        if key in seen:
            continue
        seen.add(key)
        history.append(run_candidate(config, cand, mode))
    finite = [row for row in history if math.isfinite(float(row["score"]))]
    best = min(finite or history, key=lambda row: float(row["score"]))

    factor_scale = 1.0
    for _round in range(coordinate_rounds):
        if len(history) >= max_evals:
            break
        improved = False
        for cand in coordinate_neighbors(config, best["candidate"], factor_scale):
            if len(history) >= max_evals:
                break
            key = candidate_key(cand)
            if key in seen:
                continue
            seen.add(key)
            row = run_candidate(config, cand, mode)
            history.append(row)
            if float(row["score"]) < float(best["score"]):
                best = row
                improved = True
        if not improved:
            factor_scale *= 0.5
    write_best(PROJECT_DIR, best)
    save_json(PROJECT_DIR / "optimization_history.json", history)
    return best


def parse_existing_run(config: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    params_path = run_dir / "params.json"
    if not params_path.exists():
        raise SystemExit(f"missing params.json in {run_dir}")
    candidate = load_json(params_path)["candidate"]
    schematic_rel = work_relative_path(config, "schematic")
    comp_rel = work_relative_path(config, "compensator") if config.get("compensator") else None
    paths = RunPaths(
        run_dir=run_dir,
        work_dir=run_dir / "work",
        schematic=run_dir / "work" / schematic_rel,
        compensator=(run_dir / "work" / comp_rel) if comp_rel else None,
        script=run_dir / "startup.sxscr",
        run_log=run_dir / "run.log",
        sim_status=run_dir / "sim_status.txt",
        vectors_dir=run_dir / "vectors",
        screenshots_dir=run_dir / "screenshots",
        metrics_json=run_dir / "metrics.json",
        score_json=run_dir / "score.json",
        params_json=params_path,
        result_json=run_dir / "result.json",
    )
    launch = {"mode": "parse", "returncode": 0, "timed_out": False, "cmd": []}
    metrics = parse_metrics(config, paths, candidate, launch)
    score = evaluate(config, metrics)
    return save_result(config, paths, candidate, launch, metrics, score)


def extract_best_params(best_path: Path) -> dict[str, float]:
    data = load_json(best_path)
    params = data.get("params") or data.get("candidate")
    if not isinstance(params, dict):
        raise SystemExit(f"{best_path} must contain a params or candidate object")
    return {str(name): float(value) for name, value in params.items()}


def backup_file(path: Path, stamp: str) -> Path:
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, backup)
    return backup


def apply_best(config: dict[str, Any], best_path: Path) -> dict[str, Any]:
    params = extract_best_params(best_path)
    missing = [name for name in parameter_names(config) if name not in params]
    if missing:
        raise SystemExit("best file is missing active provided parameters: " + ", ".join(missing))
    candidate = normalize_candidate(config, params)
    schematic = project_path(config, "schematic")
    compensator = project_path(config, "compensator") if config.get("compensator") else None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backups = {"schematic": str(backup_file(schematic, stamp).resolve())}
    if compensator:
        backups["compensator"] = str(backup_file(compensator, stamp).resolve())
    paths = RunPaths(
        run_dir=PROJECT_DIR,
        work_dir=PROJECT_DIR,
        schematic=schematic,
        compensator=compensator,
        script=PROJECT_DIR / "apply_best_unused.sxscr",
        run_log=PROJECT_DIR / "apply_best_unused.log",
        sim_status=PROJECT_DIR / "apply_best_unused_status.txt",
        vectors_dir=PROJECT_DIR / "apply_best_unused_vectors",
        screenshots_dir=PROJECT_DIR / "apply_best_unused_screenshots",
        metrics_json=PROJECT_DIR / "apply_best_unused_metrics.json",
        score_json=PROJECT_DIR / "apply_best_unused_score.json",
        params_json=PROJECT_DIR / "apply_best_unused_params.json",
        result_json=PROJECT_DIR / "apply_best_unused_result.json",
    )
    inject_schematic_params(config, paths, candidate)
    inject_compensator_params(config, paths, candidate)
    result = {
        "target": PROVIDED_SOURCE,
        "best": str(best_path.resolve()),
        "written": {
            "schematic": str(schematic.resolve()),
            "compensator": str(compensator.resolve()) if compensator else None,
        },
        "backups": backups,
        "params": candidate,
    }
    save_json(PROJECT_DIR / "apply_best_result.json", result)
    return result


def apply_cli_param_overrides(config: dict[str, Any], candidate: dict[str, float], items: list[str]) -> dict[str, float]:
    updated = dict(candidate)
    active = set(parameter_names(config))
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--param must use NAME=VALUE syntax: {item}")
        name, value = item.split("=", 1)
        name = name.strip()
        if name not in active:
            raise SystemExit(f"parameter is not enabled in config: {name}")
        updated[name] = float(value)
    return updated


def load_config(path: Path) -> dict[str, Any]:
    config = load_json(path)
    config["__config_dir"] = str(path.resolve().parent)
    if not Path(str(config.get("runs_dir", ""))).is_absolute():
        config["runs_dir"] = str((path.resolve().parent / config["runs_dir"]).resolve())
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Buck Type III compensation and circuit parameter optimization runner")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--mode", choices=("mock", "dry-run", "real"), help="Execution mode")
    parser.add_argument("--timeout-s", type=float, help="Override SIMetrix launch timeout in seconds")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_source_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument("--source", choices=SOURCE_CHOICES, default=PROVIDED_SOURCE)

    p = sub.add_parser("validate")
    p.add_argument("--mode", choices=("mock", "dry-run", "real"), help=argparse.SUPPRESS)
    add_source_arg(p)

    p = sub.add_parser("generate-ideal")
    p.add_argument("--run", action="store_true", help="Also run the generated ideal buck after creation")
    p.add_argument("--no-netlist-check", dest="netlist_check", action="store_false", default=True)
    p.add_argument("--dry-run", action="store_true", help="Write generator scripts only; do not launch SIMetrix")
    p.add_argument("--timeout", type=float, help="Generator/SIMetrix timeout in seconds")

    p = sub.add_parser("run-once")
    p.add_argument("--mode", choices=("mock", "dry-run", "real"), help=argparse.SUPPRESS)
    add_source_arg(p)
    p.add_argument("--param", action="append", default=[], help="Override as NAME=VALUE, e.g. --param Lout=8.2e-7")

    p = sub.add_parser("optimize")
    p.add_argument("--mode", choices=("mock", "dry-run", "real"), help=argparse.SUPPRESS)
    add_source_arg(p)
    p.add_argument("--max-evals", type=int, default=14)
    p.add_argument("--coordinate-rounds", type=int, default=2)

    p = sub.add_parser("diagnose-real")
    p.add_argument("--source", choices=SOURCE_CHOICES, default=PROVIDED_SOURCE)
    p.add_argument("--mode", choices=("dry-run", "real"), default="dry-run")
    p.add_argument("--stage-timeout-s", type=float, default=60.0)

    p = sub.add_parser("apply-best")
    p.add_argument("--target", choices=(PROVIDED_SOURCE,), default=PROVIDED_SOURCE)
    p.add_argument("--best", type=Path, default=PROJECT_DIR / "best_params.json")

    p = sub.add_parser("parse")
    p.add_argument("--mode", choices=("mock", "dry-run", "real"), help=argparse.SUPPRESS)
    add_source_arg(p)
    p.add_argument("run_dir", type=Path)

    args = parser.parse_args(argv)
    base_config = load_config(args.config.resolve())
    if args.timeout_s is not None:
        base_config["timeout_s"] = float(args.timeout_s)

    if args.cmd == "generate-ideal":
        timeout_s = float(args.timeout or base_config.get("timeout_s", 240))
        result = generate_ideal(
            base_config,
            run=bool(args.run),
            netlist_check=bool(args.netlist_check),
            dry_run=bool(args.dry_run),
            timeout_s=timeout_s,
        )
        print(json.dumps(result, indent=2, allow_nan=False))
        return 1 if result.get("failed") else 0

    if args.cmd == "apply-best":
        config = source_config(base_config, PROVIDED_SOURCE)
        issues = validate_config(config, "dry-run")
        if issues:
            for issue in issues:
                print(issue, file=sys.stderr)
            return 2
        result = apply_best(config, args.best.resolve())
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0

    source = getattr(args, "source", PROVIDED_SOURCE)
    config = source_config(base_config, source)
    mode = args.mode or str(config.get("default_mode", "mock"))
    issues = validate_config(config, mode)
    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        return 2

    if args.cmd == "validate":
        print(json.dumps({"ok": True, "mode": mode, "source": source, "project_dir": str(PROJECT_DIR)}, indent=2))
        return 0
    if args.cmd == "run-once":
        cand = initial_candidate(config)
        cand = apply_cli_param_overrides(config, cand, args.param)
        result = run_candidate(config, cand, mode)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    if args.cmd == "optimize":
        best = optimize(config, mode, args.max_evals, args.coordinate_rounds)
        print(json.dumps({"best": best}, indent=2, allow_nan=False))
        return 0
    if args.cmd == "diagnose-real":
        result = diagnose_real(config, mode, float(args.stage_timeout_s))
        print(json.dumps(result, indent=2, allow_nan=False))
        return 1 if result.get("failed") else 0
    if args.cmd == "parse":
        result = parse_existing_run(config, args.run_dir.resolve())
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
