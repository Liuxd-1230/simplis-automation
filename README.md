# SIMPLIS Automation v3

A lightweight skill for device reference, native schematic/submodule work and
SIMPLIS simulation. Start with [SKILL.md](SKILL.md); read only the reference needed
for the task. [中文说明](README.zh-CN.md).

## What changed

v3 makes the skill reference-led instead of prescribing a complete execution
pipeline. Device lookup does not start the simulator. A drawing does not imply a
sweep. A native deck does not need conversion to YAML. Examples and golden cases
remain optional; private research circuits are not bundled as templates.

The v2 Python package, CLI names and strict verification contract remain intact as
an **optional backend**. v3 is the skill version; it does not rename the existing
`simplis_automation_v2` API or change its package version. Legacy v1 instructions
are now a plain `WORKFLOW.md`, so the repository exposes only one `SKILL.md`.

## Use without installing Python dependencies

Python 3.10+ and the repository files are sufficient for the reference helper:

```powershell
python scripts/simplis_tools.py symbols --library <installed-passives.sxslb> --query cap
python scripts/simplis_tools.py symbols --library <installed-simplis.sxslb> --name simplis_prim_vcswitch
python scripts/simplis_tools.py run --exe <installed-simplis.exe> --deck <task.ckt> --out-dir <new-run-directory>
python scripts/simplis_tools.py waveform <run-directory>/input.ckt.t2 --column "V(2)" --start 0.00008 --end 0.0001
```

The helper runner stages **self-contained** decks in a fresh directory. For external
includes, use the native CLI in a task-owned directory with its dependencies or the
existing backend. These helpers are conveniences, not required interfaces.

Install the repository root as the `simplis-automation` skill using your skill
installer. No simulator install, package install or fixture run is needed just to
read the references.

## Optional v2 backend

```powershell
python -m pip install -e .
simplis --help
```

`simplis` and `simplis-v2` retain their existing behavior. See
[the backend reference](references/v2-workflow.md) when choosing that path.

## Development and evidence

```powershell
python -m unittest discover -s tests -p "test_v3_tools.py"
# With backend dependencies installed:
python -m unittest discover -s tests -p "test_*.py"
```

[Sources and limits](references/evidence.md) identify the installed manuals and
observations behind the advice. Keep installed-library excerpts, machine paths,
private circuits and generated waveforms outside version control.
