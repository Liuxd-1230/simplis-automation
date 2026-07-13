"""Pure v2 circuit compilation: YAML graph to reviewed SIMetrix script and manifest."""

from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
from typing import Any, Mapping

from .catalog import find_property, get_device, is_fully_ideal, load_catalog, parse_symbol_libraries
from .errors import CatalogError, ValidationError
from .io import sha256_file, write_json
from .schema import BUILD_MANIFEST_SCHEMA_VERSION, load_circuit, resolve_parameters, validate_circuit
from .units import evaluate_expression, format_simplis


GROUND_NETS = {"0", "GND", "gnd"}


def _quote(value: Any) -> str:
    text = str(value).replace('"', '\\"')
    return f'"{text}"' if not text or any(character.isspace() for character in text) else text


def _resolve_catalog_path(circuit: Mapping[str, Any], circuit_path: Path, explicit: str | Path | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    lock = circuit["catalog_lock"]
    path = Path(str(lock["path"]))
    return (circuit_path.parent / path).resolve() if not path.is_absolute() else path


def _runtime_symbol_dir(catalog: Mapping[str, Any]) -> Path:
    runtime = catalog.get("runtime", {})
    configured = runtime.get("symbol_library_dir") or os.environ.get("SIMPLIS_SYMBOL_LIB_DIR")
    if not configured:
        raise CatalogError("Catalog does not identify a symbol_library_dir")
    return Path(str(configured)).resolve()


def _normalize_orientation(value: Any) -> str:
    text = str(value or "0")
    if text.startswith(("N", "M")):
        text = text[1:]
    if text not in {"0", "90", "180", "270"}:
        raise ValidationError("Unsupported orientation", orientation=value)
    return text


def _transform_pin(x: int, y: int, orientation: str) -> tuple[int, int]:
    angle = int(orientation)
    if angle == 0:
        return x, y
    if angle == 90:
        return -y, x
    if angle == 180:
        return -x, -y
    return y, -x


def _term_orientation(dx: int, dy: int) -> str:
    if abs(dx) >= abs(dy):
        return "0" if dx <= 0 else "180"
    return "90" if dy <= 0 else "270"


def _component_layout(circuit: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    layout = circuit.get("layout", {}) if isinstance(circuit.get("layout"), dict) else {}
    grid = int(layout.get("grid", 120))
    origin = layout.get("origin", [-720, -360])
    if not isinstance(origin, list) or len(origin) != 2:
        raise ValidationError("layout.origin must contain [x, y]")
    group_spacing = int(layout.get("group_spacing", 960))
    row_spacing = int(layout.get("row_spacing", 480))
    col_spacing = int(layout.get("col_spacing", 360))
    groups: list[str] = [str(item) for item in layout.get("groups", [])]
    for component in circuit["components"]:
        group = str(component.get("group", "default"))
        if group not in groups:
            groups.append(group)
    counts = {group: 0 for group in groups}
    placements: dict[str, dict[str, Any]] = {}
    for component in circuit["components"]:
        component_id = str(component["id"])
        group = str(component.get("group", "default"))
        index = counts[group]
        counts[group] += 1
        component_layout = component.get("layout", {})
        if not isinstance(component_layout, dict):
            raise ValidationError("component layout must be a mapping", component=component_id)
        group_index = groups.index(group)
        x = int(component_layout.get("x", int(origin[0]) + group_index * group_spacing + int(component_layout.get("col", 0)) * col_spacing))
        y = int(component_layout.get("y", int(origin[1]) + int(component_layout.get("row", index)) * row_spacing))
        placements[component_id] = {
            "x": int(round(x / grid) * grid),
            "y": int(round(y / grid) * grid),
            "orientation": _normalize_orientation(component_layout.get("orientation", "0")),
            "group": group,
        }
    return placements


def _resolve_value(raw: Any, definition: Mapping[str, Any], parameters: Mapping[str, Any]) -> str:
    if definition.get("raw") or definition.get("value_type") in {"string", "enum"}:
        if definition.get("enum") and raw not in definition["enum"]:
            raise ValidationError("Property is outside its allowed enum", property=definition.get("native"), value=raw)
        if isinstance(raw, str) and "${" in raw:
            # Raw strings may interpolate only complete parameters; code-like expressions stay impossible.
            import re

            def substitute(match: re.Match[str]) -> str:
                name = match.group(1)
                if name not in parameters:
                    raise ValidationError("Unknown parameter in raw property", parameter=name)
                return format_simplis(parameters[name])

            return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", substitute, raw)
        return str(raw)
    quantity = evaluate_expression(raw, parameters, definition.get("dimension", "dimensionless"))
    minimum = definition.get("min")
    maximum = definition.get("max")
    if minimum is not None and quantity.value < evaluate_expression(minimum, parameters, definition.get("dimension")).value:
        raise ValidationError("Property is below its catalog minimum", property=definition.get("native"), value=raw, minimum=minimum)
    if maximum is not None and quantity.value > evaluate_expression(maximum, parameters, definition.get("dimension")).value:
        raise ValidationError("Property is above its catalog maximum", property=definition.get("native"), value=raw, maximum=maximum)
    return format_simplis(quantity)


def _interpolate_raw(raw: Any, parameters: Mapping[str, Any]) -> str:
    """Allow complete declared-parameter references in catalog placement adapters."""

    if not isinstance(raw, str) or "${" not in raw:
        return str(raw)
    import re

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in parameters:
            raise ValidationError("Unknown parameter in placement property", parameter=name)
        return format_simplis(parameters[name])

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", substitute, raw)


def _resolve_component(
    component: Mapping[str, Any],
    catalog: Mapping[str, Any],
    installed: Mapping[str, Mapping[str, Any]],
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    component_id = str(component["id"])
    kind = str(component["kind"])
    opaque = kind == "opaque_module"
    if opaque:
        native = component.get("native", {})
        if not isinstance(native, dict) or not native.get("symbol"):
            raise ValidationError("Opaque module cannot be rendered without native.symbol", component=component_id)
        symbol_name = str(native["symbol"])
        device = {"kind": kind, "symbol": {"name": symbol_name}, "pins": [{"name": name, "domain": "analog"} for name in component["pins"]], "properties": {}, "ideality": "opaque"}
    else:
        device = get_device(dict(catalog), kind)
        symbol_name = str(device.get("placement_symbol") or device["symbol"]["name"])
    symbol = installed.get(symbol_name)
    if symbol is None:
        raise CatalogError("Catalog placement symbol is not installed", component=component_id, symbol=symbol_name)
    installed_pins = {pin["name"]: pin for pin in symbol["pins"]}
    expected_pins = {pin["name"]: pin for pin in device["pins"]}
    actual_pins = component["pins"]
    unknown = sorted(set(actual_pins) - set(expected_pins))
    missing = sorted(set(expected_pins) - set(actual_pins))
    if unknown or missing:
        raise ValidationError("Component pins disagree with catalog", component=component_id, unknown=unknown, missing=missing)
    absent = sorted(set(expected_pins) - set(installed_pins))
    if absent:
        raise CatalogError("Installed symbol pins disagree with catalog", component=component_id, symbol=symbol_name, missing=absent)
    properties: dict[str, str] = {"REF": str(component.get("ref", component_id))}
    placement_properties = device.get("placement_properties", {})
    if not isinstance(placement_properties, dict):
        raise CatalogError("placement_properties must be a mapping", kind=kind)
    properties.update({str(name): _interpolate_raw(value, parameters) for name, value in placement_properties.items()})
    definitions = device.get("properties", {})
    source_properties = component.get("properties", {})
    if not isinstance(source_properties, dict):
        raise ValidationError("Component properties must be a mapping", component=component_id)
    for public_name, definition in definitions.items():
        if "default" in definition:
            properties[str(definition.get("native", public_name))] = _resolve_value(definition["default"], definition, parameters)
    for property_name, raw in source_properties.items():
        match = find_property(device, str(property_name)) if not opaque else None
        if opaque:
            # Imported opaque modules are deliberately not claimed ideal.  Their
            # unchanged native attributes may be preserved for a boundary-only
            # round trip, but cannot be parameterized or type-checked.
            if isinstance(raw, str) and "${" in raw:
                raise ValidationError("Opaque module properties cannot be parameterized", component=component_id, property=property_name)
            properties[str(property_name)] = str(raw)
            continue
        if match is None:
            raise ValidationError("Component property is not catalog-approved", component=component_id, property=property_name)
        public_name, definition = match
        properties[str(definition.get("native", public_name))] = _resolve_value(raw, definition, parameters)
    for public_name, definition in definitions.items():
        if definition.get("required") and str(definition.get("native", public_name)) not in properties:
            raise ValidationError("Required catalog property is missing", component=component_id, property=public_name)
    pin_domains = {pin["name"]: str(pin.get("domain", "analog")) for pin in device["pins"]}
    return {
        "id": component_id,
        "ref": str(component.get("ref", component_id)),
        "kind": kind,
        "symbol": symbol_name,
        "symbol_record": symbol,
        "properties": properties,
        "pins": {str(pin): str(net) for pin, net in actual_pins.items()},
        "pin_domains": pin_domains,
        "opaque": opaque,
    }


def _validate_domains(components: list[Mapping[str, Any]]) -> None:
    net_domains: dict[str, set[str]] = {}
    for component in components:
        for pin, net in component["pins"].items():
            net_domains.setdefault(net, set()).add(component["pin_domains"].get(pin, "analog"))
    for net, domains in net_domains.items():
        if "analog" in domains and "digital" in domains:
            raise ValidationError(
                "Analog and digital pins share a net without a catalog boundary adapter",
                net=net,
                domains=sorted(domains),
            )


def _build_script(components: list[Mapping[str, Any]], placements: Mapping[str, Mapping[str, Any]], paths: Mapping[str, Path], design_name: str) -> str:
    lines = [f"NewSchem /newWindow /simulator SIMPLIS {_quote(design_name)}"]
    for component in components:
        placement = placements[component["id"]]
        lines.append("Unselect")
        lines.append(
            " ".join(
                [
                    "Inst",
                    "/select",
                    "/loc",
                    str(placement["x"]),
                    str(placement["y"]),
                    str(placement["orientation"]),
                    component["symbol"],
                ]
            )
        )
        for name, value in component["properties"].items():
            lines.append(f"Prop /hideNew {_quote(name)} {_quote(value)}")
        lines.append("Unselect")
    for component in components:
        placement = placements[component["id"]]
        for pin_name, net in component["pins"].items():
            pin = component["symbol_record"]["pins"]
            matching = next((item for item in pin if item["name"] == pin_name), None)
            if matching is None:
                raise CatalogError("Symbol pin vanished while rendering", component=component["id"], pin=pin_name)
            dx, dy = _transform_pin(int(matching["x"]), int(matching["y"]), str(placement["orientation"]))
            x = int(placement["x"]) + dx
            y = int(placement["y"]) + dy
            if net in GROUND_NETS:
                lines.append(f"Inst /loc {x} {y} 0 gnd")
            else:
                lines.append(f"Inst /loc {x} {y} {_term_orientation(dx, dy)} term VALUE {_quote(net)}")
    lines.extend([f'SaveAs /force "{paths["schematic"]}"', f'Netlist /simplis "{paths["netlist"]}"', "Quit", ""])
    return "\n".join(lines)


def compile_circuit(
    circuit_path: str | Path,
    out_dir: str | Path,
    parameter_overrides: Mapping[str, Any] | None = None,
    catalog_path: str | Path | None = None,
) -> dict[str, Any]:
    """Compile a validated v2 circuit without launching SIMetrix."""

    source_path = Path(circuit_path).resolve()
    circuit = load_circuit(source_path)
    catalog_source = _resolve_catalog_path(circuit, source_path, catalog_path)
    catalog = load_catalog(catalog_source)
    requested_fingerprint = circuit.get("catalog_lock", {}).get("fingerprint")
    if requested_fingerprint and requested_fingerprint != catalog["fingerprint"]:
        raise CatalogError("Circuit catalog fingerprint is stale", expected=requested_fingerprint, actual=catalog["fingerprint"])
    parameters = resolve_parameters(circuit, parameter_overrides)
    installed = parse_symbol_libraries(_runtime_symbol_dir(catalog))
    components = [_resolve_component(component, catalog, installed, parameters) for component in circuit["components"]]
    _validate_domains(components)
    placements = _component_layout(circuit)
    root = Path(out_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    design_name = str(circuit["design"]["name"])
    paths = {
        "script": root / "create_and_netlist.sxscr",
        "schematic": root / f"{design_name}.sxsch",
        "netlist": root / f"{design_name}.net",
        "manifest": root / "build-manifest.json",
    }
    script = _build_script(components, placements, paths, design_name)
    paths["script"].write_text(script, encoding="utf-8")
    opaque_modules = [component["id"] for component in components if component["opaque"]]
    manifest = {
        "schema_version": BUILD_MANIFEST_SCHEMA_VERSION,
        "manifest_path": str(paths["manifest"]),
        "build_dir": str(root),
        "source_circuit": str(source_path),
        "catalog": {"path": str(catalog_source), "fingerprint": catalog["fingerprint"]},
        "artifacts": {key: str(value) for key, value in paths.items() if key != "manifest"},
        "expected": {
            "components": [
                {
                    "id": component["id"],
                    "ref": component["ref"],
                    "kind": component["kind"],
                    "symbol": component["symbol"],
                    "pins": component["pins"],
                    "properties": component["properties"],
                    "layout": placements[component["id"]],
                }
                for component in components
            ],
            "nets": {net: details["endpoints"] for net, details in circuit["nets"].items()},
        },
        "parameters": {
            name: {"value": value.value, "dimension": value.dimension, "simplis": format_simplis(value)}
            for name, value in parameters.items()
        },
        "classification": {
            "static_valid": True,
            "fully_ideal": not opaque_modules and is_fully_ideal(catalog, [component["kind"] for component in components]),
            "opaque_modules": opaque_modules,
        },
        "script_sha256": sha256_file(paths["script"]),
    }
    write_json(paths["manifest"], manifest)
    return manifest
