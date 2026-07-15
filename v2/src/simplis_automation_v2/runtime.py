"""Local SIMetrix/SIMPLIS runtime discovery for v2.

The compiler deliberately does not launch SIMetrix.  This module is the small,
side-effect-free boundary that turns either an explicit runtime mapping or the
local installation into an auditable runtime record.  Callers must check the
``ok`` field rather than assuming that discovery succeeded.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

import yaml

from .execution import COMPLETION_CAPABILITIES, _as_messages, _truthy
from .io import sha256_file


_EXECUTABLE_KEYS = ("executable", "simetrix_exe", "exe")
_SYMBOL_LIBRARY_KEYS = ("symbol_library", "symbol_lib_dir", "symbol_library_dir", "symbol_library_path")
_COMMON_EXECUTABLES = (
    Path("D:/Simplis8.4/bin64/SIMetrix.exe"),
    Path("D:/SIMetrix840/bin64/SIMetrix.exe"),
    Path("C:/Program Files/SIMetrix840/bin64/SIMetrix.exe"),
    Path("D:/SIMetrix830/bin64/SIMetrix.exe"),
    Path("C:/Program Files/SIMetrix830/bin64/SIMetrix.exe"),
    Path("C:/Program Files/SIMetrix/bin64/SIMetrix.exe"),
)


def _error(code: str, message: str, **details: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"code": code, "message": message}
    if details:
        item["details"] = details
    return item


def _as_path(value: Any) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    return Path(text).expanduser() if text else None


def _first_value(values: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = values.get(key)
        if value is not None and str(value).strip():
            return value
    return None


def _load_runtime_mapping(runtime: Mapping[str, Any] | str | Path | None) -> tuple[dict[str, Any], str | None, list[dict[str, Any]]]:
    """Normalize the optional runtime input without raising user-facing errors."""

    if runtime is None:
        return {}, None, []
    if isinstance(runtime, Mapping):
        return dict(runtime), "explicit_mapping", []

    source = Path(runtime).expanduser()
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, str(source), [_error("runtime_config_unreadable", "Cannot read runtime configuration", path=str(source), reason=str(exc))]
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            return {}, str(source), [_error("runtime_config_invalid", "Runtime configuration is not valid YAML or JSON", path=str(source), reason=str(exc))]
    if not isinstance(raw, dict):
        return {}, str(source), [_error("runtime_config_invalid", "Runtime configuration root must be an object", path=str(source))]
    return dict(raw), str(source), []


def _candidate_executable(config: Mapping[str, Any]) -> tuple[Path | None, str | None]:
    explicit = _as_path(_first_value(config, _EXECUTABLE_KEYS))
    if explicit is not None:
        return explicit, "explicit"
    environment = _as_path(os.environ.get("SIMETRIX_EXE"))
    if environment is not None:
        return environment, "environment:SIMETRIX_EXE"
    for candidate in _COMMON_EXECUTABLES:
        if candidate.exists():
            return candidate, "default"
    return None, None


def _candidate_symbol_library(config: Mapping[str, Any], executable: Path | None) -> tuple[Path | None, str | None]:
    explicit = _as_path(_first_value(config, _SYMBOL_LIBRARY_KEYS))
    if explicit is not None:
        return explicit, "explicit"
    environment = _as_path(os.environ.get("SIMPLIS_SYMBOL_LIB_DIR"))
    if environment is not None:
        return environment, "environment:SIMPLIS_SYMBOL_LIB_DIR"
    if executable is not None:
        # D:/SIMetrix830/bin64/SIMetrix.exe -> D:/SIMetrix830/support/symbollibs
        try:
            candidate = executable.resolve().parents[1] / "support" / "symbollibs"
        except IndexError:
            candidate = executable.parent / "support" / "symbollibs"
        return candidate, "derived_from_executable"
    return None, None


def _infer_version(config: Mapping[str, Any], executable: Path | None) -> str | None:
    explicit = config.get("version") or config.get("simetrix_version")
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()
    if executable is None:
        return None
    text = str(executable)
    # SIMetrix's Windows install name normally contains SIMetrix830.  Do not
    # execute the binary for version discovery: doctor must remain safe to run
    # in CI and does not need a GUI session.
    match = re.search(r"(?i)(?:simetrix|simplis)[^0-9]*(\d{2,4}|\d(?:[.]\d)?)", text)
    if not match:
        return None
    raw = match.group(1)
    if "." in raw:
        return raw
    if len(raw) == 3:
        # Windows folders use 830/840 for the 8.3/8.4 product line.
        return f"{raw[0]}.{raw[1]}"
    if len(raw) == 2:
        return f"{raw[0]}.{raw[1]}"
    if len(raw) == 4:
        return f"{raw[:1]}.{raw[1:]}"
    return raw


def _catalog_record(config: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = config.get("catalog")
    if not isinstance(raw, Mapping):
        return None
    path = _as_path(raw.get("path"))
    expected = raw.get("fingerprint") or raw.get("sha256")
    record: dict[str, Any] = {
        "path": str(path.resolve()) if path and path.exists() else (str(path) if path else None),
        "expected_fingerprint": str(expected) if expected else None,
        "fingerprint": None,
        "status": "not_provided",
    }
    if path is None:
        record["status"] = "missing_path"
    elif not path.is_file():
        record["status"] = "missing"
    else:
        try:
            # v2 catalog fingerprints are semantic hashes rather than raw file
            # hashes, so benign YAML whitespace changes do not invalidate a
            # reviewed device catalog.
            from .catalog import load_catalog

            record["fingerprint"] = str(load_catalog(path)["fingerprint"])
            record["file_sha256"] = sha256_file(path)
            record["status"] = "matched" if expected and record["fingerprint"] == str(expected) else ("computed" if not expected else "mismatch")
        except Exception as exc:
            record["status"] = "invalid"
            record["reason"] = str(exc)
    return record


def resolve_runtime(
    runtime: Mapping[str, Any] | str | Path | None = None,
    *,
    simetrix_exe: str | Path | None = None,
    symbol_library_dir: str | Path | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Resolve a local SIMetrix runtime into a JSON-safe status record.

    ``runtime`` may be a mapping or a JSON file with ``executable`` (or the
    legacy ``simetrix_exe``) and ``symbol_library`` (or ``symbol_lib_dir``).
    ``simetrix_exe``, ``symbol_library_dir`` and ``config_path`` are convenient
    CLI-facing overrides; explicit path arguments take precedence over config.
    The function never starts SIMetrix and never raises for an invalid local
    installation; invalid input is represented by ``ok: false`` and structured
    diagnostics.  This makes it suitable for both ``doctor`` and verifier
    preflight checks.
    """

    config: dict[str, Any] = {}
    sources: list[str] = []
    errors: list[dict[str, Any]] = []
    if config_path is not None:
        file_config, source_name, file_errors = _load_runtime_mapping(config_path)
        config.update(file_config)
        if source_name:
            sources.append(source_name)
        errors.extend(file_errors)
    if runtime is not None:
        supplied_config, source_name, supplied_errors = _load_runtime_mapping(runtime)
        config.update(supplied_config)
        if source_name:
            sources.append(source_name)
        errors.extend(supplied_errors)
    if simetrix_exe is not None:
        config["executable"] = str(simetrix_exe)
        sources.append("argument:simetrix_exe")
    if symbol_library_dir is not None:
        config["symbol_library"] = str(symbol_library_dir)
        sources.append("argument:symbol_library_dir")
    executable, executable_source = _candidate_executable(config)
    symbol_library, symbol_source = _candidate_symbol_library(config, executable)

    executable_exists = bool(executable and executable.is_file())
    symbol_exists = bool(symbol_library and symbol_library.is_dir())
    if executable is None:
        errors.append(
            _error(
                "missing_executable",
                "SIMetrix executable is not configured or discoverable",
                environment="SIMETRIX_EXE",
            )
        )
    elif not executable_exists:
        errors.append(_error("missing_executable", "SIMetrix executable does not exist", path=str(executable)))
    if symbol_library is None:
        errors.append(
            _error(
                "missing_symbol_library",
                "SIMPLIS symbol library is not configured or derivable",
                environment="SIMPLIS_SYMBOL_LIB_DIR",
            )
        )
    elif not symbol_exists:
        errors.append(_error("missing_symbol_library", "SIMPLIS symbol library does not exist", path=str(symbol_library)))

    version = _infer_version(config, executable)
    catalog = _catalog_record(config)
    symbol_count = len(list(symbol_library.glob("*.sxslb"))) if symbol_exists and symbol_library else 0
    if symbol_exists and symbol_count == 0:
        errors.append(
            _error(
                "empty_symbol_library",
                "SIMPLIS symbol library directory contains no .sxslb files",
                path=str(symbol_library),
            )
        )
    executable_text = str(executable.resolve()) if executable_exists and executable else (str(executable) if executable else None)
    symbol_text = str(symbol_library.resolve()) if symbol_exists and symbol_library else (str(symbol_library) if symbol_library else None)
    supported_version = bool(version and version.startswith("8.4"))
    warnings: list[dict[str, Any]] = []
    if not supported_version:
        warnings.append(
            _error(
                "runtime_version_unconfirmed",
                "SIMetrix/SIMPLIS 8.4 could not be confirmed from the configured runtime",
                version=version,
            )
        )

    sxcommand = executable.with_name("SxCommand.exe") if executable_exists and executable else None
    sxcommand_exists = bool(sxcommand and sxcommand.is_file())
    if executable_exists and not sxcommand_exists:
        warnings.append(
            _error(
                "sxcommand_missing",
                "SxCommand.exe is unavailable; watchdog status probing and automatic optimization are disabled",
                path=str(sxcommand) if sxcommand else None,
            )
        )

    requested_capability = str(config.get("completion_capability", "unavailable")).strip().casefold()
    capability_evidence = config.get("completion_capability_evidence")
    if requested_capability not in COMPLETION_CAPABILITIES:
        warnings.append(_error("completion_capability_invalid", "Configured completion capability is not recognized", value=requested_capability))
        requested_capability = "unavailable"
    if requested_capability != "unavailable" and not (isinstance(capability_evidence, Mapping) and capability_evidence.get("passed") is True):
        warnings.append(_error("completion_capability_unproven", "Trusted completion capability requires persisted doctor evidence", value=requested_capability))
        requested_capability = "unavailable"

    return {
        "ok": not errors,
        "status": "ready" if not errors else "invalid",
        "ready": bool(not errors and supported_version),
        "executable": executable_text,
        "symbol_library": symbol_text,
        # Compatibility aliases are useful for callers migrating from v1.
        "simetrix_exe": executable_text,
        "symbol_lib_dir": symbol_text,
        "symbol_library_dir": symbol_text,
        "executable_source": executable_source,
        "symbol_library_source": symbol_source,
        "config_source": sources or None,
        "symbol_library_count": symbol_count,
        "version": version,
        "version_supported": supported_version,
        "sxcommand_exe": str(sxcommand.resolve()) if sxcommand_exists and sxcommand else (str(sxcommand) if sxcommand else None),
        "sxcommand_exists": sxcommand_exists,
        "watchdog_ready": bool(not errors and supported_version and sxcommand_exists),
        "sxcommand_probe": config.get("sxcommand_probe"),
        "sxcommand_probe_ready": config.get("sxcommand_probe_ready") is True,
        "completion_capability": requested_capability,
        "completion_capability_evidence": dict(capability_evidence) if isinstance(capability_evidence, Mapping) else None,
        "catalog": catalog,
        "errors": errors,
        "warnings": warnings,
    }


def completion_capability_from_evidence(
    status_values: Mapping[str, Any],
    *,
    timeline: list[Mapping[str, Any]],
    vector_fresh_complete: bool,
    simulation_info_matches: bool,
) -> dict[str, Any]:
    """Classify an observed doctor run without inferring success from data alone."""

    try:
        exit_code = int(str(status_values.get("simplis_exit_code", "-1")).strip())
    except ValueError:
        exit_code = -1
    status = str(status_values.get("simulator_status", "")).strip()
    progress = any(str(item.get("simulator_status", "")).casefold() == "inprogress" for item in timeline) or any(
        index > 0 and item.get("progressed") is True for index, item in enumerate(timeline)
    )
    stage_tokens = all(
        _truthy(status_values.get(name))
        for name in ("script_started", "simulation_started", "running", "simulation_returned", "errors_collected", "vector_export_done", "completion_token")
    )
    errors_empty = not _truthy(status_values.get("simulation_has_errors")) and not _as_messages(status_values.get("simulation_errors"))
    base = exit_code == 0 and stage_tokens and errors_empty and vector_fresh_complete and simulation_info_matches
    normalized = status.casefold()
    if base and normalized in {"complete", "warnings"}:
        capability = "explicit_status"
    elif base and normalized == "none" and progress:
        capability = "calibrated_none_after_return"
    else:
        capability = "unavailable"
    return {
        "passed": capability != "unavailable",
        "capability": capability,
        "status": status,
        "exit_code": exit_code,
        "stage_tokens_complete": stage_tokens,
        "errors_empty": errors_empty,
        "watchdog_progress_observed": progress,
        "vector_fresh_complete": bool(vector_fresh_complete),
        "simulation_info_matches": bool(simulation_info_matches),
    }


def probe_sxcommand_immediate(runtime: Mapping[str, Any], work_dir: str | Path) -> dict[str, Any]:
    """Prove that the installed command server can execute an immediate query."""

    from .watchdog import query_simulator_status, simetrix_processes, sxcommand_path

    executable_value = runtime.get("executable")
    if not executable_value:
        return {"ready": False, "code": "executable_missing"}
    executable = Path(str(executable_value)).resolve()
    sxcommand = sxcommand_path(executable)
    if not sxcommand.is_file():
        return {"ready": False, "code": "sxcommand_missing", "path": str(sxcommand)}
    existing = simetrix_processes()
    if existing:
        return {"ready": False, "code": "existing_instance_prevents_isolated_probe", "pids": [item.get("pid") for item in existing]}
    root = Path(work_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    log = root / "sxcommand-probe.log"
    script = root / "sxcommand-probe.sxscr"
    status = root / "sxcommand-probe-result.json"
    for path in (log, status):
        if path.exists():
            path.unlink()
    script.write_text(
        "\n".join(
            (
                "Set EchoOn",
                f'RedirectMessages dup "{log}"',
                "Let v2_probe_sleep = Sleep(8)",
                "RedirectMessages flush",
                "RedirectMessages off",
                "Quit",
                "",
            )
        ),
        encoding="utf-8",
    )
    process = subprocess.Popen([str(executable), "/s", str(script)], cwd=str(root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 6.0
    while time.time() < deadline and not log.is_file():
        if process.poll() is not None:
            break
        time.sleep(0.2)
    time.sleep(1.0)
    queried = query_simulator_status(sxcommand, message_log=log, status_file=root / "sxcommand-immediate-status.txt", timeout=3.0)
    try:
        process.wait(timeout=12.0)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False, capture_output=True, text=True)
    ready = queried in {"None", "Paused", "InProgress", "ConvergenceFail", "SimErrors", "NetlistErrors", "Warnings", "Complete"}
    record = {"ready": ready, "status": queried, "sxcommand": str(sxcommand), "script": str(script), "message_log": str(log)}
    status.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def _completion_probe_script(schematic: Path, status_file: Path, message_log: Path, vector: Path) -> str:
    """Build a self-contained schematic probe using the GUI-equivalent run path."""

    return "\n".join(
        (
            "Set EchoOn",
            "Set precision = 16",
            f'RedirectMessages dup "{message_log}"',
            f"Let v2_status = OpenEchoFile('{status_file}', 'w')",
            "Echo script_started=true",
            "Let v2_close = CloseEchoFile()",
            "Let v2_settle = Sleep(3)",
            # Keep creation, SaveAs and simplis_run in the startup script's
            # active document window. Closing and reopening a just-created
            # sheet in the same startup script can leave SIMetrix 8.4's script
            # context on an untitled document; simplis_run then blocks on a
            # hidden "Save Schematic?" prompt even though the file exists.
            "NewSchem /simulator SIMPLIS v2_completion_probe",
            "Unselect",
            "Inst /select /loc 0 0 0 dc_source",
            "Prop /hideNew REF V1",
            "Prop /hideNew VALUE 1",
            "Unselect",
            "Inst /select /loc 2160 0 3 res",
            "Prop /hideNew REF R1",
            "Prop /hideNew VALUE 1000",
            "Unselect",
            "Inst /select /loc 4320 0 0 cap",
            "Prop /hideNew REF C1",
            "Prop /hideNew VALUE 1e-09",
            "Unselect",
            "Inst /select /loc 6480 0 0 probev_new",
            "Prop /hideNew REF P1",
            "Prop /hideNew Label VOUT",
            "Unselect",
            "Inst /loc 0 0 2 term VALUE VIN",
            "Inst /loc 0 480 0 gnd",
            "Inst /loc 2160 0 2 term VALUE VIN",
            "Inst /loc 2520 0 0 term VALUE VOUT",
            "Inst /loc 4320 0 2 term VALUE VOUT",
            "Inst /loc 4320 240 0 gnd",
            "Inst /loc 6480 0 2 term VALUE VOUT",
            f'SaveAs /force "{schematic}"',
            "Let v2_f11_written = WriteF11Lines(['.simulator SIMPLIS', '.OPTIONS', '+ PSP_NPT=101', '.TRAN 5u 0', '.simulator DEFAULT'])",
            f'SaveAs /force "{schematic}"',
            f"Let v2_status = OpenEchoFile('{status_file}', 'a')",
            "Echo schematic_created=true",
            "Echo analysis_prepared={v2_f11_written}",
            "Echo simulation_started=true",
            "Echo running=true",
            "Let v2_close = CloseEchoFile()",
            "simplis_run",
            "Let v2_exit = GetSIMPLISExitCode()",
            "Let v2_has_errors = SimulationHasErrors()",
            "Let v2_errors = GetSimulationErrors()",
            "Let v2_sim_status = GetSimulatorStatus()",
            "Let v2_info = GetSimulationInfo()",
            "Let v2_info_count = Length(v2_info)",
            "Let v2_info_safe = JoinStringArray(['<none>'], v2_info)",
            "Let v2_errors_safe = JoinStringArray(['<none>'], v2_errors)",
            "Let v2_groups = Groups()",
            "Let v2_groups_safe = JoinStringArray(['<none>'], v2_groups)",
            "Let v2_current_group = v2_groups[0]",
            "Let v2_group_vectors = VectorsInGroup(v2_current_group)",
            "Let v2_group_info = GetGroupAnalysisInfo(v2_current_group)",
            "Let v2_group_vectors_safe = JoinStringArray(['<none>'], v2_group_vectors)",
            "Let v2_group_info_safe = JoinStringArray(['<none>'], v2_group_info)",
            f"Let v2_status = OpenEchoFile('{status_file}', 'a')",
            "Echo simulation_returned=true",
            "Echo simplis_exit_code={v2_exit}",
            "Echo simulation_has_errors={v2_has_errors}",
            "Echo simulator_status={v2_sim_status}",
            "Echo simulation_errors={v2_errors_safe}",
            "Echo simulation_info={v2_info_safe}",
            "Echo simulation_info.count={v2_info_count}",
            "Echo simulation_info.netlist_path={v2_info[0]}",
            "Echo simulation_info.list_path={v2_info[1]}",
            "Echo simulation_info.using_data_file={v2_info[2]}",
            "Echo simulation_info.data_file={v2_info[3]}",
            "Echo simulation_info.options={v2_info[5]}",
            "Echo simulation_info.analysis={v2_info[6]}",
            "Echo simulation_info.title={v2_info[8]}",
            "Echo groups={v2_groups_safe}",
            "Echo current_group={v2_current_group}",
            "Echo current_group.vectors={v2_group_vectors_safe}",
            "Echo current_group.analysis={v2_group_info_safe}",
            "Echo errors_collected=true",
            "Let v2_close = CloseEchoFile()",
            "Let v2_length = Length(Vec('#VOUT'))",
            f"Let v2_status = OpenEchoFile('{status_file}', 'a')",
            "Echo group.current.vector.1.length={v2_length}",
            "Let v2_close = CloseEchoFile()",
            "IF v2_length > 0 THEN",
            f'Show /force /names "vout" /file "{vector}" Vec(\'#VOUT\')',
            "ENDIF",
            f"Let v2_status = OpenEchoFile('{status_file}', 'a')",
            "Echo vector_export_done=true",
            "Echo completion_token=true",
            "Let v2_close = CloseEchoFile()",
            "RedirectMessages flush",
            "RedirectMessages off",
            "CloseSchem /force",
            "Quit",
            "",
        )
    )


def probe_completion_capability(runtime: Mapping[str, Any], work_dir: str | Path) -> dict[str, Any]:
    """Calibrate explicit status or the tightly constrained 8.4 None return."""

    from .vectors import parse_show_file
    from .watchdog import WatchdogConfig, parse_status_file, run_script_with_watchdog

    executable_value = runtime.get("executable")
    if not executable_value:
        return {"passed": False, "capability": "unavailable", "code": "executable_missing"}
    executable = Path(str(executable_value)).resolve()
    root = Path(work_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(character in str(root) for character in ("'", '"', "\r", "\n")):
        return {"passed": False, "capability": "unavailable", "code": "probe_path_not_script_safe"}
    schematic = root / "completion-probe.sxsch"
    script = root / "completion-probe.sxscr"
    status_file = root / "completion-probe-status.txt"
    message_log = root / "completion-probe-message.log"
    vector = root / "completion-probe-vector.txt"
    for stale in root.rglob("completion-probe*"):
        if stale.is_file():
            stale.unlink()
    script.write_text(_completion_probe_script(schematic, status_file, message_log, vector), encoding="utf-8")
    started = time.time()
    run = run_script_with_watchdog(
        simetrix_exe=executable,
        script=script,
        status_file=status_file,
        work_dir=root,
        job_id="completion-capability-probe",
        message_log=message_log,
        assess_completion=False,
        config=WatchdogConfig(poll_interval=0.5, launch_timeout=15.0, stall_timeout=30.0, hard_timeout=90.0, probe_timeout=1.5),
    )
    values = parse_status_file(status_file)
    vector_ready = False
    vector_record: dict[str, Any] = {"path": str(vector), "exists": vector.is_file()}
    if vector.is_file():
        try:
            parsed = parse_show_file(vector)
            vector_ready = len(parsed["x"]) >= 2 and len(parsed["x"]) == len(parsed["y"]) and vector.stat().st_mtime >= started - 1.0
            vector_record.update({"samples": len(parsed["y"]), "sha256": parsed["sha256"], "fresh": vector.stat().st_mtime >= started - 1.0})
        except (OSError, ValueError) as exc:
            vector_record["error"] = repr(exc)
    info_raw = str(values.get("simulation_info.netlist_path", "")).strip()
    analysis_raw = str(values.get("simulation_info.analysis", "")).strip()
    info_path = Path(info_raw) if info_raw else None
    info_checks = {"path_present": bool(info_raw), "path_readable": False, "path_in_probe_root": False, "stem_matches": False, "fresh": False, "analysis_present": bool(analysis_raw)}
    try:
        if info_path is not None:
            info_path = info_path.resolve()
            info_checks["path_readable"] = info_path.is_file()
            info_path.relative_to(root)
            info_checks["path_in_probe_root"] = True
            info_checks["stem_matches"] = info_path.stem.casefold() == schematic.stem.casefold()
            info_checks["fresh"] = info_path.stat().st_mtime >= started - 1.0
    except (OSError, ValueError):
        pass
    simulation_info_matches = all(info_checks.values())
    evidence = completion_capability_from_evidence(
        values,
        timeline=[dict(item) for item in run.get("timeline", [])],
        vector_fresh_complete=vector_ready,
        simulation_info_matches=simulation_info_matches,
    )
    evidence.update(
        {
            "run_ok": bool(run.get("ok")),
            "run_classification": run.get("classification"),
            "status_values": values,
            "vector": vector_record,
            "schematic_sha256": sha256_file(schematic) if schematic.is_file() else None,
            "simulation_info_evidence": {
                "netlist_path": str(info_path) if info_path is not None else info_raw,
                "analysis": analysis_raw,
                "checks": info_checks,
            },
            "api_limitations": ["get_simulation_info_empty_after_successful_simplis_run"]
            if vector_ready and not info_raw and not analysis_raw
            else [],
            "script_sha256": sha256_file(script),
            "status_file": str(status_file),
            "message_log": str(message_log),
        }
    )
    (root / "completion-capability.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return evidence
