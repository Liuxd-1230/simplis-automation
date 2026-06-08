---
name: simplis-automation
description: Automate SIMetrix/SIMPLIS 8.4 on Windows for schematic creation or editing, SIMPLIS runs, DVM-oriented testplans, parameter sweeps, black-box optimization, and iterative circuit validation. Use when Codex needs to control SIMetrix.exe/SIMPLIS, write .sxscr scripts, generate or modify .sxsch/.sxcmp/.testplan artifacts, run a buck/PMIC simulation, extract measurements, or tune design parameters from simulation results.
---

# SIMPLIS Automation

## Core Workflow

Use `scripts/simplis_cli.py` as the entry point. Before any SIMetrix/SIMPLIS action, resolve runtime configuration from:

1. CLI flags such as `--simetrix-exe`, `--symbol-lib-dir`, or `--runtime-config`
2. Environment variables `SIMETRIX_EXE`, `SIMPLIS_SYMBOL_LIB_DIR`, or `SIMPLIS_AUTOMATION_CONFIG`
3. `config/local_config.json`
4. `config/simplis_automation_config.json`

Do not assume any SIMPLIS installation path. If `simetrix_exe` or `symbol_lib_dir` cannot be resolved and verified on disk, stop and report the missing configuration.

Prefer generating SIMetrix `.sxscr` scripts and launching them through:

```powershell
python %CODEX_HOME%\skills\simplis-automation\scripts\simplis_cli.py run-script path\to\job.sxscr
```

Use this command as the first diagnostic when paths are uncertain:

```powershell
python %CODEX_HOME%\skills\simplis-automation\scripts\simplis_cli.py show-config
```

## Structured Schematic Generation

For library-symbol schematics, use JSON/YAML specs with `generate-schematic`:

```powershell
python %CODEX_HOME%\skills\simplis-automation\scripts\simplis_cli.py generate-schematic --config %CODEX_HOME%\skills\simplis-automation\references\generated_buck_open_loop_tran.json --out-dir path\to\outputs\generated_buck_open_loop_tran --run --netlist-check --timeout 240 --batch
```

The generator:

- Parses installed `.sxslb` symbol libraries for pin names and pin coordinates.
- Places devices by group/row/col with configurable spacing.
- Uses `term VALUE <net>` labels at each connected pin to avoid long-wire misalignment.
- If `routing.mode` is `hybrid`, uses short local Manhattan wires for nearby same-net pins and keeps only boundary/local labels for each connected component.
- Injects optional F11 analysis text after saving the schematic, then netlists again so simulation settings are current.
- For POP trigger devices, resolves `{TRIG_GATE}` to the internal SIMPLIS comparator event such as `X1.!D_CYCLE` before deck execution.

Use `profiles/` for canonical symbol names derived from official examples and verified generator defaults. Prefer profile roles over guessed symbol names. For reusable compensation or transconductance blocks, prefer official `.sxcmp` modules when they are present and verified.

Use `references/generated_rc_labeled.json` as the smallest connectivity smoke test. Use `references/generated_feedback_divider_hybrid.json` as the smallest hand-drawn-style routing smoke test. Use `references/generated_buck_open_loop_tran.json` as the current 12 V buck example with body diodes, `PWM_LS` generated from `PWM_HS` through `inv_d`, `PERIODIC_OP_V8` POP trigger, and POP followed by `.TRAN 60u 0`. The buck example includes voltage probes on `VIN`, `SW`, `VOUT`, `PWM_HS`, `PWM_LS`, and `TRIG_GATE`, plus inline current probes for input current, inductor current, output-capacitor current, and load current.

## Decision Tree

- To create a proof-of-control schematic, run `simplis_cli.py create-concept --out-dir <dir>`.
- To generate a library-symbol schematic from a structured YAML/JSON spec, run `simplis_cli.py generate-schematic --config <spec.json|yaml> --out-dir <dir> --netlist-check`.
- To inspect official or generated SIMPLIS files, run `simplis_cli.py inspect-schematic --input <file-or-dir> --out <report.json>`.
- To prepare simulation output for agent analysis, run `simplis_cli.py export-agent-evidence --work-dir <dir> --out <report.json>`.
- To run an existing open-style schematic like the GUI Run button, generate a script with `simplis_run` after `OpenSchem`.
- To export POP/AC vectors from an existing schematic, generate a script with `simplis_cli.py make-vector-export`, run it with `run-script`, then parse the `Show` text files with `simplis_cli.py parse-show`.
- To run a raw SIMPLIS deck, use `RunSIMPLIS`; if starting from a generated schematic netlist, use `Netlist /simplis`, then `PreProcessNetlist`, then `RunSIMPLIS`. For generated POP designs, prefer the `generate-schematic --run` flow because it resolves `{TRIG_GATE}` first.
- To sweep a fixed grid, use `sweep_optimize.py`.
- To iterate based on prior results, use `closed_loop_optimize.py`; it supports grid, random, and coordinate search, resumes from history, launches SIMetrix, reads metric JSON, and writes `best_candidate.json`.
- To use DVM, treat it as a testplan/report layer on top of a working schematic with a DVM control symbol and `.testplan`. Read `references/dvm.md`.
- To validate the skill after edits, run `scripts/smoke_test.py`.

## Evidence Rules

This skill is fragile and tool-version dependent. Every action must depend on observed evidence:

- Before launching SIMetrix, verify the configured executable exists.
- Before placing symbols, parse the configured `.sxslb` directory and verify each symbol and pin name exists.
- Before claiming connectivity, run `Netlist /simplis` and inspect `.node_map` or generated netlist lines.
- Before claiming POP works, verify `{TRIG_GATE}` was resolved to an internal event such as `X1.!D_CYCLE` in the netlist/deck.
- Before claiming probes work, verify `.PRINT V(...)` or `.PRINT I(...)` lines exist in the generated deck.
- Before claiming waveform export works, verify the data group with `VectorsInGroup(...)`, use `SetGroup`, and reference special vector names with `Vec('...')`.
- Before choosing default devices, inspect official examples or read `profiles/`; do not invent symbol names.
- Before suggesting simulation-driven circuit changes, read an `export-agent-evidence` report.
- Do not invent SIMPLIS symbol names, pin names, command syntax, measurement functions, DVM file names, or waveform names. Search installed libraries/docs/examples or inspect generated artifacts first.
- Do not copy private research schematics into this skill. Only official/open-source-approved examples belong under `examples/official/`.
- If a step cannot be verified, say exactly what evidence is missing and what file/path/config is needed.

## Important Constraints

- SIMPLIS supplied with SIMetrix/SIMPLIS is not a standalone DOS-prompt simulator. Control it through SIMetrix scripts.
- `RunSIMPLIS` is primitive and does not preprocess netlists.
- For schematic-equivalent runs, use internal script `simplis_run` on the current schematic.
- For generated POP schematics, `PreProcessNetlist` does not resolve `{TRIG_GATE}` by itself. Let `schematic_generator.py` rewrite it to the `PERIODIC_OP` internal gate before calling `RunSIMPLIS`.
- Non-interactive schematic drawing is possible but symbol names and properties are library/version specific. Use `Inst /loc ...`, `Wire /loc ...`, and `SaveAs /force ...`.
- For robust generated connectivity, prefer `term VALUE <netname>` labels at each device pin over long coordinate wires. `schematic_generator.py` parses `.sxslb` pin locations and places terminals at the actual transformed pin coordinates.
- For cleaner visual schematics based on hand-drawn SIMPLIS style, set `routing.mode = "hybrid"` with conservative `max_wire_length` and `max_component_span`. Verify with `Netlist /simplis` because visual local wires only work when pin coordinates are exact.
- For waveform debugging, prefer `probev_new` for voltage nodes and `InlineCurrentProbe` for current paths. `InlineCurrentProbe` inserts a zero-volt source in series, so split the original net into two named nets and define the current direction as `P -> N`.
- For exported waveform data, do not hand-write `Show` lines for names like `#VOUT`, `50`, or `IN+`. Use `make-vector-export`; it emits `Vec('#VOUT')`, `Vec('50')`, and group-specific output files.
- For fragile symbol placement, first generate a visible concept schematic, inspect it, then harden the script from real symbol names in the installed libraries.

## References

- Read `references/commands.md` before writing `.sxscr` scripts.
- Read `references/dvm.md` before DVM testplan work.
- Read `references/optimization.md` before sweep/optimizer loops.
- Read `references/parameter-and-metrics.md` before wiring a real schematic's parameters and measurements into an optimization script.
- Read `references/research-validation.md` for buck PMIC validation metrics tied to ACOT/Vramp-valley work.
- Read `references/simplis-design-method.md` before deriving new schematic-generation behavior from examples.
- Read `references/verified-local-84.md` for what has already been proven on this Windows SIMPLIS 8.4 installation.
- Canonical symbol and module profiles live in `profiles/`.
- Example generator specs live in `references/generated_rc_labeled.json`, `references/generated_feedback_divider_hybrid.json`, `references/generated_buck_acot_min.json`, and `references/generated_buck_open_loop_tran.json`.
