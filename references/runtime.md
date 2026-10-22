# Runtime discovery and version selection

Use `simplis doctor` for JSON or `simplis doctor --format text` for a readable
summary. Both are static: no simulator, fixture or license checkout is started.
The dependency-free helper uses the same `simplis_automation_v2.runtime` module.

## Configuration

Priority: explicit arguments/API mapping → project configuration → environment →
Windows uninstall/App Paths records or PATH → bounded common installation locations.
Common locations are checked on local fixed drives; there is no recursive disk scan.
Project files are `simplis-runtime.json`, `.simplis-runtime.json` or
`simplis-runtime.yaml`, searched from the task directory through its parents.
Relative paths are resolved against the configuration file. YAML configuration
requires the optional backend's PyYAML dependency; JSON discovery uses stdlib only.

```json
{"install": "<your-installation-root>", "version": "8.40"}
```

Replace the placeholder, or omit `install` to use system discovery. No machine path
is distributed as a required default. Existing `--simetrix-exe`, `--symbol-lib-dir`,
`--runtime-config` and mapping aliases remain accepted. `--install` accepts a root
or executable; `--version 8.30` requires that version without silently changing it.
Existing variables remain: `SIMETRIX_EXE`, `SIMPLIS_INSTALL`, `SIMPLIS_EXE`,
`SIMPLIS_SYMBOL_LIB_DIR`, `SIMPLIS_AUTOMATION_CONFIG`; `SIMPLIS_VERSION` selects a version.
Explicit invalid paths fail instead of using another installation.

## Resources and evidence

GUI, solver, SxCommand, symbols, models, docs and examples are derived from one root.
An explicit symbol directory from another installation is rejected. PE file-version
resources confirm versions on Windows. Path names only produce an `inferred_version`;
they cannot make a runtime ready. Conflicting program versions prevent execution.
Doctor exposes program version evidence, candidates, paths, library checks and fixes.
Readiness represents static prerequisites; license availability needs a real run.

Native execution uses the shared `startup_command()`. For a script path containing
spaces, `/s` receives a command calling the documented `Execute(string)` function.
This preserves the complete path; normal OS quoting of a bare startup filename
alone failed on the tested 8.40 installation. No aliases or duplicate scripts are
created. Drawing, verification, finalization and the watchdog share this behavior.

Among compatible discovered installations, matching recorded library content and
verified feature coverage take priority; 8.40 breaks ties. YAML/catalog target
versions constrain automatic selection. Explicit user/project/environment choices
remain authoritative, and incompatible catalog resources produce a failure.
Selection and the catalog constraint are stored in run metadata.

`seed_8_3.yaml` and `seed_8_4.yaml` contain portable library/model paths and retained
content hashes. Old absolute `support/...` records are rebound to the selected root,
even if the historical installation still exists. Content changes require
revalidation. The two path-only seed migrations preserve their exact historical
catalog fingerprints through content-bound identities in `compatibility.json`;
changing their semantic content invalidates that identity. Existing custom catalog
fingerprints keep their original calculation.

`compatibility.json` packages the recorded content identities and feature coverage
for stdlib discovery. It is evidence for the listed fixtures, not all devices or
all features of a release. Reference lookup reports `requires_revalidation` when
its 8.40 evidence cannot be matched; electrical behavior stays separately qualified.

## Opt-in real verification

```powershell
python tests/validate_runtime_compatibility.py --out-dir <fresh-validation-directory>
python tests/validate_runtime_compatibility.py --version 8.30 --out-dir <fresh-830-directory>
```

The script records missing/unconfirmed installations separately, runs real native
roundtrip/hierarchy/preprocessing/transients and the default native CLI, and uses
the existing 8.40 v2 control behavioral fixture. 8.30 advanced v2 behavior remains
unverified until a matching qualified fixture is run. Ordinary unit discovery never
starts a simulator. See the [actual validation matrix](../docs/runtime-compatibility-validation.md)
for 8.30 absence, the observed AC automation timeout and path validation scope.
