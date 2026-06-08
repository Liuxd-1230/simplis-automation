#!/usr/bin/env python3
"""Generate and score simple SIMPLIS sweep candidates.

This is intentionally project-agnostic: it writes candidate JSON and delegates
actual SIMetrix/SIMPLIS work to a user-provided script template.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import subprocess
import sys
from pathlib import Path

from runtime_config import resolve_simetrix_exe


def load_spec(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def candidates(params: dict[str, list[float]]) -> list[dict[str, float]]:
    keys = list(params)
    return [dict(zip(keys, values)) for values in itertools.product(*(params[k] for k in keys))]


def render_template(template: str, cand: dict[str, float], result_path: Path) -> str:
    values = {k: str(v) for k, v in cand.items()}
    values["RESULT_JSON"] = str(result_path)
    text = template
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def score_metrics(metrics: dict, weights: dict) -> float:
    if metrics.get("failed"):
        return math.inf
    score = 0.0
    for name, weight in weights.items():
        value = metrics.get(name)
        if value is None:
            return math.inf
        score += float(weight) * abs(float(value))
    return score


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("spec", help="JSON spec with parameters, template, and weights")
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--simetrix-exe", help="Path to SIMetrix.exe; overrides runtime config")
    parser.add_argument("--runtime-config", help="JSON runtime config with simetrix_exe")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    simetrix_exe = str(resolve_simetrix_exe(args.simetrix_exe, config_path=args.runtime_config)) if not args.dry_run else ""

    spec_path = Path(args.spec).resolve()
    spec = load_spec(spec_path)
    work = Path(args.work_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    template = Path(spec["script_template"]).read_text(encoding="utf-8")
    weights = spec.get("weights", {})
    results = []

    for idx, cand in enumerate(candidates(spec["parameters"])):
        result_json = work / f"candidate_{idx:04d}_metrics.json"
        script = work / f"candidate_{idx:04d}.sxscr"
        script.write_text(render_template(template, cand, result_json), encoding="utf-8")
        rc = 0
        if not args.dry_run:
            rc = subprocess.run([simetrix_exe, "/i", "/s", str(script)], cwd=str(work), check=False).returncode
        metrics = {"failed": rc != 0}
        if result_json.exists():
            metrics.update(json.loads(result_json.read_text(encoding="utf-8")).get("metrics", {}))
        results.append({"index": idx, "candidate": cand, "returncode": rc, "metrics": metrics, "score": score_metrics(metrics, weights)})

    results.sort(key=lambda row: row["score"])
    (work / "sweep_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results[: min(10, len(results))], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
