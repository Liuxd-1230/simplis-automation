# Make v3 reference-led and preserve the optional v2 backend

Device lookup, editable drawing, behavioural simulation and publication validation
have different completion criteria. A fixed YAML/run/finalize pipeline over-scopes
small requests and conflicts with explicit native-tool choices.

Use one short root skill and load device, schematic or simulation references on
demand. Retain the v2 CLI/API and its evidence contract unchanged for users who
choose it. Offer a stdlib helper for repeated low-level tasks, not a new framework.
Rename the archived v1 skill entry to `WORKFLOW.md` to avoid duplicate discovery.

Evidence comes from matching-version installed manuals/libraries and actual-task
checks. Examples are optional test inputs. Native schematic roundtrip and electrical
tests remain useful when they support the requested claim; screenshot and strict
backend finalization requirements do not apply to unrelated lookup or deck work.

This supersedes the default-workflow part of the earlier v2 promotion decision,
not the implementation or correctness requirements of the v2 backend.
