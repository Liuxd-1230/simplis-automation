"""Side-effect-free SIMetrix/SIMPLIS 8.3/8.4 runtime discovery."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

import yaml

from .io import sha256_file


_EXECUTABLE_KEYS = ("executable", "simetrix_exe", "exe")
_SYMBOL_LIBRARY_KEYS = ("symbol_library", "symbol_lib_dir", "symbol_library_dir", "symbol_library_path")
_COMMON_EXECUTABLES = (
    Path("D:/Program Files/SIMetrix840/bin64/SIMetrix.exe"),
    Path("D:/SIMetrix840/bin64/SIMetrix.exe"),
    Path("D:/Simplis8.4/bin64/SIMetrix.exe"),
    Path("C:/Program Files/SIMetrix840/bin64/SIMetrix.exe"),
    Path("D:/Program Files/SIMetrix830/bin64/SIMetrix.exe"),
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
        if candidate.is_file():
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
        try:
            return executable.resolve().parents[1] / "support" / "symbollibs", "derived_from_executable"
        except IndexError:
            return executable.parent / "support" / "symbollibs", "derived_from_executable"
    return None, None


def _infer_version(config: Mapping[str, Any], executable: Path | None) -> str | None:
    explicit = config.get("version") or config.get("simetrix_version")
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()
    if executable is None:
        return None
    match = re.search(r"(?i)(?:simetrix|simplis)[^0-9]*(\d{2,4}|\d(?:[.]\d)?)", str(executable))
    if not match:
        return None
    raw = match.group(1)
    if "." in raw:
        return raw
    if len(raw) in {2, 3}:
        return f"{raw[0]}.{raw[1]}"
    if len(raw) == 4:
        return f"{raw[0]}.{raw[1:]}"
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
    """Resolve runtime metadata without starting SIMetrix or running a fixture."""

    config: dict[str, Any] = {}
    sources: list[str] = []
    errors: list[dict[str, Any]] = []
    if config_path is not None:
        loaded, source, diagnostics = _load_runtime_mapping(config_path)
        config.update(loaded)
        errors.extend(diagnostics)
        if source:
            sources.append(source)
    if runtime is not None:
        loaded, source, diagnostics = _load_runtime_mapping(runtime)
        config.update(loaded)
        errors.extend(diagnostics)
        if source:
            sources.append(source)
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
    if not executable_exists:
        errors.append(_error("missing_executable", "SIMetrix executable does not exist or is not discoverable", path=str(executable) if executable else None))
    if not symbol_exists:
        errors.append(_error("missing_symbol_library", "SIMPLIS symbol library does not exist or is not derivable", path=str(symbol_library) if symbol_library else None))
    symbol_count = len(list(symbol_library.glob("*.sxslb"))) if symbol_exists and symbol_library else 0
    if symbol_exists and symbol_count == 0:
        errors.append(_error("empty_symbol_library", "SIMPLIS symbol library contains no .sxslb files", path=str(symbol_library)))
    version = _infer_version(config, executable)
    supported_version = bool(version and version.startswith(("8.4", "8.3")))
    warnings: list[dict[str, Any]] = []
    if not supported_version:
        warnings.append(_error("runtime_version_unconfirmed", "SIMetrix/SIMPLIS 8.3 or 8.4 could not be confirmed", version=version))
    sxcommand = executable.with_name("SxCommand.exe") if executable_exists and executable else None
    sxcommand_exists = bool(sxcommand and sxcommand.is_file())
    if executable_exists and not sxcommand_exists:
        warnings.append(_error("sxcommand_missing", "SxCommand.exe is unavailable; watchdog control is disabled", path=str(sxcommand)))
    executable_text = str(executable.resolve()) if executable_exists and executable else (str(executable) if executable else None)
    symbol_text = str(symbol_library.resolve()) if symbol_exists and symbol_library else (str(symbol_library) if symbol_library else None)
    return {
        "ok": not errors,
        "status": "ready" if not errors else "invalid",
        "ready": bool(not errors and supported_version),
        "executable": executable_text,
        "symbol_library": symbol_text,
        "simetrix_exe": executable_text,
        "symbol_lib_dir": symbol_text,
        "symbol_library_dir": symbol_text,
        "executable_source": executable_source,
        "symbol_library_source": symbol_source,
        "config_source": sources or None,
        "symbol_library_count": symbol_count,
        "version": version,
        "version_supported": supported_version,
        "preferred_version": bool(version and version.startswith("8.4")),
        "sxcommand_exe": str(sxcommand.resolve()) if sxcommand_exists and sxcommand else (str(sxcommand) if sxcommand else None),
        "sxcommand_exists": sxcommand_exists,
        "watchdog_ready": bool(not errors and supported_version and sxcommand_exists),
        "completion_contract": "actual_task_evidence",
        "catalog": _catalog_record(config),
        "errors": errors,
        "warnings": warnings,
    }
