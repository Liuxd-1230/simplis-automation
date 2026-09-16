# Device lookup

Use this for a device fact or when selecting a symbol/model. A lookup ends once the
needed definition and its source are established; it does not launch a simulator.

## Sources

Find the installed version from the user's configuration or executable location.
Typical installation-relative locations are `support/symbollibs/*.sxslb`,
`support/models/` and `support/docs/`. Search only the likely library first.

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
