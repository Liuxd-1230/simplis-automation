"""Import text SIMetrix schematics into the v2 circuit-source shape.

The importer intentionally does not try to reverse engineer a SIMPLIS netlist.
It reads the editable text ``.sxsch`` syntax, preserves the GUI facts that can
be recovered safely, and writes a *draft* circuit source.  A later compiler is
responsible for proving the draft static-valid and a verifier is responsible for
proving that it netlists.

Only text files are accepted.  SIMetrix also stores binary schematics and
components; silently decoding those with replacement characters would make an
invented circuit graph look trustworthy, so they are rejected explicitly.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Iterable

from .errors import ImportBlockedError, ValidationError
from .io import canonical_json, load_yaml, sha256_file, sha256_text, write_yaml


_ATTRIBUTES_RE = re.compile(r"^\s*Attributes\s+(?P<attrs>.*)$")
_PROPERTY_RE = re.compile(
    r'^\s*Property\s+name="(?P<name>(?:\\.|[^"\\])*)"\s+'
    r'value="(?P<value>(?:\\.|[^"\\])*)"(?:\s|$)'
)
_WIRE_RE = re.compile(r"^\s*Wire\s+(?P<attrs>.*)$")
_KEY_VALUE_RE = re.compile(
    r'(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)='
    r'(?:(?:"(?P<quoted>(?:\\.|[^"\\])*)")|(?P<bare>[^\s]+))'
)
_PIN_REFERENCE_RE = re.compile(
    r"(?:[+\-~!]*:)?(?P<ref>[A-Za-z0-9_.:$\-]+)#(?P<pin>[A-Za-z0-9_.:$\-]+)"
)
_PARAMETER_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_COORDINATE_KEYS = {"x", "y", "x1", "y1", "x2", "y2"}
_STRUCTURAL_PROPERTIES = {"ref"}
_OPAQUE_SUFFIXES = {".sxcmp", ".sub", ".lib", ".mod", ".dll"}
_ANALYSIS_DIRECTIVE_RE = re.compile(r"^\s*\.(AC|POP|TRAN|OPTIONS|VAR)\b(.*)$", re.IGNORECASE)


def _decode_quoted(value: str) -> str:
    """Decode the two escape sequences emitted by SIMetrix text schematics."""

    return value.replace(r'\"', '"').replace(r"\\", "\\")


def _parse_attributes(text: str) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    for match in _KEY_VALUE_RE.finditer(text):
        raw = match.group("quoted")
        value = _decode_quoted(raw) if raw is not None else match.group("bare")
        key = match.group("key")
        if key in _COORDINATE_KEYS:
            try:
                attrs[key] = int(value)
                continue
            except ValueError:
                pass
        attrs[key] = value
    return attrs


def _read_text(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValidationError("Cannot read schematic", path=str(path), reason=str(exc)) from exc

    # UTF-16 text contains NUL bytes, but a BOM makes it unambiguous.  We keep
    # this narrow so arbitrary binaries cannot masquerade as a schematic.
    try:
        if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
            text = raw.decode("utf-16")
        elif raw.startswith(b"\xef\xbb\xbf"):
            text = raw.decode("utf-8-sig")
        else:
            if b"\x00" in raw:
                raise UnicodeDecodeError("utf-8", raw, raw.index(b"\x00"), raw.index(b"\x00") + 1, "NUL byte")
            text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ImportBlockedError(
            "Schematic is binary or not UTF text; save/export it as a text .sxsch first",
            path=str(path),
            reason=str(exc),
        ) from exc

    # C0 control characters other than standard whitespace are a reliable
    # binary signal even when an accidental UTF-8 decode happened to succeed.
    controls = sum(1 for char in text if ord(char) < 32 and char not in "\n\r\t")
    if controls:
        raise ImportBlockedError(
            "Schematic contains binary control characters; save/export it as text first",
            path=str(path),
            control_characters=controls,
        )
    return text


def extract_f11_experiment(text: str, *, circuit_path: str, name: str) -> dict[str, Any] | None:
    """Convert supported F11 directives into a structured experiment draft."""

    expanded = text.replace(r"\n", "\n").replace(r"\r", "")
    directives: list[dict[str, Any]] = []
    for line_number, line in enumerate(expanded.splitlines(), 1):
        match = _ANALYSIS_DIRECTIVE_RE.match(line)
        if not match:
            continue
        kind = match.group(1).upper()
        arguments = match.group(2).strip().rstrip('"')
        directives.append({"kind": kind, "arguments": arguments, "line": line_number, "raw": f".{kind} {arguments}".rstrip()})
    if not directives:
        return None
    analyses: list[dict[str, Any]] = []
    kinds = {item["kind"] for item in directives}
    if "POP" in kinds or "AC" in kinds:
        analyses.append({"type": "pop_ac", "analysis": "ac" if "AC" in kinds else "pop", "directives": [item for item in directives if item["kind"] in {"POP", "AC", "OPTIONS"}], "group": "simplis_ac1" if "AC" in kinds else "simplis_pop1", "vectors": {}})
    if "TRAN" in kinds:
        analyses.append({"type": "startup", "analysis": "tran", "directives": [item for item in directives if item["kind"] in {"TRAN", "OPTIONS"}], "group": "simplis_tran1", "vectors": {}})
    variables: dict[str, str] = {}
    for item in directives:
        if item["kind"] != "VAR" or "=" not in item["arguments"]:
            continue
        key, value = item["arguments"].split("=", 1)
        variables[key.strip()] = value.strip()
    return {
        "schema_version": "simplis-automation/v2/experiment",
        "name": f"{name}_imported_analysis",
        "circuit": circuit_path,
        "analyses": analyses,
        "imported_variables": variables,
        "execution": {"startup_timeout_s": 15, "stall_timeout_s": 60, "hard_timeout_s": 240, "poll_interval_s": 2, "warning_allowlist": []},
        "metadata": {"imported_f11": True, "directives": directives},
    }


def _parse_instances_and_wires(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return raw instances, wires, and non-fatal parser diagnostics.

    The parser is deliberately conservative: unsupported lines are retained as
    diagnostics instead of being interpreted as connectivity or properties.
    """

    instances: list[dict[str, Any]] = []
    wires: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_instance = False

    def finish_current(line_number: int, *, implicit: bool = False) -> None:
        nonlocal current
        if current is None:
            return
        if not current.get("type"):
            diagnostics.append(
                {
                    "code": "unparseable_instance",
                    "severity": "warning",
                    "line": line_number,
                    "message": "Instance has no supported Attributes type and was skipped",
                }
            )
        else:
            current["end_line"] = line_number
            if implicit:
                current["implicitly_closed"] = True
            instances.append(current)
        current = None

    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped == ".Instance":
            if in_instance:
                finish_current(line_number - 1, implicit=True)
                diagnostics.append(
                    {
                        "code": "nested_instance",
                        "severity": "warning",
                        "line": line_number,
                        "message": "Previous instance was implicitly closed",
                    }
                )
            in_instance = True
            current = {"line": line_number, "attributes": {}, "properties": {}, "details": {}}
            continue
        if stripped == ".EndInstance":
            if not in_instance:
                diagnostics.append(
                    {
                        "code": "orphan_end_instance",
                        "severity": "warning",
                        "line": line_number,
                        "message": "Encountered .EndInstance outside an instance",
                    }
                )
            else:
                finish_current(line_number)
            in_instance = False
            continue

        attributes_match = _ATTRIBUTES_RE.match(line)
        if attributes_match and in_instance and current is not None:
            attrs = _parse_attributes(attributes_match.group("attrs"))
            current["attributes"] = attrs
            current["type"] = str(attrs.get("type", ""))
            continue

        property_match = _PROPERTY_RE.match(line)
        if property_match and in_instance and current is not None:
            name = _decode_quoted(property_match.group("name"))
            value = _decode_quoted(property_match.group("value"))
            current["properties"][name] = value
            continue

        wire_match = _WIRE_RE.match(line)
        if wire_match:
            attrs = _parse_attributes(wire_match.group("attrs"))
            attrs["line"] = line_number
            wires.append(attrs)
            continue

        # ``Netnames`` is emitted by module ports and is the only structured
        # non-Property line needed to retain an opaque module boundary.
        if in_instance and current is not None and stripped.startswith("Netnames"):
            current["details"]["Netnames"] = _parse_attributes(stripped[len("Netnames") :])
            continue

        if in_instance and stripped and not stripped.startswith("."):
            diagnostics.append(
                {
                    "code": "unsupported_instance_line",
                    "severity": "warning",
                    "line": line_number,
                    "message": "Unsupported line inside instance was not interpreted",
                    "text": stripped,
                }
            )

    if in_instance:
        finish_current(len(text.splitlines()), implicit=True)
        diagnostics.append(
            {
                "code": "unterminated_instance",
                "severity": "warning",
                "line": len(text.splitlines()),
                "message": "Final instance had no .EndInstance and was implicitly closed",
            }
        )
    return instances, wires, diagnostics


def _get_property(properties: dict[str, Any], name: str) -> str | None:
    for property_name, value in properties.items():
        if property_name.lower() == name.lower():
            return str(value)
    return None


def _unique_id(base: str, used: set[str]) -> str:
    base = base or "component"
    if base not in used:
        used.add(base)
        return base
    suffix = 2
    while f"{base}_{suffix}" in used:
        suffix += 1
    identifier = f"{base}_{suffix}"
    used.add(identifier)
    return identifier


def _symbol_names(device: dict[str, Any]) -> set[str]:
    symbol = device.get("symbol")
    names: set[str] = set()
    if isinstance(symbol, str):
        names.add(symbol)
    elif isinstance(symbol, dict):
        for key in ("name", "symbol", "path"):
            value = symbol.get(key)
            if isinstance(value, str) and value:
                names.add(value)
                names.add(Path(value.replace("\\", "/")).stem)
    for key in ("name", "native_symbol", "symbol_name"):
        value = device.get(key)
        if isinstance(value, str) and value:
            names.add(value)
    return {name.casefold() for name in names}


def _catalog_devices(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize compatible catalog shapes without coupling to catalog.py."""

    raw = catalog.get("devices", catalog.get("symbols", catalog.get("components", [])))
    devices: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict):
                item = dict(value)
                item.setdefault("name", key)
                devices.append(item)
    elif isinstance(raw, list):
        devices = [dict(item) for item in raw if isinstance(item, dict)]
    return devices


def _matching_catalog_devices(symbol: str, catalog: dict[str, Any]) -> list[dict[str, Any]]:
    sought = symbol.casefold()
    return [device for device in _catalog_devices(catalog) if sought in _symbol_names(device)]


def _property_value_for_schema(properties: dict[str, Any], public_name: str, schema: dict[str, Any]) -> Any:
    native_name = schema.get("native") if isinstance(schema, dict) else None
    for candidate in (public_name, native_name):
        if not isinstance(candidate, str):
            continue
        for observed_name, observed_value in properties.items():
            if observed_name.casefold() == candidate.casefold():
                return observed_value
    return None


def _find_catalog_device(
    symbol: str,
    catalog: dict[str, Any],
    properties: dict[str, Any],
) -> tuple[dict[str, Any] | None, bool]:
    """Resolve a symbol, detecting ambiguous approved variants fail-closed.

    Some SIMetrix symbols (notably ``vwave_v2``) represent several ideal
    devices.  An enum-valued catalog property such as ``SOURCE_MODEL`` can
    select one safely; otherwise the import must remain a catalog draft.
    """

    matches = _matching_catalog_devices(symbol, catalog)
    if not matches:
        return None, False
    approved = [device for device in matches if _is_approved(device)]
    choices = approved or matches
    scored: list[tuple[int, dict[str, Any]]] = []
    for device in choices:
        compatible = True
        score = 0
        for public_name, schema in _catalog_properties(device).items():
            observed = _property_value_for_schema(properties, public_name, schema)
            if observed is None:
                continue
            enum = schema.get("enum") if isinstance(schema, dict) else None
            if isinstance(enum, list) and str(observed) not in {str(item) for item in enum}:
                compatible = False
                break
            score += 1
        if compatible:
            scored.append((score, device))
    if not scored:
        return choices[0], len(choices) > 1
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best = scored[0]
    ambiguous = len(scored) > 1 and scored[1][0] == best_score
    return best, ambiguous


def _find_catalog_module(module_path: str, catalog: dict[str, Any]) -> dict[str, Any] | None:
    """Find an explicitly catalogued opaque module by path or basename."""

    normalized = module_path.replace("\\", "/")
    names = {normalized.casefold(), Path(normalized).stem.casefold()}
    matches: list[dict[str, Any]] = []
    for device in _catalog_devices(catalog):
        kind = str(device.get("kind", "")).casefold()
        ideality = str(device.get("ideality", "")).casefold()
        if kind != "opaque_module" and ideality != "boundary_only":
            continue
        candidates = _symbol_names(device)
        for key in ("path", "module_path"):
            value = device.get(key)
            if isinstance(value, str) and value:
                normalized_value = value.replace("\\", "/")
                candidates.update({normalized_value.casefold(), Path(normalized_value).stem.casefold()})
        if names & candidates:
            matches.append(device)
    if not matches:
        return None
    return next((device for device in matches if _is_approved(device)), matches[0])


def _catalog_properties(device: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not device:
        return {}
    raw = device.get("properties", device.get("allowed_properties", {}))
    if isinstance(raw, dict):
        return {
            str(name).casefold(): dict(spec) if isinstance(spec, dict) else {}
            for name, spec in raw.items()
        }
    if isinstance(raw, list):
        result: dict[str, dict[str, Any]] = {}
        for item in raw:
            if isinstance(item, str):
                result[item.casefold()] = {}
            elif isinstance(item, dict) and isinstance(item.get("name"), str):
                result[str(item["name"]).casefold()] = dict(item)
        return result
    return {}


def _catalog_pins(device: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not device:
        return []
    pins = device.get("pins", [])
    if not isinstance(pins, list):
        return []
    return [dict(pin) for pin in pins if isinstance(pin, dict)]


def _is_approved(device: dict[str, Any] | None) -> bool:
    return device is not None and (
        str(device.get("approval", "")).casefold() == "approved" or device.get("approved") is True
    )


def _native_symbol(attrs: dict[str, Any]) -> str:
    name = attrs.get("name")
    return str(name) if name is not None else ""


def _module_hash(source: Path, module_path: str) -> str | None:
    candidate = Path(module_path)
    if not candidate.is_absolute():
        candidate = source.parent / candidate
    try:
        return sha256_file(candidate) if candidate.is_file() else None
    except OSError:
        return None


def _is_opaque_instance(raw: dict[str, Any]) -> bool:
    attrs = raw["attributes"]
    instance_type = str(attrs.get("type", "")).casefold()
    module_path = str(attrs.get("path", ""))
    return instance_type == "component" or Path(module_path).suffix.casefold() in _OPAQUE_SUFFIXES


def _layout(attrs: dict[str, Any]) -> dict[str, Any]:
    layout: dict[str, Any] = {}
    for raw_key, target_key in (("x", "x"), ("y", "y"), ("orient", "orientation")):
        if raw_key in attrs:
            layout[target_key] = attrs[raw_key]
    return layout


def _build_components(
    raw_instances: list[dict[str, Any]],
    source: Path,
    catalog: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]], bool]:
    components: list[dict[str, Any]] = []
    ref_to_id: dict[str, str] = {}
    candidates: list[dict[str, Any]] = []
    opaque_present = False
    used_ids: set[str] = set()

    for index, raw in enumerate(raw_instances, start=1):
        attrs = raw["attributes"]
        imported_properties = dict(raw["properties"])
        imported_ref = _get_property(imported_properties, "REF")
        # v2 has a first-class ``ref`` field.  Keeping REF in the source
        # properties would make the compiler treat it as an unapproved device
        # property, so preserve it through ``ref`` only.
        properties = {
            property_name: value
            for property_name, value in imported_properties.items()
            if property_name.casefold() != "ref"
        }
        opaque = _is_opaque_instance(raw)
        if opaque:
            opaque_present = True
            module_path = str(attrs.get("path", attrs.get("name", "")))
            module_device = _find_catalog_module(module_path, catalog)
            module_approved = _is_approved(module_device)
            base = imported_ref or Path(module_path.replace("\\", "/")).stem or f"module_{index}"
            identifier = _unique_id(base, used_ids)
            module_hash = _module_hash(source, module_path) if module_path else None
            native_symbol = str(attrs.get("name") or Path(module_path.replace("\\", "/")).stem)
            component: dict[str, Any] = {
                "id": identifier,
                "ref": imported_ref or identifier,
                "kind": "opaque_module",
                "native": {"symbol": native_symbol, "path": module_path},
                "properties": properties,
                "pins": {},
                "layout": _layout(attrs),
                "module": {
                    "path": module_path,
                    "sha256": module_hash,
                    "boundary_only": True,
                    "details": raw.get("details", {}),
                    "attributes": dict(attrs),
                },
                "catalog": {
                    "approved": module_approved,
                    "device_kind": module_device.get("kind") if module_device else None,
                    "ideality": module_device.get("ideality") if module_device else "boundary_only",
                    "parameterizable_properties": [],
                },
            }
            components.append(component)
            if imported_ref and imported_ref not in ref_to_id:
                ref_to_id[imported_ref] = identifier
            if not module_approved:
                candidates.append(
                    {
                        "code": "opaque_module_requires_catalog_approval",
                        "severity": "warning",
                        "component_id": identifier,
                        "module_path": module_path,
                        "candidate": {
                            "kind": "opaque_module",
                            "path": module_path,
                            "sha256": module_hash,
                            "ideality": "boundary_only",
                        },
                    }
                )
            continue

        symbol = _native_symbol(attrs)
        base = imported_ref or symbol or f"component_{index}"
        identifier = _unique_id(base, used_ids)
        device, ambiguous_device = _find_catalog_device(symbol, catalog, properties)
        approved = _is_approved(device)
        property_specs = _catalog_properties(device)
        property_schema = {
            name: property_specs[name.casefold()]
            for name in properties
            if name.casefold() in property_specs
        }
        parameterizable = [
            name
            for name in properties
            if name.casefold() in property_specs
            and not bool(property_specs[name.casefold()].get("parameterizable") is False)
        ]
        kind = str(device.get("kind", "device")) if approved and device else "catalog_candidate"
        component = {
            "id": identifier,
            "ref": imported_ref or identifier,
            "kind": kind,
            "native": {"symbol": symbol},
            "properties": properties,
            "pins": {},
            "layout": _layout(attrs),
            "catalog": {
                "approved": approved,
                "device_kind": device.get("kind") if device else None,
                "ideality": device.get("ideality") if device else None,
                "property_schema": property_schema,
                "parameterizable_properties": parameterizable if approved else [],
                "expected_pins": [str(pin.get("name")) for pin in _catalog_pins(device)] if approved else [],
            },
        }
        if not approved:
            component["opaque_boundary"] = True
            component["roundtrip_only"] = True
        if device and isinstance(device.get("symbol"), dict) and device["symbol"].get("library"):
            component["native"]["library"] = device["symbol"]["library"]
        components.append(component)
        if imported_ref and imported_ref not in ref_to_id:
            ref_to_id[imported_ref] = identifier

        if not approved:
            candidates.append(
                {
                    "code": "unknown_symbol" if device is None else "unapproved_symbol",
                    "severity": "error",
                    "component_id": identifier,
                    "symbol": symbol,
                    "candidate": {
                        "kind": "device",
                        "symbol": symbol,
                        "observed_properties": dict(sorted(properties.items())),
                        "pins": _catalog_pins(device),
                    },
                }
            )
        elif ambiguous_device:
            candidates.append(
                {
                    "code": "ambiguous_symbol_mapping",
                    "severity": "error",
                    "component_id": identifier,
                    "symbol": symbol,
                    "candidate": {
                        "symbol": symbol,
                        "matching_kinds": [
                            str(candidate.get("kind", "device"))
                            for candidate in _matching_catalog_devices(symbol, catalog)
                            if _is_approved(candidate)
                        ],
                        "message": "Observed properties do not uniquely select one approved catalog device",
                    },
                }
            )
        elif device:
            allowed = set(property_specs) | _STRUCTURAL_PROPERTIES
            for property_name, value in properties.items():
                if property_name.casefold() not in allowed:
                    candidates.append(
                        {
                            "code": "unknown_property",
                            "severity": "error",
                            "component_id": identifier,
                            "symbol": symbol,
                            "property": property_name,
                            "value": value,
                            "candidate": {
                                "symbol": symbol,
                                "property": property_name,
                                "observed_value": value,
                            },
                        }
                    )
    return components, ref_to_id, candidates, opaque_present


def _branch_endpoints(branch: Any) -> Iterable[tuple[str, str]]:
    if not isinstance(branch, str):
        return []
    return [(match.group("ref"), match.group("pin")) for match in _PIN_REFERENCE_RE.finditer(branch)]


def _find_component(components: list[dict[str, Any]], identifier: str) -> dict[str, Any] | None:
    return next((component for component in components if component["id"] == identifier), None)


def _append_endpoint(nets: dict[str, dict[str, Any]], net: str, endpoint: str) -> None:
    data = nets.setdefault(net, {"endpoints": [], "wires": []})
    if endpoint not in data["endpoints"]:
        data["endpoints"].append(endpoint)


def _build_networks(
    components: list[dict[str, Any]],
    ref_to_id: dict[str, str],
    wires: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    nets: dict[str, dict[str, Any]] = {}
    for wire_index, wire in enumerate(wires, start=1):
        net = str(wire.get("net") or f"__wire_{wire_index}")
        entry = nets.setdefault(net, {"endpoints": [], "wires": []})
        entry["wires"].append(
            {
                key: wire[key]
                for key in ("x1", "y1", "x2", "y2", "branch", "line")
                if key in wire
            }
        )
        for ref, pin in _branch_endpoints(wire.get("branch")):
            component_id = ref_to_id.get(ref)
            if component_id is None:
                candidates.append(
                    {
                        "code": "unresolved_wire_endpoint",
                        "severity": "warning",
                        "net": net,
                        "reference": ref,
                        "pin": pin,
                        "line": wire.get("line"),
                    }
                )
                continue
            component = _find_component(components, component_id)
            if component is not None:
                previous = component["pins"].get(pin)
                if previous is not None and previous != net:
                    candidates.append(
                        {
                            "code": "pin_connected_to_multiple_nets",
                            "severity": "error",
                            "component_id": component_id,
                            "pin": pin,
                            "nets": [previous, net],
                        }
                    )
                component["pins"][pin] = net
            _append_endpoint(nets, net, f"{component_id}.{pin}")

    # Component-module instances may carry their externally visible boundary
    # as ``Netnames pin1=...`` rather than as ordinary wire branches.  Preserve
    # those pin/net associations without claiming anything about the module's
    # internals.
    for component in components:
        if component.get("kind") != "opaque_module":
            continue
        module = component.get("module", {})
        details = module.get("details", {}) if isinstance(module, dict) else {}
        netnames = details.get("Netnames", {}) if isinstance(details, dict) else {}
        if not isinstance(netnames, dict):
            continue
        for pin, net_value in netnames.items():
            if not isinstance(net_value, str) or not net_value:
                continue
            net = net_value
            previous = component["pins"].get(pin)
            if previous is not None and previous != net:
                candidates.append(
                    {
                        "code": "pin_connected_to_multiple_nets",
                        "severity": "error",
                        "component_id": component["id"],
                        "pin": pin,
                        "nets": [previous, net],
                    }
                )
            component["pins"][pin] = net
            _append_endpoint(nets, net, f"{component['id']}.{pin}")

    # SIMetrix terminal symbols carry their net name as VALUE.  Retain that
    # graph edge even when the text wire did not record a branch endpoint.
    for component in components:
        symbol = str(component.get("native", {}).get("symbol", "")).casefold()
        if symbol not in {"term", "gnd", "gnd2", "modport"}:
            continue
        properties = component["properties"]
        net = _get_property(properties, "VALUE") or _get_property(properties, "netname")
        if symbol in {"gnd", "gnd2"}:
            net = net or "0"
        if not net:
            continue
        pin = "P"
        previous = component["pins"].get(pin)
        if previous is None:
            component["pins"][pin] = net
        _append_endpoint(nets, net, f"{component['id']}.{pin}")

    # Deterministic output makes a GUI import reviewable and enables stable
    # manifests even when the source has repeated wire fragments.
    for net_data in nets.values():
        net_data["endpoints"].sort()
        net_data["wires"].sort(key=lambda item: (item.get("line", 0), canonical_json(item)))
    return dict(sorted(nets.items()))


def _catalog_lock(catalog: dict[str, Any]) -> dict[str, Any]:
    raw_lock = catalog.get("catalog_lock", catalog.get("lock", {}))
    lock = dict(raw_lock) if isinstance(raw_lock, dict) else {}
    if "path" not in lock:
        for key in ("_path", "path"):
            if isinstance(catalog.get(key), str):
                lock["path"] = catalog[key]
                break
    lock.setdefault("path", "<in-memory-catalog>")
    fingerprint = lock.get("fingerprint") or catalog.get("fingerprint")
    lock["fingerprint"] = str(fingerprint or sha256_text(canonical_json(catalog)))
    return lock


def _topology_issues(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Report missing catalog pin assignments without guessing connections."""

    issues: list[dict[str, Any]] = []
    for component in components:
        catalog_info = component.get("catalog")
        if not isinstance(catalog_info, dict) or not catalog_info.get("approved"):
            continue
        if component.get("kind") == "opaque_module":
            continue
        expected = catalog_info.get("expected_pins", [])
        if not isinstance(expected, list):
            continue
        missing = sorted(str(pin) for pin in expected if str(pin) not in component.get("pins", {}))
        if missing:
            issues.append(
                {
                    "code": "missing_pin_connections",
                    "severity": "error",
                    "component_id": component["id"],
                    "missing_pins": missing,
                }
            )
    return issues


def _status(*, has_candidates: bool, opaque_present: bool, has_topology_issues: bool) -> str:
    if has_topology_issues:
        return "blocked"
    if has_candidates:
        return "catalog_pending"
    if opaque_present:
        return "boundary_verified"
    return "static_valid"


def _direct_module_draft(source: Path, text: str, catalog: dict[str, Any]) -> dict[str, Any]:
    """Import a standalone .sxcmp as one opaque boundary, never its internals."""

    raw_instances, _wires, parser_diagnostics = _parse_instances_and_wires(text)
    ports: list[dict[str, Any]] = []
    for raw in raw_instances:
        attrs = raw["attributes"]
        if str(attrs.get("name", "")).casefold() != "modport":
            continue
        ports.append(
            {
                "ref": _get_property(raw["properties"], "REF"),
                "netname": _get_property(raw["properties"], "netname")
                or _get_property(raw["properties"], "VALUE"),
                "details": raw.get("details", {}).get("Netnames", {}),
                "layout": _layout(attrs),
            }
        )
    identifier = source.stem or "opaque_module"
    module_device = _find_catalog_module(str(source), catalog) or _find_catalog_module(source.name, catalog)
    approved = _is_approved(module_device)
    candidates = []
    if not approved:
        candidates.append(
            {
                "code": "opaque_module_requires_catalog_approval",
                "severity": "warning",
                "component_id": identifier,
                "module_path": str(source),
                "candidate": {
                    "kind": "opaque_module",
                    "path": str(source),
                    "sha256": sha256_file(source),
                    "ideality": "boundary_only",
                },
            }
        )
    return {
        "schema_version": "simplis-automation/v2",
        "name": source.stem or "opaque_module",
        "catalog_lock": _catalog_lock(catalog),
        "design": {"name": source.stem or "opaque_module"},
        "parameters": {},
        "components": [
            {
                "id": identifier,
                "ref": identifier,
                "kind": "opaque_module",
                "native": {"symbol": source.stem, "path": str(source)},
                "properties": {},
                "pins": {},
                "layout": {},
                "module": {
                    "path": str(source),
                    "sha256": sha256_file(source),
                    "boundary_only": True,
                    "ports": ports,
                },
                "catalog": {
                    "approved": approved,
                    "device_kind": module_device.get("kind") if module_device else None,
                    "ideality": module_device.get("ideality") if module_device else "boundary_only",
                    "parameterizable_properties": [],
                },
            }
        ],
        "nets": {},
        "metadata": {
            "import_status": "boundary_verified" if approved else "catalog_pending",
            "source": {"path": str(source), "sha256": sha256_file(source), "format": "text-sxcmp"},
            "diagnostics": {"parser": parser_diagnostics, "catalog_candidates": candidates},
        },
    }


def import_schematic(input_path: str | Path, catalog: dict[str, Any], out_path: str | Path) -> dict[str, Any]:
    """Import one readable SIMetrix schematic and write a v2 YAML draft.

    ``catalog`` is intentionally a plain dictionary.  The function recognizes
    the v2 catalog's ``devices`` list and a few map-shaped compatibility forms,
    so it can be called by a CLI without importing an unfinished catalog module.
    Unknown symbols/properties are preserved in the draft but recorded under
    ``metadata.diagnostics.catalog_candidates`` and force ``catalog_pending``.
    """

    source = Path(input_path)
    if not source.is_file():
        raise ValidationError("Schematic input does not exist or is not a file", path=str(source))
    if not isinstance(catalog, dict):
        raise ValidationError("Catalog must be an object", received_type=type(catalog).__name__)
    if source.suffix.casefold() not in {".sxsch", ".sxcmp"}:
        raise ValidationError("Importer accepts .sxsch or .sxcmp input", path=str(source))

    text = _read_text(source)
    if source.suffix.casefold() == ".sxcmp":
        draft = _direct_module_draft(source, text, catalog)
        write_yaml(out_path, draft)
        return draft

    raw_instances, wires, parser_diagnostics = _parse_instances_and_wires(text)
    components, ref_to_id, candidates, opaque_present = _build_components(raw_instances, source, catalog)
    nets = _build_networks(components, ref_to_id, wires, candidates)
    topology_issues = _topology_issues(components)
    draft = {
        "schema_version": "simplis-automation/v2",
        "name": source.stem or "imported_schematic",
        "catalog_lock": _catalog_lock(catalog),
        "design": {"name": source.stem or "imported_schematic"},
        "parameters": {},
        "components": components,
        "nets": nets,
        "metadata": {
            "import_status": _status(
                has_candidates=bool(candidates),
                opaque_present=opaque_present,
                has_topology_issues=bool(topology_issues),
            ),
            "source": {"path": str(source), "sha256": sha256_file(source), "format": "text-sxsch"},
            "roundtrip": {"eligible": True, "mode": "source_preserving_saveas", "opaque_devices_allowed": True},
            "diagnostics": {
                "parser": parser_diagnostics,
                "catalog_candidates": candidates,
                "topology": topology_issues,
            },
        },
    }
    write_yaml(out_path, draft)
    experiment = extract_f11_experiment(text, circuit_path=str(Path(out_path).resolve()), name=source.stem or "imported_schematic")
    if experiment:
        experiment_path = Path(out_path).with_name(Path(out_path).stem + ".experiment.yaml")
        write_yaml(experiment_path, experiment)
        draft["metadata"]["experiment"] = str(experiment_path.resolve())
        # Rewrite the circuit draft once so the link itself is preserved.
        write_yaml(out_path, draft)
    return draft


def _find_promotable_component(circuit: dict[str, Any], component_id: str) -> dict[str, Any]:
    components = circuit.get("components")
    if not isinstance(components, list):
        raise ValidationError("Circuit components must be a list", path="components")
    component = next((item for item in components if isinstance(item, dict) and item.get("id") == component_id), None)
    if component is None:
        raise ValidationError("Component was not found", component_id=component_id)
    if not isinstance(component.get("properties"), dict):
        raise ValidationError("Component properties must be an object", component_id=component_id)
    return component


def _canonical_property_name(properties: dict[str, Any], requested: str) -> str | None:
    exact = next((name for name in properties if name == requested), None)
    if exact is not None:
        return exact
    matches = [name for name in properties if name.casefold() == requested.casefold()]
    return matches[0] if len(matches) == 1 else None


def _numeric_value(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str) and re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", value.strip()):
        return float(value)
    return None


def promote_parameter(
    circuit_path: str | Path,
    component_id: str,
    property_name: str,
    name: str,
    dimension: str,
    minimum: Any,
    maximum: Any,
    out_path: str | Path,
) -> dict[str, Any]:
    """Promote one catalog-whitelisted component property into a parameter card.

    Promotion is intentionally blocked for imported unknown symbols, opaque
    modules, and properties not in the approved device's schema.  The literal
    value remains the parameter default; the component property becomes
    ``${name}`` so subsequent compile/sweep operations have one source of truth.
    """

    if not _PARAMETER_NAME_RE.fullmatch(name):
        raise ValidationError("Parameter name is invalid", name=name)
    if not isinstance(dimension, str) or not dimension.strip():
        raise ValidationError("Parameter dimension is required", dimension=dimension)
    if minimum is None or maximum is None:
        raise ValidationError("Parameter minimum and maximum are required", minimum=minimum, maximum=maximum)
    lower = _numeric_value(minimum)
    upper = _numeric_value(maximum)
    if lower is not None and upper is not None and lower > upper:
        raise ValidationError("Parameter minimum exceeds maximum", minimum=minimum, maximum=maximum)

    circuit = copy.deepcopy(load_yaml(circuit_path))
    component = _find_promotable_component(circuit, component_id)
    properties = component["properties"]
    actual_property = _canonical_property_name(properties, property_name)
    if actual_property is None:
        raise ValidationError(
            "Component property was not found",
            component_id=component_id,
            property_name=property_name,
        )

    catalog_info = component.get("catalog")
    if not isinstance(catalog_info, dict) or not catalog_info.get("approved"):
        raise ImportBlockedError(
            "Only catalog-approved component properties may be parameterized",
            component_id=component_id,
            property_name=actual_property,
        )
    allowed = catalog_info.get("parameterizable_properties", [])
    if not isinstance(allowed, list) or actual_property not in allowed:
        raise ImportBlockedError(
            "Property is not whitelisted for parameter promotion",
            component_id=component_id,
            property_name=actual_property,
        )
    property_schema = catalog_info.get("property_schema", {})
    if isinstance(property_schema, dict):
        schema = property_schema.get(actual_property, {})
        expected_dimension = schema.get("dimension") if isinstance(schema, dict) else None
        if expected_dimension and str(expected_dimension) != dimension:
            raise ValidationError(
                "Parameter dimension does not match the approved property schema",
                component_id=component_id,
                property_name=actual_property,
                expected_dimension=expected_dimension,
                received_dimension=dimension,
            )

    parameters = circuit.setdefault("parameters", {})
    if not isinstance(parameters, dict):
        raise ValidationError("Circuit parameters must be an object", path="parameters")
    if name in parameters:
        raise ValidationError("Parameter name already exists", name=name)
    default = properties[actual_property]
    parameters[name] = {
        "default": default,
        "dimension": dimension,
        "min": minimum,
        "max": maximum,
        "component_id": component_id,
        "property": actual_property,
    }
    properties[actual_property] = f"${{{name}}}"

    metadata = circuit.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        raise ValidationError("Circuit metadata must be an object", path="metadata")
    diagnostics = metadata.setdefault("diagnostics", {})
    if not isinstance(diagnostics, dict):
        raise ValidationError("Circuit diagnostics must be an object", path="metadata.diagnostics")
    promotions = diagnostics.setdefault("parameter_promotions", [])
    if not isinstance(promotions, list):
        raise ValidationError("Parameter promotions must be a list", path="metadata.diagnostics.parameter_promotions")
    promotions.append({"name": name, "component_id": component_id, "property": actual_property})

    write_yaml(out_path, circuit)
    return circuit


__all__ = ["import_schematic", "promote_parameter"]
