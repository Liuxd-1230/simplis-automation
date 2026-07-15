"""End-to-end compile, run, export, provenance, and validation orchestration."""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from bisect import bisect_right
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .compiler import compile_circuit
from .execution import JOB_STAGES, JobStateMachine, assess_simulation_status, build_simulation_script, validate_group_evidence
from .io import canonical_json, sha256_file, sha256_text, write_json
from .runtime import resolve_runtime
from .roundtrip import roundtrip_schematic
from .schema import load_experiment
from .vectors import build_vector_manifest, parse_show_file
from .verifier import verify_manifest
from .watchdog import WatchdogConfig, run_script_with_watchdog
from .waveform_validation import (
    validate_ac_response,
    validate_buck_waveforms,
    validate_catalog_ac_probe,
    validate_catalog_analog_waveforms,
    validate_catalog_and2_waveforms,
    validate_catalog_reactive_ic_waveforms,
    validate_sr_latch_waveforms,
    validate_timing_primitives_waveforms,
    write_ac_chart,
    write_catalog_proof_chart,
    write_sr_latch_chart,
    write_timing_primitives_chart,
    write_validation_charts,
)


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_").lower()


def _vector_slug(value: str) -> str:
    text = value[1:] if value.startswith("#") else value
    text = _slug(text) or "vector"
    return f"n{text}" if text[0].isdigit() else text


def _align_transient_vectors(
    parsed_by_role: Mapping[str, Mapping[str, Any]],
    roles: Mapping[str, Any],
    *,
    step_roles: set[str] | None = None,
) -> dict[str, list[float]]:
    """Align independent SIMPLIS event vectors on a fresh common time axis.

    SIMPLIS piecewise vectors carry their own Ref() axis; the group-level time
    vector can be empty and digital signals commonly have different event
    counts.  Electrical checks therefore must not zip unrelated y arrays.
    """

    available: dict[str, tuple[list[float], list[float]]] = {}
    for role in roles:
        parsed = parsed_by_role.get(str(role))
        if not isinstance(parsed, Mapping):
            continue
        x = [float(value) for value in parsed.get("x", [])]
        raw_y = list(parsed.get("y", []))
        if not x or len(x) != len(raw_y) or any(isinstance(value, Mapping) for value in raw_y):
            continue
        available[str(role)] = (x, [float(value) for value in raw_y])
    if not available:
        return {}
    start = max(values[0][0] for values in available.values())
    stop = min(values[0][-1] for values in available.values())
    if stop < start:
        return {}
    axis = sorted({moment for x, _y in available.values() for moment in x if start <= moment <= stop})
    if not axis:
        return {}
    stepped = set(step_roles or set())

    def sample(x: list[float], y: list[float], moment: float, step: bool) -> float:
        right = bisect_right(x, moment)
        if right <= 0:
            return y[0]
        if right >= len(x):
            return y[-1]
        left = right - 1
        if step or x[right] <= x[left]:
            return y[left]
        fraction = (moment - x[left]) / (x[right] - x[left])
        return y[left] + fraction * (y[right] - y[left])

    aligned = {"time": axis}
    for role, (x, y) in available.items():
        aligned[role] = [sample(x, y, moment, role in stepped) for moment in axis]
    return aligned


def _analysis_contract(experiment: Mapping[str, Any]) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    groups: dict[str, list[str]] = {}
    exports: list[dict[str, Any]] = []
    seen_exports: set[tuple[str, str, str]] = set()
    for analysis in experiment.get("analyses", []):
        if not isinstance(analysis, Mapping):
            continue
        group = str(analysis.get("group", ""))
        analysis_name = str(analysis.get("analysis", analysis.get("type", "")))
        vectors = analysis.get("vectors", {})
        if not group or not isinstance(vectors, Mapping):
            continue
        for role, native in vectors.items():
            vector = str(native)
            group_vectors = groups.setdefault(group, [])
            if vector not in group_vectors:
                group_vectors.append(vector)
            key = (group, str(role), vector)
            if key not in seen_exports:
                exports.append({"group": group, "analysis": analysis_name, "role": str(role), "vector": vector})
                seen_exports.add(key)
    return groups, exports


def _partition_analysis_experiments(
    experiment: Mapping[str, Any],
    *,
    circuit_path: Path,
) -> list[dict[str, Any]]:
    """Partition an experiment into clean simulator runs by analysis/group.

    Multiple behavior checks may intentionally consume the same fresh data
    group (for example startup and load-step checks over one TRAN waveform).
    Different simulator analyses or groups must never share a SIMetrix
    instance because a POP/AC failure can otherwise hide valid TRAN evidence.
    """

    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    order: list[tuple[str, str]] = []
    for index, raw in enumerate(experiment.get("analyses", [])):
        if not isinstance(raw, Mapping):
            continue
        analysis = deepcopy(dict(raw))
        analysis_name = str(analysis.get("analysis", analysis.get("type", ""))).strip().casefold()
        group = str(analysis.get("group", "")).strip().casefold()
        key = (analysis_name or f"analysis-{index + 1}", group or f"group-{index + 1}")
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(analysis)

    partitions: list[dict[str, Any]] = []
    for index, key in enumerate(order, start=1):
        isolated = deepcopy(dict(experiment))
        isolated.pop("_source_path", None)
        isolated["circuit"] = str(circuit_path)
        isolated["analyses"] = buckets[key]
        isolated["name"] = f"{experiment.get('name', circuit_path.stem)}__{key[0]}__{key[1]}"
        # Cross-analysis thresholds are evaluated once after all isolated
        # results return. Applying them here would make a TRAN run fail merely
        # because phase-margin metrics belong to the separate AC run.
        isolated["validation"] = {"thresholds": {}}
        analysis_overrides: dict[str, Any] = {}
        for analysis in buckets[key]:
            raw_overrides = analysis.get("parameters", {})
            if raw_overrides is None:
                continue
            if not isinstance(raw_overrides, Mapping):
                raise ValueError(f"Analysis parameters must be a mapping for {key[0]}/{key[1]}")
            for name, value in raw_overrides.items():
                if name in analysis_overrides and analysis_overrides[name] != value:
                    raise ValueError(f"Conflicting analysis parameter {name!r} for shared {key[0]}/{key[1]} run")
                analysis_overrides[str(name)] = value
        partitions.append(
            {
                "id": f"{index:02d}-{_slug(key[0])}-{_slug(key[1])}",
                "analysis": key[0],
                "group": key[1],
                "experiment": isolated,
                "parameter_overrides": analysis_overrides,
            }
        )
    return partitions


def _complex_response_ratio(
    parsed_by_role: Mapping[str, Mapping[str, Any]],
    response: Mapping[str, Any],
) -> dict[str, Any]:
    """Build an explicit signed complex numerator/denominator response."""

    numerator_role = str(response.get("numerator", ""))
    denominator_role = str(response.get("denominator", ""))
    missing = [role for role in (numerator_role, denominator_role) if role not in parsed_by_role]
    if missing:
        return {"valid": False, "errors": [{"code": "ac_response_vector_missing", "roles": missing}]}
    numerator = parsed_by_role[numerator_role]
    denominator = parsed_by_role[denominator_role]
    nx, dx = list(numerator.get("x", [])), list(denominator.get("x", []))
    ny, dy = list(numerator.get("y", [])), list(denominator.get("y", []))
    if len(nx) < 3 or len(nx) != len(dx) or len(nx) != len(ny) or len(nx) != len(dy):
        return {"valid": False, "errors": [{"code": "ac_response_axes_invalid"}]}
    if any(abs(float(left) - float(right)) > max(abs(float(left)), abs(float(right)), 1.0) * 1e-9 for left, right in zip(nx, dx)):
        return {"valid": False, "errors": [{"code": "ac_response_axes_mismatch"}]}

    def values(items: list[Any], role: str) -> list[complex] | None:
        output: list[complex] = []
        for item in items:
            if not isinstance(item, Mapping) or "real" not in item or "imag" not in item:
                return None
            try:
                value = complex(float(item["real"]), float(item["imag"]))
            except (TypeError, ValueError):
                return None
            if not (math.isfinite(value.real) and math.isfinite(value.imag)):
                return None
            output.append(value)
        return output

    numerator_values = values(ny, numerator_role)
    denominator_values = values(dy, denominator_role)
    if numerator_values is None or denominator_values is None:
        return {"valid": False, "errors": [{"code": "ac_response_not_complex"}]}
    if any(abs(value) <= 1e-30 for value in denominator_values):
        return {"valid": False, "errors": [{"code": "ac_response_denominator_zero"}]}
    sign = int(response.get("sign", 1))
    ratio = [sign * left / right for left, right in zip(numerator_values, denominator_values)]
    return {
        "valid": True,
        "frequency": [float(value) for value in nx],
        "response": [{"real": value.real, "imag": value.imag} for value in ratio],
        "provenance": {"numerator": numerator_role, "denominator": denominator_role, "sign": sign},
        "errors": [],
    }


def _f11_lines(experiment: Mapping[str, Any]) -> list[str]:
    lines = [".simulator SIMPLIS"]
    seen: set[str] = set()
    raw_directives = [
        str(directive.get("raw", "") if isinstance(directive, Mapping) else directive).strip()
        for analysis in experiment.get("analyses", [])
        if isinstance(analysis, Mapping)
        for directive in analysis.get("directives", [])
    ]
    has_transient = any(line.upper().startswith(".TRAN") for line in raw_directives)
    has_psp_npt = any("PSP_NPT" in line.upper() for line in raw_directives)
    if has_transient and not has_psp_npt:
        execution = experiment.get("execution", {}) if isinstance(experiment.get("execution"), Mapping) else {}
        sample_points = int(execution.get("transient_sample_points", 10001))
        if sample_points < 2:
            raise ValueError("execution.transient_sample_points must be at least 2")
        lines.extend((".OPTIONS", f"+ PSP_NPT={sample_points}"))
        seen.update(lines[-2:])
    for analysis in experiment.get("analyses", []):
        for directive in analysis.get("directives", []) if isinstance(analysis, Mapping) else []:
            if isinstance(directive, Mapping):
                text = str(directive.get("raw", "")).strip()
            else:
                text = str(directive).strip()
            if text and text not in seen:
                lines.append(text)
                seen.add(text)
    lines.append(".simulator DEFAULT")
    for name, value in dict(experiment.get("imported_variables", {})).items():
        lines.append(f".VAR {name} = {value}")
    return lines


def _script_string_expression(value: str) -> str:
    """Encode F11 text without letting SIMetrix evaluate literal braces."""

    parts: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            parts.append("'" + "".join(buffer) + "'")
            buffer.clear()

    for character in value:
        code = {"{": 123, "}": 125, "'": 39}.get(character)
        if code is None:
            buffer.append(character)
        else:
            flush()
            parts.append(f"Chr({code})")
    flush()
    return " & ".join(parts) if parts else "''"


def _prepare_analysis_schematic(
    schematic: Path,
    experiment: Mapping[str, Any],
    root: Path,
    runtime: Mapping[str, Any],
    timeout: float,
    config: WatchdogConfig | None = None,
) -> tuple[Path | None, dict[str, Any]]:
    lines = _f11_lines(experiment)
    if len(lines) <= 2:
        return None, {"ok": False, "code": "analysis_directives_missing"}
    if any("\r" in line or "\n" in line for line in lines):
        return None, {"ok": False, "code": "analysis_directive_not_script_safe", "lines": lines}
    prepared = root / "run-input.sxsch"
    script = root / "prepare-analysis.sxscr"
    message_log = root / "prepare-analysis.log"
    status = root / "prepare-analysis-status.txt"
    expression = "[" + ", ".join(_script_string_expression(line) for line in lines) + "]"
    script.write_text(
        "\n".join(
            (
                "Set EchoOn",
                "Let v2_startup_settle = Sleep(3)",
                f'RedirectMessages dup "{message_log}"',
                f"Let v2_status = OpenEchoFile('{status}', 'w')",
                "Echo script_started=true",
                "Echo preparation_started=true",
                "Let v2_close = CloseEchoFile()",
                f'OpenSchem /cd "{schematic}"',
                f'SaveAs /force "{prepared}"',
                f"Let v2_f11_written = WriteF11Lines({expression})",
                f'SaveAs /force "{prepared}"',
                f"Let v2_status = OpenEchoFile('{status}', 'a')",
                "Echo analysis_prepared={v2_f11_written}",
                "Echo preparation_returned=true",
                "Echo completion_token=true",
                "Let v2_close = CloseEchoFile()",
                "RedirectMessages flush",
                "RedirectMessages off",
                "CloseSchem /force",
                "Quit",
                "",
            )
        ),
        encoding="utf-8",
    )
    watchdog_config = config or WatchdogConfig(hard_timeout=timeout, stall_timeout=min(60.0, timeout), launch_timeout=min(15.0, timeout))
    process = run_script_with_watchdog(
        simetrix_exe=Path(str(runtime["executable"])),
        script=script,
        status_file=status,
        work_dir=root,
        job_id=f"analysis-preparation-{uuid.uuid4()}",
        config=watchdog_config,
        message_log=message_log,
        assess_completion=False,
    )
    status_text = status.read_text(encoding="utf-8", errors="replace") if status.is_file() else ""
    prepared_token = "analysis_prepared= 1" in status_text or "analysis_prepared=1" in status_text
    if not process.get("ok") or not prepared_token or not prepared.is_file():
        return None, {"ok": False, "code": "analysis_prepare_failed", "process": process, "status": status_text, "script": str(script), "message_log": str(message_log)}
    clean = roundtrip_schematic(prepared, root / "analysis-roundtrip", runtime=runtime, timeout=timeout)
    if not clean.get("ok"):
        return None, {"ok": False, "code": "analysis_clean_reopen_failed", "roundtrip": clean}
    return Path(clean["artifacts"]["schematic"]), {"ok": True, "lines": lines, "process": process, "roundtrip": clean}


def _status_events(status_file: Path, started_at: float, job_id: str) -> JobStateMachine:
    values: dict[str, str] = {}
    if status_file.is_file():
        for raw in status_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in raw:
                key, value = raw.split("=", 1)
                values[key.strip().casefold()] = value.strip()
    machine = JobStateMachine(job_id)
    machine.advance("launched", at=started_at)
    stage_keys = {
        "script_started": "script_started",
        "simulation_started": "simulation_started",
        "running": "running",
        "simulation_returned": "simulation_returned",
        "errors_collected": "errors_collected",
        "vectors_exported": "vector_export_done",
    }
    now = time.time()
    for stage in JOB_STAGES[1:7]:
        key = stage_keys[stage]
        if str(values.get(key, "")).casefold() not in {"1", "true", "yes"}:
            break
        machine.advance(stage, at=now, evidence={key: values[key]})
    return machine


def _write_result(root: Path, result: dict[str, Any]) -> dict[str, Any]:
    write_json(root / "experiment-result.json", result)
    return result


def _simulation_artifact_hashes(
    *,
    circuit: Path,
    schematic: Path,
    run_script: Path,
    preparation: Mapping[str, Any],
) -> dict[str, str]:
    paths: dict[str, Path] = {
        "circuit": circuit,
        "run_schematic": schematic,
        "run_script": run_script,
        "analysis_netlist": Path(str(preparation.get("roundtrip", {}).get("artifacts", {}).get("netlist", ""))),
        "analysis_reopen_netlist": Path(str(preparation.get("roundtrip", {}).get("artifacts", {}).get("reopen_netlist", ""))),
    }
    decks = sorted(
        (path for path in schematic.parent.rglob("*.deck") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
    )
    if decks:
        paths["simulation_deck"] = decks[-1]
    listings = sorted(
        (path for path in schematic.parent.rglob("*.lst") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
    )
    if listings:
        paths["simulation_listing"] = listings[-1]
    return {name: sha256_file(path) for name, path in paths.items() if path.is_file()}


def _simulation_info_matches_current_run(
    status_values: Mapping[str, Any],
    *,
    schematic: Path,
    run_root: Path,
    run_started_at: float,
) -> dict[str, Any]:
    """Tie GetSimulationInfo paths and analysis text to this isolated run."""

    normalized = {str(key).casefold(): value for key, value in status_values.items()}
    raw_path = str(normalized.get("simulation_info.netlist_path", "")).strip()
    raw_analysis = str(normalized.get("simulation_info.analysis", "")).strip()
    errors: list[dict[str, Any]] = []
    path: Path | None = None
    if not raw_path or raw_path.casefold() in {"none", "<none>"}:
        errors.append({"code": "simulation_info_netlist_missing"})
    else:
        path = Path(raw_path)
        try:
            path = path.resolve()
            stat = path.stat()
        except OSError:
            errors.append({"code": "simulation_info_netlist_unreadable", "path": raw_path})
        else:
            try:
                path.relative_to(run_root.resolve())
            except ValueError:
                errors.append({"code": "simulation_info_netlist_outside_run", "path": str(path)})
            if stat.st_mtime < float(run_started_at) - 1.0:
                errors.append({"code": "simulation_info_netlist_stale", "path": str(path)})
            if path.stem.casefold() != schematic.stem.casefold():
                errors.append({"code": "simulation_info_schematic_mismatch", "expected": schematic.stem, "actual": path.stem})
    if not raw_analysis or raw_analysis.casefold() in {"none", "<none>"}:
        errors.append({"code": "simulation_info_analysis_missing"})
    return {
        "matched": not errors,
        "errors": errors,
        "netlist_path": str(path) if path else raw_path,
        "analysis": raw_analysis,
        "netlist_sha256": sha256_file(path) if path and path.is_file() and not errors else None,
    }


def _threshold_errors(validations: list[dict[str, Any]], experiment: Mapping[str, Any]) -> list[dict[str, Any]]:
    metrics: dict[str, float] = {}
    for validation in validations:
        for name, value in dict(validation.get("metrics", {})).items():
            if isinstance(value, (int, float)):
                metrics[name] = float(value)
    thresholds = experiment.get("validation", {}).get("thresholds", {}) if isinstance(experiment.get("validation"), Mapping) else {}
    errors: list[dict[str, Any]] = []
    for name, contract in thresholds.items() if isinstance(thresholds, Mapping) else []:
        if name not in metrics:
            errors.append({"code": "threshold_metric_missing", "metric": name})
            continue
        if "min" in contract and metrics[name] < float(contract["min"]):
            errors.append({"code": "threshold_min_failed", "metric": name, "value": metrics[name], "minimum": contract["min"]})
        if "max" in contract and metrics[name] > float(contract["max"]):
            errors.append({"code": "threshold_max_failed", "metric": name, "value": metrics[name], "maximum": contract["max"]})
    return errors


def _catalog_proof_eligibility(
    *,
    trust: Mapping[str, Any],
    stage_tokens_complete: bool,
    group_evidence_valid: bool,
    vectors_fresh_complete: bool,
    validations: Sequence[Mapping[str, Any]],
    charts: Sequence[str],
    threshold_errors: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Allow behavior proof without pretending it is trusted simulation data.

    A local 8.4 installation may return ``None`` after a demonstrably returned
    SIMPLIS run.  Catalog approval may record that limitation when every other
    no-error, freshness, group, behavior, and chart proof passes.  No other
    completion error is relaxed and the result is never scoring eligible.
    """

    trust_errors = [dict(item) for item in trust.get("errors", []) if isinstance(item, Mapping)]
    trust_codes = {str(item.get("code", "")) for item in trust_errors}
    simulator_status = str(trust.get("simulator_status", "None")).strip().casefold()
    none_status_only = (
        not bool(trust.get("trusted"))
        and simulator_status == "none"
        and trust_codes == {"simulator_status_incomplete"}
    )
    completion_acceptable = bool(trust.get("trusted")) or none_status_only
    behavior_valid = bool(validations) and all(bool(item.get("valid")) for item in validations) and not threshold_errors
    checks = {
        "completion_errors_limited_to_none_status": completion_acceptable,
        "stage_tokens_complete": bool(stage_tokens_complete),
        "group_evidence_valid": bool(group_evidence_valid),
        "vectors_fresh_complete": bool(vectors_fresh_complete),
        "behavior_valid": bool(behavior_valid),
        "charts_present": bool(charts),
    }
    limitations: list[str] = []
    if none_status_only:
        limitations.append("simulator_status_none")
    errors = [
        {"code": "catalog_proof_requirement_failed", "requirement": name}
        for name, passed in checks.items()
        if not passed
    ]
    return {
        "eligible": not errors,
        "scoring_eligible": False,
        "checks": checks,
        "limitations": limitations,
        "trust_errors": trust_errors,
        "threshold_errors": [dict(item) for item in threshold_errors],
        "errors": errors,
    }


def _run_behavior_validations(
    experiment: Mapping[str, Any],
    parsed_by_role: Mapping[str, Mapping[str, Any]],
    chart_root: Path,
) -> tuple[list[dict[str, Any]], list[str]]:
    validations: list[dict[str, Any]] = []
    charts: list[str] = []
    for analysis in experiment.get("analyses", []):
        if not isinstance(analysis, Mapping):
            continue
        behavior = str(analysis.get("behavior", ""))
        if analysis.get("type") in {"startup", "load_step", "pop_ac"} and behavior == "buck":
            roles = analysis.get("vectors", {})
            waveforms = _align_transient_vectors(parsed_by_role, roles, step_roles={"PWM_HS", "PWM_LS"})
            validation = validate_buck_waveforms(waveforms, analysis.get("checks", {}))
            validations.append({"type": analysis["type"], **validation})
            if waveforms and all(name in waveforms for name in ("time", "VOUT", "RIPPLE", "PWM_HS", "PWM_LS", "IL", "ILOAD")):
                charts.extend(
                    write_validation_charts(
                        waveforms,
                        validation,
                        chart_root,
                        spec=analysis.get("checks", {}),
                        name=f"buck-{_slug(str(analysis['type']))}",
                    )
                )
        if analysis.get("analysis") == "ac" or analysis.get("type") == "pop_ac":
            response_contract = analysis.get("response")
            if isinstance(response_contract, Mapping):
                ratio = _complex_response_ratio(parsed_by_role, response_contract)
                if not ratio["valid"]:
                    validations.append({"type": "ac", "valid": False, "errors": ratio["errors"], "metrics": {}})
                else:
                    ac_validation = validate_ac_response(ratio["frequency"], ratio["response"])
                    validations.append({"type": "ac", "response_provenance": ratio["provenance"], **ac_validation})
                    charts.append(write_ac_chart(ratio["frequency"], ratio["response"], ac_validation, chart_root))
        if behavior == "sr_latch":
            roles = analysis.get("vectors", {})
            waveforms = _align_transient_vectors(parsed_by_role, roles, step_roles={"S", "R", "Q", "QN"})
            latch_validation = validate_sr_latch_waveforms(waveforms, spec=analysis.get("checks", {}))
            validations.append({"type": "sr_latch", **latch_validation})
            if all(name in waveforms for name in ("time", "S", "R", "Q", "QN")):
                charts.append(write_sr_latch_chart(waveforms, latch_validation, chart_root))
        if behavior == "timing_primitives":
            roles = analysis.get("vectors", {})
            waveforms = _align_transient_vectors(
                parsed_by_role,
                roles,
                step_roles={"PWM", "BUF", "BUF_BAR", "PWM_HS", "PWM_LS"},
            )
            timing_validation = validate_timing_primitives_waveforms(waveforms, analysis.get("checks", {}))
            validations.append({"type": "timing_primitives", **timing_validation})
            if all(name in waveforms for name in ("time", "PWM", "TON_OUT", "BUF", "BUF_BAR", "PWM_HS", "PWM_LS", "RAMP", "DSCH")):
                charts.append(write_timing_primitives_chart(waveforms, timing_validation, chart_root))
        if behavior == "catalog_analog_primitives":
            roles = analysis.get("vectors", {})
            waveforms = _align_transient_vectors(parsed_by_role, roles)
            analog_validation = validate_catalog_analog_waveforms(waveforms, analysis.get("checks", {}))
            validations.append({"type": behavior, **analog_validation})
            required = ("time", "CTRL", "VCVS_OUT", "VCCS_OUT", "DIODE_IN", "DIODE_OUT", "AC_IN_DC", "AC_OUT_DC")
            if all(name in waveforms for name in required):
                charts.append(write_catalog_proof_chart(waveforms, analog_validation, chart_root, behavior=behavior))
        if behavior == "catalog_ac_probe":
            ratio = _complex_response_ratio(
                parsed_by_role,
                {"numerator": "AC_OUT", "denominator": "AC_IN", "sign": 1},
            )
            if not ratio["valid"]:
                validations.append({"type": behavior, "valid": False, "errors": ratio["errors"], "metrics": {}})
            else:
                input_vector = parsed_by_role.get("AC_IN", {})
                input_response = input_vector.get("y") if isinstance(input_vector, Mapping) else None
                ac_probe_validation = validate_catalog_ac_probe(
                    ratio["frequency"],
                    ratio["response"],
                    analysis.get("checks", {}),
                    input_response=input_response,
                )
                validations.append({"type": behavior, "response_provenance": ratio["provenance"], **ac_probe_validation})
                charts.append(
                    write_catalog_proof_chart(
                        {"frequency": ratio["frequency"], "response": ratio["response"]},
                        ac_probe_validation,
                        chart_root,
                        behavior=behavior,
                    )
                )
        if behavior == "catalog_and2":
            roles = analysis.get("vectors", {})
            waveforms = _align_transient_vectors(parsed_by_role, roles, step_roles={"A", "B", "OUT", "OUT_BAR"})
            and2_validation = validate_catalog_and2_waveforms(waveforms, analysis.get("checks", {}))
            validations.append({"type": behavior, **and2_validation})
            if all(name in waveforms for name in ("time", "A", "B", "OUT", "OUT_BAR")):
                charts.append(write_catalog_proof_chart(waveforms, and2_validation, chart_root, behavior=behavior))
        if behavior == "catalog_reactive_ic":
            roles = analysis.get("vectors", {})
            waveforms = _align_transient_vectors(parsed_by_role, roles)
            reactive_validation = validate_catalog_reactive_ic_waveforms(waveforms, analysis.get("checks", {}))
            validations.append({"type": behavior, **reactive_validation})
            if all(name in waveforms for name in ("time", "CAP_V", "IND_I")):
                charts.append(write_catalog_proof_chart(waveforms, reactive_validation, chart_root, behavior=behavior))
    return validations, charts


def _run_single_experiment(
    experiment_path: str | Path,
    out_dir: str | Path,
    *,
    runtime: Mapping[str, Any] | str | Path | None = None,
    parameter_overrides: Mapping[str, Any] | None = None,
    require_watchdog_probe: bool = False,
    catalog_path: str | Path | None = None,
    catalog_proof_kinds: Iterable[str] = (),
) -> dict[str, Any]:
    """Run one candidate/analysis partition in an isolated evidence directory.

    Any machine, provenance, or electrical failure leaves vectors as
    diagnostic-only.  The function never reuses an earlier run's data group.
    """

    source = Path(experiment_path).resolve()
    root = Path(out_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    experiment = load_experiment(source)
    circuit_path = Path(str(experiment["circuit"]))
    if not circuit_path.is_absolute():
        circuit_path = source.parent / circuit_path
    circuit_path = circuit_path.resolve()
    run_id = str(uuid.uuid4())
    candidate = dict(parameter_overrides or {})
    proof_kinds = sorted({str(kind) for kind in catalog_proof_kinds})
    proof_mode = bool(proof_kinds)
    candidate_hash = sha256_text(canonical_json(candidate))
    result: dict[str, Any] = {
        "schema_version": "simplis-automation/v2/experiment-result",
        "ok": False,
        "classification": "infrastructure_failed",
        "run_id": run_id,
        "candidate": candidate,
        "candidate_hash": candidate_hash,
        "experiment": str(source),
        "circuit": str(circuit_path),
        "artifacts": {},
        "errors": [],
        "evidence_only": proof_mode,
        "scoring_eligible": False,
    }
    if proof_mode:
        result["catalog_proof_kinds"] = proof_kinds
    runtime_status = resolve_runtime(runtime)
    result["runtime"] = runtime_status
    if not runtime_status.get("ready") or not runtime_status.get("watchdog_ready"):
        result["errors"].append({"code": "runtime_not_watchdog_ready", "details": runtime_status.get("errors", [])})
        return _write_result(root, result)
    if require_watchdog_probe and not runtime_status.get("sxcommand_probe_ready", False):
        result["errors"].append({"code": "sxcommand_probe_not_proven"})
        return _write_result(root, result)
    if require_watchdog_probe and runtime_status.get("completion_capability") not in {"explicit_status", "calibrated_none_after_return"}:
        result["errors"].append({"code": "trusted_completion_capability_unavailable"})
        return _write_result(root, result)

    build_dir = root / "build"
    try:
        manifest = compile_circuit(
            circuit_path,
            build_dir,
            parameter_overrides=candidate,
            catalog_path=catalog_path,
            catalog_proof_kinds=proof_kinds,
        )
    except Exception as exc:
        result["classification"] = "netlist_failed"
        result["errors"].append({"code": "compile_failed", "reason": repr(exc)})
        return _write_result(root, result)
    schematic = Path(manifest["artifacts"]["schematic"])
    verification = verify_manifest(manifest["manifest_path"], runtime=runtime_status, timeout=float(experiment.get("execution", {}).get("hard_timeout_s", 240)))
    result["verification"] = verification
    if not verification.get("ok"):
        result["classification"] = "netlist_failed"
        result["errors"].append({"code": "clean_netlist_verification_failed", "details": verification.get("errors", [])})
        return _write_result(root, result)
    execution = experiment.get("execution", {})
    config = WatchdogConfig(
        poll_interval=float(execution.get("poll_interval_s", 2)),
        launch_timeout=float(execution.get("startup_timeout_s", 15)),
        stall_timeout=float(execution.get("stall_timeout_s", 60)),
        hard_timeout=float(execution.get("hard_timeout_s", 240)),
    )
    prepared_schematic, preparation = _prepare_analysis_schematic(
        schematic,
        experiment,
        root,
        runtime_status,
        float(experiment.get("execution", {}).get("hard_timeout_s", 240)),
        config=config,
    )
    result["analysis_preparation"] = preparation
    if prepared_schematic is None:
        result["classification"] = "netlist_failed"
        result["errors"].append({"code": "analysis_preparation_failed", "details": preparation})
        return _write_result(root, result)
    schematic = prepared_schematic
    source_hash = sha256_file(circuit_path)
    groups, export_contract = _analysis_contract(experiment)
    if not groups:
        result["errors"].append({"code": "vector_contract_missing"})
        return _write_result(root, result)

    status_file = root / "run-status.txt"
    message_log = root / "job-message.log"
    vector_dir = root / "vectors"
    vector_dir.mkdir(parents=True, exist_ok=True)
    run_script = root / "run.sxscr"
    run_script.write_text(
        build_simulation_script(
            schematic=schematic,
            status_file=status_file,
            message_log=message_log,
            required_vectors=groups,
            vector_dir=vector_dir,
        ),
        encoding="utf-8",
    )
    result["artifacts"].update({"build_manifest": manifest["manifest_path"], "run_script": str(run_script), "message_log": str(message_log)})
    started_at = time.time()
    run = run_script_with_watchdog(
        simetrix_exe=Path(str(runtime_status["executable"])),
        script=run_script,
        status_file=status_file,
        work_dir=root,
        job_id=run_id,
        candidate=candidate,
        warning_allowlist=execution.get("warning_allowlist", []),
        config=config,
        assess_completion=False,
    )
    result["execution"] = run
    state = _status_events(status_file, started_at, run_id)
    result["state_machine"] = state.as_dict()
    if not run.get("ok"):
        result["classification"] = "infrastructure_failed" if run.get("classification") in {"runtime_invalid", "process_dead", "hard_timeout", "stalled_unresponsive"} else "simulation_failed"
        result["errors"].append({"code": "execution_untrusted", "classification": run.get("classification")})
        return _write_result(root, result)

    group_contracts: dict[str, dict[str, Any]] = {}
    for item in export_contract:
        contract = group_contracts.setdefault(item["group"], {"analysis": item["analysis"], "vectors": []})
        contract["vectors"].append(item["vector"])
    group_evidence = validate_group_evidence(run.get("status_values", {}), group_contracts)
    result["group_evidence"] = group_evidence
    if not group_evidence["valid"]:
        result["classification"] = "data_failed"
        result["errors"].extend(group_evidence["errors"])
        return _write_result(root, result)

    exports: list[dict[str, Any]] = []
    parsed_by_role: dict[str, Any] = {}
    for item in export_contract:
        path = vector_dir / f"{_slug(item['group'])}_{_vector_slug(item['vector'])}.txt"
        record = {**item, "path": str(path)}
        exports.append(record)
        if path.is_file():
            parsed_by_role[item["role"]] = parse_show_file(path)
    simulation_info_hash = sha256_text(
        canonical_json(
            {
                key: value
                for key, value in dict(run.get("status_values", {})).items()
                if str(key).casefold().startswith("simulation_info")
            }
        )
    )
    artifact_hashes = _simulation_artifact_hashes(
        circuit=circuit_path,
        schematic=schematic,
        run_script=run_script,
        preparation=preparation,
    )
    vector_manifest = build_vector_manifest(
        run_id=run_id,
        candidate_hash=candidate_hash,
        source_hash=source_hash,
        run_started_at=started_at,
        exports=exports,
        output_path=root / "vector-manifest.json",
        trusted=False,
        simulation_info_hash=simulation_info_hash,
        artifact_hashes=artifact_hashes,
    )
    result["vector_manifest"] = vector_manifest
    vectors_fresh_complete = not vector_manifest["errors"] and len(vector_manifest["vectors"]) == len(exports) and all(
        item.get("fresh") and item.get("finite") and item.get("monotonic") and int(item.get("samples", 0)) > 0
        for item in vector_manifest["vectors"]
    )
    simulation_info = _simulation_info_matches_current_run(
        run.get("status_values", {}),
        schematic=schematic,
        run_root=root,
        run_started_at=started_at,
    )
    result["simulation_info_evidence"] = simulation_info
    completion_evidence = {
        "watchdog_progress_observed": any(
            str(item.get("simulator_status", "")).casefold() == "inprogress" for item in run.get("timeline", [])
        )
        or any(index > 0 and item.get("progressed") is True for index, item in enumerate(run.get("timeline", []))),
        "stage_tokens_complete": len(state.events) == 7,
        "simulation_info_matches": simulation_info["matched"],
        "vectors_fresh_complete": vectors_fresh_complete and group_evidence["valid"],
    }
    trust = assess_simulation_status(
        run.get("status_values", {}),
        warning_allowlist=execution.get("warning_allowlist", []),
        recent_error_files=run.get("recent_error_files", []),
        completion_capability=str(runtime_status.get("completion_capability", "unavailable")),
        completion_evidence=completion_evidence,
    )
    result["completion_evidence"] = completion_evidence
    result["execution_trust"] = trust
    validations, charts = _run_behavior_validations(experiment, parsed_by_role, root / "charts")
    evidence_classification = "catalog_evidence_only" if proof_mode else ("scoring_eligible" if trust["trusted"] and vectors_fresh_complete else "diagnostic_only")
    for validation in validations:
        validation["data_classification"] = evidence_classification
    result["validation"] = validations
    result["charts"] = charts
    threshold_errors = _threshold_errors(validations, experiment)
    result["threshold_errors"] = threshold_errors
    behavior_evidence_complete = bool(validations) and all(bool(item.get("valid")) for item in validations) and not threshold_errors and bool(charts)

    if proof_mode:
        proof = _catalog_proof_eligibility(
            trust=trust,
            stage_tokens_complete=len(state.events) == 7,
            group_evidence_valid=bool(group_evidence["valid"]),
            vectors_fresh_complete=vectors_fresh_complete,
            validations=validations,
            charts=charts,
            threshold_errors=threshold_errors,
        )
        if str(runtime_status.get("completion_capability", "unavailable")) == "unavailable":
            proof["limitations"].append("trusted_completion_capability_unavailable")
        if not simulation_info["matched"]:
            proof["limitations"].append("simulation_info_not_matched")
        proof["limitations"] = sorted(set(proof["limitations"]))
        proof["proof_kinds"] = proof_kinds
        proof["behavior_proven"] = bool(proof["eligible"])
        proof["normal_simulation_complete"] = False
        proof["normal_completion_trusted"] = bool(trust["trusted"])
        proof["simulation_info_errors"] = list(simulation_info.get("errors", []))
        result["catalog_proof"] = proof
        if proof["eligible"] and len(state.events) == 7:
            state.advance("validated", at=time.time(), evidence={"validations": len(validations), "charts": len(charts), "catalog_proof_only": True})
            result["ok"] = True
            result["classification"] = "catalog_proof_passed"
        else:
            result["classification"] = "catalog_proof_failed"
            result["errors"].extend(proof["errors"])
        result["state_machine"] = state.as_dict()
        return _write_result(root, result)

    if not trust["trusted"]:
        result["classification"] = "simulation_failed"
        result["errors"].extend(trust["errors"])
        return _write_result(root, result)
    if not vectors_fresh_complete:
        result["classification"] = "data_failed"
        result["errors"].extend(vector_manifest["errors"])
        return _write_result(root, result)
    vector_manifest = build_vector_manifest(
        run_id=run_id,
        candidate_hash=candidate_hash,
        source_hash=source_hash,
        run_started_at=started_at,
        exports=exports,
        output_path=root / "vector-manifest.json",
        trusted=True,
        simulation_info_hash=simulation_info_hash,
        artifact_hashes=artifact_hashes,
    )
    result["vector_manifest"] = vector_manifest
    if not behavior_evidence_complete:
        result["classification"] = "electrical_behavior_failed"
        result["errors"].append({"code": "behavior_or_chart_evidence_failed", "threshold_errors": threshold_errors})
        return _write_result(root, result)
    if len(state.events) == 7:
        state.advance("validated", at=time.time(), evidence={"validations": len(validations), "charts": len(charts)})
        state.advance("complete", at=time.time(), evidence={"trusted": True})
    result["state_machine"] = state.as_dict()
    result["ok"] = state.complete
    result["classification"] = "complete" if result["ok"] else "state_machine_incomplete"
    result["scoring_eligible"] = bool(result["ok"])
    return _write_result(root, result)


def _aggregate_analysis_classification(results: Sequence[Mapping[str, Any]], *, proof_mode: bool) -> str:
    if results and all(bool(item.get("ok")) for item in results):
        return "catalog_proof_passed" if proof_mode else "complete"
    priority = (
        "infrastructure_failed",
        "netlist_failed",
        "simulation_failed",
        "data_failed",
        "electrical_behavior_failed",
        "state_machine_incomplete",
        "catalog_proof_failed",
    )
    classifications = [str(item.get("classification", "infrastructure_failed")) for item in results]
    for name in priority:
        if name in classifications:
            return name
    return classifications[0] if classifications else "infrastructure_failed"


def run_experiment(
    experiment_path: str | Path,
    out_dir: str | Path,
    *,
    runtime: Mapping[str, Any] | str | Path | None = None,
    parameter_overrides: Mapping[str, Any] | None = None,
    require_watchdog_probe: bool = False,
    catalog_path: str | Path | None = None,
    catalog_proof_kinds: Iterable[str] = (),
) -> dict[str, Any]:
    """Run each simulator analysis/group in a clean isolated instance.

    Startup and load-step checks may share one explicitly declared TRAN group.
    POP/AC and any other analysis/group receive their own compile, clean reopen,
    simulator process, vectors, trust decision and result file.  The aggregate
    result is scoring-eligible only when every isolated run is complete.
    """

    source = Path(experiment_path).resolve()
    root = Path(out_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    experiment = load_experiment(source)
    circuit_path = Path(str(experiment["circuit"]))
    if not circuit_path.is_absolute():
        circuit_path = source.parent / circuit_path
    circuit_path = circuit_path.resolve()
    partitions = _partition_analysis_experiments(experiment, circuit_path=circuit_path)
    if len(partitions) <= 1:
        effective_overrides = dict(parameter_overrides or {})
        if partitions:
            effective_overrides.update(dict(partitions[0].get("parameter_overrides", {})))
        return _run_single_experiment(
            source,
            root,
            runtime=runtime,
            parameter_overrides=effective_overrides,
            require_watchdog_probe=require_watchdog_probe,
            catalog_path=catalog_path,
            catalog_proof_kinds=catalog_proof_kinds,
        )

    candidate = dict(parameter_overrides or {})
    proof_kinds = sorted({str(kind) for kind in catalog_proof_kinds})
    proof_mode = bool(proof_kinds)
    run_id = str(uuid.uuid4())
    subresults: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []
    charts: list[str] = []
    errors: list[dict[str, Any]] = []
    artifacts: list[str] = []

    for partition in partitions:
        analysis_id = str(partition["id"])
        analysis_root = root / "analysis-runs" / analysis_id
        analysis_root.mkdir(parents=True, exist_ok=True)
        isolated_path = analysis_root / "isolated-experiment.json"
        write_json(isolated_path, partition["experiment"])
        effective_candidate = {**candidate, **dict(partition.get("parameter_overrides", {}))}
        try:
            subresult = _run_single_experiment(
                isolated_path,
                analysis_root,
                runtime=runtime,
                parameter_overrides=effective_candidate,
                require_watchdog_probe=require_watchdog_probe,
                catalog_path=catalog_path,
                catalog_proof_kinds=proof_kinds,
            )
        except Exception as exc:  # Keep other isolated analyses recoverable.
            subresult = {
                "ok": False,
                "classification": "infrastructure_failed",
                "errors": [{"code": "isolated_analysis_exception", "reason": repr(exc)}],
                "validation": [],
                "charts": [],
                "scoring_eligible": False,
            }
            write_json(analysis_root / "experiment-result.json", subresult)
        subresults.append(subresult)
        result_path = analysis_root / "experiment-result.json"
        artifacts.append(str(result_path))
        summary = {
            "id": analysis_id,
            "analysis": partition["analysis"],
            "group": partition["group"],
            "isolated_experiment": str(isolated_path),
            "result": str(result_path),
            "run_id": subresult.get("run_id"),
            "ok": bool(subresult.get("ok")),
            "classification": str(subresult.get("classification", "infrastructure_failed")),
            "scoring_eligible": bool(subresult.get("scoring_eligible", subresult.get("ok", False))),
            "completion_evidence": subresult.get("completion_evidence", {}),
            "effective_parameters": effective_candidate,
        }
        summaries.append(summary)
        for validation in subresult.get("validation", []):
            if isinstance(validation, Mapping):
                validations.append({"analysis_run": analysis_id, **dict(validation)})
        charts.extend(str(path) for path in subresult.get("charts", []))
        for item in subresult.get("errors", []):
            if isinstance(item, Mapping):
                errors.append({"analysis_run": analysis_id, **dict(item)})
            else:
                errors.append({"analysis_run": analysis_id, "code": "unstructured_error", "details": item})

    isolated_runs_ok = bool(subresults) and all(bool(item.get("ok")) for item in subresults)
    threshold_errors = _threshold_errors(validations, experiment)
    all_ok = isolated_runs_ok and not threshold_errors
    scoring_eligible = all_ok and all(bool(item.get("scoring_eligible", item.get("ok", False))) for item in subresults)
    classification = _aggregate_analysis_classification(subresults, proof_mode=proof_mode)
    if isolated_runs_ok and threshold_errors:
        classification = "catalog_proof_failed" if proof_mode else "electrical_behavior_failed"
        errors.append({"code": "aggregate_thresholds_failed", "threshold_errors": threshold_errors})
    result: dict[str, Any] = {
        "schema_version": "simplis-automation/v2/experiment-result",
        "ok": all_ok,
        "classification": classification,
        "run_id": run_id,
        "candidate": candidate,
        "candidate_hash": sha256_text(canonical_json(candidate)),
        "experiment": str(source),
        "circuit": str(circuit_path),
        "runtime": subresults[0].get("runtime", {}) if subresults else {},
        "analysis_isolation": {
            "enabled": True,
            "run_count": len(summaries),
            "all_returned": len(summaries) == len(partitions),
        },
        "analysis_runs": summaries,
        "artifacts": {"analysis_results": artifacts},
        "validation": validations,
        "charts": charts,
        "threshold_errors": threshold_errors,
        "errors": errors,
        "completion_evidence": {
            "isolated_analysis_runs": len(summaries),
            "all_analysis_runs_complete": isolated_runs_ok,
            "aggregate_thresholds_passed": not threshold_errors,
        },
        "evidence_only": proof_mode,
        "scoring_eligible": scoring_eligible,
        "state_machine": {"job_id": run_id, "complete": all_ok, "events": []},
    }
    if proof_mode:
        eligible = all(bool(item.get("catalog_proof", {}).get("eligible")) for item in subresults) and not threshold_errors
        result["catalog_proof_kinds"] = proof_kinds
        result["catalog_proof"] = {
            "eligible": eligible,
            "behavior_proven": eligible,
            "normal_simulation_complete": False,
            "scoring_eligible": False,
            "analysis_runs": summaries,
        }
    return _write_result(root, result)
