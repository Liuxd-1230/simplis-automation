#!/usr/bin/env python3
"""Closed-loop SIMetrix/SIMPLIS parameter optimization scaffold.

The script is intentionally schematic-agnostic. A project-specific .sxscr
template performs parameter injection, runs SIMPLIS, and writes metric JSON.
This Python wrapper proposes candidates, launches SIMetrix, scores metrics,
and resumes from history.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

from runtime_config import resolve_simetrix_exe


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, allow_nan=False), encoding="utf-8")


def normalize_parameters(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    params: dict[str, dict[str, Any]] = {}
    for name, value in raw.items():
        if isinstance(value, list):
            params[name] = {"values": value, "initial": value[0]}
        elif isinstance(value, dict):
            params[name] = dict(value)
        else:
            raise ValueError(f"Unsupported parameter spec for {name!r}: {value!r}")
    return params


def candidate_key(candidate: dict[str, float]) -> str:
    return json.dumps({k: candidate[k] for k in sorted(candidate)}, sort_keys=True)


def clip(value: float, spec: dict[str, Any]) -> float:
    bounds = spec.get("bounds")
    if bounds:
        value = max(float(bounds[0]), min(float(bounds[1]), value))
    return value


def render_template(template: str, candidate: dict[str, float], result_json: Path, candidate_json: Path) -> str:
    values = {name: f"{value:.12g}" for name, value in candidate.items()}
    values["RESULT_JSON"] = str(result_json)
    values["CANDIDATE_JSON"] = str(candidate_json)
    text = template
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def grid_candidates(params: dict[str, dict[str, Any]]) -> list[dict[str, float]]:
    names = list(params)
    value_lists = []
    for name in names:
        spec = params[name]
        if "values" not in spec:
            raise ValueError(f"Grid strategy requires parameter {name!r} to define values")
        value_lists.append([float(v) for v in spec["values"]])
    return [dict(zip(names, values)) for values in itertools.product(*value_lists)]


def random_candidate(params: dict[str, dict[str, Any]], rng: random.Random) -> dict[str, float]:
    candidate: dict[str, float] = {}
    for name, spec in params.items():
        if "values" in spec:
            candidate[name] = float(rng.choice(spec["values"]))
            continue
        lo, hi = map(float, spec["bounds"])
        if spec.get("scale") == "log":
            candidate[name] = 10 ** rng.uniform(math.log10(lo), math.log10(hi))
        else:
            candidate[name] = rng.uniform(lo, hi)
    return candidate


def initial_candidate(params: dict[str, dict[str, Any]]) -> dict[str, float]:
    candidate = {}
    for name, spec in params.items():
        if "initial" in spec:
            candidate[name] = float(spec["initial"])
        elif "values" in spec:
            candidate[name] = float(spec["values"][0])
        else:
            lo, hi = map(float, spec["bounds"])
            candidate[name] = math.sqrt(lo * hi) if spec.get("scale") == "log" else 0.5 * (lo + hi)
    return candidate


def coordinate_neighbors(center: dict[str, float], params: dict[str, dict[str, Any]], shrink: float) -> list[dict[str, float]]:
    out = [dict(center)]
    for name, spec in params.items():
        step = float(spec.get("step", 0.1))
        if spec.get("scale") == "log":
            factor = float(spec.get("factor", 10 ** step)) ** shrink
            for value in (center[name] / factor, center[name] * factor):
                cand = dict(center)
                cand[name] = clip(value, spec)
                out.append(cand)
        else:
            delta = step * shrink
            for value in (center[name] - delta, center[name] + delta):
                cand = dict(center)
                cand[name] = clip(value, spec)
                out.append(cand)
    unique: dict[str, dict[str, float]] = {}
    for cand in out:
        unique[candidate_key(cand)] = cand
    return list(unique.values())


def score_metrics(metrics: dict[str, Any], weights: dict[str, float], constraints: dict[str, dict[str, float]]) -> float:
    if metrics.get("failed"):
        return math.inf
    penalty = 0.0
    for name, rule in constraints.items():
        if name not in metrics:
            return math.inf
        value = float(metrics[name])
        if "min" in rule and value < float(rule["min"]):
            penalty += 1e9 + (float(rule["min"]) - value) ** 2
        if "max" in rule and value > float(rule["max"]):
            penalty += 1e9 + (value - float(rule["max"])) ** 2
    score = penalty
    for name, weight in weights.items():
        if name not in metrics:
            return math.inf
        score += float(weight) * abs(float(metrics[name]))
    return score


def read_metrics(path: Path, returncode: int, dry_run: bool) -> dict[str, Any]:
    if dry_run:
        return {"failed": False, "dry_run": True}
    if returncode != 0:
        return {"failed": True, "returncode": returncode}
    if not path.exists():
        return {"failed": True, "missing_metrics": True}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {"failed": True, "empty_metrics": True}
    try:
        raw = json.loads(text)
        metrics = raw.get("metrics", raw)
    except json.JSONDecodeError:
        metrics = parse_key_value_metrics(text)
    metrics.setdefault("failed", False)
    return metrics


def parse_key_value_metrics(text: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if value.lower() in {"true", "false"}:
            metrics[name] = value.lower() == "true"
            continue
        try:
            metrics[name] = float(value)
        except ValueError:
            metrics[name] = value
    if not metrics:
        return {"failed": True, "unparsed_metrics": True}
    return metrics


def mock_metrics(candidate: dict[str, float], spec: dict[str, Any]) -> dict[str, float | bool]:
    target = spec.get("mock_target", {})
    metrics = {"failed": False}
    error = 0.0
    for name, value in candidate.items():
        center = float(target.get(name, value))
        error += (float(value) - center) ** 2
    metrics["objective"] = error
    metrics["vout_dc_error_mv"] = 1000.0 * error
    metrics["loadstep_undershoot_mv"] = 100.0 * math.sqrt(error)
    return metrics


def run_candidate(
    idx: int,
    candidate: dict[str, float],
    template: str,
    work: Path,
    spec: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    result_json = work / f"candidate_{idx:04d}_metrics.json"
    candidate_json = work / f"candidate_{idx:04d}_candidate.json"
    script = work / f"candidate_{idx:04d}.sxscr"
    save_json(candidate_json, {"candidate": candidate})
    script.write_text(render_template(template, candidate, result_json, candidate_json), encoding="utf-8")

    if args.mock:
        metrics = mock_metrics(candidate, spec)
        save_json(result_json, {"candidate": candidate, "metrics": metrics})
        rc = 0
    elif args.dry_run:
        metrics = read_metrics(result_json, 0, dry_run=True)
        rc = 0
    else:
        cmd = [args.simetrix_exe]
        if args.interactive:
            cmd.append("/i")
        cmd += ["/s", str(script)]
        try:
            rc = subprocess.run(cmd, cwd=str(work), check=False, timeout=args.timeout).returncode
        except subprocess.TimeoutExpired:
            rc = 124
        metrics = read_metrics(result_json, rc, dry_run=False)

    weights = {k: float(v) for k, v in spec.get("weights", {}).items()}
    constraints = spec.get("constraints", {})
    score = score_metrics(metrics, weights, constraints)
    return {
        "index": idx,
        "candidate": candidate,
        "script": str(script),
        "result_json": str(result_json),
        "returncode": rc,
        "metrics": metrics,
        "score": score,
    }


def next_candidates(
    strategy: str,
    params: dict[str, dict[str, Any]],
    history: list[dict[str, Any]],
    spec: dict[str, Any],
    rng: random.Random,
    seen: set[str],
) -> list[dict[str, float]]:
    if strategy == "grid":
        return [cand for cand in grid_candidates(params) if candidate_key(cand) not in seen]
    if strategy == "random":
        out = []
        attempts = 0
        while len(out) < int(spec.get("batch_size", 1)) and attempts < 1000:
            attempts += 1
            cand = random_candidate(params, rng)
            key = candidate_key(cand)
            if key not in seen:
                out.append(cand)
                seen.add(key)
        return out
    if strategy == "coordinate":
        completed = [row for row in history if math.isfinite(float(row.get("score", math.inf)))]
        if completed:
            center = min(completed, key=lambda row: float(row["score"]))["candidate"]
        else:
            center = initial_candidate(params)
        rounds = max(0, len(history) // max(1, 1 + 2 * len(params)))
        shrink = float(spec.get("shrink", 0.5)) ** rounds
        return [cand for cand in coordinate_neighbors(center, params, shrink) if candidate_key(cand) not in seen]
    raise ValueError(f"Unknown strategy: {strategy}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Closed-loop SIMPLIS optimizer")
    parser.add_argument("spec", help="JSON optimization spec")
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--simetrix-exe", help="Path to SIMetrix.exe; overrides runtime config")
    parser.add_argument("--runtime-config", help="JSON runtime config with simetrix_exe")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mock", action="store_true", help="Generate synthetic metrics for optimizer validation")
    args = parser.parse_args(argv)
    if not args.mock and not args.dry_run:
        args.simetrix_exe = str(resolve_simetrix_exe(args.simetrix_exe, config_path=args.runtime_config))

    spec_path = Path(args.spec).resolve()
    spec = load_json(spec_path)
    work = Path(args.work_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    template = Path(spec["script_template"]).read_text(encoding="utf-8")
    params = normalize_parameters(spec["parameters"])
    strategy = spec.get("strategy", "coordinate")
    max_evals = int(spec.get("max_evals", spec.get("iterations", 20)))
    rng = random.Random(int(spec.get("seed", 1)))

    history_path = work / "optimization_history.json"
    history: list[dict[str, Any]] = load_json(history_path) if history_path.exists() else []
    seen = {candidate_key(row["candidate"]) for row in history}

    while len(history) < max_evals:
        batch = next_candidates(strategy, params, history, spec, rng, seen)
        if not batch:
            break
        for cand in batch:
            if len(history) >= max_evals:
                break
            key = candidate_key(cand)
            if key in seen:
                continue
            seen.add(key)
            row = run_candidate(len(history), cand, template, work, spec, args)
            history.append(row)
            save_json(history_path, history)
            best = min(history, key=lambda item: float(item.get("score", math.inf)))
            print(json.dumps({"latest": row, "best": best}, indent=2, allow_nan=False))

    if history:
        best = min(history, key=lambda item: float(item.get("score", math.inf)))
        save_json(work / "best_candidate.json", best)
        print(json.dumps({"best_candidate": best}, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
