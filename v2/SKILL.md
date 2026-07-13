---
name: simplis-automation-v2
description: Generate and verify SIMetrix/SIMPLIS circuits from a strict YAML graph using a locked local ideal-device catalog. Use for editable schematic generation, text schematic import, parameter promotion, and netlist-verified grid sweeps.
---

# SIMPLIS Automation v2

Use v2 for evidence-first circuit construction.  It is independent from the legacy
scripts in the repository root.

## Required workflow

1. Run `simplis-v2 doctor --catalog <catalog.lock.yaml>` before a real SIMetrix job.
2. Create or approve the local catalog before using a symbol in a circuit.
3. Run `simplis-v2 compile circuit.yaml --out-dir <build-dir>`.
4. Run `simplis-v2 verify <build-dir>/build-manifest.json` to create `.sxsch` and a
   SIMPLIS netlist.  Do not call a circuit correct merely because compilation passed.
5. For a grid, use `simplis-v2 sweep experiment.yaml --out-dir <sweep-dir>`; it
   performs compile plus netlist verification for each point and does not run a
   transient simulation.

## Safety rules

- `circuit.yaml` is the source of truth.  A GUI edit must be imported back into YAML.
- Only catalog-approved properties may be parameterized.  Unknown symbols or
  properties produce pending catalog candidates and block verification.
- A text `.sxsch` can be imported.  Binary/unsupported formats must be converted to
  text in SIMetrix first.
- Opaque `.sxcmp` or vendor modules are boundary-verified only; they never make a
  design `fully_ideal`.
- Keep every `verify` and `sweep` run in its own output directory.
