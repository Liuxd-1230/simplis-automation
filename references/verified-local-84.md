# Verified Local SIMetrix/SIMPLIS 8.4 Evidence

Verified on this machine on 2026-06-08.

SIMetrix executable was resolved from local runtime configuration:

```text
config/local_config.json -> simetrix_exe
```

Validated behaviors:

- `SIMetrix.exe /s create_simplis_automation_concept.sxscr` created and saved a SIMPLIS schematic.
- The generated schematic existed at a local workspace output path.
- Adding `Quit` let SIMetrix exit cleanly after saving.
- `OpenSchem "...Boost_Converter\Startup.sxsch"`, `simplis_run`, `Quit` completed successfully through `simplis_cli.py run-script --batch --timeout 90`.
- `closed_loop_optimize.py --mock` completed a coordinate-search loop and wrote `optimization_history.json` plus `best_candidate.json`.
- `OpenEchoFile` + `Echo` + `Let close_result = CloseEchoFile()` wrote a file successfully. Use single-quoted strings in `.sxscr` templates for Windows paths. Prefer `key=value` metrics over JSON text inside SIMetrix scripts to avoid quote escaping issues.
- `closed_loop_optimize.py` completed a real SIMetrix-launched grid run where each candidate script wrote `key=value` metrics and the optimizer selected the best candidate from parsed results.
- `ScreenShotWindow "file.png"` was not validated as file output; local docs describe clipboard capture.
- Library `term` symbols can be used as VirtuosoBridgeLite-style net labels. Verified by generating an RC schematic with `Inst /loc ... term VALUE <netname>`. The schematic netlisted with `.node_map VIN`, `.node_map VOUT`, `V1`, `R1`, and `C1` connected correctly, confirming same-name terminal labels connect without long coordinate wires.
- `scripts/schematic_generator.py` generated and netlist-checked the RC example from `references\generated_rc_labeled.json`; the generated netlist contained `VIN` and `VOUT`.
- `scripts/schematic_generator.py` generated and netlist-checked the minimal ACOT buck example from `references\generated_buck_acot_min.json`; the generated netlist contained `VIN`, `SW`, `VOUT`, `FB`, `VREFNEW`, `PWM_REQ`, and `PWM_HS`.
- `simplis_cli.py generate-schematic --config ... --out-dir ... --netlist-check` was verified through the RC example and now serves as the public CLI entry point for structured schematic generation.
- `scripts/schematic_generator.py` generated and ran the POP+60u buck example from `references\generated_buck_open_loop_tran.json`. The schematic uses a 12 V source, high-side/low-side SIMPLIS voltage-controlled switches, `ADI_Diode` body diodes, `inv_d` to derive `PWM_LS` from `PWM_HS`, and `PERIODIC_OP_V8` as the POP trigger. The generated netlist contained `VIN_SRC`, `VIN`, `SW`, `L_OUT`, `VOUT`, `COUT_TOP`, `LOAD_TOP`, `PWM_HS`, `PWM_LS`, and `TRIG_GATE`.
- The buck POP+60u example now includes `probev_new` voltage probes on `VIN`, `SW`, `VOUT`, `PWM_HS`, `PWM_LS`, and `TRIG_GATE`, plus `InlineCurrentProbe` devices for `IIN`, `IL`, `ICOUT`, and `ILOAD`. The verified deck contains `.PRINT V(#VIN)`, `.PRINT V(#SW)`, `.PRINT V(#VOUT)`, `.PRINT V(#PWM_HS)`, `.PRINT V(#PWM_LS)`, `.PRINT V(#TRIG_GATE)`, and `.PRINT I(V$IPRB_*)` for all four current probes.
- For generated POP designs, `schematic_generator.py` resolves `{TRIG_GATE}` to the internal event of the `PERIODIC_OP` instance before `RunSIMPLIS`. The verified v3 deck contains `.POP  TRIG_GATE=X1.!D_CYCLE ...` followed by `.TRAN 60u 0`, and `generate-schematic --run --netlist-check --batch --timeout 240` returned successfully.
- `scripts/smoke_test.py` provides a compact validation entry point. Its default RC test checks symbol placement and terminal-label connectivity without running the slower buck POP example; pass `--include-buck-run` for the full 12 V buck POP+60u run. The buck smoke test also checks that voltage/current probe `.PRINT` statements are present in the generated deck.

Use this as proof that schematic drawing and schematic-run automation are possible locally. Continue to validate project-specific symbol placement, DVM launch, measurement extraction, and result parsing per design.
