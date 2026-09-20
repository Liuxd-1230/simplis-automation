# Repository Guidelines

## Structure

Root `SKILL.md` is the v3 reference-led entry point. Conditional references live in
`references/`; `scripts/simplis_tools.py` is an optional stdlib helper.
The existing v2 backend stays under `src/simplis_automation_v2`, with tests under
`tests/`, device evidence under `catalog/`, and public examples under `examples/`.
Legacy v1 is archived with `WORKFLOW.md`, not another registered skill.

## Development

- `python -m unittest discover -s tests -p "test_v3_tools.py"`: dependency-free helper tests.
- `python -m pip install -e .`: install the optional backend and its dependencies.
- `python -m unittest discover -s tests -p "test_*.py"`: backend and helper unit tests.

Use unittest and stdlib where practical. Test changed behavior and meaningful edge
cases; gate simulator integration on an available installation. Reference-only tasks
need no simulator run. Do not add automatic fixture runs to runtime discovery.

## Publishing

Preserve unrelated worktree changes. Keep private circuits, machine-specific paths
and raw generated output in ignored `outputs/` directories. Before publishing,
run tests for changed code and relevant installed-SIMPLIS checks when execution
behavior changes. Existing examples are optional test inputs, not design templates.

Read `CONTEXT.md` and relevant `docs/adr/` for skill/backend boundaries or architecture.
Issue tracking and triage rules under `docs/agents/` apply when doing that work.
