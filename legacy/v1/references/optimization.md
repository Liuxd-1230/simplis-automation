# Sweep and Optimization Pattern

Use this pattern for buck PMIC tuning:

1. Define parameters in a JSON spec: ramp gain, valley clamp, servo gm, integrator C, anti-windup limits, load step amplitude, ESR, Vin.
2. Generate one candidate script per parameter set.
3. Run SIMetrix/SIMPLIS through `simplis_cli.py`.
4. Export scalar metrics from SIMetrix postprocess or parse generated result text.
5. Score each candidate with a weighted objective.
6. Iterate with `closed_loop_optimize.py` using grid, random search, or coordinate search. Use the separate `$optimizer` skill only when the project needs a heavier Bayesian/TuRBO-style optimizer.

Closed-loop spec example:

```json
{
  "strategy": "coordinate",
  "max_evals": 25,
  "seed": 7,
  "script_template": "D:/work/buck/run_candidate_template.sxscr",
  "parameters": {
    "ramp_gain": {"bounds": [0.4, 2.0], "initial": 1.0, "step": 0.1},
    "servo_gm": {"bounds": [1e-7, 5e-6], "initial": 1e-6, "scale": "log", "step": 0.2}
  },
  "weights": {
    "vout_dc_error_mv": 1.0,
    "loadstep_undershoot_mv": 0.25,
    "loadstep_recovery_us": 0.5,
    "period_jitter_pct": 10.0
  },
  "constraints": {
    "phase_margin_deg": {"min": 45},
    "startup_overshoot_mv": {"max": 80}
  }
}
```

Run it:

```powershell
python %CODEX_HOME%\skills\simplis-automation\scripts\closed_loop_optimize.py spec.json --work-dir path\to\buck_opt --timeout 180
```

The optimizer writes `optimization_history.json` after each candidate and `best_candidate.json` at the end, so rerunning the same command resumes from history.

Recommended metric JSON shape:

```json
{
  "candidate": {"ramp_gain": 1.0, "servo_gm": 1e-6},
  "metrics": {
    "vout_dc_error_mv": 0.8,
    "startup_overshoot_mv": 12.0,
    "loadstep_undershoot_mv": 38.0,
    "loadstep_recovery_us": 8.5,
    "period_jitter_pct": 1.2,
    "phase_margin_deg": 58.0
  }
}
```

Optimization should reject unstable candidates before scoring speed:

```text
hard fail: no startup, oscillatory startup, missing POP, runaway Vservo/Vtrim
primary: DC error, load-step undershoot/overshoot, recovery time
secondary: minimum compensation ramp, jitter, quiescent/servo current proxy
```

Keep optimization scripts project-local for real designs because measurement expression names and graph datasets are schematic-specific.
