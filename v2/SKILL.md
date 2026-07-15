---
name: simplis-automation-v2
description: Generate, import, round-trip, run, validate and tune SIMetrix/SIMPLIS 8.4 circuits from strict YAML and locked local device evidence. Use for editable schematic generation, text schematic import, parameter promotion, watchdog-isolated simulations, trusted waveform extraction and recoverable optimization.
---

# SIMPLIS Automation v2

Use v2 for evidence-first circuit construction.  It is independent from the legacy
scripts in the repository root.

## Required workflow

1. Run `simplis-v2 doctor --catalog <catalog.lock.yaml>` before a real SIMetrix job.
2. Create or approve the local catalog before using a symbol in a circuit.
   Use `simplis-v2 catalog proof-compile ... --kind <pending-kind>` only to
   gather placement/netlist evidence, then use `simplis-v2 catalog proof-run
   <experiment> --catalog ... --kind <pending-kind> --out-dir ...` for the
   minimum behavior test. Both paths are evidence-only and can never make a
   generated design or optimization eligible.
3. Run `simplis-v2 compile circuit.yaml --out-dir <build-dir>`.
4. Run `simplis-v2 verify <build-dir>/build-manifest.json` to create `.sxsch` and a
   SIMPLIS netlist.  Do not call a circuit correct merely because compilation passed.
5. Cleanly reopen the generated `.sxsch` in a fresh, responsive SIMetrix instance and
   pass the GUI screenshot gate below. Treat this as a required visual-product check,
   not as connectivity or electrical proof.
6. For a grid, use `simplis-v2 sweep experiment.yaml --out-dir <sweep-dir>`; it
   performs compile plus netlist verification for each point and does not run a
   transient simulation.
7. Use `simplis-v2 run experiment.yaml --out-dir <run-dir>` for simulation. Only a
   final `complete` state with trusted vectors, behavior checks and charts is valid.
8. Use `simplis-v2 optimize experiment.yaml --out-dir <opt-dir>` only after doctor
   proves the immediate `SxCommand.exe` status probe.

## GUI screenshot gate

- Capture the actual clean-reopened SIMetrix window with Computer Use after the
  watchdog confirms that the task-owned process is responsive. Capture a whole-sheet
  Zoom-to-Fit view and a closer detail when a dense block cannot be read at that scale.
- Verify that the active schematic caption/tab identifies the expected `.sxsch` (the
  top-level 8.4 window title may stay generic), no modal/error dialog is present, and
  the canvas is neither blank nor stale from another run.
- Inspect the screenshot for readable reference/value text, visible unclipped symbols,
  label collisions, device or block overlap, and usable whitespace. Require the power
  path to read left-to-right above the control/feedback band, with probes, POP and AC
  injection in a separate gutter.
- Require left input labs to face outward as `N180`, right output labs as `N0`, series
  two-terminal devices to read horizontally, and ground branches vertically. Treat a
  screenshot that contradicts the manifest orientation as a layout failure.
- Record the result with `simplis-v2 visual-evidence <build-manifest.json> --out
  <visual-evidence.json> ...`. Supply all named checks, the active document caption,
  clean-reopen token, capture ID or image, dimensions, role, verdict and findings.
  A Computer Use capture may remain conversation-only and records
  `persistent_image=false`; require `native_file` when a durable image artifact is
  part of the deliverable. Keep private screenshots under ignored local outputs only.
- Never use an offline preview, a screenshot of an unresponsive window, or a screenshot
  alone as proof of pin attachment, net isolation, simulation success or electrical
  correctness. Fix visual failures, rerun verify and clean reopen, then recapture.

## Safety rules

- `circuit.yaml` is the source of truth.  A GUI edit must be imported back into YAML.
- Only catalog-approved properties may be parameterized.  Unknown symbols or
  properties produce pending catalog candidates and block verification.
- A text `.sxsch` can be imported.  Binary/unsupported formats must be converted to
  text in SIMetrix first.
- Opaque `.sxcmp` or vendor modules are boundary-verified only; they never make a
  design `fully_ideal`.
- Unknown imported symbols remain non-parameterizable opaque boundaries. They may
  use source-preserving `roundtrip`, but cannot enter ideality or optimization proof.
- Declare intentionally floating catalog pins with `unconnected_pins`. Never join
  unused outputs through a shared fake NC net; undeclared missing pins must fail.
- Primitive, DIGI1 and Logic BB devices may be `ideal_native` only with all catalog
  proofs. Never replace a pending device with Generic, HC, LP311 or vendor wrappers.
- Read `DEVICE_SUMMARY_8_4.md` before selecting generated devices. For reactive
  parts, encode a declared capacitor-voltage or inductor-current `IC` through the
  catalog instead of editing native VALUE text ad hoc.
- Keep cold-start ICs at the declared startup state. For POP/AC, derive output
  capacitor voltage, inductor valley current and controller/ripple state from the
  design operating point. Hold every load-step source at V1 with
  `IDLE_IN_POP=YES`; a delayed future step is not proof of a constant POP load.
- Treat warnings as failures unless their exact captured text is allowlisted.
- Treat transient x-axis samples as nonuniform. Compute DC/current/power averages by
  integrating over time, never by averaging exported rows; require the experiment's
  declared input/output power-ratio bounds before accepting electrical behavior.
- Treat `GetSimulatorStatus() == None` as incomplete unless doctor has persisted a
  `calibrated_none_after_return` result and the current run independently proves all
  ordered stage tokens, observed progress, exit code 0, empty errors/warnings, matching
  `GetSimulationInfo()` and fresh complete vectors. Otherwise keep the vectors
  `diagnostic_only`.
- A catalog `proof-run` may record a passed behavior proof when the only completion
  failure is the observed post-return `None`, and all ordered tokens, exit/error and
  warning checks, group contracts, fresh vectors, device-specific behavior checks
  and charts pass. Keep its state machine at `validated`, set
  `normal_simulation_complete=false` and `scoring_eligible=false`, and record empty
  `GetSimulationInfo()` fields as limitations. Never apply this exception to
  `run` or `optimize`.
- Failed/hung candidates keep an error bundle; terminate only task-owned PIDs and
  continue until the declared evaluation budget is exhausted.
- Keep every `verify` and `sweep` run in its own output directory.
