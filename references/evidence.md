# Sources and limits

Prefer the manual and library shipped with the installed version. The v3 notes
were developed against SIMetrix/SIMPLIS **8.40, Rel-20.40.0**; other versions need
local confirmation. No vendor manual or library is redistributed here.

## Documented sources

Locations below are installation-relative. Search the title/section rather than
assuming PDF page indices match printed page numbers.

| ID | Source | Relevant sections |
|---|---|---|
| D1 | `support/docs/UsersManual.pdf` | 6.2 Hierarchical Schematic Entry: `.sxcmp`, `MODEL=X`, module ports |
| D2 | `support/docs/ScriptReference.pdf` | Inst; Wire; Prop; SaveAs; Netlist; PreProcessNetlist; RunSIMPLIS; OpenPDFPrinter; PrintSchematic |
| D3 | `support/docs/SIMPLIS_Reference.pdf` | 3.2 Device Types; 4.2 Models; 6.2 Options (`PSP_NPT`); 8.6 Time-domain Data Output |
| D4 | `support/symbollibs/*.sxslb`, associated `support/models/` files | Actual symbol pins/order/coordinates, properties, templates and model definitions |
| D5 | `bin/simplis.exe -help` | Native CLI: `simplis ifile [-f]` |

## Observations, not universal requirements

| ID | Observed in the 8.40 integration work | Implication |
|---|---|---|
| O1 | Raw subcircuits could not resolve the used top-level model definitions; local definitions worked. | Check model scope for this dialect. |
| O2 | A standalone transient run without `PSP_NPT` returned success but no `.t2`; specifying it produced the waveform table. | Exit code alone is insufficient. |
| O3 | Native symbol properties and hierarchical module ports changed emitted nets/subcircuits. | Inspect the actual netlist at new boundaries. |
| O4 | Some GUI print attempts produced blank PDFs; repeated file opens could leave dialogs. | Inspect visual output and diagnose before retrying. |
| O5 | Time-stamped event data contains nonuniform/repeated times. | Integrate over time; preserve transition limits. |

The experience motivating these notes included independent native drawing,
submodule checks, a complete behavioural closed loop and re-running its exported
hierarchical netlist. That is evidence for the workflow, not a transferable circuit
template, performance guarantee or transistor-level validation. Private source
circuits and raw measurements remain in their originating task workspace.

The helper's automated tests use synthetic data and tiny test circuits. They check
parser/runner semantics; they do not certify a user's power converter. Existing
v2 catalog hashes are installation-bound records and do not override local facts.
