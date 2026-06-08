#!/usr/bin/env python3
"""Small Windows-oriented helpers for SIMetrix/SIMPLIS automation."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from runtime_config import resolve_simetrix_exe, runtime_config_status
from schematic_generator import generate_from_config, quote_simetrix_string
from simetrix_waveforms import build_vector_export_script, parse_show_file


def simetrix_path(value: str | None, config_path: str | None = None) -> Path:
    path = resolve_simetrix_exe(value, config_path=config_path)
    if path is None:
        raise SystemExit("SIMetrix executable path is not configured")
    return path


def run_script(args: argparse.Namespace) -> int:
    exe = simetrix_path(args.simetrix_exe, args.runtime_config)
    script = Path(args.script).resolve()
    if not script.exists():
        raise SystemExit(f"Script not found: {script}")
    cmd = [str(exe)]
    if args.interactive:
        cmd.append("/i")
    cmd += ["/s", str(script)]
    print(json.dumps({"cmd": cmd}, indent=2))
    if args.dry_run:
        return 0
    try:
        proc = subprocess.run(cmd, cwd=str(script.parent), check=False, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        return 124
    missing = [str(Path(item).resolve()) for item in args.expect if not Path(item).resolve().exists()]
    if missing:
        print(json.dumps({"missing_expected_outputs": missing}, indent=2), file=sys.stderr)
        return 2
    return proc.returncode


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def create_concept(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir).resolve()
    schematic = out_dir / "simplis_automation_concept.sxsch"
    script = out_dir / "create_simplis_automation_concept.sxscr"
    lines = [
        'NewSchem /newWindow /simulator SIMPLIS simplis_automation_concept',
        'Wire /loc -600 -100 -200 -100',
        'Wire /loc -200 -100 -200 100',
        'Wire /loc -200 100 100 100',
        'Wire /loc 100 100 100 -100',
        'Wire /loc 100 -100 -600 -100',
        'Wire /loc -450 260 -150 260',
        'Wire /loc -150 260 -150 420',
        'Wire /loc -150 420 300 420',
        f'SaveAs /force "{schematic}"',
    ]
    if not args.keep_open:
        lines.append("Quit")
    write_text(script, "\n".join(lines) + "\n")
    print(json.dumps({"script": str(script), "schematic": str(schematic)}, indent=2))
    if args.no_run:
        return 0
    ns = argparse.Namespace(
        simetrix_exe=args.simetrix_exe,
        script=str(script),
        interactive=args.keep_open,
        dry_run=args.dry_run,
        timeout=args.timeout,
        expect=[str(schematic)],
        runtime_config=args.runtime_config,
    )
    return run_script(ns)


def make_run_schematic(args: argparse.Namespace) -> int:
    schematic = Path(args.schematic).resolve()
    if not schematic.exists():
        raise SystemExit(f"Schematic not found: {schematic}")
    out = Path(args.out).resolve()
    lines = [
        f'OpenSchem "{schematic}"',
        "simplis_run",
    ]
    if args.save_after:
        lines.append("Save /all")
    if not args.keep_open:
        lines.append("Quit")
    write_text(out, "\n".join(lines) + "\n")
    print(json.dumps({"script": str(out), "schematic": str(schematic)}, indent=2))
    return 0


def make_run_deck(args: argparse.Namespace) -> int:
    deck = Path(args.deck).resolve()
    out = Path(args.out).resolve()
    lines: list[str]
    if args.netlist:
        net = Path(args.netlist).resolve()
        lines = [
            f'Netlist /simplis "{net}"',
            f'PreProcessNetlist "{net}" "{deck}"',
            f'RunSIMPLIS /fresh "{deck}"',
        ]
    else:
        if not deck.exists():
            raise SystemExit(f"Deck not found: {deck}")
        lines = [f'RunSIMPLIS /fresh "{deck}"']
    if not args.keep_open:
        lines.append("Quit")
    write_text(out, "\n".join(lines) + "\n")
    print(json.dumps({"script": str(out)}, indent=2))
    return 0


def make_metric_writer(args: argparse.Namespace) -> int:
    out = Path(args.out).resolve()
    result = Path(args.result_json).resolve()
    metric_items = []
    for item in args.metric:
        if "=" not in item:
            raise SystemExit(f"Metric must use name=value syntax: {item}")
        name, value = item.split("=", 1)
        metric_items.append((name, value))
    lines = [
        f"Let echo_file = OpenEchoFile({quote_simetrix_string(result)}, {quote_simetrix_string('w')})",
    ]
    for name, value in metric_items:
        lines.append(f"Echo {name}={value}")
    lines.append("Let close_result = CloseEchoFile()")
    if not args.keep_open:
        lines.append("Quit")
    write_text(out, "\n".join(lines) + "\n")
    print(json.dumps({"script": str(out), "result_json": str(result)}, indent=2))
    return 0


def make_vector_export(args: argparse.Namespace) -> int:
    exports = []
    for item in args.vector:
        if ":" not in item:
            raise SystemExit(f"Vector export must use group:vector syntax: {item}")
        group_name, vector_name = item.split(":", 1)
        exports.append((group_name, vector_name))
    script = Path(args.out).resolve()
    output_dir = Path(args.out_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    status_file = Path(args.status_file).resolve() if args.status_file else output_dir / "vector_export_status.txt"
    status_file.parent.mkdir(parents=True, exist_ok=True)
    text = build_vector_export_script(
        schematic=Path(args.schematic).resolve(),
        output_dir=output_dir,
        exports=exports,
        status_file=status_file,
    )
    write_text(script, text)
    print(json.dumps({"script": str(script), "status_file": str(status_file)}, indent=2))
    return 0


def parse_show(args: argparse.Namespace) -> int:
    data = [parse_show_file(Path(item).resolve()) for item in args.files]
    text = json.dumps(data, indent=2, allow_nan=False)
    if args.out:
        write_text(Path(args.out).resolve(), text + "\n")
    else:
        print(text)
    return 0


def generate_schematic(args: argparse.Namespace) -> int:
    result = generate_from_config(
        Path(args.config).resolve(),
        Path(args.out_dir).resolve(),
        run=args.run,
        netlist_check=args.netlist_check,
        metrics=args.metrics,
        dry_run=args.dry_run,
        timeout=args.timeout,
        interactive=args.interactive,
        simetrix_exe=simetrix_path(args.simetrix_exe, args.runtime_config),
        runtime_config=args.runtime_config,
        symbol_lib_dir=args.symbol_lib_dir,
    )
    print(json.dumps(result, indent=2))
    return 1 if result.get("failed") else 0


def show_config(args: argparse.Namespace) -> int:
    print(json.dumps(runtime_config_status(
        cli_simetrix_exe=args.simetrix_exe,
        cli_symbol_lib_dir=args.symbol_lib_dir,
        config_path=args.runtime_config,
    ), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SIMetrix/SIMPLIS automation helpers")
    parser.add_argument("--simetrix-exe", help="Path to SIMetrix.exe; overrides runtime config")
    parser.add_argument("--runtime-config", help="JSON runtime config with simetrix_exe and symbol_lib_dir")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("show-config", help="Show resolved runtime configuration and path validation evidence")
    p.add_argument("--symbol-lib-dir", help="SIMPLIS symbol library directory; overrides runtime config")
    p.set_defaults(func=show_config)

    p = sub.add_parser("run-script", help="Launch SIMetrix with a .sxscr script")
    p.add_argument("script")
    p.add_argument("--interactive", action="store_true", help="Keep SIMetrix interactive while running generated scripts")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--timeout", type=float, default=None, help="Seconds before treating SIMetrix as hung")
    p.add_argument("--expect", action="append", default=[], help="Output file that must exist after the script")
    p.set_defaults(func=run_script)

    p = sub.add_parser("create-concept", help="Create a simple proof-of-control schematic script and optionally run it")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--no-run", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--timeout", type=float, default=60.0)
    p.add_argument("--keep-open", action="store_true", help="Do not append Quit to the generated script")
    p.set_defaults(func=create_concept)

    p = sub.add_parser("make-run-schematic", help="Generate a script that opens a schematic and runs simplis_run")
    p.add_argument("schematic")
    p.add_argument("--out", required=True)
    p.add_argument("--save-after", action="store_true")
    p.add_argument("--keep-open", action="store_true")
    p.set_defaults(func=make_run_schematic)

    p = sub.add_parser("make-run-deck", help="Generate a script that runs a SIMPLIS deck or netlist->deck flow")
    p.add_argument("--deck", required=True)
    p.add_argument("--netlist", help="Optional raw schematic netlist to preprocess")
    p.add_argument("--out", required=True)
    p.add_argument("--keep-open", action="store_true")
    p.set_defaults(func=make_run_deck)

    p = sub.add_parser("make-metric-writer", help="Generate a SIMetrix script that writes a metric JSON file with Echo redirection")
    p.add_argument("--result-json", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--metric", action="append", default=[], help="Metric as name=value, e.g. vout_dc_error_mv=1.2")
    p.add_argument("--keep-open", action="store_true")
    p.set_defaults(func=make_metric_writer)

    p = sub.add_parser("make-vector-export", help="Generate a SIMetrix script that exports vectors from data groups")
    p.add_argument("--schematic", required=True, help="Existing .sxsch to open and run with simplis_run")
    p.add_argument("--out-dir", required=True, help="Directory for exported vector text files")
    p.add_argument("--out", required=True, help="Output .sxscr script path")
    p.add_argument("--status-file", help="Optional Echo status file written by the generated script")
    p.add_argument("--vector", action="append", required=True, help="Vector as group:vector, e.g. simplis_pop1:#VOUT")
    p.set_defaults(func=make_vector_export)

    p = sub.add_parser("parse-show", help="Parse SIMetrix Show output files into JSON")
    p.add_argument("files", nargs="+")
    p.add_argument("--out", help="Optional output JSON path")
    p.set_defaults(func=parse_show)

    p = sub.add_parser("generate-schematic", help="Generate a SIMPLIS schematic from a YAML/JSON circuit spec")
    p.add_argument("--config", required=True, help="YAML or JSON schematic spec")
    p.add_argument("--out-dir", required=True, help="Directory for generated .sxscr/.sxsch/.net files")
    p.add_argument("--run", action="store_true", help="Generate and run a follow-up simplis_run script")
    p.add_argument("--netlist-check", action="store_true", help="Netlist after creating the schematic and validate required nets")
    p.add_argument("--metrics", action="store_true", help="Generate and run a metric-writing script")
    p.add_argument("--dry-run", action="store_true", help="Write scripts only; do not launch SIMetrix")
    p.add_argument("--batch", dest="interactive", action="store_false", default=True)
    p.add_argument("--timeout", type=float, default=60.0)
    p.add_argument("--symbol-lib-dir", help="SIMPLIS symbol library directory; overrides runtime config")
    p.set_defaults(func=generate_schematic)

    args = parser.parse_args(argv)
    args.started_at = time.time()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
