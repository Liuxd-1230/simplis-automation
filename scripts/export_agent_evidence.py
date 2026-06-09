#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from statistics import mean
from typing import Any

try:
    from simetrix_waveforms import parse_show_file
except ModuleNotFoundError:  # pragma: no cover - exercised when imported as scripts.export_agent_evidence
    from .simetrix_waveforms import parse_show_file


NODE_MAP_RE = re.compile(r"^\s*\.node_map\s+(\S+)\s+(\S+)", re.IGNORECASE)
TRIG_GATE_RE = re.compile(r"\bTRIG_GATE\b\s*=\s*(?P<value>\"[^\"]+\"|'[^']+'|\S+)", re.IGNORECASE)
ERROR_MARKER_RE = re.compile(r"(^|\W)(\*+\s*)?errors?(\s*\*+)?(\s*:|\s|\(|$)", re.IGNORECASE)
WARNING_MARKER_RE = re.compile(r"(^|\W)(\*+\s*)?warnings?(\s*\*+)?(\s*:|\s|\(|$)", re.IGNORECASE)
BENIGN_ERROR_RE = re.compile(r"\b(no|0)\s+errors?\b|\berrors?\s*[:=]\s*0\b", re.IGNORECASE)
BENIGN_WARNING_RE = re.compile(r"\b(no|0)\s+warnings?\b|\bwarnings?\s*[:=]\s*0\b", re.IGNORECASE)
LOG_SUFFIXES = {".dbg", ".err", ".health", ".log", ".lst"}
TEXT_VECTOR_SUFFIXES = {".csv", ".txt"}
MAX_CAPTURED_LINES = 200


def parse_scalar(value: str) -> Any:
    text = value.strip()
    lower = text.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        parsed = float(text)
    except ValueError:
        return text
    if math.isfinite(parsed):
        return parsed
    return text


def parse_key_value_metrics(text: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")) or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key:
            values[key] = parse_scalar(value)
    return values


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _sort_paths(paths: list[Path]) -> list[Path]:
    return sorted(paths, key=lambda item: str(item).lower())


def _path_strings(paths: list[Path]) -> list[str]:
    return [str(path) for path in _sort_paths(paths)]


def _is_metrics_file(path: Path) -> bool:
    name = path.name.lower()
    return path.suffix.lower() in {".json", ".txt"} and "metric" in name


def discover_artifacts(root: Path) -> tuple[dict[str, list[Path]], list[str]]:
    empty: dict[str, list[Path]] = {
        "schematics": [],
        "scripts": [],
        "netlists": [],
        "decks": [],
        "logs": [],
        "metrics": [],
        "waveforms": [],
    }
    warnings: list[str] = []
    if not root.exists():
        warnings.append(f"work directory does not exist: {root}")
        return empty, warnings

    if root.is_file():
        all_files = [root]
        warnings.append(f"work path is a file; exporting only that file: {root}")
    else:
        all_files = _sort_paths([item for item in root.rglob("*") if item.is_file()])

    metrics = [item for item in all_files if _is_metrics_file(item)]
    metric_set = set(metrics)
    artifacts = {
        "schematics": [item for item in all_files if item.suffix.lower() == ".sxsch"],
        "scripts": [item for item in all_files if item.suffix.lower() == ".sxscr"],
        "netlists": [item for item in all_files if item.suffix.lower() == ".net"],
        "decks": [item for item in all_files if item.suffix.lower() == ".deck"],
        "logs": [item for item in all_files if item.suffix.lower() in LOG_SUFFIXES],
        "metrics": metrics,
        "waveforms": [
            item
            for item in all_files
            if item.suffix.lower() in TEXT_VECTOR_SUFFIXES and item not in metric_set
        ],
    }
    return {kind: _sort_paths(paths) for kind, paths in artifacts.items()}, warnings


def parse_netlists(files: list[Path]) -> dict[str, Any]:
    node_map: dict[str, str] = {}
    node_map_entries: list[dict[str, Any]] = []
    devices: list[dict[str, Any]] = []
    device_lines = 0

    for file in _sort_paths(files):
        for line_number, line in enumerate(read_text(file).splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            match = NODE_MAP_RE.match(stripped)
            if match:
                net = match.group(1)
                node = match.group(2)
                node_map[net] = node
                node_map_entries.append(
                    {"file": str(file), "line_number": line_number, "net": net, "node": node}
                )
                continue
            if stripped.startswith((".", "*", ";", "+")):
                continue
            device_lines += 1
            if len(devices) < MAX_CAPTURED_LINES:
                devices.append({"file": str(file), "line_number": line_number, "line": stripped})

    return {
        "files": _path_strings(files),
        "node_map": dict(sorted(node_map.items())),
        "node_map_entries": node_map_entries,
        "device_lines": device_lines,
        "devices": devices,
    }


def _clean_assignment_value(value: str) -> str:
    cleaned = value.strip().rstrip(",;")
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        cleaned = cleaned[1:-1]
    return cleaned


def parse_decks(files: list[Path]) -> dict[str, Any]:
    pop_lines: list[str] = []
    tran_lines: list[str] = []
    ac_lines: list[str] = []
    print_lines: list[str] = []
    graph_lines: list[str] = []
    trigger_gates: list[str] = []
    trigger_gate_lines: list[dict[str, Any]] = []
    periodic_op_lines: list[str] = []

    for file in _sort_paths(files):
        for line_number, line in enumerate(read_text(file).splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith(("*", ";", "#")):
                continue
            upper = stripped.upper()
            if upper.startswith(".POP"):
                pop_lines.append(stripped)
            if upper.startswith(".TRAN"):
                tran_lines.append(stripped)
            if upper.startswith(".AC"):
                ac_lines.append(stripped)
            if upper.startswith(".PRINT"):
                print_lines.append(stripped)
            if upper.startswith(".GRAPH"):
                graph_lines.append(stripped)
            if "PERIODIC_OP" in upper:
                periodic_op_lines.append(stripped)
            for match in TRIG_GATE_RE.finditer(stripped):
                value = _clean_assignment_value(match.group("value"))
                if value:
                    trigger_gates.append(value)
                    trigger_gate_lines.append(
                        {"file": str(file), "line_number": line_number, "line": stripped, "value": value}
                    )

    unresolved = [gate for gate in trigger_gates if "{" in gate or "}" in gate]
    return {
        "files": _path_strings(files),
        "pop": pop_lines,
        "tran": tran_lines,
        "ac": ac_lines,
        "has_pop": bool(pop_lines),
        "has_tran": bool(tran_lines),
        "has_ac": bool(ac_lines),
        "print_count": len(print_lines),
        "graph_count": len(graph_lines),
        "print_lines": print_lines,
        "graph_lines": graph_lines,
        "periodic_op_lines": periodic_op_lines,
        "trigger_gates": trigger_gates,
        "trigger_gate_lines": trigger_gate_lines,
        "unresolved_trigger_gates": unresolved,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def parse_metrics(files: list[Path]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    by_file: dict[str, Any] = {}
    warnings: list[str] = []

    for file in _sort_paths(files):
        text = read_text(file)
        parsed: Any
        if file.suffix.lower() == ".json":
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                warnings.append(f"{file}: invalid JSON metrics: {exc}")
                parsed = parse_key_value_metrics(text)
        else:
            parsed = parse_key_value_metrics(text)

        parsed = _json_safe(parsed)
        by_file[str(file)] = parsed
        if isinstance(parsed, dict):
            for key, value in parsed.items():
                if key in values and values[key] != value:
                    warnings.append(
                        f"{file}: metric key {key!r} overwrites previous value {values[key]!r} with {value!r}"
                    )
                values[key] = value

    return {"files": _path_strings(files), "values": _json_safe(values), "by_file": by_file, "warnings": warnings}


def _parse_numeric_row(line: str) -> tuple[float | None, float] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", ";")):
        return None
    pieces = [piece for piece in re.split(r"[\s,]+", stripped) if piece]
    if not pieces:
        return None
    numeric: list[float] = []
    for piece in pieces:
        try:
            value = float(piece)
        except ValueError:
            continue
        if math.isfinite(value):
            numeric.append(value)
    if not numeric:
        return None
    y_value = numeric[-1]
    x_value = numeric[0] if len(numeric) > 1 else None
    return x_value, y_value


def _looks_like_show_header(file: Path) -> bool:
    try:
        with file.open("r", encoding="utf-8", errors="replace") as handle:
            header = handle.readline().split()
    except OSError:
        return False
    if len(header) < 2:
        return False
    return _parse_numeric_row(" ".join(header[:2])) is None


def _phase_deg(real: float, imag: float) -> float:
    return math.degrees(math.atan2(imag, real))


def _summarize_series(prefix: str, values: list[float], result: dict[str, Any]) -> None:
    if not values:
        return
    result.update(
        {
            f"{prefix}_min": min(values),
            f"{prefix}_max": max(values),
            f"{prefix}_mean": mean(values),
            f"{prefix}_first": values[0],
            f"{prefix}_last": values[-1],
        }
    )


def _summarize_show_data(data: dict[str, Any]) -> dict[str, Any]:
    x_values = [value for value in data.get("x", []) if isinstance(value, (int, float)) and math.isfinite(value)]
    y_values = data.get("y", [])
    result: dict[str, Any] = {
        "samples": len(y_values),
        "skipped_lines": 0,
        "x_name": data.get("x_name"),
        "y_name": data.get("y_name"),
    }
    if x_values:
        result.update({"x_min": min(x_values), "x_max": max(x_values)})
    if not y_values:
        return result

    if all(isinstance(value, (int, float)) for value in y_values):
        real_values = [float(value) for value in y_values if math.isfinite(float(value))]
        result["kind"] = "real"
        if real_values:
            result.update(
                {
                    "min": min(real_values),
                    "max": max(real_values),
                    "mean": mean(real_values),
                    "first": real_values[0],
                    "last": real_values[-1],
                }
            )
        return result

    complex_values = [
        {"real": float(value["real"]), "imag": float(value["imag"])}
        for value in y_values
        if isinstance(value, dict)
        and isinstance(value.get("real"), (int, float))
        and isinstance(value.get("imag"), (int, float))
        and math.isfinite(float(value["real"]))
        and math.isfinite(float(value["imag"]))
    ]
    result["kind"] = "complex"
    result["samples"] = len(complex_values)
    if not complex_values:
        return result

    real_values = [value["real"] for value in complex_values]
    imag_values = [value["imag"] for value in complex_values]
    magnitude_values = [math.hypot(value["real"], value["imag"]) for value in complex_values]
    phase_values = [_phase_deg(value["real"], value["imag"]) for value in complex_values]
    result.update({"first_complex": complex_values[0], "last_complex": complex_values[-1]})
    _summarize_series("real", real_values, result)
    _summarize_series("imag", imag_values, result)
    _summarize_series("magnitude", magnitude_values, result)
    _summarize_series("phase_deg", phase_values, result)
    return result


def parse_waveform_file(file: Path) -> dict[str, Any]:
    if _looks_like_show_header(file):
        try:
            return _summarize_show_data(parse_show_file(file))
        except (OSError, ValueError):
            pass

    y_values: list[float] = []
    x_values: list[float] = []
    skipped_lines = 0

    for line in read_text(file).splitlines():
        parsed = _parse_numeric_row(line)
        if parsed is None:
            if line.strip():
                skipped_lines += 1
            continue
        x_value, y_value = parsed
        if x_value is not None:
            x_values.append(x_value)
        y_values.append(y_value)

    result: dict[str, Any] = {"samples": len(y_values), "skipped_lines": skipped_lines}
    if y_values:
        result.update(
            {
                "min": min(y_values),
                "max": max(y_values),
                "mean": mean(y_values),
                "first": y_values[0],
                "last": y_values[-1],
            }
        )
    if x_values:
        result.update({"x_min": min(x_values), "x_max": max(x_values)})
    return result


def parse_waveforms(files: list[Path]) -> dict[str, Any]:
    vectors: dict[str, Any] = {}
    for file in _sort_paths(files):
        key = file.name
        if key in vectors:
            key = str(file)
        vectors[key] = parse_waveform_file(file)
    return {"files": _path_strings(files), "vectors": vectors}


def parse_logs(files: list[Path]) -> dict[str, Any]:
    error_entries: list[dict[str, Any]] = []
    warning_entries: list[dict[str, Any]] = []

    for file in _sort_paths(files):
        for line_number, line in enumerate(read_text(file).splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            entry = {"file": str(file), "line_number": line_number, "line": stripped}
            if _is_log_error(stripped):
                error_entries.append(entry)
            elif _is_log_warning(stripped):
                warning_entries.append(entry)

    return {
        "files": _path_strings(files),
        "errors": [entry["line"] for entry in error_entries[:MAX_CAPTURED_LINES]],
        "warnings": [entry["line"] for entry in warning_entries[:MAX_CAPTURED_LINES]],
        "error_entries": error_entries[:MAX_CAPTURED_LINES],
        "warning_entries": warning_entries[:MAX_CAPTURED_LINES],
        "error_count": len(error_entries),
        "warning_count": len(warning_entries),
    }


def _is_log_error(line: str) -> bool:
    return bool(ERROR_MARKER_RE.search(line)) and not BENIGN_ERROR_RE.search(line)


def _is_log_warning(line: str) -> bool:
    return bool(WARNING_MARKER_RE.search(line)) and not BENIGN_WARNING_RE.search(line)


def _redaction_variants(root: Path) -> list[str]:
    text = str(root)
    variants = {text, text.replace("\\", "/"), text.replace("/", "\\")}
    try:
        resolved = str(root.resolve(strict=False))
    except OSError:
        resolved = text
    variants.update({resolved, resolved.replace("\\", "/"), resolved.replace("/", "\\")})
    return sorted({item.rstrip("\\/") for item in variants if item and item not in {".", "/"}}, key=len, reverse=True)


def redact_path(text: str, *, roots: list[Path]) -> str:
    result = text
    variants: list[str] = []
    for root in roots:
        variants.extend(_redaction_variants(root))
    for variant in sorted(set(variants), key=len, reverse=True):
        result = re.sub(re.escape(variant), "<redacted>", result, flags=re.IGNORECASE)
    return result


def _redact_value(value: Any, *, roots: list[Path]) -> Any:
    if isinstance(value, str):
        return redact_path(value, roots=roots)
    if isinstance(value, list):
        return [_redact_value(item, roots=roots) for item in value]
    if isinstance(value, dict):
        return {
            _redact_value(str(key), roots=roots): _redact_value(item, roots=roots)
            for key, item in value.items()
        }
    return value


def _agent_summary(report: dict[str, Any], artifacts: dict[str, list[Path]]) -> list[str]:
    summary = [
        (
            f"Found {len(artifacts['netlists'])} netlist file(s), "
            f"{len(artifacts['decks'])} deck file(s), and {len(artifacts['logs'])} log file(s)."
        )
    ]
    if report["netlist"]["node_map"]:
        summary.append(f"Netlist node_map entries: {len(report['netlist']['node_map'])}")
    if report["deck"]["unresolved_trigger_gates"]:
        summary.append(f"Unresolved trigger gates: {len(report['deck']['unresolved_trigger_gates'])}")
    if report["simulator_logs"]["error_count"]:
        summary.append(f"Errors: {report['simulator_logs']['error_count']}")
    if report["simulator_logs"]["warning_count"]:
        summary.append(f"Warnings: {report['simulator_logs']['warning_count']}")
    if report["metrics"]["values"].get("failed") is True:
        reason = report["metrics"]["values"].get("reason", "no reason provided")
        summary.append(f"Metrics failed: {reason}")
    return summary


def export_evidence(root: Path, *, redact_paths: bool = False) -> dict[str, Any]:
    artifacts, warnings = discover_artifacts(root)
    report: dict[str, Any] = {
        "root": str(root),
        "warnings": warnings,
        "artifacts": {kind: _path_strings(files) for kind, files in artifacts.items()},
        "netlist": parse_netlists(artifacts["netlists"]),
        "deck": parse_decks(artifacts["decks"]),
        "metrics": parse_metrics(artifacts["metrics"]),
        "waveforms": parse_waveforms(artifacts["waveforms"]),
        "simulator_logs": parse_logs(artifacts["logs"]),
    }
    report["agent_summary"] = _agent_summary(report, artifacts)
    safe_report = _json_safe(report)
    if redact_paths:
        roots = [root.resolve(strict=False), root.parent.resolve(strict=False)]
        return _redact_value(safe_report, roots=roots)
    return safe_report


def write_summary(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# SIMPLIS Agent Evidence",
        "",
        f"- Root: `{report['root']}`",
        f"- Warnings: {len(report.get('warnings', []))}",
        "",
        "## Agent Summary",
    ]
    summary = report.get("agent_summary") or []
    if summary:
        lines.extend(f"- {item}" for item in summary)
    else:
        lines.append("- No notable evidence found")

    lines.extend(
        [
            "",
            "## Netlist",
            f"- Files: {len(report['netlist']['files'])}",
            f"- Nodes: {len(report['netlist']['node_map'])}",
            f"- Device lines: {report['netlist']['device_lines']}",
            "",
            "## Deck",
            f"- POP: {report['deck']['has_pop']}",
            f"- TRAN: {report['deck']['has_tran']}",
            f"- AC: {report['deck']['has_ac']}",
            f"- PRINT lines: {report['deck']['print_count']}",
            f"- GRAPH lines: {report['deck']['graph_count']}",
            f"- Trigger gates: {len(report['deck']['trigger_gates'])}",
            "",
            "## Metrics",
            f"- Files: {len(report['metrics']['files'])}",
            f"- Values: {len(report['metrics']['values'])}",
            "",
            "## Waveforms",
            f"- Files: {len(report['waveforms']['files'])}",
            f"- Vectors: {len(report['waveforms']['vectors'])}",
            "",
            "## Logs",
            f"- Errors: {report['simulator_logs']['error_count']}",
            f"- Warnings: {report['simulator_logs']['warning_count']}",
        ]
    )

    if report.get("warnings"):
        lines.extend(["", "## Warnings"])
        lines.extend(f"- {warning}" for warning in report["warnings"])

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export agent-readable evidence from a SIMPLIS work directory.")
    parser.add_argument("--work-dir", required=True, type=Path, help="Generated SIMPLIS work directory")
    parser.add_argument("--out", required=True, type=Path, help="Output JSON report path")
    parser.add_argument("--summary-md", type=Path, help="Optional Markdown summary path")
    parser.add_argument("--redact-paths", action="store_true", help="Redact absolute work directory paths")
    args = parser.parse_args(argv)

    report = export_evidence(args.work_dir, redact_paths=args.redact_paths)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    if args.summary_md:
        write_summary(report, args.summary_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
