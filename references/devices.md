# Device lookup

Use this for a device fact or when selecting a symbol/model. A lookup ends once the
needed definition and its source are established; it does not launch a simulator.

## Sources

Start with [the curated user reference](../assets/device-reference/README.md):
24 device types with Chinese functions, actual pins, example instance values and
verification scope. `devices.sxsch` is an unwired reference sheet. Its original
`.sxcmp` filename did not give it an external module interface.

```powershell
python scripts/simplis_tools.py find --query "需要测量支路电流"
python scripts/simplis_tools.py find --query transformer --install <installation-root>
python scripts/simplis_tools.py find --query "电压控制开关" --official --install <installation-root>
python scripts/simplis_tools.py locate --install <installation-root-or-executable> --query ScriptReference
```

`find` uses bilingual functional aliases and descriptions. Reference matches return
the original symbol definition, pin order/coordinates, property definitions,
instance defaults, version and provenance. This is a bounded index: refine the
function name or translate it when no alias matches. Inspect suitability before
selecting a result. `--official` continues to installed libraries when reference
choices are unsuitable. With no reference match it automatically searches installed
libraries; provide `--install`, `SIMPLIS_INSTALL` or a `simplis.exe` on PATH.
Match lists are bounded and include counts.

An inline current probe goes **in series**, with its arrow defining positive current.
The actual symbol emits a zero-volt sensing source plus probe directives. Replacing
its properties with an ordinary DC source loses that probe behavior. A voltage
probe attaches to a node. An independent current source drives current. Select by
the task and actual template.

Some supplied digital symbols are embedded definitions backed by
`support/models/SIMPLIS_DIGI1.lb`; they are not standalone entries in the tested
`.sxslb` files. The index records this distinction. Parameterized models require
native preprocessing. Default values are examples; required instance/model fields
follow `SIMPLIS_TEMPLATE` and the matching manual. The `vcvs`/`vccs` reference symbols
are marked Obsolete in the installed source; inspect available alternatives as needed.

Find the installed version from the user's configuration or executable location.
Typical installation-relative locations are `support/symbollibs/*.sxslb`,
`support/models/` and `support/docs/`. Search only the likely library first.

`locate` reports local manual/example paths and useful manual topics without
copying manuals. Use existing PDF extraction or text search for chapter contents:
`ScriptReference.pdf` for Inst/Wire/Prop/Netlist/PreProcessNetlist; `UsersManual.pdf`
for probes, hierarchy and command-line options; `SIMPLIS_Reference.pdf` for device
equations, models, initialization and output controls. Search installed models by
`PARAM_MODEL_NAME` and official examples by the device/function.

```powershell
python scripts/simplis_tools.py symbols --library <library.sxslb> --query switch
python scripts/simplis_tools.py symbols --library <library.sxslb> --name simplis_prim_vcswitch
```

The helper reports the actual library hash, symbol block, pin order/coordinates and
properties. This is a local definition, not proof that the model is loaded or that
a complete circuit works. Multiple symbol-name matches are reported as ambiguous.

| Need | Likely library / search term |
|---|---|
| R, C, L | `passives.sxslb`: `res`, `cap`, `ind` |
| Controlled switch | `simplis.sxslb`: `simplis_prim_vcswitch` |
| Net label / hierarchy port | `connection.sxslb`: `term`, `modport`, `scterm` |
| Sources | `sources.sxslb`; inspect `SIMPLIS_TEMPLATE` |
| Logic / control | installed SIMPLIS logic libraries or documented native primitives |

These names were observed in 8.4, not promised for every installation. Source map:
[evidence](evidence.md), D1-D3 and O1.

## Important distinctions

- A symbol pin's drawing position is not its netlist order. Read `order` and the
  symbol template. Rotation also transforms pin coordinates.
- Device properties, model parameters and netlist template expressions are different
  layers. Inspect the emitted netlist when wiring a new device boundary.
- In 8.4 native primitives, `!D` logic has output(s), reference node, then input(s).
  COMP, INV and AND/SR models have different required parameters; AND/SR include
  `LOGIC`. Consult the matching manual instead of substituting a generic gate name.
- Put models used by a raw subcircuit in that subcircuit's scope. Top-level model
  definitions were not resolved inside independently authored subcircuits in the
  tested 8.40 runtime. Avoid assuming SPICE dialects have identical scoping.
- Digital-domain gates without a ground reference are not automatically valid at
  analog switch-control inputs. Inspect their documented analog/digital boundary.
- `catalog/` contains historical, machine-bound v2 evidence, not a portable vendor
  library. Reuse its facts only after checking the local definition/version. Its
  approval mechanism remains relevant when using the v2 compiler.
