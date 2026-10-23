# Simulation and waveform checks

## Choose the execution path

Use [shared runtime discovery](runtime.md). The normal managed entries are:

```powershell
simplis native <source.sxsch> --vector "#VOUT" --out-dir <fresh-directory>
simplis run <experiment.yaml> --out-dir <fresh-directory>
python scripts/simplis_tools.py run --deck <self-contained.ckt> --out-dir <fresh-directory>
```

These register inputs, software/library evidence, logs and raw data by default.
`--exe` remains available for explicit standalone solver selection.

For a self-contained native deck, the tested 8.40 CLI is:

```powershell
& '<installation>/bin/simplis.exe' '<task-directory>/circuit.ckt' -f
```

`-f` forces a fresh simulation. Use a task-owned working directory. The optional
helper stages a self-contained deck into a new directory and records exit status,
log, input hash and waveform summary:

```powershell
python scripts/simplis_tools.py run --exe <simplis.exe> --deck <circuit.ckt> --out-dir <new-directory> --timeout 60
```

It deliberately does not resolve external include trees. For those, use native
execution with the required files staged or choose the existing backend. It also
does not preprocess SIMetrix macro libraries: parameterized native symbols may need
SIMetrix's `PreProcessNetlist` before their deck can run in standalone SIMPLIS.

The report identifies the authoritative standalone netlist and states that this
command does not deliver an editable schematic. When the deck was exported from a
native design, keep its `.sxsch`/`.sxcmp` source and report that relationship in the
delivery. Generated outputs are registered for optional
[evidence retention and cleanup](run-storage.md). Select `--kind temporary` for
scan points and `--kind final` for retained evidence; failures become diagnostic.

For an existing schematic, `simplis_run` uses the native schematic workflow.
`Netlist /simplis` exports hierarchy; `RunSIMPLIS` is a lower-level command and does
not perform that preprocessing itself. A GUI command returning is not sufficient
proof of completion. Check actual solver output; don't wait on a status string
alone or accept stale vectors left by a previous run.

For POP/AC, the tested 8.40 frontend can finish solving and then block inside its
optional `simplis_health_report`. Managed scripts temporarily disable only that
HTML report with `Set /temp SimplisSimulationHealthReportGlobalEnable=FALSE`;
solver errors, warnings and logs remain active. Record the return token immediately
after `simplis_run`, before querying status. See the
[real Buck diagnosis](../docs/pop-ac-buck-validation.md).

The editable [native Buck](../examples/native/acot_buck/README.md) shares one core
between cold-start, line-step and precharged POP/AC benches. Its device-connected
ACOT timer senses live VIN/VOUT and starts from the actual high-side drive; see
[measured timer checks](../docs/acot-timer-validation.md). `simplis native --group simplis_ac1`
accepts complex Show data; `simplis_pop1` expects time-domain data. Specify `PSP_NPT`
to obtain POP waveforms. Inspect the exported hierarchy for the real trigger name.
GUI `.t2/.f2` files can contain headers without numeric rows: use current-group
Show exports. Standalone AC produces a full `.f2`; the helper checks real/imaginary
pairs, sample count and increasing positive frequencies. Raw AC data remain retained.

Sources: [evidence](evidence.md), D2-D3, O2-O3.

## Obtain data

Set analysis controls appropriate to the question. For transient data, the manual
states that `PSP_NPT` must be specified to generate output print data, for example:

```text
.OPTIONS PSP_NPT=10001
.TRAN 100u 0
.PRINT V(2) I(L1)
```

This is a syntax illustration, not a compulsory time length or sampling count.
Standalone 8.40 produced text `.t2` waveforms. `.t0` contains state/snapshot data;
it is not the same waveform table. Read `.err` when the solver fails.

In a SIMetrix-exported numeric netlist, `.NODE_MAP VOUT 4` provides a named alias:
the tested standalone SIMPLIS syntax is `.PRINT V(#VOUT)` or `.PRINT V(4)`.
`.PRINT V(VOUT)` failed despite that map. Inspect the actual emitted nodes.

Check non-empty, current output, expected columns, finite values and requested time
coverage. Then check behavior relevant to the claim: steady-state values, event
timing, conservation or a specified transient response. A successful solver run
does not mean a stable or physically adequate converter.

```powershell
python scripts/simplis_tools.py waveform <file.t2> --column "V(2)" --start 0.00008 --end 0.0001
```

SIMPLIS inserts event points and may repeat timestamps at transitions. Compute DC
or average power using time integrals, not the mean of rows. Clip integration to
the requested bounds, retain left/right transition values, and avoid extrapolation.
The helper supports the observed whitespace-delimited `.t2` text table and refuses
malformed/truncated data. For other formats, use the matching native export.

`waveform` now reads `.t2` one row at a time for summaries and selected-column
means. It retains adjacent event limits, interpolates window boundaries, compensates
floating-point accumulation and validates the entire file, including rows after a
measurement window. Memory scales with a row and the header, not the point count.
`read_t2` remains the full-memory API when the caller actually needs all rows.
Native Show exports and the existing v2 validators still use their current array
readers; this change does not replace them. Actual million-point measurements:
[validation](../docs/control-storage-validation.md).

## Model fidelity and stopping

- Finite-Ron controlled switches can be appropriate for behavioural work. Zero Ron
  removes the signal from a conduction-voltage current sensor. Switches alone do
  not reproduce body diodes, deadtime, Coss, gate losses, PVT or self-heating.
- Declare independently chosen compensation, sampling windows and initial state.
  A precharged output test is not a startup test; forced synchronous negative
  current is not DCM validation.
- Test submodules at useful boundaries, then check their interaction in the complete
  circuit. Use missing/late samples when timing validity is the issue, not an
  unrelated golden converter.
- Add POP/AC, corners or optimization when needed for the user's question. Evaluate
  warnings for relevance instead of globally ignoring them or requiring textual
  allowlists on every independent run.
- After a timeout or unchanged failure, inspect the log/model/GUI condition before
  retrying. Terminate only processes owned by this task. Keep useful failure evidence.
