# Repository Guidelines

## Structure

The repository root contains the default v2 Python package under
`src/simplis_automation_v2`, unit tests under `tests/`, locked device evidence under
`catalog/`, and public circuits under `examples/`. The retired implementation is
preserved under `legacy/v1/` and is not a default entry point.

## Development commands

- `python -m pip install -e .`: install `simplis` and the `simplis-v2` alias.
- `python -m unittest discover -s tests -p "test_*.py"`: run the unit suite.
- `simplis doctor --catalog catalog/seed_8_4.yaml`: static runtime/catalog check.
- `simplis run <experiment> --out-dir <dir>`: one-process fast path.
- `simplis finalize <experiment-result.json> --out-dir <dir>`: strict delivery gate.

Tests use `unittest`. Keep Windows-native behavior behind small testable boundaries;
mock GUI/process calls in unit tests and reserve installed SIMetrix for integration
checks. Never add an automatic calibration fixture to doctor.

## Safety and publishing

Do not commit private schematics, local absolute paths, screenshots or raw generated
outputs. Preserve unrelated worktree changes. Commit focused changes with imperative
subjects and run unit plus relevant SIMetrix integration checks before publishing.

Issue tracking, triage and domain documentation rules live under `docs/agents/`.
