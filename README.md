# simplis-automation

Evidence-first SIMetrix/SIMPLIS 8.3/8.4 automation. The v2 implementation is now the
repository default; the former implementation is archived in `legacy/v1/`.

## Install

```powershell
python -m pip install -e .
simplis doctor --catalog catalog/seed_8_4.yaml
```

Runtime discovery honors explicit CLI arguments, environment variables and runtime
config first. Automatic discovery prefers SIMetrix 8.4 and falls back to 8.3.
`doctor` is side-effect free and never starts SIMetrix.

## Fast iteration and strict delivery

```powershell
simplis run examples/acot_buck_v84_experiment.yaml --out-dir outputs/acot
simplis finalize outputs/acot/experiment-result.json --out-dir outputs/acot-final
simplis finalize-review outputs/acot-final/finalization-request.json `
  --review outputs/acot-final/review.json
```

One analysis partition uses one SIMetrix process to create the editable schematic,
write analysis directives, produce a real netlist, run SIMPLIS and export vectors.
Sweep and optimization candidates use the same fast path. Only the selected result is
clean-reopened in a second process.

Finalization captures the task-owned window directly with Win32 `PrintWindow`.
The image is reviewed by Codex multimodal vision through the versioned checklist;
there is no model API integration or desktop capture.

`simplis-v2` is retained as an alias. `compile`, `verify` and `roundtrip` remain
available for diagnosis. Legacy v1 commands must be invoked from `legacy/v1/`.

## Trust contract

A normal run is scoring eligible only when ordered stage tokens, SIMPLIS exit and
error state, warning policy, analysis-group contract, fresh vectors, behavioral
checks and required charts all pass. A post-return `GetSimulatorStatus() == None`
does not require an artificial calibration circuit; it is accepted only when the
real task supplies every independent proof.

Private schematics, local paths, screenshots and generated outputs must remain under
ignored output directories.
