---
name: simplis-automation
description: Generate, import, run, validate, finalize and tune SIMetrix/SIMPLIS 8.3 or 8.4 circuits from strict YAML and locked local device evidence.
---

# SIMPLIS Automation

This repository root is the default evidence-first v2 implementation. The archived
v1 implementation is available only under `legacy/v1/`.

## Efficient default workflow

1. Run `simplis doctor --catalog <catalog.lock.yaml>` after installation or runtime
   configuration changes. Doctor is static: it never launches SIMetrix or a fixture.
2. Approve a catalog device only when it is new or its installed-library fingerprint
   changed. Do not repeat proof runs for an unchanged approved catalog.
3. Run `simplis run experiment.yaml --out-dir <run-dir>`. One analysis partition uses
   one task-owned SIMetrix process for create, analysis cards, real netlist,
   simulation and vector export.
4. Use `simplis sweep` or `simplis optimize` on the same fast path. Do not clean-reopen
   or capture every candidate.
5. For the selected deliverable, run `simplis finalize
   <run-dir>/experiment-result.json --out-dir <final-dir>`. This performs the sole
   additional clean-reopen, re-netlist and native HWND screenshot.
6. Read the emitted PNG with Codex `view_image`, write the required review JSON, then
   run `simplis finalize-review <finalization-request.json> --review <review.json>`.
   Bind that review with `finalization_request_sha256` and `screenshot_sha256` from
   the exact request and PNG; a review for another capture must fail closed.

`simplis-v2` remains an alias for `simplis`. `compile`, `verify` and `roundtrip` are
diagnostic commands, not mandatory steps in the default flow.

## Completion and visual evidence

- A real task proves completion through ordered stage tokens, exit code 0, empty
  error and unapproved-warning sets, matching analysis groups, and fresh non-empty
  vectors. `GetSimulatorStatus() == None` is acceptable only when all of those
  independent facts pass.
- Finalization captures only the task-owned SIMetrix HWND with Win32 `PrintWindow`.
  Never capture the desktop and never substitute an offline preview.
- Codex must inspect readable text, symbol and label spacing, terminal/series/ground
  orientation, functional bands and analysis gutter. A failed check makes the
  deliverable fail closed.
- Visual evidence proves readability only. The real netlist, vector provenance and
  behavioral validation remain the electrical evidence.

## Safety rules

- Treat `circuit.yaml` as the source of truth. Import any GUI edit back into YAML.
- Use only fingerprint-locked catalog symbols and approved parameter properties.
- Unknown or opaque modules remain non-parameterizable boundaries and cannot make a
  design fully ideal.
- Preserve task isolation, terminate only task-owned PIDs, and retain failure bundles.
- Treat warnings as failures unless their exact text is allowlisted.
- Integrate nonuniform SIMPLIS samples over time; never use a plain row average for
  DC, current or power metrics.
- Keep generated schematics, screenshots, local runtime paths and raw outputs in
  ignored output directories.
