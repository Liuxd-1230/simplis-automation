from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time


def attributes(line: str) -> dict[str, str]:
    return {m[1]: m[2] if m[2] is not None else m[3] for m in
            re.finditer(r'(\w+)=(?:"((?:\\.|[^"\\])*)"|([^\s]+))', line)}


def symbols(path: Path, query: str = "", name: str | None = None) -> dict:
    raw = path.read_bytes()
    # ASCII syntax; retain UTF-8 descriptions where available without losing pins.
    text = raw.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
    found = []
    for block in re.findall(r"(?m)^\.Symbol\s*\n(.*?)^\.EndSymbol\s*$", text, re.S):
        lines = block.splitlines()
        meta = next((attributes(s) for s in lines if s.startswith("Attributes ")), {})
        if name is not None and meta.get("name") != name:
            continue
        if name is None and query.lower() not in (meta.get("name", "") + " " + meta.get("description", "")).lower():
            continue
        entry = {"name": meta.get("name"), "description": meta.get("description", "")}
        if name is not None:
            entry["pins"] = sorted((attributes(s) for s in lines if s.startswith("Pin ")),
                                   key=lambda p: int(p["order"]))
            entry["properties"] = [attributes(s) for s in lines if s.startswith("Property ")]
            entry["definition"] = ".Symbol\n" + block + ".EndSymbol"
        found.append(entry)
    if name is not None and len(found) != 1:
        raise ValueError(f"Expected one symbol {name!r}; found {len(found)} in {path.name}")
    return {"library": str(path.resolve()), "sha256": hashlib.sha256(raw).hexdigest(), "symbols": found}


def read_t2(path: Path) -> tuple[list[str], list[list[float]]]:
    with path.open(encoding="ascii") as stream:
        head = stream.readline().split()
        if len(head) < 4 or head[0] != "$$$":
            raise ValueError("Expected a SIMPLIS text .t2 table (not .t0 or a native binary group)")
        count, variables = int(head[2]), int(head[3])
        if count < 2 or variables < 1:
            raise ValueError("Waveform needs at least two points and one variable")
        for _ in range(4):
            if not stream.readline():
                raise ValueError("Truncated waveform header")
        names = []
        for index in range(variables + 1):
            fields = stream.readline().strip().split(maxsplit=1)
            if len(fields) != 2 or fields[0] != str(index):
                raise ValueError("Invalid waveform column header")
            names.append(fields[1])
        if names[0].upper() != "TIME" or len(set(names)) != len(names):
            raise ValueError("Expected unique columns beginning with TIME")
        rows = []
        for line in stream:
            if not line.strip():
                continue
            row = [float(s) for s in line.split()]
            if len(row) != len(names) or not all(math.isfinite(v) for v in row):
                raise ValueError("Invalid waveform row width or non-finite value")
            if rows and row[0] < rows[-1][0]:
                raise ValueError("Waveform time runs backwards")
            rows.append(row)
        if len(rows) != count:
            raise ValueError(f"Truncated/extra waveform rows: expected {count}, found {len(rows)}")
        if rows[-1][0] <= rows[0][0]:
            raise ValueError("Waveform has no positive time span")
    return names, rows


def time_mean(rows: list[list[float]], column: int, start: float, end: float) -> float:
    if not (math.isfinite(start) and math.isfinite(end) and rows[0][0] <= start < end <= rows[-1][0]):
        raise ValueError("Integration bounds must lie inside the waveform and start < end")
    integral = 0.0
    for a, b in zip(rows, rows[1:]):
        lo, hi = max(start, a[0]), min(end, b[0])
        if hi <= lo:
            continue  # repeated timestamps retain jump limits without adding area
        slope = (b[column] - a[column]) / (b[0] - a[0])
        ylo = a[column] + slope * (lo - a[0])
        yhi = a[column] + slope * (hi - a[0])
        integral += (hi - lo) * (ylo + yhi) / 2
    return integral / (end - start)


def waveform(path: Path, column: str | None = None,
             start: float | None = None, end: float | None = None) -> dict:
    names, rows = read_t2(path)
    result = {"points": len(rows), "columns": names, "start": rows[0][0], "end": rows[-1][0]}
    if column is not None:
        if column not in names:
            raise ValueError(f"Column {column!r} not found; available: {names}")
        start = rows[0][0] if start is None else start
        end = rows[-1][0] if end is None else end
        result["measurement"] = {"column": column, "start": start, "end": end,
                                 "time_mean": time_mean(rows, names.index(column), start, end)}
    elif start is not None or end is not None:
        raise ValueError("--start/--end require --column")
    return result


def run_deck(exe: Path, deck: Path, out: Path, timeout: float = 60, *,
             kind: str = "candidate", environment: dict | None = None) -> dict:
    from run_storage import KINDS, register_run
    if kind not in KINDS:
        raise ValueError(f"Unknown run kind: {kind}")
    exe, deck, out = exe.resolve(), deck.resolve(), out.resolve()
    if not exe.is_file() or not deck.is_file():
        raise ValueError("Executable and deck must be existing files")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Timeout must be finite and positive")
    source = deck.read_bytes()
    if re.search(rb"(?im)^\s*\.(?:include|inc|lib|load)\b", source):
        raise ValueError("This helper stages self-contained decks only; use native execution or the backend for external dependencies")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError("Output directory must be new or empty; existing results are preserved")
    out.mkdir(parents=True, exist_ok=True)
    staged = out / "input.ckt"
    staged.write_bytes(source)
    result = {"input_sha256": hashlib.sha256(source).hexdigest(), "solver": str(exe),
              "source": str(deck), "authoritative_input": "standalone netlist",
              "editable_schematic": None, "settings": environment or {},
              "started_utc": datetime.now(timezone.utc).isoformat(),
              "completed": False, "behavior_validated": False}
    begun = time.monotonic()
    try:
        with (out / "solver.log").open("wb") as log:
            process = subprocess.run([str(exe), staged.name, "-f"], cwd=out,
                                     stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        result["exit_code"] = process.returncode
        error_file = out / "input.ckt.err"
        errors = error_file.read_text(errors="replace").strip() if error_file.exists() else ""
        if process.returncode != 0 or errors:
            result["error"] = errors or "Solver failed; see solver.log"
        else:
            result["waveform"] = waveform(out / "input.ckt.t2")
            result["completed"] = True
    except subprocess.TimeoutExpired:
        # subprocess.run kills and reaps only the solver process it launched.
        result.update(exit_code=124, error="Solver timeout; inspect solver.log before retrying")
    except (OSError, ValueError) as exc:
        result["error"] = str(exc)
    result["elapsed_seconds"] = round(time.monotonic() - begun, 3)
    banner = re.search(r"(?im)^simplis VERSION[^\r\n]+", (out / "solver.log").read_text(errors="replace"))
    result["solver_version_banner"] = banner.group(0) if banner else None
    (out / "run.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    roles = {}
    for path in out.rglob("*"):
        if path.is_file():
            roles[path.relative_to(out).as_posix()] = ("input" if path == staged else "record" if path.name == "run.json"
                                else "log" if path.suffix in {".log", ".err"}
                                else "raw" if path.suffix == ".t2" else "scratch")
    register_run(out, roles, kind=kind if result["completed"] else "diagnostic",
                 completed=result["completed"], environment=environment)
    return result


def main() -> int:
    # 中文索引经 Windows 管道输出时保持 UTF-8
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Optional device lookup, standalone simulation and waveform tools")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("symbols", help="Read one installed .sxslb; never launch SIMetrix")
    p.add_argument("--library", required=True, type=Path)
    group = p.add_mutually_exclusive_group()
    group.add_argument("--query", default="")
    group.add_argument("--name")
    p = commands.add_parser("find", help="Find devices by function, user reference first")
    p.add_argument("--query", required=True)
    p.add_argument("--install", type=Path)
    p.add_argument("--reference", type=Path)
    p.add_argument("--official", action="store_true", help="Reference unsuitable: inspect installed official definitions")
    p = commands.add_parser("locate", help="Locate installed libraries, manuals and examples")
    p.add_argument("--install", type=Path)
    p.add_argument("--query", default="")
    p = commands.add_parser("run", help="Run a self-contained transient deck in a fresh directory")
    p.add_argument("--exe", type=Path, required=True)
    p.add_argument("--deck", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--timeout", type=float, default=60)
    p.add_argument("--kind", choices=("temporary", "candidate", "final"), default="candidate")
    p.add_argument("--environment", type=Path, help="JSON with simulator/model version, parameters and conditions")
    p = commands.add_parser("waveform", help="Inspect a text .t2 table or calculate a time-weighted mean")
    p.add_argument("file", type=Path)
    p.add_argument("--column")
    p.add_argument("--start", type=float)
    p.add_argument("--end", type=float)
    p.add_argument("--save-run", type=Path, help="Save this measurement in the registered run before raw-data cleanup")
    args = parser.parse_args()
    try:
        if args.command == "symbols":
            result = symbols(args.library, args.query, args.name)
        elif args.command in {"find", "locate"}:
            from device_lookup import find, locate, REFERENCE
            result = (find(args.query, args.install, args.reference or REFERENCE, args.official)
                      if args.command == "find" else locate(args.install, args.query))
        elif args.command == "waveform":
            result = waveform(args.file, args.column, args.start, args.end)
            if args.save_run:
                from run_storage import register_metrics
                if args.column is None or args.file.resolve().parent != args.save_run.resolve():
                    raise ValueError("Save a measured column from the selected run's own waveform")
                metrics_path = args.save_run / "metrics.json"
                metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
                metrics.setdefault("measurements", []).append(dict(file=args.file.name, **result["measurement"]))
                register_metrics(args.save_run, metrics)
        else:
            environment = json.loads(args.environment.read_text(encoding="utf-8")) if args.environment else None
            result = run_deck(args.exe, args.deck, args.out_dir, args.timeout, kind=args.kind, environment=environment)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1 if args.command == "run" and not result["completed"] else 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
