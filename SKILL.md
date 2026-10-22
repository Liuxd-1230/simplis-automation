---
name: simplis-automation
description: Consult SIMetrix/SIMPLIS devices, build editable schematics or submodules, and run or inspect circuit simulations.
metadata:
  version: "3.1.0"
---

# SIMPLIS Automation v3

Use the smallest useful path for the requested result. Reference lookup, drawing,
simulation and model validation are separate tasks, not mandatory stages of one
pipeline. Follow the user's circuit, model fidelity and chosen tools.

## Read only the relevant reference

| Task | Reference |
|---|---|
| Find a device, its pins, parameters or model | [Devices](references/devices.md) |
| Create/edit `.sxsch` or `.sxcmp`; inspect connectivity | [Native schematics](references/native-schematics.md) |
| Run a deck, read waveforms, diagnose or measure | [Simulation](references/simulation.md) |
| Retain evidence, inspect disk use or prune generated runs | [Run storage](references/run-storage.md) |
| Explicitly use the existing YAML compiler, sweeps or strict finalization | [Optional v2 backend](references/v2-workflow.md) |

Direct native commands, standalone SIMPLIS, small task scripts and the existing v2
backend are all valid approaches. The optional stdlib helper
`python scripts/simplis_tools.py --help` supports library queries, self-contained
deck runs and `.t2` measurements without installing the backend. Functional lookup:
`python scripts/simplis_tools.py find --query "测量支路电流"`. Start with the
curated [user reference](assets/device-reference/README.md); if unsuitable or absent,
inspect installed official libraries, then matching-version manuals/examples.

## Deliver the requested circuit source

- For simulation-only work, a standalone deck is sufficient. State its path,
  parameter changes and whether an editable schematic exists.
- For circuit design, modification or delivery, maintain the native `.sxsch` and
  its `.sxcmp` dependencies as the authoritative source; export decks from it.
  Rebuild from a deck when needed and check the exported topology. Arbitrary
  netlist-to-schematic conversion is not promised.
- Run in a fresh task-owned directory. The helper registers generated files;
  extract metrics, protect representative/final raw evidence, then preview cleanup.
  Never recursively delete by extension or adopt a user's project as scratch space.
- Deliver a short explanation of artifact paths, circuit function/changes,
  editability and actual checks/results. Include a relevant preview when useful;
  do not require a screenshot, large report or internal-tool approval for every task.

## Evidence and scope

- Check suitability of the user reference, then prefer the installed library and matching-version manual for syntax and device
  facts. The [source notes](references/evidence.md) distinguish documented behavior
  from observations. Catalog approval belongs to the v2 compiler contract; it is
  not a prerequisite for looking up or independently using a native device.
- Keep user schematics and research circuits in the task workspace. Derive the
  design from the user's specification; examples and golden cases are optional
  test material, never an automatic starting circuit or prerequisite run.
- Check what supports the requested claim: pins/properties for lookup; readable
  layout and intended connections for a drawing; fresh solver output and relevant
  electrical behavior for simulation. A picture, exit code or passing fixture alone
  does not establish circuit correctness.
- State material assumptions and remaining model limits. Ideal switches can replace
  MOSFETs when appropriate; retain finite on-resistance if the method senses their
  conduction voltage. Read the simulation reference when that approximation matters.
- Finish when the requested artifact and necessary checks are complete. Add POP,
  corners, optimization, clean-reopen or screenshots when the question needs them,
  rather than attaching the entire workflow to every task. Retry against new error
  evidence; an unchanged failure calls for diagnosis, not repeated waits.
