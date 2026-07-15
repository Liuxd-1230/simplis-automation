"""Versioned local device catalogs and installed symbol-library discovery."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from typing import Any, Iterable

from .errors import CatalogError
from .io import canonical_json, load_yaml, sha256_file, sha256_text, write_yaml


CATALOG_SCHEMA_VERSION = "simplis-automation/v2/catalog"
CATALOG_CANDIDATE_SCHEMA_VERSION = "simplis-automation/v2/catalog-candidate"

_ATTRIBUTE_RE = re.compile(r'Attributes\b.*\bname="([^"]+)"')
_PIN_RE = re.compile(r'Pin\s+name="([^"]+)"\s+order=([0-9]+)\s+x=(-?[0-9]+)\s+y=(-?[0-9]+)')
_PROPERTY_RE = re.compile(r'Property\s+name="([^"]+)"\s+value="([^"]*)"')
_SCALAR_COORD_RE = re.compile(r'\b(x1|x2|x|l|r|y1|y2|y|t|b)=(-?[0-9]+)')
_LIST_COORD_RE = re.compile(r'\b([xy])="([^"]+)"')
_LINE_ATTRIBUTE_RE = re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)=(?:"((?:\\.|[^"])*)"|([^\s]+))')
_SUBCKT_RE = re.compile(r"^\s*\.SUBCKT\s+(\S+)\s*(.*?)\s*$", re.IGNORECASE)
_CATALOG_MODEL_RE = re.compile(r"\b(?:model|name)\s*=\s*['\"]?([^'\"\s,;]+)", re.IGNORECASE)

_DEFAULT_TEXT_CHARACTER_WIDTH = 60
_DEFAULT_TEXT_LINE_HEIGHT = 120

IDEAL_NATIVE_PROOFS = (
    "library_hash",
    "pin_contract",
    "gui_placement",
    "clean_reopen",
    "netlist",
    "behavior_test",
)
BANNED_WRAPPERS = {
    "lp311",
    "hc08",
    "hc04",
    "hc04d",
    "hc14",
    "hc00",
    "hc02",
    "hc32",
    "hc74",
    "gen_switch",
}


def _line_attributes(line: str) -> dict[str, str]:
    """Parse the simple key/value fields used by saved symbol records."""

    return {name: quoted if quoted != "" else bare for name, quoted, bare in _LINE_ATTRIBUTE_RE.findall(line)}


def _text_bbox(value: str, x: int, y: int, align: str = "") -> dict[str, int]:
    """Conservatively approximate one visible Default-font annotation.

    SIMetrix stores the text anchor rather than a rendered bounding rectangle.
    Its default schematic grid/font pairing is close to 60 units per glyph and
    120 units per line.  Keeping this estimate in the catalog makes the layout
    fail safe for long references instead of treating their anchor as a point.
    """

    rows = str(value).replace("\\n", "\n").splitlines() or [""]
    width = max(1, max(len(row) for row in rows)) * _DEFAULT_TEXT_CHARACTER_WIDTH
    height = max(1, len(rows)) * _DEFAULT_TEXT_LINE_HEIGHT
    normalized = align.casefold()
    if "left" in normalized:
        min_x = x
    elif "right" in normalized:
        min_x = x - width
    else:
        min_x = x - width // 2
    if "top" in normalized:
        min_y = y
    elif "base" in normalized or "bottom" in normalized:
        min_y = y - height
    else:
        min_y = y - height // 2
    return {"min_x": min_x, "min_y": min_y, "max_x": min_x + width, "max_y": min_y + height}


def _bounds(x_values: list[int], y_values: list[int]) -> dict[str, int]:
    if not x_values or not y_values:
        return {"min_x": 0, "min_y": 0, "max_x": 0, "max_y": 0, "width": 0, "height": 0}
    min_x, max_x = min(x_values), max(x_values)
    min_y, max_y = min(y_values), max(y_values)
    return {
        "min_x": min_x,
        "min_y": min_y,
        "max_x": max_x,
        "max_y": max_y,
        "width": max_x - min_x,
        "height": max_y - min_y,
    }


def _symbol_geometry(
    lines: Iterable[str],
    pins: Iterable[dict[str, Any]],
) -> tuple[dict[str, int], dict[str, int], list[dict[str, Any]]]:
    """Return total/default geometry, drawing geometry and visible properties."""

    x_values = [int(pin["x"]) for pin in pins]
    y_values = [int(pin["y"]) for pin in pins]
    drawing_prefixes = (
        "Arc ",
        "Bezier ",
        "Box ",
        "Circle ",
        "Ellipse ",
        "FilledPoly ",
        "Line ",
        "Poly ",
        "Rectangle ",
        "Segment ",
        "Text ",
    )
    explicit_annotation_boxes: list[dict[str, int]] = []
    visible_properties: list[dict[str, Any]] = []
    for raw in lines:
        line = raw.strip()
        include = line.startswith(drawing_prefixes)
        property_line = line.startswith("Property ")
        attributes = _line_attributes(line) if property_line else {}
        visible_property = property_line and attributes.get("visible", "1") != "0"
        if visible_property:
            descriptor: dict[str, Any] = {
                "name": attributes.get("name", ""),
                "value": attributes.get("value", ""),
                "autopos": int(attributes.get("autopos", "1")),
                "normal": attributes.get("normal", "Right"),
                "rotated": attributes.get("rotated", attributes.get("normal", "Right")),
                "align": attributes.get("align", ""),
                "display_name": attributes.get("displayName", "0") == "1",
                "font": attributes.get("font", "Default"),
                "order": int(attributes.get("order", "0")),
            }
            if "x" in attributes and "y" in attributes:
                descriptor["x"] = int(attributes["x"])
                descriptor["y"] = int(attributes["y"])
                shown = f"{descriptor['name']}={descriptor['value']}" if descriptor["display_name"] else str(descriptor["value"])
                explicit_annotation_boxes.append(
                    _text_bbox(shown, descriptor["x"], descriptor["y"], str(descriptor["align"]))
                )
            visible_properties.append(descriptor)
        if not include:
            continue
        for name, value in _SCALAR_COORD_RE.findall(line):
            if name in {"x", "x1", "x2", "l", "r"}:
                x_values.append(int(value))
            else:
                y_values.append(int(value))
        for axis, values in _LIST_COORD_RE.findall(line):
            parsed = [int(value) for value in re.findall(r"-?[0-9]+", values)]
            (x_values if axis == "x" else y_values).extend(parsed)
    drawing_bbox = _bounds(x_values, y_values)
    total_x = list(x_values)
    total_y = list(y_values)
    for box in explicit_annotation_boxes:
        total_x.extend((box["min_x"], box["max_x"]))
        total_y.extend((box["min_y"], box["max_y"]))
    return _bounds(total_x, total_y), drawing_bbox, visible_properties


def _symbol_record(
    lines: list[str],
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, int], dict[str, int], list[dict[str, Any]]]:
    pins: list[dict[str, Any]] = []
    properties: dict[str, str] = {}
    for line in lines:
        pin = _PIN_RE.search(line)
        if pin:
            pins.append({"name": pin.group(1), "index": int(pin.group(2)), "x": int(pin.group(3)), "y": int(pin.group(4))})
        prop = _PROPERTY_RE.search(line)
        if prop:
            properties[prop.group(1)] = prop.group(2)
    ordered = sorted(pins, key=lambda item: item["index"])
    bbox, drawing_bbox, visible_properties = _symbol_geometry(lines, ordered)
    return ordered, properties, bbox, drawing_bbox, visible_properties


def _proof_passed(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, dict):
        return value.get("passed") is True and bool(value.get("artifact") or value.get("sha256") or value.get("details"))
    return False


def ideal_native_proof_status(device: dict[str, Any]) -> dict[str, Any]:
    """Report whether an ideal-native claim has all six independent proofs."""

    evidence = device.get("evidence", {})
    proofs = evidence.get("proofs", {}) if isinstance(evidence, dict) else {}
    missing = [name for name in IDEAL_NATIVE_PROOFS if not _proof_passed(proofs.get(name))]
    return {"eligible": not missing, "required": list(IDEAL_NATIVE_PROOFS), "missing": missing}


def _normalize_device(raw: dict[str, Any]) -> dict[str, Any]:
    value = deepcopy(raw)
    kind = value.get("kind")
    if not isinstance(kind, str) or not kind:
        raise CatalogError("Catalog device needs a non-empty kind")
    symbol = value.get("symbol")
    if not isinstance(symbol, dict) or not isinstance(symbol.get("name"), str):
        raise CatalogError("Catalog device needs symbol.name", kind=kind)
    pins = value.get("pins")
    if not isinstance(pins, list) or not pins:
        raise CatalogError("Catalog device needs a non-empty pins list", kind=kind)
    seen: set[str] = set()
    for pin in pins:
        if not isinstance(pin, dict) or not isinstance(pin.get("name"), str):
            raise CatalogError("Catalog pin needs a name", kind=kind)
        if pin["name"] in seen:
            raise CatalogError("Catalog device has duplicate pin", kind=kind, pin=pin["name"])
        seen.add(pin["name"])
        pin.setdefault("domain", "analog")
    properties = value.setdefault("properties", {})
    if not isinstance(properties, dict):
        raise CatalogError("Catalog properties must be a mapping", kind=kind)
    for name, definition in properties.items():
        if not isinstance(definition, dict):
            raise CatalogError("Catalog property definition must be a mapping", kind=kind, property=name)
        definition.setdefault("native", name)
        definition.setdefault("dimension", "dimensionless")
        definition.setdefault("required", False)
    value.setdefault("approval", "approved")
    value.setdefault("ideality", "ideal")
    if value.get("ideality") == "ideal_native":
        proof = ideal_native_proof_status(value)
        if value.get("approval") == "approved" and not proof["eligible"]:
            raise CatalogError("ideal_native device lacks required approval evidence", kind=kind, missing=proof["missing"])
    tokens = " ".join(
        str(item)
        for item in (
            kind,
            value.get("role", ""),
            value.get("model", ""),
            value.get("placement_symbol", ""),
            value.get("symbol", {}).get("name", ""),
        )
    ).casefold()
    banned = sorted(token for token in BANNED_WRAPPERS if re.search(rf"(?<![a-z0-9_]){re.escape(token)}(?![a-z0-9_])", tokens))
    if banned and value.get("approval") == "approved":
        raise CatalogError("Approved catalog device references a banned wrapper", kind=kind, tokens=banned)
    return value


def catalog_fingerprint(catalog: dict[str, Any]) -> str:
    """Fingerprint semantic catalog content, excluding derived lookup indexes."""

    payload = {key: value for key, value in catalog.items() if key not in {"_path", "_by_kind", "fingerprint"}}
    return sha256_text(canonical_json(payload))


def validate_catalog(raw: dict[str, Any]) -> dict[str, Any]:
    value = deepcopy(raw)
    if value.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise CatalogError(
            "Unsupported catalog schema version",
            expected=CATALOG_SCHEMA_VERSION,
            actual=value.get("schema_version"),
        )
    runtime = value.get("runtime")
    if not isinstance(runtime, dict):
        raise CatalogError("Catalog needs a runtime mapping")
    devices = value.get("devices")
    if not isinstance(devices, list) or not devices:
        raise CatalogError("Catalog needs a non-empty devices list")
    normalized = [_normalize_device(device) for device in devices if isinstance(device, dict)]
    if len(normalized) != len(devices):
        raise CatalogError("Every catalog device must be a mapping")
    by_kind: dict[str, dict[str, Any]] = {}
    for device in normalized:
        if device["kind"] in by_kind:
            raise CatalogError("Catalog has duplicate device kind", kind=device["kind"])
        by_kind[device["kind"]] = device
    value["devices"] = normalized
    value["_by_kind"] = by_kind
    value["fingerprint"] = catalog_fingerprint(value)
    return value


def load_catalog(path: str | Path) -> dict[str, Any]:
    catalog = validate_catalog(load_yaml(path))
    catalog["_path"] = str(Path(path).resolve())
    return catalog


def get_device(catalog: dict[str, Any], kind: str, *, approved_only: bool = True) -> dict[str, Any]:
    device = catalog.get("_by_kind", {}).get(kind)
    if device is None:
        raise CatalogError("Device kind is absent from the local catalog", kind=kind)
    if approved_only and device.get("approval") != "approved":
        raise CatalogError("Device kind is pending catalog approval", kind=kind, approval=device.get("approval"))
    return device


def find_property(device: dict[str, Any], name: str) -> tuple[str, dict[str, Any]] | None:
    target = str(name).casefold()
    for public_name, definition in device.get("properties", {}).items():
        if str(public_name).casefold() == target or str(definition.get("native", "")).casefold() == target:
            return str(public_name), definition
    return None


def is_fully_ideal(catalog: dict[str, Any], kinds: Iterable[str]) -> bool:
    accepted = {"ideal", "ideal_native", "fully_ideal"}
    return all(get_device(catalog, kind).get("ideality") in accepted for kind in kinds if kind != "opaque_module")


def parse_symbol_libraries(symbol_library_dir: str | Path) -> dict[str, dict[str, Any]]:
    """Read enough `.sxslb` structure to safely name symbols, pins, and defaults."""

    root = Path(symbol_library_dir)
    if not root.is_dir():
        raise CatalogError("SIMetrix symbol library directory does not exist", path=str(root))
    symbols: dict[str, dict[str, Any]] = {}
    for library in sorted(root.glob("*.sxslb")):
        current_name: str | None = None
        lines: list[str] = []
        in_symbol = False
        try:
            contents = library.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            raise CatalogError("Cannot read symbol library", path=str(library), reason=str(exc)) from exc
        for raw in contents:
            token = raw.strip()
            if token == ".Symbol":
                current_name = None
                lines = []
                in_symbol = True
                continue
            if token == ".EndSymbol":
                if current_name and current_name not in symbols:
                    pins, properties, bbox, drawing_bbox, visible_properties = _symbol_record(lines)
                    symbols[current_name] = {
                        "name": current_name,
                        "library": str(library),
                        "library_sha256": sha256_file(library),
                        "pins": pins,
                        "properties": properties,
                        "bbox": bbox,
                        "drawing_bbox": drawing_bbox,
                        "visible_properties": visible_properties,
                    }
                current_name = None
                lines = []
                in_symbol = False
                continue
            if in_symbol:
                lines.append(raw)
                if current_name is None:
                    match = _ATTRIBUTE_RE.search(raw)
                    if match:
                        current_name = match.group(1)
    return symbols


def _parse_symbol_blocks(path: Path, *, source_type: str) -> dict[str, dict[str, Any]]:
    """Parse symbols embedded in .sxsch/.sxcmp files using the same saved format."""

    symbols: dict[str, dict[str, Any]] = {}
    try:
        contents = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise CatalogError("Cannot read embedded symbol source", path=str(path), reason=str(exc)) from exc
    in_symbol = False
    current_name: str | None = None
    lines: list[str] = []
    for raw in contents:
        token = raw.strip()
        if token == ".Symbol":
            in_symbol = True
            current_name = None
            lines = []
            continue
        if token == ".EndSymbol" and in_symbol:
            if current_name:
                pins, properties, bbox, drawing_bbox, visible_properties = _symbol_record(lines)
                symbols[current_name] = {
                    "name": current_name,
                    "source": str(path),
                    "source_type": source_type,
                    "source_sha256": sha256_file(path),
                    "pins": pins,
                    "properties": properties,
                    "bbox": bbox,
                    "drawing_bbox": drawing_bbox,
                    "visible_properties": visible_properties,
                    "placement_adapter": "embedded_clone",
                    # Keep the exact official symbol block so the compiler can
                    # construct a minimal host schematic.  Instances, labels,
                    # wires and the final view are still created by SIMetrix.
                    "definition_lines": [".Symbol", *lines, ".EndSymbol"],
                }
            in_symbol = False
            current_name = None
            lines = []
            continue
        if in_symbol:
            lines.append(raw)
            if current_name is None:
                match = _ATTRIBUTE_RE.search(raw)
                if match:
                    current_name = match.group(1)
    return symbols


def load_embedded_symbol(
    source: str | Path,
    symbol_name: str,
    *,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Load one hash-locked symbol embedded in an official schematic/module.

    Discovery is not approval.  This helper only provides geometry and the
    exact symbol definition after checking the catalog's source hash.
    """

    path = Path(source).resolve()
    if not path.is_file():
        raise CatalogError("Embedded symbol source does not exist", path=str(path), symbol=symbol_name)
    actual_sha256 = sha256_file(path)
    if expected_sha256 and actual_sha256.casefold() != str(expected_sha256).casefold():
        raise CatalogError(
            "Embedded symbol source hash does not match the locked catalog",
            path=str(path),
            symbol=symbol_name,
            expected_sha256=expected_sha256,
            actual_sha256=actual_sha256,
        )
    symbols = _parse_symbol_blocks(path, source_type="embedded_symbol")
    try:
        record = symbols[symbol_name]
    except KeyError as exc:
        matches = [item for name, item in symbols.items() if name.casefold() == symbol_name.casefold()]
        if len(matches) != 1:
            raise CatalogError("Embedded symbol is absent from its locked source", path=str(path), symbol=symbol_name) from exc
        record = matches[0]
    return deepcopy(record)


def discover_catalog_sources(
    *,
    symbol_library_dir: str | Path,
    model_dirs: Iterable[str | Path] = (),
    example_dirs: Iterable[str | Path] = (),
) -> dict[str, Any]:
    """Discover all three supported 8.4 evidence families without approving them.

    The result is inventory evidence only.  In particular, finding a .SUBCKT
    or embedded symbol does not imply that GUI placement or behavior works.
    """

    symbol_root = Path(symbol_library_dir).resolve()
    symbols = parse_symbol_libraries(symbol_root)
    models: dict[str, list[dict[str, Any]]] = {}
    catalog_entries: list[dict[str, Any]] = []
    model_files: list[Path] = []
    for raw_root in model_dirs:
        root = Path(raw_root).resolve()
        if root.is_file():
            model_files.append(root)
        elif root.is_dir():
            model_files.extend(path for path in root.rglob("*") if path.is_file() and path.suffix.casefold() in {".lb", ".cat"})
    for path in sorted(set(model_files), key=lambda item: str(item).casefold()):
        text = path.read_text(encoding="utf-8", errors="replace")
        file_hash = sha256_file(path)
        if path.suffix.casefold() == ".lb":
            for line_number, line in enumerate(text.splitlines(), 1):
                match = _SUBCKT_RE.match(line)
                if not match:
                    continue
                name = match.group(1)
                pins = [item for item in match.group(2).split() if not item.casefold().startswith("vars:")]
                models.setdefault(name, []).append(
                    {"name": name, "pins": pins, "path": str(path), "sha256": file_hash, "line": line_number, "source_type": "model_library"}
                )
        else:
            for line_number, line in enumerate(text.splitlines(), 1):
                match = _CATALOG_MODEL_RE.search(line)
                if match:
                    catalog_entries.append(
                        {"name": match.group(1), "path": str(path), "sha256": file_hash, "line": line_number, "source_type": "model_catalog"}
                    )
    embedded: dict[str, list[dict[str, Any]]] = {}
    for raw_root in example_dirs:
        root = Path(raw_root).resolve()
        paths = [root] if root.is_file() else [path for path in root.rglob("*") if path.suffix.casefold() in {".sxsch", ".sxcmp"}]
        for path in sorted(paths, key=lambda item: str(item).casefold()):
            for name, record in _parse_symbol_blocks(path, source_type="embedded_symbol").items():
                embedded.setdefault(name, []).append(record)
    return {
        "schema_version": "simplis-automation/v2/catalog-inventory",
        "symbol_library_dir": str(symbol_root),
        "symbols": symbols,
        "models": models,
        "catalog_entries": catalog_entries,
        "embedded_symbols": embedded,
    }


def _find_symbol(symbols: dict[str, dict[str, Any]], symbol_name: str) -> dict[str, Any]:
    exact = symbols.get(symbol_name)
    if exact:
        return exact
    matches = [symbol for name, symbol in symbols.items() if name.casefold() == symbol_name.casefold()]
    if len(matches) == 1:
        return matches[0]
    raise CatalogError("Symbol is not present in installed libraries", symbol=symbol_name)


def create_catalog_candidate(
    symbol_library_dir: str | Path,
    symbol_name: str,
    kind: str,
    output_path: str | Path,
    *,
    role: str | None = None,
) -> dict[str, Any]:
    """Extract a pending, human-reviewable catalog item from the local installation."""

    symbol = _find_symbol(parse_symbol_libraries(symbol_library_dir), symbol_name)
    candidate = {
        "schema_version": CATALOG_CANDIDATE_SCHEMA_VERSION,
        "runtime": {"symbol_library_dir": str(Path(symbol_library_dir).resolve())},
        "device": {
            "kind": kind,
            "approval": "pending",
            "role": role or kind,
            "symbol": {"name": symbol["name"], "library": symbol["library"], "sha256": symbol["library_sha256"]},
            "footprint": deepcopy(symbol.get("bbox", {})),
            "pins": [{"name": pin["name"], "index": pin["index"], "domain": "analog", "x": pin["x"], "y": pin["y"]} for pin in symbol["pins"]],
            "properties": {
                name: {"native": name, "default": default, "dimension": "dimensionless", "required": False}
                for name, default in sorted(symbol["properties"].items())
            },
            "ideality": "pending_review",
            "evidence": {"source": "catalog import", "symbol_library_sha256": symbol["library_sha256"]},
            "notes": "Set pin domains, property dimensions/ranges, and ideality before approval.",
        },
    }
    write_yaml(output_path, candidate)
    return candidate


def approve_catalog_candidate(
    candidate_path: str | Path,
    output_path: str | Path,
    *,
    base_catalog_path: str | Path | None = None,
) -> dict[str, Any]:
    """Merge a reviewed candidate into a catalog.  Approval is explicit and local."""

    candidate = load_yaml(candidate_path)
    if candidate.get("schema_version") != CATALOG_CANDIDATE_SCHEMA_VERSION:
        raise CatalogError("Not a v2 catalog candidate", path=str(candidate_path))
    device = _normalize_device(candidate.get("device", {}))
    if device.get("ideality") in {None, "pending_review"}:
        raise CatalogError("Candidate ideality must be declared before approval", kind=device.get("kind"))
    device["approval"] = "approved"
    if base_catalog_path:
        merged = load_catalog(base_catalog_path)
        catalog = {key: deepcopy(value) for key, value in merged.items() if not key.startswith("_") and key != "fingerprint"}
    else:
        catalog = {"schema_version": CATALOG_SCHEMA_VERSION, "runtime": deepcopy(candidate.get("runtime", {})), "devices": []}
    catalog.setdefault("devices", [])
    catalog["devices"] = [item for item in catalog["devices"] if item.get("kind") != device["kind"]]
    catalog["devices"].append(device)
    validated = validate_catalog(catalog)
    persisted = {key: value for key, value in validated.items() if not key.startswith("_")}
    write_yaml(output_path, persisted)
    return persisted


def catalog_runtime_evidence(catalog: dict[str, Any], symbol_library_dir: str | Path | None = None) -> dict[str, Any]:
    """Return structured hash/path evidence; callers decide whether to require it."""

    runtime = catalog.get("runtime", {})
    root = Path(symbol_library_dir or runtime.get("symbol_library_dir", ""))
    evidence: dict[str, Any] = {
        "catalog_fingerprint": catalog.get("fingerprint"),
        "symbol_library_dir": str(root),
        "symbol_library_dir_exists": root.is_dir(),
        "libraries": [],
    }
    for entry in runtime.get("symbol_libraries", []):
        if not isinstance(entry, dict):
            continue
        path = Path(entry.get("path", ""))
        if not path.is_absolute() and root:
            path = root / path
        present = path.is_file()
        actual = sha256_file(path) if present else None
        evidence["libraries"].append(
            {
                "path": str(path),
                "exists": present,
                "expected_sha256": entry.get("sha256"),
                "actual_sha256": actual,
                "matches": present and (not entry.get("sha256") or entry.get("sha256") == actual),
            }
        )
    evidence["ready"] = evidence["symbol_library_dir_exists"] and all(item["matches"] for item in evidence["libraries"])
    return evidence
