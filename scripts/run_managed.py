from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from simplis_automation_v2.native_runner import snapshot_native, run_native
from simplis_automation_v2.run_storage import fresh_directory, finish_run as finish
from simplis_automation_v2.experiment_runner import run_experiment


def run_yaml(experiment: Path, out: Path, *, runtime: dict | None = None, kind: str = "candidate") -> dict:
    run_experiment(experiment, out, runtime=runtime, _execution_subdir="execution", kind=kind)
    return json.loads((out / "run.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Compatibility commands for managed v2 or native execution")
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
        subparser.add_argument("--runtime", type=Path, help="Existing v2 runtime JSON")
        subparser.add_argument("--kind", choices=("temporary", "candidate", "final"), default="candidate")
    args = parser.parse_args()
    runtime = json.loads(args.runtime.read_text(encoding="utf-8")) if args.runtime else None
    result = (run_yaml(args.experiment, args.out_dir, runtime=runtime, kind=args.kind) if args.command == "v2"
              else run_native(args.schematic, args.out_dir, runtime=runtime, vectors=args.vector,
                              project_root=args.project_root, group=args.group, kind=args.kind, timeout=args.timeout))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["completed"] else 1)
