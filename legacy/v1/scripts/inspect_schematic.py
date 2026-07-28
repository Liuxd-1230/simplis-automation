#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


ATTRIBUTES_RE = re.compile(r"^\s*Attributes\s+(?P<attrs>.*)$")
PROPERTY_RE = re.compile(
    r'^\s*Property\s+name="(?P<name>(?:\\.|[^"\\])*)"\s+value="(?P<value>(?:\\.|[^"\\])*)"(?:\s|$)'
)
WIRE_RE = re.compile(r"^\s*Wire\s+(?P<attrs>.*)$")
KEY_VALUE_RE = re.compile(r'(?P<key>\w+)=("(?P<quoted>(?:\\.|[^"\\])*)"|(?P<bare>\S+))')
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")
COORDINATE_KEYS = {"x", "y", "x1", "y1", "x2", "y2"}
FLAG_KEYS = {"selected", "protected", "disabled"}
MODULE_EXCLUDED_PROPERTY_KEYS = {
    "graph_name",
    "icanal",
    "editpropscript",
    "init_script",
    "model",
    "modulename",
    "minus",
    "netnames",
    "netnamesdetail",
    "netname",
    "paramsscript",
    "param_model_name",
    "persistence",
    "plus",
    "probe_disabled",
    "ref",
    "scterm",
    "simplis_template",
    "simulator",
    "stylenormal",
    "styleselected",
    "template",
    "useic",
    "value",
    "valuescript",
}
MODULE_EXCLUDED_PROPERTY_PREFIXES = (
    "axis",
    "curve_",
    "gain_",
    "measure_spec",
    "phase_",
    "plot_",
    "port_",
    "vertical_",
)
PORT_PROPERTY_NAMES = {"Netnames", "NetnamesDetail", "netname", "NetName", "VALUE", "plus", "minus"}

ROLE_SYMBOLS: dict[str, dict[str, tuple[str, ...] | str]] = {
    "voltage_source": {"default": "dc_source", "aliases": ()},
    "current_source": {"default": "dc_isource", "aliases": ("iwave_v2",)},
    "resistor": {"default": "res", "aliases": ("websim_resz1", "resz")},
    "capacitor": {"default": "cap", "aliases": ("websim_cap", "websim_ecap1")},
    "inductor": {"default": "ind", "aliases": ("websim_ind1", "websim_cind1")},
    "ground": {"default": "gnd", "aliases": ("gnd2",)},
    "net_terminal": {"default": "term", "aliases": ()},
    "power_switch": {"default": "simplis_prim_vcswitch", "aliases": ("websim_pwr_nmos", "PWRNMOS_wCOSS")},
    "body_diode": {"default": "dio", "aliases": ()},
    "voltage_probe": {"default": "probev_new", "aliases": ("Differential_Voltage_Probe",)},
    "bode_probe": {"default": "Bode_Probe2", "aliases": ("Websim_Bode_Probe",)},
    "current_probe": {"default": "InlineCurrentProbe", "aliases": ("Power_Probe",)},
    "pop_trigger": {"default": "PERIODIC_OP_V8", "aliases": ()},
    "pwm_source": {"default": "vwave_v2", "aliases": ()},
    "digital_comparator": {"default": "SIMPLIS_DIGI1_COMP_Y", "aliases": ()},
    "digital_buffer": {"default": "SIMPLIS_DIGI1_BUF_Y", "aliases": ()},
    "digital_latch": {"default": "SIMPLIS_DIGI1_SRLATCH_1_NONE_Y", "aliases": ()},
    "digital_and": {"default": "SIMPLIS_DIGI1_AND_2I0_Y", "aliases": ()},
    "digital_or": {"default": "SIMPLIS_DIGI1_OR_2I0_Y", "aliases": ()},
}

PROBE_SYMBOLS = {"probev_new", "InlineCurrentProbe", "Bode_Probe2"}
REF_OPTIONAL_SYMBOLS = {"gnd", "gnd2", "term", "modport", "Free_text"}


def truncate_value(value: str, *, limit: int = 160) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


def _coerce_attr(key: str, value: str) -> Any:
    if key in COORDINATE_KEYS:
        try:
            return int(value)
        except ValueError:
            return value
    if key in FLAG_KEYS:
        if value in {"0", "1"}:
            return bool(int(value))
        return value
    return value


def parse_attrs(text: str) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    for match in KEY_VALUE_RE.finditer(text):
        value = match.group("quoted") if match.group("quoted") is not None else match.group("bare")
        attrs[match.group("key")] = _coerce_attr(match.group("key"), _decode_quoted_value(value))
    return attrs


def _decode_quoted_value(value: str) -> str:
    return value.replace(r"\"", '"').replace(r"\\", "\\")


def _file_metadata(source: Path, text: str, *, file_format: str = "text", size_bytes: int | None = None) -> dict[str, Any]:
    return {
        "path": str(source),
        "name": source.name,
        "kind": source.suffix.lower().lstrip("."),
        "suffix": source.suffix.lower(),
        "format": file_format,
        "line_count": len(text.splitlines()),
        "size_chars": len(text),
        "size_bytes": size_bytes if size_bytes is not None else len(text.encode("utf-8")),
    }


def _new_instance(attrs: dict[str, Any]) -> dict[str, Any]:
    symbol = str(attrs.get("name", ""))
    return {
        "symbol": symbol,
        "ref": None,
        "x": attrs.get("x"),
        "y": attrs.get("y"),
        "orient": attrs.get("orient", "N0"),
        "selected": attrs.get("selected"),
        "protected": attrs.get("protected"),
        "disabled": attrs.get("disabled"),
        "attributes": attrs,
        "properties": {},
    }


def _new_component(attrs: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": str(attrs.get("path", "")),
        "name": Path(str(attrs.get("path", ""))).stem,
        "ref": None,
        "x": attrs.get("x"),
        "y": attrs.get("y"),
        "orient": attrs.get("orient", "N0"),
        "selected": attrs.get("selected"),
        "protected": attrs.get("protected"),
        "disabled": attrs.get("disabled"),
        "attributes": attrs,
        "properties": {},
        "tunable_properties": [],
    }


def _is_ref_optional_symbol(symbol: str) -> bool:
    return symbol in REF_OPTIONAL_SYMBOLS or "probe" in symbol.lower()


def _symbol_catalog(attrs: dict[str, Any]) -> dict[str, str] | None:
    if attrs.get("type") is not None or "name" not in attrs:
        return None
    if "format" not in attrs and "revision" not in attrs:
        return None
    catalog: dict[str, str] = {}
    if attrs.get("description"):
        catalog["description"] = str(attrs["description"])
    if attrs.get("catalog"):
        catalog["catalog"] = str(attrs["catalog"])
    return catalog


def _is_tunable_property(property_name: str) -> bool:
    key = property_name.lower()
    return key not in MODULE_EXCLUDED_PROPERTY_KEYS and not key.startswith(MODULE_EXCLUDED_PROPERTY_PREFIXES)


def _safe_placeholder_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name)) and _is_tunable_property(name)


def _property_summary(instances: list[dict[str, Any]], *, max_examples: int = 5) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    canonical_names: dict[str, str] = {}
    for instance in instances:
        for name, value in instance["properties"].items():
            key = name.lower()
            canonical_name = canonical_names.setdefault(key, name)
            item = summary.setdefault(canonical_name, {"count": 0, "examples": []})
            item["count"] += 1
            example = truncate_value(str(value))
            if example not in item["examples"] and len(item["examples"]) < max_examples:
                item["examples"].append(example)
    return dict(sorted(summary.items()))


def _wire_stats(wires: list[dict[str, Any]]) -> dict[str, Any]:
    xs: list[int] = []
    ys: list[int] = []
    horizontal = 0
    vertical = 0
    diagonal_or_unknown = 0
    total_manhattan_length = 0

    for wire in wires:
        coords = [wire.get(key) for key in ("x1", "y1", "x2", "y2")]
        if not all(isinstance(coord, int) for coord in coords):
            diagonal_or_unknown += 1
            continue
        x1, y1, x2, y2 = coords
        xs.extend([x1, x2])
        ys.extend([y1, y2])
        if y1 == y2:
            horizontal += 1
        elif x1 == x2:
            vertical += 1
        else:
            diagonal_or_unknown += 1
        total_manhattan_length += abs(x2 - x1) + abs(y2 - y1)

    stats: dict[str, Any] = {
        "horizontal": horizontal,
        "vertical": vertical,
        "diagonal_or_unknown": diagonal_or_unknown,
        "total_manhattan_length": total_manhattan_length,
    }
    if xs and ys:
        stats["bbox"] = {"min_x": min(xs), "min_y": min(ys), "max_x": max(xs), "max_y": max(ys)}
    else:
        stats["bbox"] = None
    return stats


def _tunable_properties(instances: list[dict[str, Any]]) -> list[str]:
    tunables: set[str] = set()
    for instance in instances:
        for property_name, value in instance["properties"].items():
            if _is_tunable_property(property_name):
                tunables.add(property_name)
            for placeholder in PLACEHOLDER_RE.findall(str(value)):
                if _safe_placeholder_name(placeholder):
                    tunables.add(placeholder)
    return sorted(tunables)


def _module_ports(instances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ports: list[dict[str, Any]] = []
    for instance in instances:
        if instance["symbol"] != "modport":
            continue
        properties = {
            name: value
            for name, value in instance["properties"].items()
            if name in PORT_PROPERTY_NAMES
        }
        ports.append(
            {
                "ref": instance.get("ref"),
                "x": instance.get("x"),
                "y": instance.get("y"),
                "orient": instance.get("orient"),
                "properties": properties,
            }
        )
    return ports


def _apply_netnames_to_current(line: str, current: dict[str, Any] | None) -> bool:
    if current is None or not line.strip().startswith("Netnames"):
        return False
    netnames = parse_attrs(line.strip()[len("Netnames") :])
    current["properties"]["NetnamesDetail"] = netnames
    return True


def _terms(instances: list[dict[str, Any]]) -> Counter[str]:
    terms: Counter[str] = Counter()
    for instance in instances:
        if instance["symbol"] != "term":
            continue
        value = instance["properties"].get("VALUE")
        if value:
            terms[str(value)] += 1
    return terms


def _probes(instances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        instance
        for instance in instances
        if instance["symbol"] in PROBE_SYMBOLS or "probe" in instance["symbol"].lower()
    ]


def _module_report(source: Path, symbols: Counter[str], instances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if source.suffix.lower() != ".sxcmp":
        return []
    ports = _module_ports(instances)
    return [
        {
            "path": str(source),
            "name": source.stem,
            "symbols": dict(symbols),
            "instance_count": len(instances),
            "tunable_properties": _tunable_properties(instances),
            "ports": ports,
            "exported_pins": ports,
        }
    ]


def _empty_file_report(source: Path, data: bytes, *, warning: str) -> dict[str, Any]:
    return {
        "file": _file_metadata(source, "", file_format="binary", size_bytes=len(data)),
        "symbols": {},
        "symbol_catalogs": {},
        "instances": [],
        "property_names": [],
        "properties": {},
        "wires": {
            "count": 0,
            "nets": {},
            "items": [],
            "horizontal": 0,
            "vertical": 0,
            "diagonal_or_unknown": 0,
            "total_manhattan_length": 0,
            "bbox": None,
        },
        "terminals": {"terms": {}, "grounds": 0},
        "probes": [],
        "modules": [],
        "warnings": [warning],
    }


def parse_schematic_file(file: Path) -> dict[str, Any]:
    data = file.read_bytes()
    if b"\x00" in data:
        return _empty_file_report(
            file,
            data,
            warning=(
                "unsupported binary SIMetrix file; save or export this .sxsch/.sxcmp as text "
                "before using it as parsed evidence"
            ),
        )
    return parse_schematic_text(data.decode("utf-8", errors="replace"), source=file)


def _component_modules(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    modules: list[dict[str, Any]] = []
    for component in components:
        tunables = sorted(
            property_name
            for property_name in component["properties"]
            if _is_tunable_property(property_name)
        )
        module = dict(component)
        module["tunable_properties"] = tunables
        modules.append(module)
    return modules


def parse_schematic_text(text: str, *, source: Path) -> dict[str, Any]:
    instances: list[dict[str, Any]] = []
    components: list[dict[str, Any]] = []
    symbol_catalogs: dict[str, dict[str, str]] = {}
    wires: list[dict[str, Any]] = []
    warnings: list[str] = []
    current: dict[str, Any] | None = None
    current_kind: str | None = None
    in_instance = False

    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped == ".Instance":
            in_instance = True
            current = None
            current_kind = None
            continue
        if stripped == ".EndInstance":
            if in_instance and current is None:
                warnings.append(f"line {line_number}: instance block ended without symbol attributes")
            elif current_kind == "symbol" and current is not None and current.get("ref") is None:
                symbol = str(current.get("symbol", ""))
                if not _is_ref_optional_symbol(symbol):
                    warnings.append(f"line {line_number}: symbol instance missing ref for {symbol}")
            elif current_kind == "component" and current is not None and current.get("ref") is None:
                warnings.append(f"line {line_number}: component instance missing ref for {current.get('path')}")
            in_instance = False
            current = None
            current_kind = None
            continue

        attributes_match = ATTRIBUTES_RE.match(line)
        if attributes_match:
            attrs = parse_attrs(attributes_match.group("attrs"))
            if attrs.get("type") == "symbol":
                if "name" not in attrs:
                    warnings.append(f"line {line_number}: symbol attributes missing name")
                    continue
                current = _new_instance(attrs)
                current_kind = "symbol"
                instances.append(current)
            elif attrs.get("type") == "component":
                current = _new_component(attrs)
                current_kind = "component"
                components.append(current)
            else:
                catalog = _symbol_catalog(attrs)
                if catalog is not None:
                    symbol_catalogs[str(attrs["name"])] = catalog
            continue

        property_match = PROPERTY_RE.match(line)
        if property_match:
            if current is None:
                if in_instance:
                    warnings.append(f"line {line_number}: property outside symbol instance ignored")
                continue
            name = _decode_quoted_value(property_match.group("name"))
            value = _decode_quoted_value(property_match.group("value"))
            current["properties"][name] = value
            if current_kind == "symbol" and name.lower() == "ref":
                current["ref"] = value
            elif current_kind == "component" and name.lower() == "ref":
                current["ref"] = value
            continue

        wire_match = WIRE_RE.match(line)
        if wire_match:
            wires.append(parse_attrs(wire_match.group("attrs")))
            continue

        if _apply_netnames_to_current(line, current):
            continue

        if in_instance and stripped.startswith("UnsupportedThing"):
            warnings.append(f"line {line_number}: unsupported line inside instance: {stripped}")
            continue

    symbols = Counter(str(instance["symbol"]) for instance in instances)
    nets: Counter[str] = Counter(str(wire["net"]) for wire in wires if "net" in wire)
    terms = _terms(instances)
    grounds = sum(count for symbol, count in symbols.items() if symbol in {"gnd", "gnd2"})
    wire_report = {"count": len(wires), "nets": dict(nets), "items": wires}
    wire_report.update(_wire_stats(wires))

    return {
        "file": _file_metadata(source, text),
        "symbols": dict(symbols),
        "symbol_catalogs": symbol_catalogs,
        "instances": instances,
        "property_names": sorted({name for instance in instances for name in instance["properties"]}),
        "properties": _property_summary(instances),
        "wires": wire_report,
        "terminals": {"terms": dict(terms), "grounds": grounds},
        "probes": _probes(instances),
        "modules": _module_report(source, symbols, instances) + _component_modules(components),
        "warnings": warnings,
    }


def discover_files(path: Path) -> list[Path]:
    if not path.exists():
        raise FileNotFoundError(path)
    if path.is_file():
        return [path] if path.suffix.lower() in {".sxsch", ".sxcmp"} else []
    return sorted(file for file in path.rglob("*") if file.suffix.lower() in {".sxsch", ".sxcmp"})


def canonical_symbols_from_counts(counts: Counter[str]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for role, role_symbols in ROLE_SYMBOLS.items():
        default = str(role_symbols["default"])
        aliases = tuple(str(alias) for alias in role_symbols["aliases"])
        observed_aliases = [symbol for symbol in aliases if counts.get(symbol, 0)]
        default_observed = counts.get(default, 0) > 0
        if default_observed or observed_aliases:
            observed = default if default_observed else observed_aliases[0]
            canonical[role] = {
                "symbol": default,
                "count": counts.get(default, 0),
                "aliases": observed_aliases,
                "default_observed": default_observed,
                "observed": observed,
                "observed_count": counts[observed],
                "observed_aliases": observed_aliases,
                "observed_symbols": [
                    {"symbol": symbol, "count": counts[symbol]}
                    for symbol in (default, *aliases)
                    if counts.get(symbol, 0)
                ],
            }
    return canonical


def inspect_path(path: Path) -> dict[str, Any]:
    files = discover_files(path)
    file_reports = [parse_schematic_file(file) for file in files]

    symbols: Counter[str] = Counter()
    symbol_files: dict[str, set[str]] = {}
    symbol_categories: dict[str, set[str]] = {}
    symbol_descriptions: dict[str, set[str]] = {}
    modules: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    for report in file_reports:
        symbols.update(report["symbols"])
        for symbol in report["symbols"]:
            symbol_files.setdefault(symbol, set()).add(report["file"]["path"])
            catalog = report.get("symbol_catalogs", {}).get(symbol)
            if catalog:
                if catalog.get("catalog"):
                    symbol_categories.setdefault(symbol, set()).add(catalog["catalog"])
                if catalog.get("description"):
                    symbol_descriptions.setdefault(symbol, set()).add(catalog["description"])
        modules.extend(report["modules"])
        warnings.extend({"path": report["file"]["path"], "message": message} for message in report["warnings"])

    symbol_reports = {
        symbol: {
            "count": count,
            "files": sorted(symbol_files.get(symbol, set())),
            "categories": sorted(symbol_categories.get(symbol, set())),
            "descriptions": sorted(symbol_descriptions.get(symbol, set())),
        }
        for symbol, count in symbols.most_common()
    }

    return {
        "root": str(path),
        "totals": {
            "files": len(file_reports),
            "instances": sum(len(report["instances"]) for report in file_reports),
            "wires": sum(report["wires"]["count"] for report in file_reports),
        },
        "files": file_reports,
        "symbols": symbol_reports,
        "canonical_symbols": canonical_symbols_from_counts(symbols),
        "modules": modules,
        "warnings": warnings,
    }


def write_summary(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# SIMPLIS Inspection Summary",
        "",
        f"- Root: `{report['root']}`",
        f"- Files: {report['totals']['files']}",
        f"- Instances: {report['totals']['instances']}",
        f"- Wires: {report['totals']['wires']}",
        f"- Warnings: {len(report['warnings'])}",
        "",
        "## Canonical Symbols",
    ]
    if report["canonical_symbols"]:
        for role, data in sorted(report["canonical_symbols"].items()):
            lines.append(f"- `{role}`: `{data['symbol']}` ({data['count']})")
    else:
        lines.append("- None observed")

    lines.extend(["", "## Top Symbols"])
    if report["symbols"]:
        for symbol, data in list(report["symbols"].items())[:30]:
            lines.append(f"- `{symbol}`: {data['count']}")
    else:
        lines.append("- None observed")

    if report["modules"]:
        lines.extend(["", "## Modules"])
        for module in report["modules"]:
            tunables = ", ".join(module["tunable_properties"]) or "none detected"
            lines.append(f"- `{module['path']}`: {tunables}")

    if report["warnings"]:
        lines.extend(["", "## Warnings"])
        for warning in report["warnings"]:
            lines.append(f"- `{warning['path']}`: {warning['message']}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect SIMPLIS .sxsch/.sxcmp files.")
    parser.add_argument("--input", required=True, type=Path, help="Input .sxsch/.sxcmp file or directory")
    parser.add_argument("--out", required=True, type=Path, help="Output JSON report path")
    parser.add_argument("--summary-md", type=Path, help="Optional Markdown summary path")
    args = parser.parse_args(argv)

    report = inspect_path(args.input)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.summary_md:
        write_summary(report, args.summary_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
