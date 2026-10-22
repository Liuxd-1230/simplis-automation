from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from device_lookup import find, installation, locate
from run_storage import cleanup, load_run, protect, raw_retention, register_metrics
from simplis_tools import run_deck, waveform


def validate(solver: Path, install: Path, out: Path):
    out = out.resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("Use a fresh validation directory")
    out.mkdir(parents=True, exist_ok=True)
    reference = find("测量支路电流")
    assert reference["matches"][0]["name"] == "InlineCurrentProbe"
    fallback = find("变压器", install)
    assert fallback["source"] == "installed-official-libraries" and fallback["matches"], fallback
    unsuitable = find("测量支路电流", install, official=True)
    assert any(m["name"] == "InlineCurrentProbe" for m in unsuitable["matches"]), unsuitable
    assert locate(install)["documents_and_examples"]
    runs = []
    for index, resistance in enumerate((1000, 2000, 3000)):
        deck = out / f"source_{index}.ckt"
        deck.write_text(f"* RC storage validation\nV1 1 0 1\nR1 1 2 {resistance}\nC1 2 0 1u\n"
                        ".INIT V(C1)=0\n.OPTIONS PSP_NPT=1001\n.TRAN 5m 0\n.PRINT V(2)\n.END\n")
        run = out / f"run_{index}"
        result = run_deck(solver, deck, run, kind="temporary", environment={
            "model_id": "native linear RC", "R_ohm": resistance, "C_F": 1e-6, "Vin_V": 1})
        assert result["completed"] and result["solver_version_banner"], result
        assert result["editable_schematic"] is None
        assert next(f for f in cleanup(run)["files"] if f["path"] == "input.ckt.t2")["action"] == "keep"
        register_metrics(run, waveform(run / "input.ckt.t2", "V(2)", .004, .005))
        runs.append(run)
    protect(runs[1], "Representative sweep point")
    raw_retention(runs[0], ["input.ckt.t2"], discard=True,
                  reason="Disposable RC scan point; selected representative raw data retained in run_1")
    (runs[2] / "input.ckt.t2").write_text((runs[2] / "input.ckt.t2").read_text() + "\n")
    (runs[0] / "manual.txt").write_text("user annotations")
    preview = [cleanup(run) for run in runs]
    assert preview[0]["reclaimable_bytes"] > 0
    assert preview[1]["reclaimable_bytes"] == 0
    assert all((run / "input.ckt.t2").exists() for run in runs)
    reports = [cleanup(run, apply=True) for run in runs]
    assert not (runs[0] / "input.ckt.t2").exists()
    assert (runs[0] / "manual.txt").exists()
    assert (runs[1] / "input.ckt.t2").exists()
    assert (runs[2] / "input.ckt.t2").exists()
    for index, run in enumerate(runs):
        assert (out / f"source_{index}.ckt").exists()
        assert all((run / f).exists() for f in ("input.ckt", "run.json", "solver.log", "metrics.json"))
    bad = out / "undefined_node.ckt"
    bad.write_text("* Deliberate invalid node for REAL solver failure\nV1 1 0 1\nR1 1 0 1k\n"
                   ".OPTIONS PSP_NPT=11\n.TRAN 1m 0\n.PRINT V(99)\n.END\n")
    failure = run_deck(solver, bad, out / "failure")
    assert not failure["completed"] and failure["error"]
    assert load_run(out / "failure")["kind"] == "diagnostic"
    cleanup(out / "failure", apply=True)
    assert (out / "failure/input.ckt.err").stat().st_size > 0
    report = dict(reference_query=True, missing_reference_fallback=True, unsuitable_reference_fallback=True,
                  batch_runs=3, failure_recorded=True, retained_run_and_manual_changes_preserved=True,
                  reclaimed_bytes=sum(r["reclaimable_bytes"] for r in reports),
                  failures_are_real_solver_outputs=True)
    (out / "validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Opt-in real installed-library, CLI and storage verification")
    parser.add_argument("--solver", type=Path, required=True)
    parser.add_argument("--install", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    validate(args.solver.resolve(), installation(args.install), args.out_dir)
