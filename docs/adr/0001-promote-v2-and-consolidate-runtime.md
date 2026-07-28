# Promote v2 and consolidate each analysis into one process

The evidence-first v2 implementation is the repository default and v1 is archived
under `legacy/v1/`. Each analysis partition combines creation, netlisting, analysis
cards, simulation and export in one task-owned process; this avoids repeated startup
cost while retaining per-task isolation instead of a stateful resident SIMetrix
worker.
