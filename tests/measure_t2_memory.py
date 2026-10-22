from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from simplis_tools import read_t2, time_mean, waveform


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Measure actual Python memory/time on an existing solver .t2")
    parser.add_argument("file", type=Path)
    parser.add_argument("--mode", choices=("memory", "stream"), required=True)
    parser.add_argument("--column", required=True)
    parser.add_argument("--start", type=float, required=True)
    parser.add_argument("--end", type=float, required=True)
    args = parser.parse_args()
    tracemalloc.start()
    begun = time.perf_counter()
    if args.mode == "memory":
        names, rows = read_t2(args.file)
        value = time_mean(rows, names.index(args.column), args.start, args.end)
        points = len(rows)
    else:
        result = waveform(args.file, args.column, args.start, args.end)
        value = result["measurement"]["time_mean"]
        points = result["points"]
    elapsed = time.perf_counter() - begun
    _, peak = tracemalloc.get_traced_memory()
    print(json.dumps(dict(mode=args.mode, points=points, file_bytes=args.file.stat().st_size,
                         peak_python_bytes=peak, elapsed_s=elapsed, time_mean=value)))
