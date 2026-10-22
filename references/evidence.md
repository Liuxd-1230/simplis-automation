# Sources and limits

Prefer the manual and library shipped with the installed version. The v3 notes
were developed against SIMetrix/SIMPLIS **8.40, Rel-20.40.0**; other versions need
local confirmation. No complete vendor manual or library is redistributed here.
The curated [user reference](../assets/device-reference/README.md) includes selected
embedded symbol drawings/properties with a separate rights/provenance notice.

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
| D6 | `support/docs/UsersManual.pdf`, section 18.3 | `/s` startup script; `/i` explicitly selects an existing instance |

## Observations, not universal requirements

| ID | Observed in the 8.40 integration work | Implication |
|---|---|---|
| O1 | Raw subcircuits could not resolve the used top-level model definitions; local definitions worked. | Check model scope for this dialect. |
| O2 | A standalone transient run without `PSP_NPT` returned success but no `.t2`; specifying it produced the waveform table. | Exit code alone is insufficient. |
| O3 | Native symbol properties and hierarchical module ports changed emitted nets/subcircuits. | Inspect the actual netlist at new boundaries. |
| O4 | Some GUI print attempts produced blank PDFs; repeated file opens could leave dialogs. | Inspect visual output and diagnose before retrying. |
| O5 | Time-stamped event data contains nonuniform/repeated times. | Integrate over time; preserve transition limits. |
| O6 | Revision-8 bare wire records were ignored without native `.Wire` objects. | Preserve full wire objects or use native placement. |
| O7 | Direct OpenSchem/SaveAs on a child container removed its external symbol. | Preserve its interface; validate from its parent. |
| O8 | `CloseSchem /force` stalled the batch; documented `CloseSheet /force` completed. | Use the matching documented command. |
| O9 | Standalone named-node PRINT needed `#VOUT` with `.NODE_MAP`. | Check exported node aliases and print syntax. |
| O10 | Solver state files `.t0`/`.tc` were read-only on Windows. | Cleanup may change that attribute only for eligible owned generated files. |

The experience motivating these notes included independent native drawing,
submodule checks, a complete behavioural closed loop and re-running its exported
hierarchical netlist. That is evidence for the workflow, not a transferable circuit
template, performance guarantee or transistor-level validation. Private source
circuits and raw measurements remain in their originating task workspace.

The helper's automated tests use synthetic data and tiny test circuits. They check
parser/runner semantics; they do not certify a user's power converter. Existing
v2 catalog hashes are installation-bound records and do not override local facts.

The v3.1 opt-in integration checks use the actual installed libraries and solver,
including a deliberately invalid node for failure handling. They are separate from
unit-test fixtures. Public results and rerun commands: [validation](../docs/v3.1-validation.md).
