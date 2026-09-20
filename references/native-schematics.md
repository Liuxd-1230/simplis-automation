# Editable native schematics and modules

Use the existing file or derive the topology from the user's specification. A
native sketch may contain declared functional blocks; a runnable model needs actual
device definitions behind those blocks. Keep those claims separate.

## Build and connect

Use installed symbols and a task-specific layout. Native `Inst /loc`, `Wire /loc`,
`Prop`, `SaveAs /force` and `Netlist /simplis` are documented in the local
`ScriptReference.pdf`; syntax and references are indexed in [evidence](evidence.md).
Inspect a matching-version file when directly writing the format. Native APIs can
be more reliable than guessing serialization or orientation codes.

Observed 8.4 pitfalls:

- Use compatible grid coordinates for both instance pins and wire endpoints.
  Native placement can snap coordinates. Split wires at intended terminal/tap
  points and inspect the resulting netlist; a visible crossing is not proof of a net.
- Include needed instance properties, not only library defaults. Missing `VALUE`,
  `netname` or templates can silently change connectivity or device emission.
- Keep display abbreviations separate from the actual `SIMPLIS_TEMPLATE` and model
  value; shortening a waveform label must not shorten the executed waveform.
- `SaveAs /force` avoids an overwrite prompt for task-owned generated files.
  Reopening an already-open file edited on disk can produce reload/exit dialogs.
  Reuse the active document deliberately or use a fresh task-owned session.

## Hierarchy

A native `.sxcmp` contains the external `.Symbol` and underlying `.Schematic`.
Its symbol uses `MODEL=X`; its pins must match underlying **module ports**.
In the tested library, `modport` instances use `scterm=1` plus the port's `VALUE`.
Ordinary `term` net labels alone did not cause the netlister to emit the child
subcircuit. A parent component refers to the `.sxcmp` path.

For runnable hierarchy, inspect `Netlist /simplis` output for the expected
`.subckt` definitions, port mappings and actual R/C/source/gate parameters. For a
new generation path, a small run of the exported netlist is useful confirmation.
Don't redraw or re-run unrelated validated modules just to satisfy a global ritual.

## Drawing completion

View the result at readable scale and check the relevant connections. A native
preview or export is sufficient for ordinary layout review; check it is not blank.
`OpenPDFPrinter`, `PrintSchematic`, `ClosePrinter` are available in the tested 8.4
runtime, but a returned file alone did not reliably establish a rendered page.
A PNG/SVG drawn independently may illustrate the design but is not evidence that
SIMetrix opened the native file.

Strict native-window screenshot hashes and clean-reopen finalization belong to the
[v2 backend path](v2-workflow.md) when chosen, not every schematic task.
