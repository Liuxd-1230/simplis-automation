---
name: simplis-automation
description: Automate SIMetrix/SIMPLIS 8.4 on Windows for schematic creation or editing, SIMPLIS runs, DVM-oriented testplans, parameter sweeps, black-box optimization, and iterative circuit validation. Use when Codex needs to control SIMetrix.exe/SIMPLIS, write .sxscr scripts, generate or modify .sxsch/.sxcmp/.testplan artifacts, run a buck/PMIC simulation, extract measurements, or tune design parameters from simulation results.
---

# SIMPLIS Automation

## Core Workflow

Use `scripts/simplis_cli.py` as the entry point. Prefer generating SIMetrix `.sxscr` scripts and launching them through:

```powershell
python %CODEX_HOME%\skills\simplis-automation\scripts\simplis_cli.py run-script path\to\job.sxscr
```

Default SIMetrix path on this machine:

```text
D:\Simplis8.4\bin64\SIMetrix.exe
```

If the path differs, pass `--simetrix-exe` or set `SIMETRIX_EXE`.

## Structured Schematic Generation

For library-symbol schematics, use JSON/YAML specs with `generate-schematic`:

```powershell
python %CODEX_HOME%\skills\simplis-automation\scripts\simplis_cli.py generate-schematic --config %CODEX_HOME%\skills\simplis-automation\references\generated_buck_open_loop_tran.json --out-dir path\to\outputs\generated_buck_open_loop_tran --run --netlist-check --timeout 240 --batch
```

The generator:

- Parses installed `.sxslb` symbol libraries for pin names and pin coordinates.
- Places devices by group/row/col with configurable spacing.
- Uses `term VALUE <net>` labels at each connected pin to avoid long-wire misalignment.
- Injects optional F11 analysis text after saving the schematic, then netlists again so simulation settings are current.
- For POP trigger devices, resolves `{TRIG_GATE}` to the internal SIMPLIS comparator event such as `X1.!D_CYCLE` before deck execution.

Use `references/generated_rc_labeled.json` as the smallest connectivity smoke test. Use `references/generated_buck_open_loop_tran.json` as the current 12 V buck example with body diodes, `PWM_LS` generated from `PWM_HS` through `inv_d`, `PERIODIC_OP_V8` POP trigger, and POP followed by `.TRAN 60u 0`. The buck example includes voltage probes on `VIN`, `SW`, `VOUT`, `PWM_HS`, `PWM_LS`, and `TRIG_GATE`, plus inline current probes for input current, inductor current, output-capacitor current, and load current.

## Decision Tree

- To create a proof-of-control schematic, run `simplis_cli.py create-concept --out-dir <dir>`.
- To generate a library-symbol schematic from a structured YAML/JSON spec, run `simplis_cli.py generate-schematic --config <spec.json|yaml> --out-dir <dir> --netlist-check`.
- To run an existing open-style schematic like the GUI Run button, generate a script with `simplis_run` after `OpenSchem`.
- To run a raw SIMPLIS deck, use `RunSIMPLIS`; if starting from a generated schematic netlist, use `Netlist /simplis`, then `PreProcessNetlist`, then `RunSIMPLIS`. For generated POP designs, prefer the `generate-schematic --run` flow because it resolves `{TRIG_GATE}` first.
- To sweep a fixed grid, use `sweep_optimize.py`.
- To iterate based on prior results, use `closed_loop_optimize.py`; it supports grid, random, and coordinate search, resumes from history, launches SIMetrix, reads metric JSON, and writes `best_candidate.json`.
- To use DVM, treat it as a testplan/report layer on top of a working schematic with a DVM control symbol and `.testplan`. Read `references/dvm.md`.
- To validate the skill after edits, run `scripts/smoke_test.py`.

## Important Constraints

- SIMPLIS supplied with SIMetrix/SIMPLIS is not a standalone DOS-prompt simulator. Control it through SIMetrix scripts.
- `RunSIMPLIS` is primitive and does not preprocess netlists.
- For schematic-equivalent runs, use internal script `simplis_run` on the current schematic.
- For generated POP schematics, `PreProcessNetlist` does not resolve `{TRIG_GATE}` by itself. Let `schematic_generator.py` rewrite it to the `PERIODIC_OP` internal gate before calling `RunSIMPLIS`.
- Non-interactive schematic drawing is possible but symbol names and properties are library/version specific. Use `Inst /loc ...`, `Wire /loc ...`, and `SaveAs /force ...`.
- For robust generated connectivity, prefer `term VALUE <netname>` labels at each device pin over long coordinate wires. `schematic_generator.py` parses `.sxslb` pin locations and places terminals at the actual transformed pin coordinates.
- For waveform debugging, prefer `probev_new` for voltage nodes and `InlineCurrentProbe` for current paths. `InlineCurrentProbe` inserts a zero-volt source in series, so split the original net into two named nets and define the current direction as `P -> N`.
- For fragile symbol placement, first generate a visible concept schematic, inspect it, then harden the script from real symbol names in the installed libraries.

## References

- Read `references/commands.md` before writing `.sxscr` scripts.
- Read `references/dvm.md` before DVM testplan work.
- Read `references/optimization.md` before sweep/optimizer loops.
- Read `references/parameter-and-metrics.md` before wiring a real schematic's parameters and measurements into an optimization script.
- Read `references/research-validation.md` for buck PMIC validation metrics tied to ACOT/Vramp-valley work.
- Read `references/verified-local-84.md` for what has already been proven on this Windows SIMPLIS 8.4 installation.
- Example generator specs live in `references/generated_rc_labeled.json`, `references/generated_buck_acot_min.json`, and `references/generated_buck_open_loop_tran.json`.
