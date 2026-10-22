from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import shutil
import sys
import time

from run_storage import register_run, write_json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from simplis_automation_v2.execution import assess_simulation_status, build_simulation_script, validate_group_evidence, _vector_slug
from simplis_automation_v2.experiment_runner import run_experiment, _simulation_info_matches_current_run
from simplis_automation_v2.importer import _parse_instances_and_wires
from simplis_automation_v2.io import load_yaml
from simplis_automation_v2.runtime import resolve_runtime
from simplis_automation_v2.vectors import parse_show_file
from simplis_automation_v2.watchdog import WatchdogConfig, run_script_with_watchdog


def fresh_directory(root: Path) -> Path:
    root = root.resolve()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ValueError("Managed execution requires a new or empty output directory")
    root.mkdir(parents=True, exist_ok=True)
    return root


def finish(root: Path, result: dict, kind: str) -> None:
    write_json(root / "run.json", result)
    roles = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if path.suffix.casefold() in {".sxsch", ".sxcmp", ".yaml", ".net", ".deck", ".sxscr", ".ckt"} or relative.parts[0] == "inputs":
            role = "input"
        elif path.suffix.casefold() in {".log", ".err", ".warn", ".lst"}:
            role = "log"
        elif path.suffix.casefold() == ".json":
            role = "record"
        elif "vectors" in relative.parts or "SIMPLIS_Data" in relative.parts or path.suffix.casefold() in {".t0", ".t2", ".tc", ".sxdat", ".sxd"}:
            role = "raw"
        elif "TMP" in relative.parts:
            role = "scratch"
        else:
            role = "evidence"
        roles[relative.as_posix()] = role
    register_run(root, roles, kind=kind if result["completed"] else "diagnostic",
                 completed=result["completed"], environment=result.get("runtime", {}), source=result["source"])


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


def run_yaml(experiment: Path, out: Path, *, runtime: dict | None = None,
             kind: str = "candidate") -> dict:
    experiment = experiment.resolve()
    config = load_yaml(experiment)
    circuit = (experiment.parent / config["circuit"]).resolve()
    circuit_config = load_yaml(circuit)
    catalog = (circuit.parent / circuit_config["catalog_lock"]["path"]).resolve()
    root = fresh_directory(out)
    inputs = root / "inputs"
    inputs.mkdir()
    for label, path in (("experiment", experiment), ("circuit", circuit), ("catalog", catalog)):
        shutil.copy2(path, inputs / (label + path.suffix))
    result = dict(completed=False, workflow="v2", started_utc=datetime.now(timezone.utc).isoformat(),
                  source=dict(kind="v2-yaml", authoritative=[str(experiment), str(circuit)],
                              snapshots={label: dict(original=str(path), file=f"inputs/{label}{path.suffix}")
                                         for label, path in (("experiment", experiment), ("circuit", circuit), ("catalog", catalog))},
                              native_outputs="generated; import GUI edits into YAML before rerunning"))
    try:
        backend = run_experiment(experiment, root / "execution", runtime=runtime)
        result.update(completed=bool(backend["ok"]), backend_result="execution/experiment-result.json",
                      runtime=backend.get("runtime", {}), classification=backend["classification"])
    except Exception as exc:
        result["error"] = repr(exc)
        raise
    finally:
        finish(root, result, kind)
    return result


def run_native(schematic: Path, out: Path, *, runtime: dict, vectors: list[str],
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
        active = resolve_runtime(runtime)
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


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Optional storage registration around unchanged v2 or native execution")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("v2")
    p.add_argument("--experiment", type=Path, required=True)
    p = commands.add_parser("native")
    p.add_argument("--schematic", type=Path, required=True)
    p.add_argument("--project-root", type=Path)
    p.add_argument("--vector", nargs="+", required=True)
    p.add_argument("--group", default="simplis_tran1")
    p.add_argument("--timeout", type=float, default=120)
    for subparser in commands.choices.values():
        subparser.add_argument("--out-dir", type=Path, required=True)
        subparser.add_argument("--runtime", type=Path, required=True, help="Existing v2 runtime JSON")
        subparser.add_argument("--kind", choices=("temporary", "candidate", "final"), default="candidate")
    args = parser.parse_args()
    runtime = json.loads(args.runtime.read_text(encoding="utf-8"))
    result = (run_yaml(args.experiment, args.out_dir, runtime=runtime, kind=args.kind) if args.command == "v2"
              else run_native(args.schematic, args.out_dir, runtime=runtime, vectors=args.vector,
                              project_root=args.project_root, group=args.group, kind=args.kind, timeout=args.timeout))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["completed"] else 1)
