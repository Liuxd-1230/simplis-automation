# Repository Guidelines

## Project Structure & Module Organization

This repository is a Windows-oriented Python automation skill for SIMetrix/SIMPLIS 8.4. Core source lives in `scripts/`: `simplis_cli.py` is the main CLI, `schematic_generator.py` builds schematic scripts and artifacts, and helper modules handle runtime config, waveform parsing, inspection, optimization, and evidence export. Tests live in `tests/test_*.py`. Reusable symbol and circuit defaults are in `profiles/`, public runtime defaults in `config/`, command notes and generated reference specs in `references/`, and approved sample evidence in `examples/official/`.

## Build, Test, and Development Commands

There is no package build step. Use Python 3.10+ from the repo root.

- `python -m unittest discover -s tests`: run the unit test suite.
- `python scripts/simplis_cli.py show-config`: show resolved SIMetrix and symbol library paths.
- `python scripts/smoke_test.py --timeout 90`: run the lightweight RC smoke validation.
- `python scripts/smoke_test.py --include-buck-run --timeout 240`: run the fuller buck POP/transient validation when SIMetrix is available.
- `python scripts/simplis_cli.py generate-schematic --config references/generated_buck_open_loop_tran.json --out-dir outputs/buck --dry-run`: generate scripts without launching SIMetrix.

## Coding Style & Naming Conventions

Follow the existing stdlib-only style: 4-space indentation, `from __future__ import annotations`, `Path` for filesystem work, `argparse` for CLIs, and JSON-compatible dictionaries for command output. Use `snake_case` for modules, functions, variables, and CLI subcommands. Keep generated filenames descriptive, for example `generated_buck_open_loop_tran.json` or `run_001_evidence.json`.

## Testing Guidelines

Tests use `unittest`, not pytest-specific APIs. Name new files `tests/test_<feature>.py` and test classes `<Feature>Tests`. Prefer deterministic tests that create temporary files with `tempfile`; reserve SIMetrix-dependent behavior for smoke tests or clearly gated integration checks. Add coverage for parser, config, and generator edge cases before changing command behavior.

## Commit & Pull Request Guidelines

Recent history uses short imperative subjects such as `Add SIMetrix waveform export helpers` and `Fix automation script robustness`. Keep commits focused and explain generated reference updates in the body when relevant. Pull requests should include a concise summary, commands run, linked issue if any, and screenshots or exported evidence summaries when schematic output changes.

## Security & Configuration Tips

Do not commit `config/local_config.json`, private schematics, local absolute paths, or raw generated SIMPLIS output. Use `outputs/` or `.codex_tmp/` for scratch work; both are ignored. Public examples should stay limited to approved/open-source-safe evidence.



DO NOT send optional commentary

## Agent skills

### Issue tracker

Issues and PRDs are tracked in GitHub Issues for `Liuxd-1230/simplis-automation`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the five canonical triage labels defined for this repository. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository using root-level domain documentation. See `docs/agents/domain.md`.
