# simplis-automation v2

`v2/` is an isolated, evidence-first rewrite of the SIMPLIS automation skill.
It does not modify the legacy `scripts/` implementation.

The public workflow is deliberately narrow:

1. `simplis-v2 doctor` verifies the local SIMetrix/SIMPLIS runtime.
2. `simplis-v2 catalog import` and `catalog approve` create a locked local device catalog.
3. `simplis-v2 compile` turns a YAML circuit graph into a reviewed build manifest and SIMetrix script.
4. `simplis-v2 verify` creates the editable schematic and actual SIMPLIS netlist.
5. `simplis-v2 sweep` repeats compile/verify for a deterministic parameter grid.

The compiler never labels a design as simulated.  It distinguishes static validation,
netlisting, fully-ideal designs, and boundary-only opaque-module designs.
