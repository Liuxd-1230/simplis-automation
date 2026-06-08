#!/usr/bin/env python3
"""Helpers for exporting and parsing SIMetrix/SIMPLIS waveform text files."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable


PLAIN_VECTOR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
COMPLEX_RE = re.compile(
    r"^\(?\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*,\s*"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*\)?$"
)


def quote_simetrix_string(value: Any) -> str:
    text = str(value)
    if any(ch in text for ch in ("'", "\r", "\n")):
        raise ValueError(f"SIMetrix script string contains unsupported characters: {text!r}")
    return f"'{text}'"


def quote_sxscr_path(value: Any) -> str:
    text = str(value).replace('"', '\\"')
    return f'"{text}"'


def simetrix_vector_expr(vector_name: str) -> str:
    """Return a SIMetrix expression that safely references a vector."""
    if PLAIN_VECTOR_RE.match(vector_name):
        return vector_name
    return f"Vec({quote_simetrix_string(vector_name)})"


def vector_label(vector_name: str) -> str:
    label = vector_name[1:] if vector_name.startswith("#") else vector_name
    label = re.sub(r"[^A-Za-z0-9_]+", "_", label).strip("_")
    if not label:
        label = "vector"
    if label[0].isdigit():
        label = f"n{label}"
    return label


def group_label(group_name: str) -> str:
    text = group_name.lower()
    if "pop" in text:
        return "pop"
    if "ac" in text:
        return "ac"
    label = re.sub(r"[^A-Za-z0-9_]+", "_", group_name).strip("_").lower()
    return label or "group"


def export_filename(output_dir: Path, group_name: str, vector_name: str) -> Path:
    return output_dir / f"{group_label(group_name)}_{vector_label(vector_name).lower()}.txt"


def build_vector_export_script(
    schematic: Path,
    output_dir: Path,
    exports: Iterable[tuple[str, str]],
    status_file: Path | None = None,
) -> str:
    output_dir = Path(output_dir).resolve()
    status_file = Path(status_file).resolve() if status_file is not None else output_dir / "vector_export_status.txt"
    grouped: dict[str, list[str]] = {}
    for group_name, vector_name in exports:
        grouped.setdefault(group_name, []).append(vector_name)

    lines = [
        "Set EchoOn",
        "Set precision = 16",
        f"Let echo_file = OpenEchoFile({quote_simetrix_string(status_file)}, 'w')",
        "Echo start_vector_export=1",
        "Let close_result = CloseEchoFile()",
        f"OpenSchem {quote_sxscr_path(schematic)}",
        "simplis_run",
        "Let sx_exit = GetSIMPLISExitCode()",
    ]
    for group_name, vector_names in grouped.items():
        lines.append(f"SetGroup {group_name}")
        for vector_name in vector_names:
            out_file = export_filename(output_dir, group_name, vector_name)
            label = vector_label(vector_name)
            expr = simetrix_vector_expr(vector_name)
            lines.append(f'Show /force /names "{label}" /file {quote_sxscr_path(out_file)} {expr}')
    lines.extend(
        [
            f"Let echo_file = OpenEchoFile({quote_simetrix_string(status_file)}, 'a')",
            "Echo simplis_exit_code={sx_exit}",
            "Echo vector_export_done=1",
            "Let close_result = CloseEchoFile()",
            "Quit",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_scalar(text: str) -> float | dict[str, float]:
    value = text.strip()
    match = COMPLEX_RE.match(value)
    if match:
        return {"real": float(match.group(1)), "imag": float(match.group(2))}
    return float(value)


def parse_show_file(path: Path) -> dict[str, Any]:
    rows: list[tuple[float, float | dict[str, float]]] = []
    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        header = handle.readline().split()
        if len(header) < 2:
            raise ValueError(f"SIMetrix Show file must start with x/y header: {path}")
        x_name, y_name = header[0], header[1]
        for line in handle:
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                continue
            rows.append((float(parts[0]), parse_scalar(parts[1])))
    return {
        "source": str(Path(path)),
        "x_name": x_name,
        "y_name": y_name,
        "x": [x for x, _ in rows],
        "y": [y for _, y in rows],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build SIMetrix vector export scripts or parse Show output")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("make-export-script")
    p.add_argument("--schematic", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--script", required=True)
    p.add_argument("--status-file")
    p.add_argument("--vector", action="append", required=True, help="Export as group:vector, e.g. simplis_pop1:#VOUT")

    p = sub.add_parser("parse-show")
    p.add_argument("files", nargs="+")
    p.add_argument("--out")

    args = parser.parse_args(argv)
    if args.cmd == "make-export-script":
        exports = []
        for item in args.vector:
            if ":" not in item:
                raise SystemExit(f"Vector export must use group:vector syntax: {item}")
            group_name, vector_name = item.split(":", 1)
            exports.append((group_name, vector_name))
        output_dir = Path(args.out_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        status_file = Path(args.status_file) if args.status_file else output_dir / "vector_export_status.txt"
        status_file.parent.mkdir(parents=True, exist_ok=True)
        text = build_vector_export_script(
            schematic=Path(args.schematic),
            output_dir=output_dir,
            exports=exports,
            status_file=status_file,
        )
        script = Path(args.script)
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(text, encoding="utf-8")
        print(json.dumps({"script": str(script), "status_file": str(status_file)}, indent=2))
        return 0
    if args.cmd == "parse-show":
        data = [parse_show_file(Path(item)) for item in args.files]
        text = json.dumps(data, indent=2, allow_nan=False)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
        return 0
    raise SystemExit(f"Unhandled command: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
