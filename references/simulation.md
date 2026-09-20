# Simulation and waveform checks

## Choose the execution path

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

For an existing schematic, `simplis_run` uses the native schematic workflow.
`Netlist /simplis` exports hierarchy; `RunSIMPLIS` is a lower-level command and does
not perform that preprocessing itself. A GUI command returning is not sufficient
proof of completion. Check actual solver output; don't wait on a status string
alone or accept stale vectors left by a previous run.

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
