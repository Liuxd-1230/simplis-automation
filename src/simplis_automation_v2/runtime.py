from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Mapping


_EXECUTABLE_KEYS = ("executable", "simetrix_exe", "exe")
_SYMBOL_LIBRARY_KEYS = ("symbol_library", "symbol_lib_dir", "symbol_library_dir", "symbol_library_path")
_COMMON_EXECUTABLES: tuple[Path, ...] = ()
_PROJECT_FILES = ("simplis-runtime.json", ".simplis-runtime.json", "simplis-runtime.yaml")


def _error(code: str, message: str, **details: Any) -> dict:
    return dict(code=code, message=message, **({"details": details} if details else {}))


def _first_value(values: Mapping, keys: tuple[str, ...]) -> Any:
    return next((values[k] for k in keys if values.get(k) is not None and str(values[k]).strip()), None)


def normalize_version(value: Any) -> str | None:
    match = re.search(r"(?<!\d)(\d+)\.(\d+)", str(value or ""))
    if not match:
        return None
    major, minor = match.groups()
    return f"{int(major)}.{minor.rstrip('0') or '0'}"


def startup_command(executable: str | Path, script: str | Path) -> list[str]:
    path = Path(script)
    argument = str(path)
    if any(c.isspace() for c in argument):
        name = path.as_posix()
        if "'" in name:
            raise ValueError("SIMetrix Execute script path cannot contain an apostrophe")
        # /s 接受命令；Execute(string) 保留带空格的完整脚本名称
        argument = f"Let sa_startup_result = Execute('{name}')"
    return [str(executable), "/s", argument]


def _file_version(path: Path) -> str | None:
    # Windows PE 版本资源读取不会启动程序，也不依赖安装目录名称
    if os.name != "nt" or not path.is_file():
        return None
    api = ctypes.WinDLL("version", use_last_error=True)
    size_fn = api.GetFileVersionInfoSizeW
    size_fn.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
    size_fn.restype = ctypes.c_uint32
    unused = ctypes.c_uint32()
    size = size_fn(str(path), ctypes.byref(unused))
    if not size:
        return None
    data = ctypes.create_string_buffer(size)
    get_fn = api.GetFileVersionInfoW
    get_fn.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    if not get_fn(str(path), 0, size, data):
        return None
    pointer, length = ctypes.c_void_p(), ctypes.c_uint32()
    query = api.VerQueryValueW
    query.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32)]
    if not query(data, "\\", ctypes.byref(pointer), ctypes.byref(length)) or length.value < 52:
        return None
    fields = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32))
    if fields[0] != 0xFEEF04BD:
        return None
    ms, ls = fields[2], fields[3]
    return f"{ms >> 16}.{ms & 65535}.{ls >> 16}.{ls & 65535}"


def _load_runtime_mapping(runtime: Mapping | str | Path | None) -> tuple[dict, str | None, list]:
    if runtime is None:
        return {}, None, []
    if isinstance(runtime, Mapping):
        return dict(runtime), "explicit_mapping", []
    source = Path(runtime).expanduser().resolve()
    decoder = json.loads
    parse_errors = (OSError, ValueError)
    if source.suffix.lower() != ".json":
        import yaml
        decoder = yaml.safe_load
        parse_errors += (yaml.YAMLError,)
    try:
        text = source.read_text(encoding="utf-8-sig")
        raw = decoder(text)
        if not isinstance(raw, dict):
            raise ValueError("Configuration root must be an object")
    except parse_errors as exc:
        return {}, str(source), [_error("runtime_config_invalid", str(exc), path=str(source))]
    for key in (*_EXECUTABLE_KEYS, *_SYMBOL_LIBRARY_KEYS, "install", "install_root", "simplis_exe"):
        if raw.get(key):
            path = Path(str(raw[key])).expanduser()
            raw[key] = str(path if path.is_absolute() else source.parent / path)
    if isinstance(raw.get("catalog"), dict) and raw["catalog"].get("path"):
        path = Path(raw["catalog"]["path"])
        raw["catalog"]["path"] = str(path if path.is_absolute() else source.parent / path)
    return raw, str(source), []


def _registry_locations() -> list[Path]:
    if os.name != "nt":
        return []
    import winreg
    found = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            for key_name in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                             r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\SIMetrix.exe"):
                try:
                    with winreg.OpenKey(hive, key_name, 0, winreg.KEY_READ | view) as key:
                        if "App Paths" in key_name:
                            found.append(Path(winreg.QueryValueEx(key, "")[0]))
                            continue
                        for index in range(winreg.QueryInfoKey(key)[0]):
                            with winreg.OpenKey(key, winreg.EnumKey(key, index)) as item:
                                try:
                                    name = winreg.QueryValueEx(item, "DisplayName")[0]
                                    if re.search("simetrix|simplis", name, re.I):
                                        location = winreg.QueryValueEx(item, "InstallLocation")[0]
                                        if location.strip():
                                            found.append(Path(location))
                                except OSError:
                                    continue
                except OSError:
                    continue
    return found


def _common_locations() -> list[Path]:
    # 只检查固定深度的常见位置；不遍历用户磁盘
    bases = {Path(os.environ[k]) for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA") if os.environ.get(k)}
    if os.name == "nt":
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for index in range(26):
            if mask & (1 << index):
                drive = Path(f"{chr(65 + index)}:/")
                if ctypes.windll.kernel32.GetDriveTypeW(str(drive)) == 3:
                    bases.update((drive, drive / "Program Files", drive / "Program Files (x86)"))
    names = ("SIMetrix840", "SIMetrix830", "SIMetrix", "Simplis8.4", "Simplis8.3")
    return list(_COMMON_EXECUTABLES) + [base / name for base in sorted(bases) for name in names]


def _installation(path: Path, source: str) -> dict:
    path = path.expanduser().resolve()
    root = path.parent.parent if path.suffix.lower() == ".exe" else path
    def program(name: str, folders: tuple[str, ...]) -> Path | None:
        return next((root / folder / name for folder in folders if (root / folder / name).is_file()), None)
    gui = path if path.name.lower() == "simetrix.exe" else program("SIMetrix.exe", ("bin64", "bin"))
    solver = path if path.name.lower() == "simplis.exe" else program("simplis.exe", ("bin", "bin64"))
    command = program("SxCommand.exe", (gui.parent.name,) if gui else ("bin64", "bin"))
    versions = {name: _file_version(p) for name, p in (("gui", gui), ("solver", solver), ("command", command)) if p}
    confirmed = {normalize_version(v) for v in versions.values() if v}
    match = re.search(r"(?i)(?:simetrix|simplis)[^\d]*(8[.]?[34]0?)", str(root))
    inferred = normalize_version(match[1]) if match and "." in match[1] else (f"8.{match[1][1]}" if match else None)
    version = normalize_version(versions.get("gui") if gui else versions.get("solver")) if len(confirmed) <= 1 else None
    symbols = root / "support/symbollibs"
    return dict(install_root=str(root), executable=str(gui) if gui else None,
                simplis_exe=str(solver) if solver else None, sxcommand_exe=str(command) if command else None,
                symbol_library=str(symbols), models=str(root / "support/models"), docs=str(root / "support/docs"),
                examples=str(root / "support/examples"), source=source, version=version,
                inferred_version=inferred, version_status="confirmed" if version else "conflicting" if len(confirmed) > 1 else "inferred" if inferred else "unknown",
                version_evidence=versions, version_supported=version in {"8.3", "8.4"},
                symbol_library_count=len(list(symbols.glob("*.sxslb"))),
                resources_match=len(confirmed) <= 1, verification="not_checked")


def installed_source_path(source: str | Path, symbol_library_dir: str | Path) -> Path:
    # 支持旧目录锁定记录和新的 support 相对路径，始终使用所选安装
    parts = [p for p in str(source).replace("\\", "/").split("/") if p]
    index = next((i for i, p in enumerate(parts) if p.lower() == "support"), None)
    if index is not None:
        return Path(symbol_library_dir).resolve().parent.parent.joinpath(*parts[index:])
    return Path(source)


def format_runtime(runtime: Mapping) -> str:
    lines = [f"Runtime: {runtime['status']} (static checks; simulator not launched)"]
    for item in runtime.get("candidates", []):
        lines.append(f"Detected: {item['version'] or '?'} [{item['version_status']}] {item['install_root']}")
    lines.append(f"Selected version: {runtime['version'] or '?'} ({runtime['version_status']})")
    lines.append("Version evidence: " + json.dumps(runtime["version_evidence"]))
    for key in ("install_root", "executable", "simplis_exe", "sxcommand_exe", "symbol_library", "models", "docs", "examples", "verification"):
        lines.append(f"{key}: {runtime.get(key)}")
    lines.append(f"GUI execution ready: {runtime['watchdog_ready']}")
    for item in [*runtime["errors"], *runtime["warnings"]]:
        lines.append(f"{item['code']}: {item['message']}")
    lines.extend("Unverified: " + item for item in runtime["unverified_features"])
    lines.extend("Suggestion: " + item for item in runtime["repair_suggestions"])
    return "\n".join(lines)


def resolve_runtime(runtime: Mapping[str, Any] | str | Path | None = None, *,
                    simetrix_exe: str | Path | None = None, symbol_library_dir: str | Path | None = None,
                    config_path: str | Path | None = None, install: str | Path | None = None,
                    version: str | None = None, project_dir: str | Path | None = None,
                    target_version: str | None = None) -> dict[str, Any]:
    if target_version and isinstance(runtime, Mapping) and runtime.get("executable_source") in {"registry", "PATH", "common_location"} and not runtime.get("requested_version"):
        # CLI 的自动发现结果尚未启动程序；按任务的 catalog 约束重新选择
        runtime = None
    project = Path(project_dir or Path.cwd()).resolve()
    project_config = next((parent / name for parent in (project, *project.parents) for name in _PROJECT_FILES if (parent / name).is_file()), None)
    config, sources, errors = {}, [], []
    for value in (os.environ.get("SIMPLIS_AUTOMATION_CONFIG"), project_config, config_path, runtime):
        if value is not None:
            loaded, source, diagnostics = _load_runtime_mapping(value)
            config.update(loaded)
            sources.append(source)
            errors.extend(diagnostics)
    explicit_path = simetrix_exe or install or _first_value(config, _EXECUTABLE_KEYS) or config.get("install") or config.get("install_root") or config.get("simplis_exe")
    symbol_override = symbol_library_dir or _first_value(config, _SYMBOL_LIBRARY_KEYS) or os.environ.get("SIMPLIS_SYMBOL_LIB_DIR")
    requested_raw = version or config.get("version") or config.get("simetrix_version") or os.environ.get("SIMPLIS_VERSION")
    if not requested_raw and not explicit_path and not any(os.environ.get(k) for k in ("SIMETRIX_EXE", "SIMPLIS_INSTALL", "SIMPLIS_EXE")):
        requested_raw = target_version
    requested = normalize_version(requested_raw)
    if requested_raw and not requested:
        errors.append(_error("invalid_version", "Specify a version such as 8.30 or 8.40", requested=str(requested_raw)))
    pinned = explicit_path or os.environ.get("SIMETRIX_EXE") or os.environ.get("SIMPLIS_INSTALL") or os.environ.get("SIMPLIS_EXE")
    if not pinned and symbol_override:
        configured_symbols = Path(symbol_override).expanduser().resolve()
        if configured_symbols.name.lower() == "symbollibs" and configured_symbols.parent.name.lower() == "support":
            pinned = configured_symbols.parent.parent
    pinned_source = "explicit" if explicit_path or symbol_library_dir or _first_value(config, _SYMBOL_LIBRARY_KEYS) else "environment"
    locations = [(Path(pinned), pinned_source)] if pinned else []
    locations += [(p, "registry") for p in _registry_locations()]
    locations += [(Path(p), "PATH") for name in ("SIMetrix.exe", "simplis.exe") if (p := shutil.which(name))]
    locations += [(p, "common_location") for p in _common_locations()]
    candidates, seen = [], set()
    for path, source in locations:
        item = _installation(path, source)
        key = item["install_root"].casefold()
        if key in seen or (not path.exists() and not (pinned and not candidates)):
            continue
        seen.add(key)
        candidates.append(item)
    # 匹配已有内容证据后才提高默认选择优先级；目录名不能提供验证分数
    records = json.loads(Path(__file__).with_name("compatibility.json").read_text(encoding="utf-8"))
    for item in candidates:
        record = records.get(item["version"])
        if record:
            checks = []
            for relative, expected in record["libraries"].items():
                file = Path(item["install_root"]) / relative
                actual = hashlib.sha256(file.read_bytes()).hexdigest() if file.is_file() else None
                checks.append(dict(path=relative, expected_sha256=expected, actual_sha256=actual, matches=actual == expected))
            item["library_checks"] = checks
            item["catalog_match"] = bool(checks) and all(c["matches"] for c in checks)
            item["verified_features"] = record["verified_features"] if item["catalog_match"] else []
            item["verification"] = "matching_recorded_libraries" if item["catalog_match"] else "library_revalidation_required"
        else:
            item["catalog_match"] = None
            item["verified_features"] = []
    eligible = [i for i in candidates if (not requested or i["version"] == requested)]
    selected = candidates[0] if pinned and candidates else max(eligible, key=lambda i: (i["version_supported"] and i["resources_match"] and bool(i["executable"]) and bool(i["symbol_library_count"]), len(i["verified_features"]), bool(i["catalog_match"]), i["version"] == "8.4"), default=None)
    if selected is None:
        selected = _installation(Path(explicit_path or project / "__unconfigured__"), "unavailable")
    if requested and selected["version"] != requested:
        errors.append(_error("requested_version_unavailable", "The selected installation does not confirm the requested version; no version switch was performed", requested=requested, actual=selected["version"]))
    executable, symbols = selected["executable"], Path(selected["symbol_library"])
    if not executable or not Path(executable).is_file():
        errors.append(_error("missing_executable", "Configure --install, --simetrix-exe or simplis-runtime.json"))
    if symbol_override:
        override = Path(symbol_override).expanduser().resolve()
        if override != symbols:
            errors.append(_error("mixed_installation_resources", "Symbol library must belong to the selected installation", configured=str(override), expected=str(symbols)))
    if not symbols.is_dir():
        errors.append(_error("missing_symbol_library", "Selected installation has no support/symbollibs", path=str(symbols)))
    elif not selected["symbol_library_count"]:
        errors.append(_error("empty_symbol_library", "No .sxslb files in selected installation"))
    if not selected["resources_match"]:
        errors.append(_error("conflicting_program_versions", "GUI, solver or command executable versions differ"))
    warnings = []
    if not selected["version_supported"]:
        warnings.append(_error("runtime_version_unconfirmed", "Confirmed supported version required to run; directory names are hints only"))
    if not selected["sxcommand_exe"]:
        warnings.append(_error("sxcommand_missing", "SxCommand.exe is unavailable; watchdog control is disabled"))
    unknown_programs = [name for name, value in selected["version_evidence"].items() if value is None]
    if unknown_programs:
        warnings.append(_error("program_version_unconfirmed", "Some executable version resources could not be read", programs=unknown_programs))
    ready = not errors and selected["version_supported"]
    catalog = None
    if isinstance(config.get("catalog"), Mapping):
        from .catalog import load_catalog, catalog_runtime_evidence
        path = Path(config["catalog"]["path"])
        loaded = load_catalog(path)
        expected = config["catalog"].get("fingerprint") or config["catalog"].get("sha256")
        catalog = dict(path=str(path), fingerprint=loaded["fingerprint"], file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                       status="matched" if expected == loaded["fingerprint"] else "computed" if not expected else "mismatch",
                       resources=catalog_runtime_evidence(loaded, symbols))
    return dict(selected, ok=not errors, status="ready" if ready else "invalid" if errors else "unconfirmed", ready=ready,
                simetrix_exe=executable, symbol_lib_dir=str(symbols), symbol_library_dir=str(symbols),
                executable_source=selected["source"], symbol_library_source="selected_installation",
                config_source=sources or None, requested_version=requested, catalog_target_version=normalize_version(target_version), preferred_version=selected["version"] == "8.4",
                sxcommand_exists=bool(selected["sxcommand_exe"]), watchdog_ready=ready and bool(selected["sxcommand_exe"]),
                candidates=candidates, catalog=catalog, errors=errors, warnings=warnings,
                completion_contract="actual_task_evidence", process_launches=0,
                selection_policy="explicit > project > environment > verified compatible installation; 8.4 breaks ties",
                unverified_features=["Per-circuit device suitability", "POP/AC and native SIMetrix depend on task-specific evidence"],
                repair_suggestions=["Set install and version in simplis-runtime.json", "Run doctor --catalog with the intended device catalog"] if not ready else [])
