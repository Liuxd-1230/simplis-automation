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
    return all(get_device(catalog, kind).get("ideality") == "ideal" for kind in kinds if kind != "opaque_module")


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
                    pins: list[dict[str, Any]] = []
                    properties: dict[str, str] = {}
                    for line in lines:
                        pin = _PIN_RE.search(line)
                        if pin:
                            pins.append({"name": pin.group(1), "index": int(pin.group(2)), "x": int(pin.group(3)), "y": int(pin.group(4))})
                        prop = _PROPERTY_RE.search(line)
                        if prop:
                            properties[prop.group(1)] = prop.group(2)
                    symbols[current_name] = {
                        "name": current_name,
                        "library": str(library),
                        "library_sha256": sha256_file(library),
                        "pins": sorted(pins, key=lambda item: item["index"]),
                        "properties": properties,
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
