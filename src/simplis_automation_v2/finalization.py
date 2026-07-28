"""Clean-reopen finalization with task-owned native window evidence."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping

from PIL import Image, ImageStat

from .errors import ValidationError
from .io import sha256_file, sha256_text, write_json
from .runtime import resolve_runtime
from .watchdog import capture_dialogs, process_snapshot, simetrix_processes


FINALIZATION_SCHEMA = "simplis-automation/v2/finalization-request"
REVIEW_SCHEMA = "simplis-automation/v2/finalization-review"
REVIEW_CHECKS = (
    "readable_text",
    "symbol_spacing",
    "label_spacing",
    "terminal_orientation",
    "series_orientation",
    "ground_orientation",
    "functional_bands",
    "analysis_gutter",
)


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{label} is not readable JSON", path=str(path), reason=str(exc)) from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{label} root must be an object", path=str(path))
    return value


def _quote(path: Path) -> str:
    text = str(path.resolve())
    if any(character in text for character in ('"', "\r", "\n")):
        raise ValidationError("Path cannot be represented in a SIMetrix script", path=text)
    return f'"{text}"'


def build_finalize_script(schematic: Path, reopened_netlist: Path, status: Path, message_log: Path) -> str:
    return "\n".join(
        (
            "Set EchoOn",
            "Let v2_startup_settle = Sleep(3)",
            f"RedirectMessages dup {_quote(message_log)}",
            f"Let v2_status = OpenEchoFile('{status.resolve()}', 'w')",
            "Echo script_started=true",
            "Let v2_close = CloseEchoFile()",
            f"OpenSchem /cd /readonly {_quote(schematic)}",
            f"Netlist /simplis {_quote(reopened_netlist)}",
            "WM_CloseAllSystemWidgets",
            "Focus schem",
            "Zoom full",
            f"Let v2_status = OpenEchoFile('{status.resolve()}', 'a')",
            "Echo clean_reopen_completed=true",
            "Echo netlist_completed=true",
            "Echo screenshot_ready=true",
            "Let v2_close = CloseEchoFile()",
            "Let v2_capture_wait = Sleep(15)",
            "RedirectMessages flush",
            "RedirectMessages off",
            "CloseSchem /force",
            "Quit",
            "",
        )
    )


def _windows_for_pid(pid: int) -> list[tuple[int, str]]:
    if not hasattr(ctypes, "windll"):
        return []
    user32 = ctypes.windll.user32
    records: list[tuple[int, str]] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd: int, _lparam: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == int(pid) and user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            records.append((int(hwnd), buffer.value))
        return True

    user32.EnumWindows(callback_type(visit), 0)
    return records


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = (
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    )


class _BitmapInfo(ctypes.Structure):
    _fields_ = (("bmiHeader", _BitmapInfoHeader), ("bmiColors", wintypes.DWORD * 3))


def capture_window_png(hwnd: int, output_path: str | Path) -> dict[str, Any]:
    """Capture exactly one HWND with PrintWindow; never capture the desktop."""

    if not hasattr(ctypes, "windll"):
        raise ValidationError("Native window capture is available only on Windows")
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    rect = wintypes.RECT()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        raise ValidationError("Cannot read target window bounds", hwnd=hwnd)
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width < 320 or height < 240:
        raise ValidationError("Target window is too small for visual evidence", width=width, height=height)
    window_dc = user32.GetWindowDC(wintypes.HWND(hwnd))
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    previous = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not user32.PrintWindow(wintypes.HWND(hwnd), memory_dc, 2):
            raise ValidationError("PrintWindow failed for the task-owned SIMetrix window", hwnd=hwnd)
        info = _BitmapInfo()
        info.bmiHeader = _BitmapInfoHeader(
            ctypes.sizeof(_BitmapInfoHeader), width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0
        )
        buffer = ctypes.create_string_buffer(width * height * 4)
        if not gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(info), 0):
            raise ValidationError("Cannot extract captured window pixels", hwnd=hwnd)
        image = Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1)
        stats = ImageStat.Stat(image.convert("L"))
        extrema = stats.extrema[0]
        if extrema[1] - extrema[0] < 8 or stats.mean[0] < 2 or stats.stddev[0] < 1:
            raise ValidationError("Captured SIMetrix window is blank or near-uniform", extrema=list(extrema), mean=stats.mean[0], stddev=stats.stddev[0])
        target = Path(output_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, format="PNG")
        return {"path": str(target), "sha256": sha256_file(target), "width": width, "height": height, "backend": "win32_printwindow"}
    finally:
        gdi32.SelectObject(memory_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(wintypes.HWND(hwnd), window_dc)


def _normalized_netlist_hash(path: Path) -> str:
    lines = [
        " ".join(line.split())
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip() and not line.lstrip().startswith("*")
    ]
    return sha256_text("\n".join(lines))


def _prepare_capture_window(hwnd: int, sxcommand: Path) -> None:
    """Maximize the task window, then fit the active schematic by script."""

    user32 = ctypes.windll.user32
    user32.ShowWindow(wintypes.HWND(hwnd), 3)
    if sxcommand.is_file():
        for command in ("Focus schem", "Zoom full", "Zoom out"):
            try:
                subprocess.run(
                    [str(sxcommand), "-quiet", "-immediate", command],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except (OSError, subprocess.TimeoutExpired):
                raise ValidationError("Cannot prepare the SIMetrix window for whole-sheet capture", command=command)
    time.sleep(1)


def finalize_experiment(
    result_path: str | Path,
    out_dir: str | Path,
    *,
    runtime: Mapping[str, Any] | str | Path | None = None,
    timeout: float = 60,
) -> dict[str, Any]:
    source = Path(result_path).resolve()
    result = _load_json(source, "Experiment result")
    if result.get("ok") is not True or result.get("classification") != "complete":
        raise ValidationError("Only a completed experiment result can be finalized", classification=result.get("classification"))
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise ValidationError("Experiment result has no artifact map")
    if not artifacts.get("schematic") and isinstance(artifacts.get("analysis_results"), list):
        selected: dict[str, Any] | None = None
        for candidate in artifacts["analysis_results"]:
            candidate_path = Path(str(candidate)).resolve()
            if not candidate_path.is_file():
                continue
            candidate_result = _load_json(candidate_path, "Analysis experiment result")
            candidate_artifacts = candidate_result.get("artifacts")
            if candidate_result.get("ok") is True and isinstance(candidate_artifacts, Mapping) and candidate_artifacts.get("schematic"):
                selected = candidate_result
                break
        if selected is None:
            raise ValidationError("Aggregate result has no completed analysis artifact to finalize")
        artifacts = selected["artifacts"]
    schematic = Path(str(artifacts.get("schematic", ""))).resolve()
    original_netlist = Path(str(artifacts.get("netlist", ""))).resolve()
    if not schematic.is_file() or not original_netlist.is_file():
        raise ValidationError("Finalization input artifacts are missing", schematic=str(schematic), netlist=str(original_netlist))
    existing = simetrix_processes()
    if existing:
        raise ValidationError("Unrelated SIMetrix instance prevents isolated finalization", pids=[item.get("pid") for item in existing])
    runtime_status = resolve_runtime(runtime)
    if not runtime_status.get("watchdog_ready"):
        raise ValidationError("SIMetrix runtime is not ready for finalization", diagnostics=runtime_status.get("errors", []))
    root = Path(out_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    reopened_netlist = root / f"{schematic.stem}.reopen.net"
    status = root / "finalize-status.txt"
    message_log = root / "finalize-message.log"
    script = root / "finalize.sxscr"
    screenshot = root / f"{schematic.stem}.png"
    script.write_text(build_finalize_script(schematic, reopened_netlist, status, message_log), encoding="utf-8")
    process = subprocess.Popen(
        [str(runtime_status["executable"]), "/s", str(script)],
        cwd=str(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.time() + timeout
    status_text = ""
    while time.time() < deadline:
        if status.is_file():
            status_text = status.read_text(encoding="utf-8", errors="replace")
        windows = _windows_for_pid(process.pid)
        if "screenshot_ready=true" in status_text and windows:
            break
        if process.poll() is not None:
            raise ValidationError("Finalization process exited before screenshot readiness", returncode=process.returncode)
        time.sleep(0.2)
    else:
        process.kill()
        raise ValidationError("Finalization did not reach screenshot readiness", timeout=timeout)
    snapshot = process_snapshot(process.pid)
    if snapshot.get("responding") is not True:
        process.kill()
        raise ValidationError("Finalization window is not responsive", pid=process.pid)
    dialogs = capture_dialogs(process.pid)
    if dialogs:
        process.kill()
        raise ValidationError("Finalization window has a modal dialog", dialogs=dialogs)
    hwnd, title = max(windows, key=lambda item: len(item[1]))
    _prepare_capture_window(hwnd, Path(str(runtime_status["sxcommand_exe"])))
    capture = capture_window_png(hwnd, screenshot)
    try:
        stdout, stderr = process.communicate(timeout=25)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
    if not reopened_netlist.is_file():
        raise ValidationError("Clean-reopen netlist was not produced", path=str(reopened_netlist))
    original_normalized = _normalized_netlist_hash(original_netlist)
    reopened_normalized = _normalized_netlist_hash(reopened_netlist)
    if original_normalized != reopened_normalized:
        raise ValidationError(
            "Clean-reopen netlist differs from the fast-path netlist",
            original_normalized_sha256=original_normalized,
            reopened_normalized_sha256=reopened_normalized,
        )
    request = {
        "schema_version": FINALIZATION_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending_visual_review",
        "experiment_result": {"path": str(source), "sha256": sha256_file(source)},
        "schematic": {"path": str(schematic), "sha256": sha256_file(schematic)},
        "netlist": {
            "path": str(original_netlist),
            "sha256": sha256_file(original_netlist),
            "reopen_path": str(reopened_netlist),
            "reopen_sha256": sha256_file(reopened_netlist),
            "normalized_sha256": original_normalized,
            "consistent": True,
        },
        "clean_reopen": {
            "passed": "clean_reopen_completed=true" in status_text and "netlist_completed=true" in status_text,
            "process_launches": 1,
            "pid": process.pid,
            "returncode": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
        },
        "capture": {
            **capture,
            "method": "native_window",
            "pid": process.pid,
            "hwnd": hwnd,
            "window_title": title,
            "active_document": schematic.name,
            "responsive": True,
            "modal_free": True,
            "canvas_nonblank": True,
        },
        "review_contract": {"reviewer": "codex_multimodal", "checklist_version": 1, "checks": list(REVIEW_CHECKS)},
    }
    request_path = root / "finalization-request.json"
    write_json(request_path, request)
    return {"ok": True, "request": str(request_path), "screenshot": str(screenshot), "result": request}


def finalize_review(request_path: str | Path, review_path: str | Path, output_path: str | Path | None = None) -> dict[str, Any]:
    request_file = Path(request_path).resolve()
    review_file = Path(review_path).resolve()
    request = _load_json(request_file, "Finalization request")
    review = _load_json(review_file, "Finalization review")
    if request.get("schema_version") != FINALIZATION_SCHEMA:
        raise ValidationError("Unsupported finalization request schema", schema=request.get("schema_version"))
    if review.get("schema_version") != REVIEW_SCHEMA:
        raise ValidationError("Unsupported finalization review schema", schema=review.get("schema_version"))
    capture = request.get("capture", {})
    image = Path(str(capture.get("path", ""))).resolve()
    if not image.is_file() or sha256_file(image) != capture.get("sha256"):
        raise ValidationError("Finalization screenshot is missing or changed", path=str(image))
    request_sha256 = sha256_file(request_file)
    if review.get("finalization_request_sha256") != request_sha256:
        raise ValidationError("Review is not bound to this finalization request", expected=request_sha256)
    if review.get("screenshot_sha256") != capture.get("sha256"):
        raise ValidationError("Review is not bound to this screenshot", expected=capture.get("sha256"))
    if review.get("reviewer") != "codex_multimodal" or int(review.get("checklist_version", 0)) != 1:
        raise ValidationError("Review does not identify the required Codex multimodal contract")
    checks = review.get("checks")
    if not isinstance(checks, Mapping) or set(checks) != set(REVIEW_CHECKS):
        raise ValidationError("Review checks do not match the required checklist", required=list(REVIEW_CHECKS))
    invalid = {name: value for name, value in checks.items() if value not in {"pass", "fail", "not_applicable"}}
    if invalid:
        raise ValidationError("Review contains invalid check values", checks=invalid)
    verdict = str(review.get("verdict", "")).casefold()
    if verdict not in {"pass", "fail"}:
        raise ValidationError("Review verdict must be pass or fail", verdict=verdict)
    failures = [name for name, value in checks.items() if value == "fail"]
    passed = verdict == "pass" and not failures and request.get("clean_reopen", {}).get("passed") is True
    record = {
        "schema_version": "simplis-automation/v2/finalization-evidence",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ok": passed,
        "status": "passed" if passed else "failed",
        "request": {"path": str(request_file), "sha256": request_sha256},
        "capture": dict(capture),
        "review": review,
        "limitations": [
            "Visual evidence does not prove pin attachment or net isolation.",
            "Visual evidence does not prove simulation completion or electrical correctness.",
        ],
    }
    target = Path(output_path).resolve() if output_path else request_file.with_name("finalization-evidence.json")
    write_json(target, record)
    return record
