#!/usr/bin/env python3
"""Runtime configuration helpers for SIMPLIS automation scripts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parents[1]
CONFIG_DIR = SKILL_DIR / "config"
DEFAULT_CONFIG = CONFIG_DIR / "simplis_automation_config.json"
LOCAL_CONFIG = CONFIG_DIR / "local_config.json"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"Runtime config root must be an object: {path}")
    return data


def load_runtime_config(config_path: str | Path | None = None) -> tuple[dict[str, Any], list[str]]:
    """Load public config, local override, then explicit/env override."""
    merged: dict[str, Any] = {}
    sources: list[str] = []

    for path in (DEFAULT_CONFIG, LOCAL_CONFIG):
        if path.exists():
            merged.update(_read_json(path))
            sources.append(str(path))

    explicit = config_path or os.environ.get("SIMPLIS_AUTOMATION_CONFIG")
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise SystemExit(f"Runtime config file not found: {path}")
        merged.update(_read_json(path))
        sources.append(str(path))

    return merged, sources


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def resolve_simetrix_exe(cli_value: str | Path | None = None, config_path: str | Path | None = None, *, required: bool = True) -> Path | None:
    config, _sources = load_runtime_config(config_path)
    raw = _clean(cli_value) or _clean(os.environ.get("SIMETRIX_EXE")) or _clean(config.get("simetrix_exe"))
    if not raw:
        if not required:
            return None
        raise SystemExit(
            "SIMetrix executable path is not configured. Set simetrix_exe in "
            "config/local_config.json, set SIMETRIX_EXE, or pass --simetrix-exe."
        )
    path = Path(raw).expanduser()
    if not path.exists():
        raise SystemExit(f"Configured SIMetrix executable does not exist: {path}")
    return path.resolve()


def runtime_config_status(
    *,
    cli_simetrix_exe: str | Path | None = None,
    cli_symbol_lib_dir: str | Path | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    config, sources = load_runtime_config(config_path)
    out: dict[str, Any] = {
        "config_sources": sources,
        "configured_simetrix_exe": _clean(cli_simetrix_exe) or _clean(os.environ.get("SIMETRIX_EXE")) or _clean(config.get("simetrix_exe")),
        "configured_symbol_lib_dir": _clean(cli_symbol_lib_dir) or _clean(os.environ.get("SIMPLIS_SYMBOL_LIB_DIR")) or _clean(config.get("symbol_lib_dir")),
    }
    simetrix = resolve_simetrix_exe(cli_simetrix_exe, config_path=config_path, required=False)
    out["simetrix_exe"] = str(simetrix) if simetrix else None
    out["simetrix_exe_exists"] = bool(simetrix and simetrix.exists())
    try:
        symbols = resolve_symbol_lib_dir(cli_symbol_lib_dir, config_path=config_path, simetrix_exe=simetrix)
        out["symbol_lib_dir"] = str(symbols)
        out["symbol_lib_dir_exists"] = symbols.exists()
        out["symbol_library_count"] = len(list(symbols.glob("*.sxslb"))) if symbols.exists() else 0
    except SystemExit as exc:
        out["symbol_lib_dir"] = None
        out["symbol_lib_dir_exists"] = False
        out["symbol_library_count"] = 0
        out["symbol_lib_error"] = str(exc)
    out["ready"] = bool(out["simetrix_exe_exists"] and out["symbol_lib_dir_exists"] and out["symbol_library_count"])
    return out


def resolve_symbol_lib_dir(
    cli_value: str | Path | None = None,
    config_path: str | Path | None = None,
    *,
    simetrix_exe: str | Path | None = None,
) -> Path:
    config, _sources = load_runtime_config(config_path)
    raw = _clean(cli_value) or _clean(os.environ.get("SIMPLIS_SYMBOL_LIB_DIR")) or _clean(config.get("symbol_lib_dir"))
    if raw:
        path = Path(raw).expanduser()
        if not path.exists():
            raise SystemExit(f"Configured SIMPLIS symbol library directory does not exist: {path}")
        return path.resolve()

    exe = Path(simetrix_exe) if simetrix_exe else resolve_simetrix_exe(config_path=config_path, required=False)
    if exe:
        derived = exe.resolve().parents[1] / "support" / "symbollibs"
        if derived.exists():
            return derived.resolve()

    raise SystemExit(
        "SIMPLIS symbol library directory is not configured. Set symbol_lib_dir in "
        "config/local_config.json, set SIMPLIS_SYMBOL_LIB_DIR, or pass --symbol-lib-dir."
    )
