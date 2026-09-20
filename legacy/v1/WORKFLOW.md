# Archived v1 workflow (opt-in historical reference)

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

If the SIMetrix command/message window is scrolling script lines, immediately turn off command echo and clear the window:

```powershell
python %CODEX_HOME%\skills\simplis-automation\scripts\simplis_cli.py --simetrix-exe "D:\SIMetrix830\bin64\SIMetrix.exe" quiet-shell --out path\to\quiet_shell.sxscr --run
```

Use `quiet-shell` when the command shell is scrolling script lines. It removes a persistent `EchoOn=` entry from the SIMetrix user `Base.sxprj` config with a timestamped backup, then sends `Unset EchoOn`, `ClearMessageWindow`, and `CloseSimplisStatusBox` through `/i /s` if `--run` is supplied. It does not modify schematics or circuit data. Add `--no-repair-user-config` if you only want the transient GUI cleanup script.

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

Use `profiles/` for canonical symbol names derived from official examples and verified generator defaults. Prefer profile roles over guessed symbol names. For reusable compensation or transconductance blocks, prefer official `.sxcmp` modules only when they are text-parseable or verified in the target installation.

Use `references/generated_rc_labeled.json` as the smallest connectivity smoke test. Use `references/generated_feedback_divider_hybrid.json` as the smallest hand-drawn-style routing smoke test. Use `references/generated_buck_open_loop_tran.json` as the current 12 V buck example with body diodes, `PWM_LS` generated from `PWM_HS` through `inv_d`, `PERIODIC_OP_V8` POP trigger, and POP followed by `.TRAN 60u 0`. The buck example includes voltage probes on `VIN`, `SW`, `VOUT`, `PWM_HS`, `PWM_LS`, and `TRIG_GATE`, plus inline current probes for input current, inductor current, output-capacitor current, and load current.

## Decision Tree

- To create a proof-of-control schematic, run `simplis_cli.py create-concept --out-dir <dir>`.
- To generate a library-symbol schematic from a structured YAML/JSON spec, run `simplis_cli.py generate-schematic --config <spec.json|yaml> --out-dir <dir> --netlist-check`.
- To inspect official or generated SIMPLIS files, run `simplis_cli.py inspect-schematic --input <file-or-dir> --out <report.json>`.
- To prepare simulation output for agent analysis, run `simplis_cli.py export-agent-evidence --work-dir <dir> --out <report.json>`.
- To run an existing open-style schematic like the GUI Run button, generate a script with `simplis_run` after `OpenSchem`.
- To export POP/AC vectors from an existing schematic, generate a script with `simplis_cli.py make-vector-export`, run it with `run-script`, then parse the `Show` text files with `simplis_cli.py parse-show`.
- To tune Type II or Type III loop compensation on a parameterized schematic, use `examples/buck_comp_optimizer/buck_opt.py` and read `references/loop-compensation-optimizer.md`. Require a pre-placed SIMPLIS Bode Plot Probe; if it is missing, ask the user to place it before real AC-loop optimization.
- To stop command shell/message-window scrolling, run `simplis_cli.py quiet-shell --out <file.sxscr> --run`. If startup itself prints `Running script init_phase_1` and every script line, confirm the command reports that `EchoOn=` was removed from the SIMetrix user config. Avoid `Set EchoOn` in generated batch scripts unless actively debugging command parsing.
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
- Before claiming POP is valid, export and inspect time-domain vectors for the real switching state. A missing `.deck.err` is not enough: check key digital/control nodes such as `FSW`, `CLK`, duty, `VOUT`, `FB`, and `VC` for expected edges, frequency, DC level, and ripple.
- Before claiming AC loop stability, verify POP waveform sanity first, then export the actual Bode expression from the deck such as `db(:#FB/:120)` and `ph(:#FB/:120)`. Do not assume stale probe node numbers.
- Before choosing default devices, inspect official examples or read `profiles/`; do not invent symbol names.
- Before suggesting simulation-driven circuit changes, read an `export-agent-evidence` report.
- Do not invent SIMPLIS symbol names, pin names, command syntax, measurement functions, DVM file names, or waveform names. Search installed libraries/docs/examples or inspect generated artifacts first.
- Do not copy private research schematics into this skill. Only official/open-source-approved examples belong under `examples/official/`.
- If a step cannot be verified, say exactly what evidence is missing and what file/path/config is needed.

## SIMPLIS Error Modal Handling

When SIMetrix shows a modal dialog such as `Error detected during the execution of simplis.exe`, do not treat the SIMetrix process return code as the result. The dialog usually points to the real evidence file under the schematic's `SIMPLIS_Data` directory.

Required loop after every run that may fail or block:

1. Capture `GetSIMPLISExitCode()` into a status file, but do not rely on it alone.
2. Inspect generated `SIMPLIS_Data/*.deck.err`, `*.deck.warn`, `*.deck.health`, and `*.deck.dbg` files.
3. Run `simplis_cli.py export-agent-evidence --work-dir <SIMPLIS_Data> --out <evidence.json> --summary-md <evidence.md>` before diagnosing.
4. If a GUI modal is present, use Windows Computer Use to snapshot the modal/status window, then dismiss it only after recording the referenced file path.
5. Diagnose from the file evidence first. For POP failures, check `.deck.warn` and `.deck.health` for non-converging state references such as capacitor or inductor designators, then tune those state variables, initial conditions, startup timing, or loop parameters one change at a time.

## Closed-Loop Tuning Discipline

Do not sweep compensation parameters blindly. For buck or PMIC loops, first identify whether the comparator is valley-mode, peak-mode, average-mode, or another sampled structure. Determine the comparator collision point, ramp/ripple amplitude, and the expected `VC` DC value before changing the type-II network. Then derive candidate zero, pole, and gain values from the baseline plant response near the target crossover.

Treat initial conditions as part of the design state. `*.deck.init` can seed a new run, but only back-annotate independent energy-storage elements. Do not bulk-copy all `C*` and `L*` values: sampled/held internal capacitors or mirrored external capacitors can violate KVL/KCL at `t=0` if only one side is changed. After any IC edit, run a baseline candidate and check `.deck.err` for `Error Message ID: 5013`.

Parameter candidates must pass this ladder before being considered usable:

1. SIMPLIS files contain no blocking `.deck.err` and POP convergence is credible.
2. POP vectors show real switching behavior, not latched logic or a static false operating point.
3. DC values (`VOUT`, `FB`, `VC`, ramp/collision point) match the intended control law.
4. AC Bode vectors meet the target crossover and phase margin.
5. TRAN is enabled and checked only after POP and AC are valid.

## Artifact Hygiene

Open existing user schematics as read-only for measurement and vector export: `OpenSchem /cd /readonly "...sxsch"`. Keep every candidate in its own work directory so `SIMPLIS_Data` files do not overwrite previous evidence. Generated read-only runs can leave read-only waveform files behind; clear the attribute before deleting a candidate directory on Windows. Never modify the user's original `Downloads` schematic unless explicitly asked.

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
- Keep `EchoOn` disabled by default in generated scripts. If a GUI starts scrolling script lines, run `quiet-shell`; it handles both transient GUI echo and persistent `EchoOn=` in user config. If a script itself is hung, press `Esc` in SIMetrix or terminate only the SIMetrix process launched for that batch run.
- For loop-compensation optimization, do not auto-place or rewire the Bode Plot Probe. A missing probe is a user-action blocker because probe direction determines loop sign and phase-margin convention.
- For fragile symbol placement, first generate a visible concept schematic, inspect it, then harden the script from real symbol names in the installed libraries.

## References

- Read `references/commands.md` before writing `.sxscr` scripts.
- Read `references/dvm.md` before DVM testplan work.
- Read `references/loop-compensation-optimizer.md` before tuning Type II/Type III compensation or Bode crossover/phase-margin targets.
- Read `references/optimization.md` before sweep/optimizer loops.
- Read `references/parameter-and-metrics.md` before wiring a real schematic's parameters and measurements into an optimization script.
- Read `references/research-validation.md` for buck PMIC validation metrics tied to ACOT/Vramp-valley work.
- Read `references/simplis-design-method.md` before deriving new schematic-generation behavior from examples.
- Read `references/simplis-automation-skill-guide.zh-CN.md` for a Chinese, user-facing operating guide and current capability boundaries.
- Read `references/verified-local-84.md` for what has already been proven on this Windows SIMPLIS 8.4 installation.
- Canonical symbol and module profiles live in `profiles/`.
- Example generator specs live in `references/generated_rc_labeled.json`, `references/generated_feedback_divider_hybrid.json`, `references/generated_buck_acot_min.json`, and `references/generated_buck_open_loop_tran.json`.
