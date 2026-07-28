"""Lossless source-schematic round-trip through SIMetrix-owned save objects."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

from .io import sha256_file, write_json
from .runtime import resolve_runtime
from .watchdog import recent_error_artifacts, simetrix_processes


def _quoted(path: Path) -> str:
    text = str(path.resolve())
    if any(character in text for character in ('"', "\r", "\n")):
        raise ValueError("Path cannot be represented in a SIMetrix script")
    return f'"{text}"'


def _string(value: Any) -> str:
    text = str(value)
    if any(character in text for character in ("'", "\r", "\n")):
        raise ValueError("Value cannot be represented in a SIMetrix script")
    return f"'{text}'"


def build_roundtrip_scripts(
    *, source: Path, output: Path, netlist: Path, status: Path, updates: Mapping[str, Any]
) -> tuple[str, str]:
    first = [
        "Set EchoOn",
        "Let v2_startup_settle = Sleep(3)",
        f"OpenSchem /cd {_quoted(source)}",
        "prepare_set_component_default",
    ]
    for key, value in sorted(updates.items()):
        first.append(f"Let v2_set = SetComponentValue({_string(key)}, {_string(value)})")
    first.extend(
        [
            f"SaveAs /force {_quoted(output)}",
            f"Netlist /simplis {_quoted(netlist)}",
            f"SaveAs /force {_quoted(output)}",
            "CloseSchem /force",
            f"Let v2_status = OpenEchoFile({_string(status)}, 'w')",
            "Echo save_completed=true",
            "Let v2_close = CloseEchoFile()",
            "Quit",
            "",
        ]
    )
    reopen_netlist = netlist.with_name(netlist.stem + ".reopen.net")
    second = [
        "Set EchoOn",
        "Let v2_startup_settle = Sleep(3)",
        f"OpenSchem /cd /readonly {_quoted(output)}",
        f"Netlist /simplis {_quoted(reopen_netlist)}",
        "CloseSchem /force",
        f"Let v2_status = OpenEchoFile({_string(status)}, 'a')",
        "Echo clean_reopen_completed=true",
        "Let v2_close = CloseEchoFile()",
        "Quit",
        "",
    ]
    return "\n".join(first), "\n".join(second)


def _run(executable: Path, script: Path, timeout: float) -> dict[str, Any]:
    started = time.time()
    process = subprocess.Popen([str(executable), "/s", str(script)], cwd=str(script.parent), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return {"returncode": process.returncode, "stdout": stdout, "stderr": stderr, "timed_out": False, "started_at": started, "pid": process.pid}
    except subprocess.TimeoutExpired as exc:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False, capture_output=True, text=True)
        return {"returncode": 124, "stdout": str(exc.stdout or ""), "stderr": str(exc.stderr or ""), "timed_out": True, "started_at": started, "pid": process.pid}


def _wait_for_clean_exit(timeout: float = 10.0) -> list[dict[str, Any]]:
    deadline = time.time() + timeout
    current = simetrix_processes()
    while current and time.time() < deadline:
        time.sleep(0.25)
        current = simetrix_processes()
    return current


def _terminate_records(records: list[dict[str, Any]]) -> None:
    for record in records:
        pid = int(record.get("pid") or 0)
        if pid > 0:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True, text=True)


def roundtrip_schematic(
    source_path: str | Path,
    out_dir: str | Path,
    *,
    runtime: Mapping[str, Any] | str | Path | None = None,
    updates: Mapping[str, Any] | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    source = Path(source_path).resolve()
    root = Path(out_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"schema_version": "simplis-automation/v2/roundtrip-result", "ok": False, "source": str(source), "errors": [], "stages": {}}
    if not source.is_file() or source.suffix.casefold() != ".sxsch":
        result["errors"].append({"code": "source_invalid"})
        write_json(root / "roundtrip-result.json", result)
        return result
    preexisting = simetrix_processes()
    if preexisting:
        result["errors"].append({"code": "unrelated_instance_present", "pids": [item.get("pid") for item in preexisting]})
        write_json(root / "roundtrip-result.json", result)
        return result
    runtime_status = resolve_runtime(runtime)
    if not runtime_status.get("ready"):
        result["errors"].append({"code": "runtime_invalid", "details": runtime_status.get("errors", [])})
        write_json(root / "roundtrip-result.json", result)
        return result
    output = root / f"{source.stem}.roundtrip.sxsch"
    netlist = root / f"{source.stem}.roundtrip.net"
    reopen_netlist = root / f"{source.stem}.roundtrip.reopen.net"
    status = root / "roundtrip-status.txt"
    script1 = root / "roundtrip-save.sxscr"
    script2 = root / "roundtrip-reopen.sxscr"
    first, second = build_roundtrip_scripts(source=source, output=output, netlist=netlist, status=status, updates=updates or {})
    script1.write_text(first, encoding="utf-8")
    script2.write_text(second, encoding="utf-8")
    source_before = sha256_file(source)
    executable = Path(str(runtime_status["executable"]))
    save_result = _run(executable, script1, timeout)
    result["stages"]["save"] = save_result
    remaining_after_save = _wait_for_clean_exit()
    if remaining_after_save:
        result["errors"].append({"code": "save_instance_not_closed", "pids": [item.get("pid") for item in remaining_after_save]})
        _terminate_records(remaining_after_save)
    reopen_result = _run(executable, script2, timeout) if save_result["returncode"] == 0 and output.is_file() and not remaining_after_save else {"returncode": 125, "skipped": True}
    result["stages"]["clean_reopen"] = reopen_result
    remaining_after_reopen = _wait_for_clean_exit() if not reopen_result.get("skipped") else []
    if remaining_after_reopen:
        result["errors"].append({"code": "reopen_instance_not_closed", "pids": [item.get("pid") for item in remaining_after_reopen]})
        _terminate_records(remaining_after_reopen)
    status_text = status.read_text(encoding="utf-8", errors="replace") if status.is_file() else ""
    errors = recent_error_artifacts(root, started_at=float(save_result.get("started_at", time.time())))
    result["artifacts"] = {
        "schematic": str(output),
        "netlist": str(netlist),
        "reopen_netlist": str(reopen_netlist),
        "status": str(status),
        "error_files": [str(path) for path in errors if path.suffix.casefold() in {".err", ".warn"}],
    }
    if sha256_file(source) != source_before:
        result["errors"].append({"code": "source_overwritten"})
    if save_result.get("returncode") != 0 or "save_completed=true" not in status_text:
        result["errors"].append({"code": "save_failed"})
    if reopen_result.get("returncode") != 0 or "clean_reopen_completed=true" not in status_text:
        result["errors"].append({"code": "clean_reopen_failed"})
    for name, path in (("schematic", output), ("netlist", netlist), ("reopen_netlist", reopen_netlist)):
        if not path.is_file() or path.stat().st_size == 0:
            result["errors"].append({"code": "artifact_missing", "artifact": name})
    if result["artifacts"]["error_files"]:
        result["errors"].append({"code": "simetrix_error_artifact"})
    result["ok"] = not result["errors"]
    result["classification"] = "roundtrip_verified" if result["ok"] else "roundtrip_failed"
    if result["ok"]:
        result["sha256"] = {name: sha256_file(path) for name, path in (("source", source), ("schematic", output), ("netlist", netlist), ("reopen_netlist", reopen_netlist))}
    write_json(root / "roundtrip-result.json", result)
    return result
