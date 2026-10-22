# Run data and cleanup

Use one fresh, task-owned directory per run. `simplis_tools.py run` registers only
files produced in that fresh directory in `storage.json`. It does not adopt an
existing project. The original deck stays outside the run; staged `input.ckt`,
`run.json`, `solver.log`, error messages and saved `metrics.json` survive cleanup.
`run.json` records source, settings, actual solver version banner, status and signal
coverage; supply known conditions/model identifiers with `--environment <JSON>`.
The complete staged deck retains values, directives and inline models. Unknown
temperature/model conditions remain unspecified in reported conclusions.

| Kind | Use | Cleanup |
|---|---|---|
| temporary | Debugging or scan points | Prune registered scratch; successful raw output requires saved metrics |
| candidate | Successful result under consideration; default | Same preview/apply rules; select useful raw evidence before pruning |
| final | Paper evidence or delivery, selected with `--kind final` | Protected automatically; preserve raw evidence |
| diagnostic | Failed run, assigned automatically | Keep input/status/log/error text; redundant raw/scratch may be pruned |

## Small workflow

```powershell
python scripts/simplis_tools.py run --exe <simplis.exe> --deck <circuit.ckt> --out-dir <task/run-01> --kind temporary --environment <conditions.json>
python scripts/simplis_tools.py waveform <task/run-01/input.ckt.t2> --column "V(2)" --start 0.00008 --end 0.0001 --save-run <task/run-01>
python scripts/run_storage.py protect <task/run-01> --reason "Representative result used in the paper"
python scripts/run_storage.py cleanup <task/run-02> <task/run-03>
python scripts/run_storage.py cleanup <task/run-02> <task/run-03> --apply
```

`waveform --save-run` appends the signal/window and time-weighted mean to metrics.
Preserve the relevant raw waveform for critical claims; metrics alone cannot
independently establish ripple, stability or event timing. For task-specific
measurements, `register_metrics(run_path, metrics_dict)` from `scripts/run_storage.py`
registers the saved JSON. Include signal names, windows, definitions and extraction
script/version. Failed metric extraction should stop pruning successful raw data.

For a scan, keep a compact parameter/measurement summary, protect selected and
representative runs, then pass remaining run paths explicitly to cleanup. There is
no recursive project discovery or extension-based deletion. Limit `.PRINT` signals,
`PSP_NPT` and analysis duration to what the question needs. Keep sufficient sampling
to resolve the events and ripple being measured.

## Deletion boundaries and space report

Cleanup previews by default. `--apply` deletes only registered raw/scratch files
whose size and modification time still match registration. It checks resolved
paths against the run root, preserves changed files and unregistered user files,
and never removes directories. Original native sources must stay outside the run;
never register them as scratch. Protected/final runs are retained in full. Protection
is intentionally a one-way CLI action; use a new temporary run for further work.

The report lists actions/reasons, reclaimable bytes, remaining registered bytes
and unregistered files. On tested Windows 8.40, SIMPLIS marks some state outputs
read-only: apply clears that attribute only for eligible unchanged generated files.
Failed cleanup stops at the operating-system error; inspect it before retrying.

This helper manages its own new standalone runs. Existing v2 outputs keep their
current contract; retain selected v2 evidence and clean only task-owned disposable
outputs with an explicitly reviewed file list. They are not automatically enrolled.
External models/native source dependencies remain the task's responsibility. Keep
their versions and required files with final results. Inputs and software identifiers
support reproduction; they do not guarantee identical future solver behavior.
