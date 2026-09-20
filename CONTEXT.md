# SIMPLIS Automation

The v3 skill is a small router to task-specific references and optional tools.
The v2 backend remains an opt-in implementation with its existing strict contract.
Its vocabulary below describes that backend, not mandatory steps for every task.

## Language

**Fast Run**:
A task-owned SIMetrix session that creates, netlists, simulates and exports one analysis partition.
_Avoid_: verification chain, multi-stage run

**Finalization**:
The clean-reopen, re-netlist and native screenshot gate applied once to a selected completed result.
_Avoid_: routine verification, visual smoke test

**Actual-Task Evidence**:
Completion evidence produced by the user circuit itself: ordered tokens, exit/error state, analysis groups and fresh vectors.
_Avoid_: calibration-only evidence

**Visual Review**:
A hash-bound Codex multimodal assessment of a native task-window screenshot.
_Avoid_: GUI proof, electrical proof

**Legacy v1**:
The archived first-generation automation implementation under `legacy/v1/`.
_Avoid_: default automation, fallback implementation
