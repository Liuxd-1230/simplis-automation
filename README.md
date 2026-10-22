# SIMPLIS Automation v3

A lightweight skill for device reference, native schematic/submodule work and
SIMPLIS simulation. Start with [SKILL.md](SKILL.md); read only the reference needed
for the task. [中文说明](README.zh-CN.md).

## What changed

v3.1 adds the curated editable [device reference](assets/device-reference/README.md),
bilingual functional lookup, installed-document discovery, two editable
[native examples](examples/native/README.md), and preview-first
[run storage](references/run-storage.md). The helper preserves inputs/logs/metrics,
protects selected evidence and prunes only registered unchanged generated files.

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
python scripts/simplis_tools.py find --query "测量支路电流"
python scripts/simplis_tools.py find --query transformer --install <installation-root>
python scripts/simplis_tools.py locate --install <installation-root-or-executable> --query ScriptReference
python scripts/simplis_tools.py symbols --library <installed-passives.sxslb> --query cap
python scripts/simplis_tools.py symbols --library <installed-simplis.sxslb> --name simplis_prim_vcswitch
python scripts/simplis_tools.py run --exe <installed-simplis.exe> --deck <task.ckt> --out-dir <new-run-directory>
python scripts/simplis_tools.py waveform <run-directory>/input.ckt.t2 --column "V(2)" --start 0.00008 --end 0.0001
python scripts/run_storage.py cleanup <run-directory>
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
private circuits and generated waveforms outside version control, except explicitly
curated public reference/example files. Selected vendor symbols retain their rights;
see [third-party notes](assets/device-reference/README.md).

Opt-in real integration (not run by unittest discovery):

```powershell
python tests/validate_v3_native.py --gui <SIMetrix.exe> --solver <simplis.exe> --out-dir <fresh-native-check>
python tests/validate_v3_runtime.py --solver <simplis.exe> --install <installation-root> --out-dir <fresh-runtime-check>
```

The native test requires optional backend dependencies and checks actual exported
topology plus an RC/feedback transient. The runtime test checks real library
fallback, standalone success/failure, scan retention and cleanup. Tested versions,
measurements and remaining limits are recorded in [v3.1 validation](docs/v3.1-validation.md).
