# Loop Compensation Optimizer

Use the buck compensation optimizer example when a user wants SIMPLIS-driven Type II or Type III compensation tuning from a parameterized schematic.

## Preconditions

- The user provides a SIMPLIS `.sxsch` schematic, or uses the checked-in buck example.
- Compensation components are already parameterized in the schematic or a text `.sxcmp` module.
- A SIMPLIS Bode Plot Probe is already placed in the loop. Do not auto-place it; probe direction changes loop sign and phase-margin interpretation.
- The optimizer config maps each active parameter to a whitelisted target.

If the Bode probe is missing, stop and ask the user to place it. The runner validates this in real mode and emits a "missing Bode plot probe" issue.

## Entry Point

```powershell
cd <repo>\examples\buck_comp_optimizer
python .\buck_opt.py --config .\optimizer_config.json validate --source provided --mode real
python .\buck_opt.py run-once --source provided --mode mock
python .\buck_opt.py diagnose-real --source provided --mode real --stage-timeout-s 60
python .\buck_opt.py run-once --source provided --mode real
python .\buck_opt.py optimize --source provided --mode real --max-evals 30 --coordinate-rounds 2
```

Use `--config <user-config.json>` when the user's schematic lives outside the example directory. Relative paths in that config resolve from the config file's directory.

## Parameter Targets

Use `schematic_var` when the parameter is a top-level SIMPLIS variable:

```json
"Rz": {
  "target": "schematic_var",
  "maps_to": "RZ",
  "initial": 10000,
  "bounds": [1000, 100000],
  "coarse": [4700, 10000, 22000]
}
```

Use `compensator_alias` when the parameter lives in a `.sxcmp` module copied by `compensator`:

```json
"Rz1": {
  "target": "compensator_alias",
  "maps_to": "R5",
  "initial": 2848.377,
  "bounds": [800, 12000],
  "coarse": [1500, 2848.377, 5600]
}
```

Use `schematic_property` or `schematic_value_token` only for explicit circuit-parameter whitelist entries. Do not add topology edits, polarity edits, rewiring, or current-sense changes.

## Type II and Type III

Type II is just a smaller active parameter set, commonly `Rz`, `Cz`, and `Cp`.

Type III commonly uses `Rz1`, `Cz1`, `Rz2`, `Cz2`, and `Cp1`.

The optimizer does not care about the names themselves. What matters is that `maps_to` names match variables already used by the SIMPLIS schematic/module.

## Bode and Vector Export

The config must identify AC and transient vectors:

```json
"vector_export": {
  "enabled": true,
  "ac": {"group": "simplis_ac1", "output": "7", "input": "20"},
  "tran": {"group": "simplis_tran1", "vout": "7", "vc": "6"}
}
```

After a real run, the runner writes:

- `vectors/*.txt` from SIMetrix `Show`
- `ac_loop.csv`
- `tran.csv`
- `bode.svg`
- `metrics.json`
- `score.json`
- `result.json`

If the user changes schematic/probes, verify vector names before trusting optimization. Missing vectors become a failed run with penalty, not a crash.

## Goals and Scoring

Use `targets.fc_hz` for a target crossover, or `targets.fc_hz_min/fc_hz_max` for an acceptable range. Use `targets.phase_margin_deg` as the minimum phase margin unless `phase_margin_deg_min/max` are specified.

For multiple 0 dB crossings, set:

- `nearest_target` to select the crossing nearest `targets.fc_hz`
- `last_down` for the highest falling crossover
- `first` for legacy first crossing behavior

The default example uses `nearest_target` because LC peaking can produce multiple crossings.

## Applying Results

Optimization runs only modify `runs/run_xxxx/work/`. To write back:

```powershell
python .\buck_opt.py apply-best --target provided --best .\best_params.json
```

This creates timestamped `.bak-*` files before writing only whitelisted parameters.
