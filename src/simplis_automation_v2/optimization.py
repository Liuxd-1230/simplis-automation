"""Recoverable controller-only optimization with ESR continuation."""

from __future__ import annotations

import math
import shutil
from pathlib import Path
from typing import Any, Callable, Mapping

from .experiment_runner import run_experiment
from .io import canonical_json, load_yaml, sha256_text, write_json, write_yaml
from .schema import load_experiment


Evaluator = Callable[[dict[str, float], Path, Mapping[str, Any]], Mapping[str, Any]]
CandidateBackend = Callable[[Mapping[str, Mapping[str, Any]], Mapping[str, float], int], dict[str, float]]

FAILURE_PENALTIES = {
    "infrastructure_failed": 1_400_000.0,
    "netlist_failed": 1_300_000.0,
    "simulation_failed": 1_200_000.0,
    "data_failed": 1_100_000.0,
    "electrical_behavior_failed": 1_000_000.0,
}

_BACKENDS: dict[str, CandidateBackend] = {}


def register_optimizer_backend(name: str, backend: CandidateBackend) -> None:
    """Register an optional refinement backend (for example a TuRBO adapter)."""

    if not name or not callable(backend):
        raise ValueError("Optimizer backend needs a non-empty name and callable")
    _BACKENDS[name.casefold()] = backend


def _bounds(definition: Mapping[str, Any]) -> tuple[float, float, float]:
    low = float(definition.get("min", definition.get("bounds", [0, 1])[0]))
    high = float(definition.get("max", definition.get("bounds", [0, 1])[1]))
    initial = float(definition.get("initial", (low + high) / 2))
    if not math.isfinite(low) or not math.isfinite(high) or not low < high or not low <= initial <= high:
        raise ValueError(f"Invalid optimizer bounds: {definition}")
    return low, high, initial


def _unit_point(index: int, dimension: int) -> float:
    # Deterministic low-discrepancy radical-inverse sequence.
    primes = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29)
    base = primes[dimension % len(primes)]
    value = index + 1
    factor = 1.0 / base
    result = 0.0
    while value:
        result += factor * (value % base)
        value //= base
        factor /= base
    return result


def _initial_candidate(parameters: Mapping[str, Mapping[str, Any]], index: int) -> dict[str, float]:
    candidate: dict[str, float] = {}
    for dimension, (name, definition) in enumerate(sorted(parameters.items())):
        low, high, initial = _bounds(definition)
        if index == 0:
            candidate[name] = initial
            continue
        unit = _unit_point(index - 1, dimension)
        if str(definition.get("scale", "linear")).casefold() == "log":
            if low <= 0:
                raise ValueError(f"Log-scaled optimizer parameter {name} must be positive")
            candidate[name] = math.exp(math.log(low) + unit * (math.log(high) - math.log(low)))
        else:
            candidate[name] = low + unit * (high - low)
    return candidate


def _coordinate_candidate(parameters: Mapping[str, Mapping[str, Any]], best: Mapping[str, float], index: int) -> dict[str, float]:
    names = sorted(parameters)
    name = names[index % len(names)]
    direction = -1.0 if (index // len(names)) % 2 else 1.0
    generation = index // max(1, 2 * len(names))
    candidate = dict(best)
    low, high, _ = _bounds(parameters[name])
    step = (high - low) * 0.2 * (0.5**generation)
    candidate[name] = min(high, max(low, float(best[name]) + direction * step))
    return candidate


register_optimizer_backend("coordinate", _coordinate_candidate)


def _flatten_metrics(result: Mapping[str, Any]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for validation in result.get("validation", []):
        if isinstance(validation, Mapping):
            for name, value in dict(validation.get("metrics", {})).items():
                if isinstance(value, (int, float)) and math.isfinite(float(value)):
                    metrics[str(name)] = float(value)
    return metrics


def _score(result: Mapping[str, Any], optimize: Mapping[str, Any]) -> tuple[float, dict[str, float]]:
    metrics = _flatten_metrics(result)
    if not result.get("ok"):
        classification = str(result.get("classification", "infrastructure_failed"))
        return FAILURE_PENALTIES.get(classification, 1_500_000.0), metrics
    score = 0.0
    objective = optimize.get("objective", {})
    for name, definition in objective.items() if isinstance(objective, Mapping) else []:
        if name not in metrics:
            return 900_000.0, metrics
        if isinstance(definition, Mapping):
            target = float(definition.get("target", 0.0))
            weight = float(definition.get("weight", 1.0))
            sense = str(definition.get("sense", "min"))
            residual = metrics[name] - target
            score += weight * (residual if sense == "min" else -residual)
        else:
            score += float(definition) * metrics[name]
    constraints = optimize.get("constraints", {})
    for name, definition in constraints.items() if isinstance(constraints, Mapping) else []:
        if name not in metrics:
            score += 100_000.0
            continue
        if "min" in definition:
            score += 10_000.0 * max(0.0, float(definition["min"]) - metrics[name]) ** 2
        if "max" in definition:
            score += 10_000.0 * max(0.0, metrics[name] - float(definition["max"])) ** 2
    return float(score), metrics


def _default_evaluator(experiment_path: Path, runtime: Any) -> Evaluator:
    def evaluate(candidate: dict[str, float], directory: Path, context: Mapping[str, Any]) -> Mapping[str, Any]:
        overrides = dict(candidate)
        if context.get("esr_parameter"):
            overrides[str(context["esr_parameter"])] = float(context["esr_target"]) * float(context["esr_multiplier"])
        overrides.update(context.get("corner", {}))
        return run_experiment(experiment_path, directory, runtime=runtime, parameter_overrides=overrides)

    return evaluate


def run_optimization(
    experiment_path: str | Path,
    out_dir: str | Path,
    *,
    runtime: Mapping[str, Any] | str | Path | None = None,
    evaluator: Evaluator | None = None,
) -> dict[str, Any]:
    """Exhaust the declared budget while isolating every failed candidate."""

    source = Path(experiment_path).resolve()
    root = Path(out_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    experiment = load_experiment(source)
    optimize = experiment["optimize"]
    parameters = optimize["parameters"]
    backend_name = str(optimize.get("backend", "coordinate")).casefold()
    backend = _BACKENDS.get(backend_name)
    if backend is None:
        raise ValueError(f"Optimizer backend {backend_name!r} is not registered; install/register its adapter before running")
    budget = int(optimize.get("max_evaluations", 40))
    seed_count = min(budget, max(2 * len(parameters), len(parameters) + 1))
    continuation = optimize.get("esr_continuation", {})
    multipliers = list(continuation.get("multipliers", [4, 2, 1]))
    if not multipliers or float(multipliers[-1]) != 1.0:
        raise ValueError("ESR continuation must finish at the target multiplier 1")
    context_base = {
        "esr_parameter": continuation.get("parameter"),
        "esr_target": continuation.get("target", 0.005),
    }
    evaluate = evaluator or _default_evaluator(source, runtime)
    history: list[dict[str, Any]] = []
    counts = {name: 0 for name in (*FAILURE_PENALTIES, "complete", "exception")}
    best: dict[str, Any] | None = None
    best_by_stage: dict[str, dict[str, Any]] = {}
    for index in range(budget):
        stage_index = min(len(multipliers) - 1, (index * len(multipliers)) // budget)
        multiplier = float(multipliers[stage_index])
        stage = f"esr_{multiplier:g}x"
        if index < seed_count or best is None:
            candidate = _initial_candidate(parameters, index)
        else:
            stage_best = best_by_stage.get(stage, best)
            candidate = backend(parameters, stage_best["candidate"], index - seed_count)
        candidate_id = f"candidate-{index:03d}"
        candidate_dir = root / "candidates" / candidate_id
        candidate_dir.mkdir(parents=True, exist_ok=True)
        context = {**context_base, "esr_multiplier": multiplier, "stage": stage, "corner": {}}
        try:
            raw_result = evaluate(candidate, candidate_dir, context)
            result = dict(raw_result)
            classification = str(result.get("classification", "complete" if result.get("ok") else "infrastructure_failed"))
            counts[classification] = counts.get(classification, 0) + 1
            score, metrics = _score(result, optimize)
        except Exception as exc:
            result = {"ok": False, "classification": "exception", "reason": repr(exc)}
            classification = "exception"
            counts["exception"] += 1
            score, metrics = 1_600_000.0, {}
        record = {
            "id": candidate_id,
            "index": index,
            "candidate": candidate,
            "candidate_hash": sha256_text(canonical_json(candidate)),
            "stage": stage,
            "esr_multiplier": multiplier,
            "classification": classification,
            "score": float(score),
            "metrics": metrics,
            "result_path": str(candidate_dir / "experiment-result.json"),
        }
        history.append(record)
        if best is None or record["score"] < best["score"]:
            best = record
        if stage not in best_by_stage or record["score"] < best_by_stage[stage]["score"]:
            best_by_stage[stage] = record
        write_json(root / "optimization-history.json", {"budget": budget, "evaluations": len(history), "counts": counts, "history": history})

    target_stage = "esr_1x"
    final_best = best_by_stage.get(target_stage)
    corners = optimize.get("corners", [])
    corner_results: list[dict[str, Any]] = []
    if final_best and final_best["classification"] == "complete":
        for corner_index, corner in enumerate(corners if isinstance(corners, list) else []):
            corner_dir = root / "promotion" / f"corner-{corner_index:02d}"
            corner_dir.mkdir(parents=True, exist_ok=True)
            context = {**context_base, "esr_multiplier": 1.0, "stage": "promotion", "corner": dict(corner.get("parameters", {}))}
            try:
                corner_result = dict(evaluate(dict(final_best["candidate"]), corner_dir, context))
            except Exception as exc:
                corner_result = {"ok": False, "classification": "exception", "reason": repr(exc)}
            corner_score, corner_metrics = _score(corner_result, optimize)
            corner_results.append(
                {
                    "name": corner.get("name", f"corner-{corner_index}"),
                    "score": corner_score,
                    "metrics": corner_metrics,
                    "result": corner_result,
                }
            )
    passed = bool(final_best and final_best["classification"] == "complete" and all(item["result"].get("ok") for item in corner_results))
    worst_corner = max(corner_results, key=lambda item: float(item["score"])) if corner_results else None
    output = {
        "schema_version": "simplis-automation/v2/optimization-result",
        "ok": passed,
        "budget": budget,
        "evaluations": len(history),
        "budget_exhausted": len(history) == budget,
        "counts": counts,
        "best_target_esr": final_best,
        "corner_results": corner_results,
        "worst_corner": worst_corner,
        "classification": "complete" if passed else "no_fully_valid_candidate",
    }
    if passed and final_best:
        derived = root / "best-derived"
        derived.mkdir(parents=True, exist_ok=True)
        write_json(derived / "parameter-diff.json", final_best["candidate"])
        derived_experiment = load_yaml(source)
        derived_experiment["derived_from"] = str(source)
        derived_experiment["fixed_parameters"] = dict(final_best["candidate"])
        write_yaml(derived / "experiment.yaml", derived_experiment)
        circuit_source = Path(str(experiment["circuit"]))
        if not circuit_source.is_absolute():
            circuit_source = source.parent / circuit_source
        if circuit_source.is_file():
            derived_circuit = load_yaml(circuit_source)
            for name, value in final_best["candidate"].items():
                definition = derived_circuit.get("parameters", {}).get(name)
                if isinstance(definition, dict):
                    definition["default"] = value
            derived_circuit["derived_from"] = str(circuit_source.resolve())
            write_yaml(derived / "circuit.yaml", derived_circuit)
        source_result = Path(final_best["result_path"])
        if source_result.is_file():
            shutil.copy2(source_result, derived / "experiment-result.json")
            candidate_root = source_result.parent
            evidence = derived / "evidence"
            evidence.mkdir(exist_ok=True)
            for name in ("vector-manifest.json", "run-status.txt", "job-message.log", "error-bundle.json"):
                item = candidate_root / name
                if item.is_file():
                    shutil.copy2(item, evidence / name)
            for directory in ("vectors", "charts"):
                item = candidate_root / directory
                if item.is_dir():
                    shutil.copytree(item, derived / directory, dirs_exist_ok=True)
            schematics = list((candidate_root / "build").glob("*.sxsch")) if (candidate_root / "build").is_dir() else []
            if schematics:
                shutil.copy2(schematics[0], derived / schematics[0].name)
        output["derived_output"] = str(derived)
    write_json(root / "optimization-result.json", output)
    return output
