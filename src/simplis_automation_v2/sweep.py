"""Deterministic compile-and-netlist parameter sweeps.

The sweep layer is intentionally a coordinator, not an optimizer.  It expands
an explicit parameter grid in a stable order, builds every point in an isolated
directory, then delegates real netlist verification to :mod:`verifier`.
"""

from __future__ import annotations

import itertools
import math
import re
from pathlib import Path
from typing import Any, Callable, Mapping

from .io import load_yaml, write_json


EXPERIMENT_SCHEMA = "simplis-automation/v2/experiment"
_QUANTITY_RE = re.compile(
    r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([A-Za-zµΩ]*)\s*$"
)
_PREFIXES: tuple[tuple[str, float], ...] = (
    ("meg", 1e6),
    ("MEG", 1e6),
    ("T", 1e12),
    ("G", 1e9),
    ("M", 1e6),
    ("k", 1e3),
    ("K", 1e3),
    ("m", 1e-3),
    ("u", 1e-6),
    ("µ", 1e-6),
    ("n", 1e-9),
    ("p", 1e-12),
    ("f", 1e-15),
)


Compiler = Callable[..., Mapping[str, Any] | dict[str, Any]]
Verifier = Callable[..., Mapping[str, Any] | dict[str, Any]]


class _SweepInputError(ValueError):
    pass


def _error(code: str, message: str, **details: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"code": code, "message": message}
    if details:
        item["details"] = details
    return item


def _quantity(value: Any) -> tuple[float, str, float, bool]:
    """Return SI value, base unit, display scale, and whether input was text."""

    if isinstance(value, bool):
        raise _SweepInputError("Boolean values are not valid sweep bounds")
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise _SweepInputError("Sweep bounds must be finite")
        return float(value), "", 1.0, False
    match = _QUANTITY_RE.match(str(value))
    if not match:
        raise _SweepInputError(f"Unsupported numeric quantity {value!r}")
    numeric = float(match.group(1))
    suffix = match.group(2)
    scale = 1.0
    base_unit = suffix
    for prefix, candidate_scale in _PREFIXES:
        if suffix.startswith(prefix):
            scale = candidate_scale
            base_unit = suffix[len(prefix) :]
            break
    return numeric * scale, base_unit, scale, True


def _format_quantity(value_si: float, *, base_unit: str, display_scale: float, textual: bool, display_suffix: str) -> Any:
    display = value_si / display_scale
    if abs(display) < 1e-15:
        display = 0.0
    text = format(display, ".12g")
    return f"{text}{display_suffix}" if textual else float(text)


def _range_values(name: str, mode: str, specification: Mapping[str, Any]) -> list[Any]:
    start_raw = specification.get("start")
    stop_raw = specification.get("stop")
    count_raw = specification.get("count", specification.get("points", specification.get("steps")))
    if start_raw is None or stop_raw is None or count_raw is None:
        raise _SweepInputError(f"Grid parameter {name!r} needs start, stop, and count")
    try:
        count = int(count_raw)
    except (TypeError, ValueError) as exc:
        raise _SweepInputError(f"Grid parameter {name!r} count must be an integer") from exc
    if count < 1:
        raise _SweepInputError(f"Grid parameter {name!r} count must be at least 1")
    start, start_unit, display_scale, start_textual = _quantity(start_raw)
    stop, stop_unit, _stop_scale, stop_textual = _quantity(stop_raw)
    if start_unit.lower() != stop_unit.lower():
        raise _SweepInputError(
            f"Grid parameter {name!r} range endpoints have incompatible units: {start_unit or 'dimensionless'} vs {stop_unit or 'dimensionless'}"
        )
    if start_textual != stop_textual:
        raise _SweepInputError(f"Grid parameter {name!r} must use either numeric or textual bounds consistently")
    display_suffix = str(start_raw).strip()
    quantity_match = _QUANTITY_RE.match(display_suffix)
    display_suffix = quantity_match.group(2) if quantity_match else ""
    if mode == "linear":
        values_si = [start] if count == 1 else [start + (stop - start) * index / (count - 1) for index in range(count)]
    elif mode == "log":
        if start <= 0 or stop <= 0:
            raise _SweepInputError(f"Log grid parameter {name!r} requires positive start and stop values")
        values_si = [start] if count == 1 else [start * (stop / start) ** (index / (count - 1)) for index in range(count)]
    else:  # pragma: no cover - guarded by caller
        raise _SweepInputError(f"Unsupported grid mode {mode!r}")
    return [
        _format_quantity(
            item,
            base_unit=start_unit,
            display_scale=display_scale,
            textual=start_textual,
            display_suffix=display_suffix,
        )
        for item in values_si
    ]


def _parameter_values(name: str, raw: Any) -> list[Any]:
    if isinstance(raw, list):
        if not raw:
            raise _SweepInputError(f"Grid parameter {name!r} has an empty explicit value list")
        return list(raw)
    if not isinstance(raw, Mapping):
        raise _SweepInputError(f"Grid parameter {name!r} must be a list or object")
    explicit = raw.get("values")
    if explicit is not None:
        if not isinstance(explicit, list) or not explicit:
            raise _SweepInputError(f"Grid parameter {name!r} values must be a non-empty list")
        return list(explicit)
    for mode in ("linear", "log"):
        nested = raw.get(mode)
        if nested is not None:
            if not isinstance(nested, Mapping):
                raise _SweepInputError(f"Grid parameter {name!r} {mode} range must be an object")
            return _range_values(name, mode, nested)
    mode = str(raw.get("mode", raw.get("spacing", ""))).lower()
    if mode in {"linear", "log"}:
        return _range_values(name, mode, raw)
    raise _SweepInputError(f"Grid parameter {name!r} must define values, linear, or log")


def enumerate_cases(experiment: Mapping[str, Any], *, max_cases: int = 128, allow_large: bool = False) -> list[dict[str, Any]]:
    """Expand an experiment to stable parameter dictionaries without compiling."""

    raw_grid = experiment.get("grid", experiment.get("parameters", experiment.get("sweep")))
    if raw_grid is None:
        raw_grid = {}
    if not isinstance(raw_grid, Mapping):
        raise _SweepInputError("Experiment grid must be an object")
    names = sorted(str(name) for name in raw_grid)
    value_lists = [_parameter_values(name, raw_grid[name]) for name in names]
    total = math.prod(len(values) for values in value_lists) if value_lists else 1
    if total > max_cases and not allow_large:
        raise _SweepInputError(f"Sweep expands to {total} cases, exceeding max_cases={max_cases}; set allow_large=True to continue")
    combinations = itertools.product(*value_lists) if value_lists else [()]
    return [{name: value for name, value in zip(names, values)} for values in combinations]


def _default_compiler(circuit_path: Path, case_dir: Path, parameters: Mapping[str, Any], catalog_path: Any) -> Mapping[str, Any]:
    # Delayed import avoids a compiler -> sweep -> compiler import cycle.
    from .compiler import compile_circuit

    return compile_circuit(circuit_path, case_dir, parameter_overrides=dict(parameters), catalog_path=catalog_path)


def _default_verifier(manifest_path: Path, runtime: Any) -> Mapping[str, Any]:
    # Delayed import keeps standalone grid expansion usable without a runtime.
    from .verifier import verify_manifest

    return verify_manifest(manifest_path, runtime=runtime)


def _invoke_compiler(
    compiler: Compiler | None,
    circuit_path: Path,
    case_dir: Path,
    parameters: Mapping[str, Any],
    catalog_path: Any,
) -> Mapping[str, Any]:
    if compiler is None:
        return _default_compiler(circuit_path, case_dir, parameters, catalog_path)
    try:
        return compiler(circuit_path, case_dir, parameter_overrides=dict(parameters), catalog_path=catalog_path)
    except TypeError:
        # External test doubles often use a compact three-argument signature.
        return compiler(circuit_path, case_dir, dict(parameters))


def _invoke_verifier(verifier: Verifier | None, manifest_path: Path, runtime: Any) -> Mapping[str, Any]:
    if verifier is None:
        return _default_verifier(manifest_path, runtime)
    try:
        return verifier(manifest_path, runtime=runtime)
    except TypeError:
        return verifier(manifest_path)


def _save_result(out_dir: Path, result: Mapping[str, Any]) -> None:
    try:
        write_json(out_dir / "sweep-result.json", dict(result))
    except OSError:
        pass


def run_sweep(
    experiment_path: str | Path,
    out_dir: str | Path,
    runtime: Mapping[str, Any] | str | Path | None = None,
    max_cases: int = 128,
    allow_large: bool = False,
    *,
    compiler: Compiler | None = None,
    verifier: Verifier | None = None,
) -> dict[str, Any]:
    """Compile and netlist-verify every deterministic point of an experiment.

    ``compiler`` and ``verifier`` are optional injection seams for tests.  They
    are intentionally keyword-only so the public command contract remains the
    compact ``run_sweep(experiment_path, out_dir, runtime, max_cases,
    allow_large)`` shape.
    """

    source = Path(experiment_path).expanduser().resolve()
    destination = Path(out_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "schema_version": 1,
        "ok": False,
        "status": "failed",
        "experiment_path": str(source),
        "out_dir": str(destination),
        "max_cases": max_cases,
        "allow_large": allow_large,
        "parameter_order": [],
        "case_count": 0,
        "cases": [],
        "errors": [],
    }
    if max_cases < 1:
        result["errors"].append(_error("invalid_max_cases", "max_cases must be at least 1", max_cases=max_cases))
        _save_result(destination, result)
        return result
    try:
        experiment = load_yaml(source)
    except Exception as exc:
        result["errors"].append(_error("experiment_invalid", "Cannot parse experiment YAML", reason=str(exc)))
        _save_result(destination, result)
        return result
    schema = experiment.get("schema_version")
    if schema not in {None, EXPERIMENT_SCHEMA}:
        result["errors"].append(_error("experiment_schema_unsupported", "Unsupported experiment schema", schema_version=schema))
        _save_result(destination, result)
        return result
    circuit_value = experiment.get("circuit", experiment.get("source_circuit"))
    if circuit_value is None:
        result["errors"].append(_error("circuit_missing", "Experiment must name a source circuit"))
        _save_result(destination, result)
        return result
    circuit_path = Path(str(circuit_value)).expanduser()
    if not circuit_path.is_absolute():
        circuit_path = source.parent / circuit_path
    circuit_path = circuit_path.resolve()
    if not circuit_path.is_file():
        result["errors"].append(_error("circuit_missing", "Experiment source circuit does not exist", path=str(circuit_path)))
        _save_result(destination, result)
        return result
    declared_limit = experiment.get("max_points")
    effective_max_cases = max_cases
    if declared_limit is not None:
        try:
            declared_limit = int(declared_limit)
        except (TypeError, ValueError):
            result["errors"].append(_error("grid_invalid", "experiment.max_points must be an integer", max_points=declared_limit))
            _save_result(destination, result)
            return result
        if declared_limit < 1:
            result["errors"].append(_error("grid_invalid", "experiment.max_points must be at least 1", max_points=declared_limit))
            _save_result(destination, result)
            return result
        # A document can make a sweep stricter, but it cannot silently bypass
        # the caller's process-wide safety limit.
        effective_max_cases = min(max_cases, declared_limit)
    try:
        cases = enumerate_cases(experiment, max_cases=effective_max_cases, allow_large=allow_large)
    except _SweepInputError as exc:
        result["errors"].append(_error("grid_invalid", str(exc)))
        _save_result(destination, result)
        return result
    result["parameter_order"] = sorted(next(iter(cases), {}).keys()) if cases else []
    result["case_count"] = len(cases)
    catalog_path: Any = experiment.get("catalog_path", experiment.get("catalog"))
    if isinstance(catalog_path, Mapping):
        catalog_path = catalog_path.get("path")
    if catalog_path is not None:
        catalog_candidate = Path(str(catalog_path)).expanduser()
        catalog_path = (source.parent / catalog_candidate).resolve() if not catalog_candidate.is_absolute() else catalog_candidate.resolve()
    width = max(3, len(str(max(len(cases) - 1, 0))))
    for index, parameters in enumerate(cases):
        case_id = f"case-{index:0{width}d}"
        case_dir = destination / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        case_record: dict[str, Any] = {
            "id": case_id,
            "index": index,
            "parameters": parameters,
            "out_dir": str(case_dir),
            "status": "failed",
            "compile": None,
            "verification": None,
        }
        try:
            compiled = _invoke_compiler(compiler, circuit_path, case_dir, parameters, catalog_path)
            if not isinstance(compiled, Mapping):
                raise TypeError("Compiler returned a non-object result")
            case_record["compile"] = dict(compiled)
            if compiled.get("ok") is False:
                case_record["error"] = _error("compile_failed", "Compiler reported failure", details=dict(compiled))
                result["cases"].append(case_record)
                continue
            manifest_value = compiled.get("manifest_path") or compiled.get("build_manifest")
            manifest_path = Path(str(manifest_value)) if manifest_value else case_dir / "build-manifest.json"
            if not manifest_path.is_absolute():
                manifest_path = case_dir / manifest_path
            manifest_path = manifest_path.resolve()
            if not manifest_path.is_file():
                case_record["error"] = _error("manifest_missing", "Compiler did not write a build manifest", path=str(manifest_path))
                result["cases"].append(case_record)
                continue
            verified = _invoke_verifier(verifier, manifest_path, runtime)
            if not isinstance(verified, Mapping):
                raise TypeError("Verifier returned a non-object result")
            case_record["manifest_path"] = str(manifest_path)
            case_record["verification"] = dict(verified)
            case_record["status"] = "passed" if verified.get("ok") is True else "failed"
        except Exception as exc:  # Preserve later sweep points and their evidence.
            case_record["error"] = _error("case_exception", "Sweep case raised an exception", reason=repr(exc))
        result["cases"].append(case_record)
    failures = [case for case in result["cases"] if case.get("status") != "passed"]
    result["ok"] = not failures
    result["status"] = "passed" if result["ok"] else "failed"
    _save_result(destination, result)
    return result
