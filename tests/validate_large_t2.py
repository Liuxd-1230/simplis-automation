from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from simplis_tools import run_deck
from run_storage import protect, register_metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Opt-in memory comparison using a REAL million-point SIMPLIS output")
    parser.add_argument("--solver", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--points", type=int, default=1000001)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source = out / "large_rc.ckt"
    source.write_text("* Memory measurement input\nV1 1 0 1\nR1 1 2 1k\nC1 2 0 1u\n"
                      f".INIT V(C1)=0\n.OPTIONS PSP_NPT={args.points}\n.TRAN 5m 0\n.PRINT V(2) V(1) I(R1)\n.END\n")
    run = out / "run"
    solved = run_deck(args.solver, source, run, environment={"model": "native RC", "R_ohm": 1000, "C_F": 1e-6})
    assert solved["completed"], solved
    results = []
    for mode in ("memory", "stream"):
        command = [sys.executable, str(ROOT / "tests/measure_t2_memory.py"), str(run / "input.ckt.t2"),
                   "--mode", mode, "--column", "V(2)", "--start", "0.004", "--end", "0.005"]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        measured = json.loads(result.stdout)
        results.append(measured)
        (out / (mode + ".json")).write_text(json.dumps(measured, indent=2))
        print(json.dumps(measured), flush=True)
    expected = 1 - (math.exp(-4) - math.exp(-5))
    assert abs(results[0]["time_mean"] - results[1]["time_mean"]) < 1e-11, results
    assert abs(results[1]["time_mean"] - expected) < 2e-7, results
    assert results[1]["peak_python_bytes"] < results[0]["peak_python_bytes"] / 10, results
    register_metrics(run, dict(measurements=results, expected_time_mean=expected))
    protect(run, "Independent full-memory/streaming comparison on actual solver output")
