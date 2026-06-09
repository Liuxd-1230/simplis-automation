# SIMPLIS Design Method

Use this method when creating or modifying SIMPLIS circuits with `simplis-automation`.

## Evidence Order

1. Inspect official examples with `inspect-schematic`.
2. Use canonical symbol profiles from `profiles/`.
3. Verify every selected symbol and pin against the configured installed `.sxslb` libraries.
4. Generate schematic artifacts.
5. Netlist and inspect `.node_map` before claiming connectivity.
6. Run simulation when needed.
7. Export agent evidence before suggesting circuit changes.

## Default Device Names

Default device names come from official examples, installed-library evidence, and generator profiles, not from memory. Prefer profile roles such as `power_switch`, `body_diode`, `voltage_probe`, and `compensator_3p2z` over ad hoc symbol names.

Profiles record both the canonical generator symbol and official observed aliases. If an official example uses `websim_resz1` but the profile preferred symbol is `res`, keep the distinction clear and verify the actual installed symbol before placement.

## Reusable Modules

Use packaged official modules directly only when they match the need and are text-parseable or verified in the target installation. The 3p2z/type-III compensation block is parsed with tunable properties. The checked-in transconductance amplifier files are binary-format path-level evidence; convert/save them as text or inspect them in the target installation before reusing them or exposing tunable parameters.

## Privacy Boundary

Do not copy private research schematics into examples, tests, reports, or docs. Private files can inform local reasoning only at the level of generic design principles.

## Agent Evidence

Before recommending circuit edits based on a run, execute `export-agent-evidence` on the generated work directory. Read the exported JSON/Markdown report first. Do not invent waveform names, net names, trigger gates, or failure causes that are not in the report.
