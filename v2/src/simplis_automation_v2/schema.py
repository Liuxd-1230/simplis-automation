"""Strict v2 YAML schema validation and parameter resolution."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from typing import Any, Mapping

from .errors import ValidationError
from .io import load_yaml
from .units import Quantity, evaluate_expression, parse_dimension


CIRCUIT_SCHEMA_VERSION = "simplis-automation/v2"
EXPERIMENT_SCHEMA_VERSION = "simplis-automation/v2/experiment"
BUILD_MANIFEST_SCHEMA_VERSION = "simplis-automation/v2/build-manifest"
CIRCUIT_SCHEMA_REVISION = 3

_PARAMETER_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be a mapping", actual=type(value).__name__)
    return value


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be a non-empty string")
    return value


def _component_endpoints(component: Mapping[str, Any]) -> set[str]:
    component_id = str(component["id"])
    pins = component.get("pins", {})
    return {f"{component_id}.{pin}" for pin in pins}


def validate_circuit(circuit: Mapping[str, Any]) -> dict[str, Any]:
    """Validate structural v2 invariants without consulting a device catalog."""

    value = deepcopy(dict(circuit))
    if "blocks" in value:
        from .blocks import expand_blocks

        value, block_manifest = expand_blocks(value)
        value["_block_manifest"] = block_manifest
    if value.get("schema_version") != CIRCUIT_SCHEMA_VERSION:
        raise ValidationError(
            "Unsupported circuit schema version",
            expected=CIRCUIT_SCHEMA_VERSION,
            actual=value.get("schema_version"),
        )
    revision = int(value.get("schema_revision", CIRCUIT_SCHEMA_REVISION))
    if revision != CIRCUIT_SCHEMA_REVISION:
        raise ValidationError("Unsupported circuit schema revision", expected=CIRCUIT_SCHEMA_REVISION, actual=revision)
    value["schema_revision"] = revision
    catalog_lock = _require_mapping(value.get("catalog_lock"), "catalog_lock")
    _require_string(catalog_lock.get("path"), "catalog_lock.path")
    design = _require_mapping(value.get("design"), "design")
    _require_string(design.get("name"), "design.name")

    layout = value.setdefault("layout", {})
    layout = _require_mapping(layout, "layout")
    mode = str(layout.setdefault("mode", "hybrid")).casefold()
    if mode not in {"hybrid", "auto", "manual"}:
        raise ValidationError("layout.mode must be hybrid, auto, or manual", mode=mode)
    layout["mode"] = mode
    clearance = layout.setdefault("clearance", {"horizontal": 480, "vertical": 360})
    clearance = _require_mapping(clearance, "layout.clearance")
    for axis, default in (("horizontal", 480), ("vertical", 360)):
        raw = clearance.setdefault(axis, default)
        if not isinstance(raw, int) or raw < 0:
            raise ValidationError("layout clearance must be a non-negative integer", axis=axis, value=raw)
    for name, default, minimum in (("grid", 120, 1), ("label_padding", 120, 0), ("band_spacing", 1560, 1)):
        raw = layout.setdefault(name, default)
        if not isinstance(raw, int) or raw < minimum:
            raise ValidationError(f"layout.{name} must be an integer >= {minimum}", value=raw)
    origin = layout.setdefault("origin", [-720, -360])
    if not isinstance(origin, list) or len(origin) != 2 or any(not isinstance(item, int) for item in origin):
        raise ValidationError("layout.origin must contain two integers")

    parameters = value.setdefault("parameters", {})
    _require_mapping(parameters, "parameters")
    for name, definition in parameters.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(name)):
            raise ValidationError("Invalid parameter name", parameter=name)
        definition = _require_mapping(definition, f"parameters.{name}")
        if "dimension" not in definition or "default" not in definition:
            raise ValidationError("Each parameter needs dimension and default", parameter=name)
        parse_dimension(str(definition["dimension"]))
        for bound in ("min", "max"):
            if bound in definition and definition[bound] is None:
                raise ValidationError("Parameter bound cannot be null", parameter=name, bound=bound)

    components = value.get("components")
    if not isinstance(components, list) or not components:
        raise ValidationError("components must be a non-empty list")
    identifiers: set[str] = set()
    refs: set[str] = set()
    derived_endpoints: dict[str, list[str]] = {}
    for index, component in enumerate(components):
        component = _require_mapping(component, f"components[{index}]")
        component_id = _require_string(component.get("id"), f"components[{index}].id")
        if component_id in identifiers:
            raise ValidationError("Duplicate component id", component=component_id)
        identifiers.add(component_id)
        ref = _require_string(component.get("ref", component_id), f"components[{index}].ref")
        if ref in refs:
            raise ValidationError("Duplicate schematic reference", ref=ref)
        refs.add(ref)
        _require_string(component.get("kind"), f"components[{index}].kind")
        properties = component.setdefault("properties", {})
        _require_mapping(properties, f"components[{index}].properties")
        pins = component.get("pins")
        if not isinstance(pins, dict) or not pins:
            raise ValidationError("Each component needs a non-empty pins mapping", component=component_id)
        unconnected_pins = component.get("unconnected_pins", [])
        if not isinstance(unconnected_pins, list) or any(not isinstance(pin, str) or not pin.strip() for pin in unconnected_pins):
            raise ValidationError("unconnected_pins must be a list of non-empty pin names", component=component_id)
        if len(set(unconnected_pins)) != len(unconnected_pins):
            raise ValidationError("unconnected_pins cannot contain duplicates", component=component_id)
        overlap = sorted(set(pins) & set(unconnected_pins))
        if overlap:
            raise ValidationError("A component pin cannot be both connected and unconnected", component=component_id, pins=overlap)
        for pin, net in pins.items():
            _require_string(pin, f"components[{index}].pins key")
            net_name = _require_string(net, f"components[{index}].pins.{pin}")
            derived_endpoints.setdefault(net_name, []).append(f"{component_id}.{pin}")
        if "layout" in component:
            layout = _require_mapping(component["layout"], f"components[{index}].layout")
            if "x" in layout and not isinstance(layout["x"], int):
                raise ValidationError("layout.x must be an integer", component=component_id)
            if "y" in layout and not isinstance(layout["y"], int):
                raise ValidationError("layout.y must be an integer", component=component_id)
            if "orientation" in layout and str(layout["orientation"]) not in {"0", "90", "180", "270", "N0", "N90", "N180", "N270", "M0", "M90", "M180", "M270"}:
                raise ValidationError("Unsupported layout orientation", component=component_id)

    declared_nets = value.get("nets")
    if declared_nets is None:
        value["nets"] = {net: {"endpoints": endpoints} for net, endpoints in sorted(derived_endpoints.items())}
    else:
        _require_mapping(declared_nets, "nets")
        for net_name, details in declared_nets.items():
            _require_string(net_name, "nets key")
            if isinstance(details, list):
                endpoints = details
            else:
                details = _require_mapping(details, f"nets.{net_name}")
                endpoints = details.get("endpoints")
            if not isinstance(endpoints, list):
                raise ValidationError("net endpoints must be a list", net=net_name)
            expected = sorted(derived_endpoints.get(str(net_name), []))
            actual = sorted(str(endpoint) for endpoint in endpoints)
            if expected != actual:
                raise ValidationError("Declared net endpoints disagree with component pins", net=net_name, expected=expected, actual=actual)
        missing = sorted(set(derived_endpoints) - set(str(key) for key in declared_nets))
        if missing:
            raise ValidationError("Every component net must be declared", missing=missing)

    for net, endpoints in derived_endpoints.items():
        if net != "0" and len(endpoints) < 2:
            raise ValidationError("Non-ground net must connect at least two component pins", net=net, endpoints=endpoints)
    return value


def load_circuit(path: str | Path) -> dict[str, Any]:
    circuit = validate_circuit(load_yaml(path))
    circuit["_source_path"] = str(Path(path).resolve())
    return circuit


def _references(value: Any) -> set[str]:
    if not isinstance(value, str):
        return set()
    return set(_PARAMETER_REFERENCE.findall(value))


def resolve_parameters(circuit: Mapping[str, Any], overrides: Mapping[str, Any] | None = None) -> dict[str, Quantity]:
    """Resolve parameter defaults/overrides in dependency order and enforce bounds."""

    definitions = _require_mapping(circuit.get("parameters", {}), "parameters")
    overrides = dict(overrides or {})
    unknown = sorted(set(overrides) - set(definitions))
    if unknown:
        raise ValidationError("Parameter override is not declared", parameters=unknown)
    unresolved = dict(definitions)
    resolved: dict[str, Quantity] = {}
    while unresolved:
        progress = False
        for name, definition in list(unresolved.items()):
            dimension = str(definition["dimension"])
            raw = overrides.get(name, definition["default"])
            refs = _references(raw)
            if not refs.issubset(resolved):
                continue
            quantity = evaluate_expression(raw, resolved, dimension)
            minimum = definition.get("min")
            maximum = definition.get("max")
            if minimum is not None and quantity.value < evaluate_expression(minimum, resolved, dimension).value:
                raise ValidationError("Parameter is below its minimum", parameter=name, value=quantity.value, minimum=minimum)
            if maximum is not None and quantity.value > evaluate_expression(maximum, resolved, dimension).value:
                raise ValidationError("Parameter is above its maximum", parameter=name, value=quantity.value, maximum=maximum)
            resolved[str(name)] = quantity
            del unresolved[name]
            progress = True
        if not progress:
            raise ValidationError("Parameter definitions contain a cycle or unknown reference", parameters=sorted(unresolved))
    return resolved


def load_experiment(path: str | Path) -> dict[str, Any]:
    experiment = validate_experiment(load_yaml(path))
    experiment["_source_path"] = str(Path(path).resolve())
    return experiment


def validate_experiment(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate sweep, run, corner, and optimization experiment documents."""

    experiment = deepcopy(dict(raw))
    if experiment.get("schema_version") != EXPERIMENT_SCHEMA_VERSION:
        raise ValidationError(
            "Unsupported experiment schema version",
            expected=EXPERIMENT_SCHEMA_VERSION,
            actual=experiment.get("schema_version"),
        )
    _require_string(experiment.get("circuit"), "circuit")
    experiment.setdefault("name", Path(str(experiment["circuit"])).stem)
    analyses = experiment.setdefault("analyses", [])
    if analyses and not isinstance(analyses, list):
        raise ValidationError("analyses must be a list")
    allowed_analyses = {"pop_ac", "startup", "load_step", "corner", "sweep", "optimize", "catalog_proof"}
    for index, analysis in enumerate(analyses):
        analysis = _require_mapping(analysis, f"analyses[{index}]")
        kind = _require_string(analysis.get("type"), f"analyses[{index}].type")
        if kind not in allowed_analyses:
            raise ValidationError("Unsupported analysis type", analysis=kind, supported=sorted(allowed_analyses))
        if analysis.get("parameters") is not None:
            _require_mapping(analysis.get("parameters"), f"analyses[{index}].parameters")
        response = analysis.get("response")
        if response is not None:
            response = _require_mapping(response, f"analyses[{index}].response")
            numerator = _require_string(response.get("numerator"), f"analyses[{index}].response.numerator")
            denominator = _require_string(response.get("denominator"), f"analyses[{index}].response.denominator")
            vectors = _require_mapping(analysis.get("vectors"), f"analyses[{index}].vectors")
            missing_roles = [role for role in (numerator, denominator) if role not in vectors]
            if missing_roles:
                raise ValidationError("AC response roles must name declared vectors", analysis=index, missing=missing_roles)
            sign = response.setdefault("sign", 1)
            if sign not in {-1, 1, -1.0, 1.0}:
                raise ValidationError("AC response sign must be +1 or -1", analysis=index, sign=sign)
            response["sign"] = int(sign)
    grid = experiment.get("grid")
    if grid is not None:
        grid = _require_mapping(grid, "grid")
        if not grid:
            raise ValidationError("grid must not be empty")
    optimize = experiment.get("optimize")
    if optimize is not None:
        optimize = _require_mapping(optimize, "optimize")
        budget = int(optimize.get("max_evaluations", 40))
        if budget < 1:
            raise ValidationError("optimize.max_evaluations must be positive")
        optimize["max_evaluations"] = budget
        parameters = _require_mapping(optimize.get("parameters"), "optimize.parameters")
        if not parameters:
            raise ValidationError("optimize.parameters must not be empty")
        optimize.setdefault("backend", "coordinate")
        experiment["optimize"] = optimize
    if not analyses and grid is None and optimize is None:
        raise ValidationError("experiment needs analyses, grid, or optimize")
    execution = experiment.setdefault("execution", {})
    _require_mapping(execution, "execution")
    execution.setdefault("startup_timeout_s", 15)
    execution.setdefault("stall_timeout_s", 60)
    execution.setdefault("hard_timeout_s", 240)
    execution.setdefault("poll_interval_s", 2)
    warning_allowlist = execution.setdefault("warning_allowlist", [])
    if not isinstance(warning_allowlist, list):
        raise ValidationError("execution.warning_allowlist must be a list")
    return experiment


def migrate_old_v2_document(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Migrate draft-era v2 circuit/experiment keys without changing sources."""

    value = deepcopy(dict(raw))
    changes: list[str] = []
    schema = value.get("schema_version")
    if schema in {"simplis-automation/v2/circuit", CIRCUIT_SCHEMA_VERSION}:
        if schema != CIRCUIT_SCHEMA_VERSION:
            value["schema_version"] = CIRCUIT_SCHEMA_VERSION
            changes.append("schema_version")
        if "catalog" in value and "catalog_lock" not in value:
            catalog = value.pop("catalog")
            value["catalog_lock"] = catalog if isinstance(catalog, dict) else {"path": catalog}
            changes.append("catalog->catalog_lock")
        if isinstance(value.get("design"), str):
            value["design"] = {"name": value["design"]}
            changes.append("design string->mapping")
        if int(value.get("schema_revision", 1)) != CIRCUIT_SCHEMA_REVISION:
            value["schema_revision"] = CIRCUIT_SCHEMA_REVISION
            changes.append(f"schema_revision->{CIRCUIT_SCHEMA_REVISION}")
        parameters = value.get("parameters")
        if isinstance(parameters, dict):
            old_ripple = parameters.pop("ripple_inject_r", None)
            old_sense = parameters.pop("ripple_sense_r", None)
            if "ripple_r" not in parameters and (old_ripple is not None or old_sense is not None):
                parameters["ripple_r"] = old_ripple if old_ripple is not None else old_sense
                changes.append("ripple_inject_r/ripple_sense_r->ripple_r")
            if (old_ripple is not None or old_sense is not None) and "ripple_c" not in parameters:
                parameters["ripple_c"] = {"default": "220pF", "dimension": "capacitance", "min": "22pF", "max": "2.2nF"}
                changes.append("add ripple_c=220pF")
        for block in value.get("blocks", []) if isinstance(value.get("blocks"), list) else []:
            if not isinstance(block, dict) or block.get("type") != "synthetic_ripple":
                continue
            block_parameters = block.setdefault("parameters", {})
            if not isinstance(block_parameters, dict):
                continue
            old_ripple = block_parameters.pop("rinject", None)
            old_sense = block_parameters.pop("rsense", None)
            if "ripple_r" not in block_parameters and (old_ripple is not None or old_sense is not None):
                selected = old_ripple if old_ripple is not None else old_sense
                block_parameters["ripple_r"] = "${ripple_r}" if selected in {"${ripple_inject_r}", "${ripple_sense_r}"} else selected
            block_parameters.setdefault("ripple_c", "${ripple_c}" if isinstance(parameters, dict) and "ripple_c" in parameters else "220pF")
    elif schema in {None, EXPERIMENT_SCHEMA_VERSION} and ("circuit" in value or "source_circuit" in value):
        value["schema_version"] = EXPERIMENT_SCHEMA_VERSION
        if "source_circuit" in value and "circuit" not in value:
            value["circuit"] = value.pop("source_circuit")
            changes.append("source_circuit->circuit")
        if "parameters" in value and "grid" not in value:
            value["grid"] = value.pop("parameters")
            changes.append("parameters->grid")
        optimize = value.get("optimize")
        optimize_parameters = optimize.get("parameters") if isinstance(optimize, dict) else None
        if isinstance(optimize_parameters, dict):
            old_ripple = optimize_parameters.pop("ripple_inject_r", None)
            old_sense = optimize_parameters.pop("ripple_sense_r", None)
            if "ripple_r" not in optimize_parameters and (old_ripple is not None or old_sense is not None):
                optimize_parameters["ripple_r"] = old_ripple if old_ripple is not None else old_sense
                changes.append("optimize ripple resistors->ripple_r")
            if (old_ripple is not None or old_sense is not None) and "ripple_c" not in optimize_parameters:
                optimize_parameters["ripple_c"] = {"min": 22e-12, "max": 2.2e-9, "initial": 220e-12, "scale": "log"}
                changes.append("optimize add ripple_c")
    else:
        raise ValidationError("Document is not a recognized old v2 circuit or experiment", schema_version=schema)
    value.setdefault("metadata", {})["migration"] = {"from": schema, "changes": changes}
    return value
