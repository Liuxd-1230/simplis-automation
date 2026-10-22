from __future__ import annotations

from datetime import datetime, timezone
import math
from pathlib import Path
import shutil
import time

from .execution import assess_simulation_status, build_simulation_script, validate_group_evidence, _vector_slug
from .experiment_runner import _simulation_info_matches_current_run
from .importer import _parse_instances_and_wires
from .runtime import resolve_runtime
from .vectors import parse_show_file
from .watchdog import WatchdogConfig, run_script_with_watchdog
from .run_storage import fresh_directory, finish_run as finish

def snapshot_native(source: Path, project: Path, destination: Path) -> Path:
    pending = [source]
    seen = set()
    while pending:
        path = pending.pop().resolve()
        if path in seen:
            continue
        if not path.is_relative_to(project):
            raise ValueError(f"Native module is outside declared project root: {path}")
        text = path.read_text(encoding="utf-8-sig")
        instances, _, diagnostics = _parse_instances_and_wires(text)
        if diagnostics:
            raise ValueError(f"Native source could not be read: {diagnostics}")
        for item in instances:
            attrs = item["attributes"]
            if attrs.get("type") == "component":
                dependency = Path(attrs["path"])
                if dependency.is_absolute():
                    raise ValueError("Staged native modules must use project-relative component paths")
                pending.append(path.parent / dependency)
        target = destination / path.relative_to(project)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        seen.add(path)
    return destination / source.relative_to(project)


def run_native(schematic: Path, out: Path, *, runtime: dict | None = None, vectors: list[str],
               project_root: Path | None = None, group: str = "simplis_tran1",
               kind: str = "candidate", timeout: float = 120) -> dict:
    source = schematic.resolve()
    if source.suffix.casefold() != ".sxsch":
        raise ValueError("The native authoritative source must be a .sxsch")
    root = fresh_directory(out)
    result = dict(completed=False, behavior_validated=False, workflow="native",
                  started_utc=datetime.now(timezone.utc).isoformat(),
                  source=dict(kind="native", authoritative=[str(source)], snapshots="execution/source",
                              generated_outputs="netlists and waveforms; edit original .sxsch and rerun"))
    try:
        work = root / "execution"
        staged = snapshot_native(source, (project_root or source.parent).resolve(), work / "source")
        result["source"]["staged_schematic"] = staged.relative_to(root).as_posix()
        active = resolve_runtime(runtime, project_dir=source.parent)
        result["runtime"] = active
        if not active["watchdog_ready"]:
            raise ValueError(active["errors"])
        status, log = work / "native-status.txt", work / "native.log"
        vector_dir = work / "vectors"
        vector_dir.mkdir()
        script = work / "native-run.sxscr"
        script.write_text(build_simulation_script(schematic=staged, status_file=status,
                          message_log=log, required_vectors={group: vectors}, vector_dir=vector_dir), encoding="utf-8")
        begun = time.time()
        process = run_script_with_watchdog(simetrix_exe=Path(active["executable"]), script=script,
                    status_file=status, work_dir=work, message_log=log, job_id=root.name,
                    config=WatchdogConfig(hard_timeout=timeout), assess_completion=False)
        result["execution"] = process
        if process["ok"]:
            state = process["status_values"]
            groups = validate_group_evidence(state, {group: {"analysis": "tran", "vectors": vectors}})
            info = _simulation_info_matches_current_run(state, schematic=staged, run_root=work, run_started_at=begun)
            exports = []
            for vector in vectors:
                path = vector_dir / f"{_vector_slug(group)}_{_vector_slug(vector)}.txt"
                data = parse_show_file(path)
                exports.append(dict(file=path.relative_to(root).as_posix(), vector=vector,
                                    samples=len(data["y"]), finite=all(math.isfinite(v) for values in (data["x"], data["y"]) for v in values),
                                    monotonic=all(b >= a for a, b in zip(data["x"], data["x"][1:])),
                                    fresh=path.stat().st_mtime >= begun - 1))
            result.update(group_evidence=groups, simulation_info_evidence=info, vectors=exports)
            netlist = staged.parent / "SIMPLIS_Data" / (staged.stem + ".net")
            current_netlist = netlist.is_file() and netlist.stat().st_mtime >= begun - 1
            if current_netlist:
                with netlist.open(encoding="utf-8") as stream:
                    header = stream.readline().strip().replace("\\", "/")
                current_netlist = header == "* " + staged.as_posix()
            evidence = dict(watchdog_progress_observed=any(t.get("progressed") for t in process["timeline"]),
                            stage_tokens_complete=all(state.get(k) == "true" for k in
                                  ("simulation_started", "simulation_returned", "errors_collected", "vector_export_done", "completion_token")),
                            vectors_fresh_complete=groups["valid"] and bool(exports) and
                                  all(v["samples"] and v["finite"] and v["monotonic"] and v["fresh"] for v in exports))
            trust = assess_simulation_status(state, warning_allowlist=[],
                        recent_error_files=process.get("recent_error_files", []), completion_evidence=evidence)
            result.update(execution_trust=trust, current_native_netlist=current_netlist)
            result["completed"] = trust["trusted"] and current_netlist
    except Exception as exc:
        result["error"] = repr(exc)
        raise
    finally:
        finish(root, result, kind)
    return result
