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
    if value.get("schema_version") != CIRCUIT_SCHEMA_VERSION:
        raise ValidationError(
            "Unsupported circuit schema version",
            expected=CIRCUIT_SCHEMA_VERSION,
            actual=value.get("schema_version"),
        )
    catalog_lock = _require_mapping(value.get("catalog_lock"), "catalog_lock")
    _require_string(catalog_lock.get("path"), "catalog_lock.path")
    design = _require_mapping(value.get("design"), "design")
    _require_string(design.get("name"), "design.name")

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
    experiment = load_yaml(path)
    if experiment.get("schema_version") != EXPERIMENT_SCHEMA_VERSION:
        raise ValidationError(
            "Unsupported experiment schema version",
            expected=EXPERIMENT_SCHEMA_VERSION,
            actual=experiment.get("schema_version"),
        )
    _require_string(experiment.get("circuit"), "circuit")
    grid = _require_mapping(experiment.get("grid"), "grid")
    if not grid:
        raise ValidationError("grid must not be empty")
    return experiment
