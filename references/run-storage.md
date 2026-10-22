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
| temporary | Debugging or scan points | Prune registered scratch; keep raw unless specific files are marked disposable |
| candidate | Successful result under consideration; default | Same file-level rules; retain raw needed for waveform checks |
| final | Paper evidence or delivery, selected with `--kind final` | Protected automatically; preserve raw evidence |
| diagnostic | Failed run, assigned automatically | Keep input/status/log/error and raw; explicitly select redundant raw before pruning |

## Small workflow

```powershell
python scripts/simplis_tools.py run --exe <simplis.exe> --deck <circuit.ckt> --out-dir <task/run-01> --kind temporary --environment <conditions.json>
python scripts/simplis_tools.py waveform <task/run-01/input.ckt.t2> --column "V(2)" --start 0.00008 --end 0.0001 --save-run <task/run-01>
python scripts/run_storage.py protect <task/run-01> --reason "Representative result used in the paper"
python scripts/run_storage.py raw-retention <task/run-02> --file input.ckt.t2 --discard --reason "Disposable scan point; selected raw evidence retained in run-01"
python scripts/run_storage.py cleanup <task/run-02> <task/run-03>
python scripts/run_storage.py cleanup <task/run-02> <task/run-03> --apply
```

`waveform --save-run` appends the signal/window and time-weighted mean to metrics.
Preserve the relevant raw waveform for critical claims; metrics alone cannot
independently establish ripple, stability or event timing. For task-specific
measurements, `register_metrics(run_path, metrics_dict)` from `scripts/run_storage.py`
registers the saved JSON. Include signal names, windows, definitions and extraction
script/version. Saving any metric does **not** authorize deletion of any raw file.
All raw waveforms/state files stay by default. `raw-retention --file ... --discard`
records an explicit file-level decision and reason. Use `--retain` for raw needed
by timing, ripple, transient or independent waveform verification; protect entire
selected runs for finalization/publication. Never mark those files disposable just
because an unrelated average was saved. Changed files are preserved even if an
earlier decision marked them disposable. Failed runs follow the same raw policy.

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
and total bytes, plus unregistered files. On tested Windows 8.40, SIMPLIS marks some state outputs
read-only: apply clears that attribute only for eligible unchanged generated files.
Failed cleanup stops at the operating-system error; inspect it before retrying.

## Default native and v2 execution

The installed `simplis` / `simplis-v2` commands now register normal `run` execution
by default. `run_experiment()` is the common layer for direct API calls and the
default optimizer evaluator. `simplis native` uses the same native runner and
storage policy. `verify` and the original compile/netlist `sweep` register their
verification subdirectories; those commands do not perform electrical simulation.
Compile failures before verification remain in the sweep's aggregate diagnostic
record; custom evaluators are responsible for their external execution records.
Finalization protects its managed input run and registers its own final artifacts.
The stdlib deck runner already registers its outputs.

All execution destinations must be new or empty. Existing results and manually
edited files are preserved; select a new output path for another run. YAML inputs
are snapshotted and checked for changes during execution. Compilation records the
source identity; verification detects changes after compilation. Native execution
runs its staged schematic/module snapshot. Run records link backend measurements
and include registered space use. Raw retention remains explicit per file.

External custom optimizer evaluators, hand-launched simulator commands, arbitrary
native SIMetrix analyses and historical directories are not automatically enrolled.
The default native entry currently supports SIMPLIS transient exports only.

`scripts/run_managed.py` retains its previous commands and `execution/` result paths:

```powershell
python scripts/run_managed.py v2 --experiment <experiment.yaml> --out-dir <fresh-run> --runtime <runtime.json>
python scripts/run_managed.py native --schematic <circuit.sxsch> --out-dir <fresh-run> --runtime <runtime.json> --vector "#VOUT" "#Q"
```

`runtime.json` is optional; shared discovery is used when omitted. Existing fields
`executable` and `symbol_library` remain accepted. Backend dependencies are required. The v2 command calls
the existing runner and preserves its status, validation, analysis isolation and
result files under `execution/`. `run.json` links that result; it does not promote a
failed/unvalidated v2 run to success. Snapshots retain original YAML text plus
original-to-copy paths. Restore that mapping and installed model dependencies when
reconstructing an old run; snapshot filenames are not rewritten executable projects.

The native command stages `.sxsch` and relative `.sxcmp` dependencies in a fresh
directory and uses the existing native execution/export commands. This entry reads
UTF-8 text schematic/module files. Set `--project-root`
when modules live in a wider project tree. Absolute module paths are rejected.
External include/model trees are not automatically bundled. The current path checks
transient Show exports; it does not implement new AC/POP validation. Show readers
keep their existing memory behavior. Native solver success remains separate from
electrical behavior validation. Session isolation follows the existing watchdog:
an unrelated SIMetrix instance prevents launch and is left untouched. Output metadata may be unavailable from
`GetSimulationInfo` in 8.40; a fresh exported native netlist, completion/error state,
group selection and fresh finite vectors supply actual run evidence.

Native workflows keep the original `.sxsch` and its modules authoritative; snapshots
are immutable run inputs. V2 workflows keep the original YAML protocol, with native
schematics as generated outputs. Import GUI edits to YAML before rerunning v2.
Neither path edits the user's original files. Files changed after registration,
input snapshots, source recipes, logs, records and unknown evidence files survive
cleanup. Existing v2 directories are not retroactively enrolled. New managed runs
require an empty destination, so arbitrary project files cannot be claimed as output.

External models/native source dependencies remain the task's responsibility. Keep
their versions and required files with final results. Inputs and software identifiers
support reproduction; they do not guarantee identical future solver behavior.
