from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable, Iterable, Mapping

from .execution import assess_simulation_status, classify_watchdog
from .io import sha256_file, write_json
from .runtime import startup_command


@dataclass(frozen=True)
class WatchdogConfig:
    poll_interval: float = 2.0
    launch_timeout: float = 15.0
    stall_timeout: float = 60.0
    hard_timeout: float = 240.0
    probe_timeout: float = 2.0


class WatchdogMonitor:
    def __init__(self, *, started_at: float, config: WatchdogConfig) -> None:
        self.started_at = float(started_at)
        self.last_progress_at = float(started_at)
        self.launch_observed = False
        self.launch_observed_at: float | None = None
        self.last_cpu_seconds: float | None = None
        self.last_artifact_signature: str | None = None
        self.last_dialog_signature: str | None = None
        self.config = config
        self.timeline: list[dict[str, Any]] = []

    def observe(
        self,
        *,
        now: float,
        process_alive: bool,
        launch_observed: bool | None = None,
        cpu_seconds: float | None,
        artifact_signature: str,
        simulator_status: str,
        dialogs: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        progressed = False
        # ``launch_timeout`` proves that the task-owned GUI process actually
        # became responsive.  It must not require the startup script to have
        # written its first token: SIMetrix 8.4 can show a usable main window
        # several seconds before it starts processing /s.  Missing script
        # progress is handled by the independent stall timeout instead.
        current_launch_observed = process_alive if launch_observed is None else bool(launch_observed)
        if current_launch_observed and not self.launch_observed:
            self.launch_observed = True
            self.launch_observed_at = float(now)
            progressed = True
        # A wedged SIMetrix GUI still consumes small amounts of CPU while its
        # Windows message loop is alive.  CPU is progress evidence only when
        # the remote simulator state explicitly says a run is in progress.
        if (
            str(simulator_status).casefold() == "inprogress"
            and cpu_seconds is not None
            and (self.last_cpu_seconds is None or cpu_seconds > self.last_cpu_seconds + 1e-6)
        ):
            progressed = True
        if self.last_artifact_signature is None or artifact_signature != self.last_artifact_signature:
            progressed = True
        # Status windows repaint elapsed/CPU counters while a command script is
        # already stuck.  Strip numeric churn so those repaints cannot postpone
        # the no-progress timeout indefinitely, while title/state changes still
        # count as UI progress.
        normalized_dialogs = []
        for item in dialogs:
            record = dict(item)
            record["text"] = re.sub(r"\d+(?:\.\d+)?", "#", str(record.get("text", "")))
            normalized_dialogs.append(record)
        dialog_signature = json.dumps(normalized_dialogs, ensure_ascii=False, sort_keys=True)
        if self.last_dialog_signature is None or dialog_signature != self.last_dialog_signature:
            progressed = True
        if progressed:
            self.last_progress_at = float(now)
        self.last_cpu_seconds = cpu_seconds
        self.last_artifact_signature = artifact_signature
        self.last_dialog_signature = dialog_signature
        dialog = dialogs[0] if dialogs else None
        result = classify_watchdog(
            started_at=self.started_at,
            now=now,
            last_progress_at=self.last_progress_at,
            process_alive=process_alive,
            simulator_status=simulator_status,
            dialog=dialog,
            stall_timeout=self.config.stall_timeout,
            hard_timeout=self.config.hard_timeout,
        )
        if (
            result["classification"] == "running"
            and not self.launch_observed
            and float(now) - self.started_at >= self.config.launch_timeout
        ):
            result = {
                **result,
                "classification": "launch_timeout",
                "terminal": True,
            }
        event = {
            "at": float(now),
            "process_alive": process_alive,
            "launch_observed": self.launch_observed,
            "launch_observed_at": self.launch_observed_at,
            "cpu_seconds": cpu_seconds,
            "artifact_signature": artifact_signature,
            "simulator_status": simulator_status,
            "dialogs": [dict(item) for item in dialogs],
            "progressed": progressed,
            **result,
        }
        self.timeline.append(event)
        return result


def parse_status_file(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    target = Path(path)
    try:
        exists = target.is_file()
    except OSError:
        return result
    if not exists:
        return result
    text: str | None = None
    # OpenEchoFile briefly takes an exclusive Windows lock while SIMetrix
    # appends a status batch.  Treat that as a transient observation gap, not
    # an infrastructure exception that abandons the task-owned process.
    for attempt in range(4):
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
            break
        except OSError:
            if attempt < 3:
                time.sleep(0.025)
    if text is None:
        return result
    repeated = {"simulation_errors", "simulation_warnings"}
    for raw in text.splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip().casefold()
        value = value.strip()
        if not key:
            continue
        if key in repeated:
            result.setdefault(key, []).append(value)
        else:
            result[key] = value
    return result


def captured_warning_lines(message_log: Path) -> list[str]:
    if not Path(message_log).is_file():
        return []
    warnings: list[str] = []
    try:
        lines = Path(message_log).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        text = line.strip()
        if re.search(r"(?i)^(?:\*\*\*\s*)?warning(?:\s+message\s+id\s*:[^:]+)?\s*[:\-]", text):
            warnings.append(text)
    return list(dict.fromkeys(warnings))


def captured_script_error_lines(message_log: Path) -> list[str]:
    return _ScriptErrorReader(message_log).read()


class _ScriptErrorReader:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.offset = 0
        self.errors: dict[str, None] = {}

    @staticmethod
    def _error(raw: bytes) -> str | None:
        text = raw.decode("utf-8", errors="replace").strip()
        return text if re.search(r"(?i)^error\s*:\s*line\b", text) or "the expression cannot be evaluated" in text.casefold() else None

    def read(self) -> list[str]:
        try:
            with self.path.open("rb") as handle:
                if self.path.stat().st_size < self.offset:
                    self.offset = 0
                    self.errors.clear()
                handle.seek(self.offset)
                while raw := handle.readline():
                    error = self._error(raw)
                    if not raw.endswith(b"\n"):
                        # 未完成的末行留到下一次读取，避免拆开 UTF-8 字符或错误消息。
                        return list(dict.fromkeys([*self.errors, *([error] if error else [])]))
                    self.offset = handle.tell()
                    if error:
                        self.errors[error] = None
        except OSError as exc:
            logging.getLogger(__name__).debug("Script log observation unavailable: %s", exc)
            return list(self.errors)
        return list(self.errors)


def _artifact_signature(root: Path, *, excluded_paths: Iterable[Path] = (), snapshot: list[dict[str, Any]] | None = None) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    excluded = {Path(path).resolve() for path in excluded_paths}
    for item in _artifact_snapshot(root) if snapshot is None else snapshot:
        path = root / item["path"]
        if path.resolve() in excluded:
            continue
        if path.name.casefold() in {"job-message.log", "remote-simulator-status.txt", "process.stdout.log", "process.stderr.log", "error-bundle.json"}:
            continue
        digest.update(item["path"].encode("utf-8", errors="replace"))
        digest.update(str(item["size"]).encode("ascii"))
        digest.update(str(item["mtime_ns"]).encode("ascii"))
    return digest.hexdigest()


def _artifact_snapshot(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not root.exists():
        return records
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: str(item).casefold()):
        try:
            stat = path.stat()
        except OSError:
            continue
        records.append({"path": str(path.relative_to(root)), "size": stat.st_size, "mtime": stat.st_mtime, "mtime_ns": stat.st_mtime_ns})
    return records


def _powershell_json(script: str, *, timeout: float = 5.0) -> Any:
    logger = logging.getLogger(__name__)
    logger.debug("PowerShell observation started; timeout=%s", timeout)
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);" + script],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("PowerShell observation failed: %s", type(exc).__name__)
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        logger.debug("PowerShell observation returned %s; stderr=%r", completed.returncode, completed.stderr[-2000:])
        return None
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        logger.debug("PowerShell JSON invalid: %s; stdout=%r", exc, completed.stdout[:500])
        return None


def simetrix_processes() -> list[dict[str, Any]]:
    script = (
        "$p=Get-CimInstance Win32_Process -Filter \"Name='SIMetrix.exe'\" -ErrorAction SilentlyContinue;"
        "@($p|ForEach-Object{$live=Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue;"
        "if($live){[pscustomobject]@{pid=$_.ProcessId;path=$_.ExecutablePath;command_line=$_.CommandLine;"
        "cpu=$live.CPU;responding=$live.Responding;title=$live.MainWindowTitle}}})"
        "|ConvertTo-Json -Compress"
    )
    value = _powershell_json(script)
    if isinstance(value, dict):
        return [value]
    return value if isinstance(value, list) else []


def process_snapshot(pid: int) -> dict[str, Any]:
    script = (
        f"$p=Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue;"
        "if($p){[pscustomobject]@{pid=$p.Id;cpu=$p.CPU;responding=$p.Responding;title=$p.MainWindowTitle}|ConvertTo-Json -Compress}"
    )
    value = _powershell_json(script)
    return value if isinstance(value, dict) else {}


def responsive_process_observed(snapshots: Iterable[Mapping[str, Any]]) -> bool:
    """Return whether a task-owned Windows process has become responsive."""

    for snapshot in snapshots:
        value = snapshot.get("responding")
        if value is True or str(value).strip().casefold() in {"1", "true", "yes"}:
            return True
    return False


def capture_dialogs(pid: int) -> list[dict[str, Any]]:
    script = (
        "Add-Type -AssemblyName UIAutomationClient -ErrorAction SilentlyContinue;"
        f"$pidWanted={int(pid)};"
        "$root=[System.Windows.Automation.AutomationElement]::RootElement;"
        "$cond=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty,$pidWanted);"
        "$wins=$root.FindAll([System.Windows.Automation.TreeScope]::Children,$cond);"
        "$out=@();foreach($w in $wins){$texts=$w.FindAll([System.Windows.Automation.TreeScope]::Descendants,"
        "(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Text)));"
        "$buttons=$w.FindAll([System.Windows.Automation.TreeScope]::Descendants,"
        "(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Button)));"
        "$isModal=$false;try{$isModal=[bool]$w.GetCurrentPropertyValue([System.Windows.Automation.WindowPatternIdentifiers]::IsModalProperty)}catch{};"
        "$out+=[pscustomobject]@{title=$w.Current.Name;text=(($texts|ForEach-Object{$_.Current.Name}) -join ' | ');buttons=@($buttons|ForEach-Object{$_.Current.Name});is_modal=$isModal}};"
        "$out|ConvertTo-Json -Compress -Depth 4"
    )
    value = _powershell_json(script, timeout=3.0)
    if isinstance(value, dict):
        return [value]
    return value if isinstance(value, list) else []


def sxcommand_path(simetrix_exe: Path) -> Path:
    return Path(simetrix_exe).resolve().with_name("SxCommand.exe")


def query_simulator_status(
    sxcommand: Path,
    *,
    message_log: Path | None = None,
    status_file: Path | None = None,
    timeout: float = 2.0,
) -> str:
    if not Path(sxcommand).is_file():
        return "Unavailable"
    command = "Echo V2_REMOTE_STATUS={GetSimulatorStatus()}"
    if status_file is not None:
        target = str(Path(status_file).resolve())
        if any(character in target for character in ("'", "\r", "\n")):
            return "Unavailable"
        commands = [
            "RedirectMessages flush",
            f"Let global:v2_remote_echo = OpenEchoFile('{target}', 'w')",
            "Echo V2_REMOTE_STATUS={GetSimulatorStatus()}",
            "Let global:v2_remote_close = CloseEchoFile()",
        ]
        query = Path(status_file).with_name(Path(status_file).name + ".query.sxscr")
        content = "\n".join(commands) + "\n"
        if not query.exists():
            query.write_text(content, encoding="utf-8")
        elif query.read_text(encoding="utf-8") != content:
            raise ValueError(f"Status query script was modified: {query}")
        command = f"Let global:v2_remote_query = Execute('{query.resolve().as_posix()}')"
    outputs: list[str] = []
    try:
        completed = subprocess.run(
            [str(sxcommand), "-quiet", "-immediate", command],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout,
        )
        outputs.extend((completed.stdout, completed.stderr))
    except (OSError, subprocess.TimeoutExpired):
        pass
    text = "\n".join(outputs).strip()
    if status_file is not None and Path(status_file).is_file():
        try:
            text += "\n" + Path(status_file).read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    if message_log is not None and Path(message_log).is_file():
        try:
            with Path(message_log).open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                handle.seek(max(0, handle.tell() - 80000))
                text += "\n" + handle.read().decode("utf-8", errors="replace")[-20000:]
        except OSError:
            pass
    matches = re.findall(r"V2_REMOTE_STATUS\s*=\s*(InProgress|ConvergenceFail|SimErrors|NetlistErrors|Warnings|Complete|Paused|None)", text, re.IGNORECASE)
    if matches:
        canonical = {name.casefold(): name for name in ("InProgress", "ConvergenceFail", "SimErrors", "NetlistErrors", "Warnings", "Complete", "Paused", "None")}
        return canonical[matches[-1].casefold()]
    for state in ("InProgress", "ConvergenceFail", "SimErrors", "NetlistErrors", "Warnings", "Complete", "Paused", "None"):
        if state.casefold() in text.casefold():
            return state
    return "Unavailable"


def recent_error_artifacts(root: Path, *, started_at: float, snapshot: list[dict[str, Any]] | None = None) -> list[Path]:
    suffixes = {".err", ".warn", ".dbg", ".lst", ".log"}
    result: list[Path] = []
    if not root.exists():
        return result
    for item in _artifact_snapshot(root) if snapshot is None else snapshot:
        path = root / item["path"]
        if path.suffix.casefold() not in suffixes or item["size"] == 0:
            continue
        if item["mtime"] >= started_at - 1.0:
            result.append(path)
    return sorted(result, key=lambda item: str(item).casefold())


def solver_diagnostics(artifacts: Iterable[Path]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    pop_records: list[dict[str, Any]] = []
    for source in artifacts:
        path = Path(source)
        if path.suffix.casefold() not in {".err", ".warn", ".dbg", ".lst", ".log"}:
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for index, line in enumerate(lines):
            if re.search(r"\b(?:error|warning)\s+(?:message\s+)?(?:id|number)\s*[:#=]?\s*\d+", line, re.I):
                number = re.search(r"(?:id|number)\s*[:#=]?\s*(\d+)", line, re.I)
                messages.append(dict(path=str(path), line=index + 1, error_id=number.group(1),
                                     text="\n".join(lines[max(0, index-1):index+9])))
            if re.search(r"POP (?:analysis|convergence)|Required POP convergence", line, re.I):
                pop_records.append(dict(path=str(path), line=index+1, text=line.strip()))
    return dict(messages=messages, pop_records=pop_records)


def write_error_bundle(
    path: Path,
    *,
    job_id: str,
    candidate: Mapping[str, Any],
    classification: str,
    timeline: Iterable[Mapping[str, Any]],
    status_values: Mapping[str, Any],
    dialogs: Iterable[Mapping[str, Any]],
    artifacts: Iterable[Path],
) -> Path:
    records: list[dict[str, Any]] = []
    for artifact in artifacts:
        item = Path(artifact)
        try:
            is_file = item.is_file()
            size = item.stat().st_size if is_file else None
        except OSError as exc:
            is_file = False
            size = None
            stat_error = repr(exc)
        else:
            stat_error = None
        record: dict[str, Any] = {"path": str(item), "size": size}
        if stat_error:
            record["read_error"] = stat_error
        if is_file:
            try:
                record["sha256"] = sha256_file(item)
                record["excerpt"] = item.read_text(encoding="utf-8", errors="replace")[:4000]
            except OSError as exc:
                record["read_error"] = repr(exc)
        records.append(record)
    payload = {
        "schema_version": 1,
        "job_id": job_id,
        "candidate": dict(candidate),
        "classification": classification,
        "timeline": [dict(item) for item in timeline],
        "status_values": dict(status_values),
        "dialogs": [dict(item) for item in dialogs],
        "artifacts": records,
        "solver_diagnostics": solver_diagnostics(Path(item["path"]) for item in records
                                                  if not item.get("read_error") and item.get("size")),
    }
    write_json(path, payload)
    return path


def _terminate_owned_pids(pids: Iterable[int]) -> None:
    for pid in sorted({int(item) for item in pids if int(item) > 0}, reverse=True):
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True, text=True)


def run_script_with_watchdog(
    *,
    simetrix_exe: Path,
    script: Path,
    status_file: Path,
    work_dir: Path,
    job_id: str,
    candidate: Mapping[str, Any] | None = None,
    warning_allowlist: Iterable[str] = (),
    config: WatchdogConfig | None = None,
    message_log: Path | None = None,
    assess_completion: bool = True,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Run one isolated SIMetrix script and preserve evidence on every exit."""

    config = config or WatchdogConfig()
    executable = Path(simetrix_exe).resolve()
    source = Path(script).resolve()
    root = Path(work_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not executable.is_file() or not source.is_file():
        return {"ok": False, "classification": "runtime_invalid", "errors": ["SIMetrix executable or script is missing"]}
    preexisting = {int(item["pid"]) for item in simetrix_processes() if item.get("pid") is not None}
    if preexisting:
        return {"ok": False, "classification": "unrelated_instance_present", "pids": sorted(preexisting)}

    status_target = Path(status_file).resolve()
    try:
        status_target.relative_to(root)
    except ValueError:
        return {"ok": False, "classification": "status_path_outside_work_dir", "status_file": str(status_target)}
    if status_target.exists():
        status_target.unlink()
    message_target = Path(message_log or root / "job-message.log").resolve()
    try:
        message_target.relative_to(root)
    except ValueError:
        return {"ok": False, "classification": "message_path_outside_work_dir", "message_log": str(message_target)}

    started_at = clock()
    process = subprocess.Popen(startup_command(executable, source), cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace")
    owned_pids = {int(process.pid)}
    monitor = WatchdogMonitor(started_at=started_at, config=config)
    last_dialogs: list[dict[str, Any]] = []
    next_dialog_at = started_at
    log_errors = _ScriptErrorReader(message_target)
    classification = "running"
    while True:
        now = clock()
        current_processes = simetrix_processes()
        owned_pids.update(int(item["pid"]) for item in current_processes if int(item.get("pid", 0)) not in preexisting)
        snapshots = [{key: item[key] for key in ("pid", "cpu", "responding", "title")}
                     for item in current_processes if int(item["pid"]) in owned_pids]
        alive_snapshots = [item for item in snapshots if item]
        cpu = sum(float(item.get("cpu") or 0.0) for item in alive_snapshots) if alive_snapshots else None
        dialogs: list[dict[str, Any]] = []
        status_values = parse_status_file(status_target)
        completed = str(status_values.get("completion_token", "")).casefold() in {"1", "true", "yes"}
        # Querying the command server while SIMetrix is still processing its
        # startup command can starve that command on some 8.4 installations.
        # Start remote polling only after the script proves it has begun.
        remote_status = (
            query_simulator_status(
                sxcommand_path(executable),
                message_log=message_target,
                status_file=root / "remote-simulator-status.txt",
                timeout=config.probe_timeout,
            )
            if not completed and str(status_values.get("script_started", "")).casefold() in {"1", "true", "yes"}
            else "Unavailable"
        )
        if not completed and (now >= next_dialog_at or any(not item.get("responding") for item in alive_snapshots)):
            for pid in owned_pids:
                dialogs.extend(capture_dialogs(pid))
            next_dialog_at = now + max(10.0, config.poll_interval)
            last_dialogs = dialogs
        else:
            dialogs = last_dialogs
        artifacts = _artifact_snapshot(root)
        result = monitor.observe(
            now=now,
            process_alive=bool(alive_snapshots) or process.poll() is None,
            launch_observed=responsive_process_observed(alive_snapshots),
            cpu_seconds=cpu,
            artifact_signature=_artifact_signature(root, excluded_paths=[message_target], snapshot=artifacts),
            simulator_status=remote_status,
            dialogs=dialogs,
        )
        monitor.timeline[-1]["owned_pids"] = sorted(owned_pids)
        monitor.timeline[-1]["processes"] = alive_snapshots
        monitor.timeline[-1]["artifacts"] = artifacts
        monitor.timeline[-1]["script_progress"] = status_values.get("progress")
        classification = str(result["classification"])
        script_errors = log_errors.read()
        current_error_files = [
            path
            for path in recent_error_artifacts(root, started_at=started_at, snapshot=artifacts)
            if path.suffix.casefold() in {".err", ".warn"}
        ]
        if current_error_files:
            classification = "simulator_error_artifact"
            monitor.timeline[-1]["classification"] = classification
            monitor.timeline[-1]["terminal"] = True
            monitor.timeline[-1]["error_files"] = [str(path) for path in current_error_files]
            break
        if script_errors:
            classification = "script_error_blocked"
            monitor.timeline[-1]["classification"] = classification
            monitor.timeline[-1]["terminal"] = True
            monitor.timeline[-1]["script_errors"] = script_errors
            break
        if str(status_values.get("completion_token", "")).casefold() in {"1", "true", "yes"}:
            classification = "script_complete"
            break
        if result["terminal"]:
            break
        time.sleep(config.poll_interval)

    error_artifacts = recent_error_artifacts(root, started_at=started_at)
    status_values = parse_status_file(status_target)
    if classification == "script_complete":
        if not assess_completion:
            captured_warnings = captured_warning_lines(message_target)
            if captured_warnings:
                status_values["simulation_warnings"] = captured_warnings
            if process.poll() is None:
                try:
                    process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    _terminate_owned_pids(owned_pids)
            return {
                "ok": True,
                "classification": "script_complete",
                "timeline": monitor.timeline,
                "status_values": status_values,
                "recent_error_files": [str(item) for item in error_artifacts if item.suffix.casefold() in {".err", ".warn"}],
                "owned_pids": sorted(owned_pids),
                "solver_diagnostics": solver_diagnostics(error_artifacts),
            }
        captured_warnings = captured_warning_lines(message_target)
        if captured_warnings:
            status_values["simulation_warnings"] = captured_warnings
        trust = assess_simulation_status(
            status_values,
            warning_allowlist=warning_allowlist,
            recent_error_files=[str(item) for item in error_artifacts if item.suffix.casefold() in {".err", ".warn"}],
        )
        if process.poll() is None:
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                _terminate_owned_pids(owned_pids)
        return {
            "ok": bool(trust["trusted"]),
            "classification": trust["classification"],
            "trust": trust,
            "timeline": monitor.timeline,
            "status_values": status_values,
            "owned_pids": sorted(owned_pids),
            "solver_diagnostics": solver_diagnostics(error_artifacts),
        }

    termination_classification = classification
    _terminate_owned_pids(owned_pids)
    try:
        stdout, stderr = process.communicate(timeout=3.0)
    except subprocess.TimeoutExpired:
        stdout, stderr = "", ""
    stdout_path = root / "process.stdout.log"
    stderr_path = root / "process.stderr.log"
    stdout_path.write_text(stdout or "", encoding="utf-8")
    stderr_path.write_text(stderr or "", encoding="utf-8")
    # 8.40 可能独占日志；进程退出释放文件后，补充实际脚本错误及原文。
    script_errors = log_errors.read()
    if script_errors:
        classification = "script_error_blocked"
        status_values["script_errors"] = script_errors
    error_artifacts.extend((stdout_path, stderr_path))
    evidence_inputs = [source]
    for pattern in ("*.net", "*.deck", "*.sxsch"):
        evidence_inputs.extend(path for path in root.rglob(pattern) if path.is_file())
    error_artifacts.extend(path for path in evidence_inputs if path not in error_artifacts)
    bundle = write_error_bundle(
        root / "error-bundle.json",
        job_id=job_id,
        candidate=candidate or {},
        classification=classification,
        timeline=monitor.timeline,
        status_values=status_values,
        dialogs=last_dialogs,
        artifacts=error_artifacts,
    )
    return {
        "ok": False,
        "classification": classification,
        "termination_classification": termination_classification,
        "error_bundle": str(bundle),
        "timeline": monitor.timeline,
        "status_values": status_values,
        "owned_pids": sorted(owned_pids),
        "solver_diagnostics": json.loads(bundle.read_text(encoding="utf-8"))["solver_diagnostics"],
    }
