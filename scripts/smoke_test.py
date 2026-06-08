#!/usr/bin/env python3
"""Smoke tests for the simplis-automation skill."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
CLI = SKILL_DIR / "scripts" / "simplis_cli.py"
DEFAULT_OUT = Path.cwd() / ".codex_tmp" / "simplis_automation_smoke"


def run_cmd(cmd: list[str], timeout: float) -> dict[str, object]:
    proc = subprocess.run(cmd, check=False, text=True, capture_output=True, timeout=timeout)
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def parse_json_tail(text: str) -> dict[str, object]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("command did not emit a JSON object")
    return json.loads(text[start : end + 1])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run SIMPLIS automation smoke tests")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--include-buck-run", action="store_true", help="Also run the 12 V buck POP+60u example")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tests: list[dict[str, object]] = [
        {
            "name": "rc_netlist_check",
            "cmd": [
                sys.executable,
                str(CLI),
                "generate-schematic",
                "--config",
                str(SKILL_DIR / "references" / "generated_rc_labeled.json"),
                "--out-dir",
                str(out_dir / "rc"),
                "--netlist-check",
                "--timeout",
                str(args.timeout),
                "--batch",
            ],
            "required_nets": {"VIN", "VOUT"},
            "required_deck_text": [],
            "timeout": max(args.timeout + 30.0, 120.0),
        }
    ]
    if args.include_buck_run:
        tests.append(
            {
                "name": "buck_pop60u_run",
                "cmd": [
                    sys.executable,
                    str(CLI),
                    "generate-schematic",
                    "--config",
                    str(SKILL_DIR / "references" / "generated_buck_open_loop_tran.json"),
                    "--out-dir",
                    str(out_dir / "buck_pop60u"),
                    "--run",
                    "--netlist-check",
                    "--timeout",
                    str(max(args.timeout, 240.0)),
                    "--batch",
                ],
                "required_nets": {"VIN_SRC", "VIN", "SW", "L_OUT", "VOUT", "COUT_TOP", "LOAD_TOP", "PWM_HS", "PWM_LS", "TRIG_GATE"},
                "required_deck_text": [
                    ".POP  TRIG_GATE=",
                    ".TRAN 60u 0",
                    ".PRINT I(V$IPRB_VIN)",
                    ".PRINT I(V$IPRB_L)",
                    ".PRINT I(V$IPRB_COUT)",
                    ".PRINT I(V$IPRB_LOAD)",
                    ".PRINT V(#VIN)",
                    ".PRINT V(#SW)",
                    ".PRINT V(#VOUT)",
                    ".PRINT V(#PWM_HS)",
                    ".PRINT V(#PWM_LS)",
                    ".PRINT V(#TRIG_GATE)",
                ],
                "timeout": max(args.timeout + 30.0, 300.0),
            }
        )

    results = []
    for test in tests:
        name = str(test["name"])
        cmd = list(test["cmd"])  # type: ignore[arg-type]
        required_nets = set(test["required_nets"])  # type: ignore[arg-type]
        required_deck_text = list(test["required_deck_text"])  # type: ignore[arg-type]
        raw = run_cmd(cmd, timeout=float(test["timeout"]))
        passed = raw["returncode"] == 0
        details: dict[str, object] = {}
        missing_deck_text: list[str] = []
        if passed:
            details = parse_json_tail(str(raw["stdout"]))
            validation = details.get("netlist_validation", {})
            node_map = set(validation.get("node_map", [])) if isinstance(validation, dict) else set()
            passed = required_nets.issubset(node_map) and not details.get("failed", True)
            deck = Path(str(details.get("deck", "")))
            if required_deck_text:
                deck_text = deck.read_text(encoding="utf-8", errors="replace") if deck.exists() else ""
                missing_deck_text = [item for item in required_deck_text if item not in deck_text]
                passed = passed and not missing_deck_text
        results.append({"name": name, "passed": passed, "returncode": raw["returncode"], "missing_deck_text": missing_deck_text, "details": details})
        if not passed:
            print(json.dumps({"failed_test": name, "raw": raw, "results": results}, indent=2))
            return 1

    print(json.dumps({"passed": True, "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
