# simplis-automation v2

`v2/` is an isolated, evidence-first rewrite of the SIMPLIS automation skill.
It does not modify the legacy `scripts/` implementation.

The public workflow is deliberately narrow:

1. `simplis-v2 doctor` verifies the local SIMetrix/SIMPLIS runtime.
2. `simplis-v2 catalog import`, `catalog proof-compile`, `catalog proof-run` and
   `catalog approve` create a six-proof locked local device catalog. Proof commands
   are evidence-only and never scoring eligible.
3. `simplis-v2 compile` turns a YAML circuit graph into a reviewed build manifest and SIMetrix script.
4. `simplis-v2 verify` creates the editable schematic and actual SIMPLIS netlist.
5. `simplis-v2 visual-evidence` records the responsive clean-reopen screenshot gate,
   active schematic, dimensions, required layout checks and concrete findings.
6. `simplis-v2 run` runs one structured experiment through the watchdog, vector
   provenance checks, electrical validation and mandatory charts.
7. `simplis-v2 sweep` repeats compile/verify for a deterministic parameter grid.
8. `simplis-v2 optimize` exhausts a recoverable controller-only budget with ESR
   continuation; failed candidates retain evidence and receive finite penalties.
9. `simplis-v2 roundtrip` preserves an existing `.sxsch` through derived SaveAs,
   clean reopen and two real netlists without overwriting the input.

The 8.4 catalog inventory command accepts all supported evidence families:

```powershell
simplis-v2 catalog inventory --symbol-lib-dir D:\Simplis8.4\support\symbollibs `
  --model-dir D:\Simplis8.4\support\models `
  --example-dir D:\Simplis8.4\support\examples\SIMPLIS --out inventory.json
```

`ideal_native` approval requires six recorded proofs: matching library hash, pin
contract, GUI placement, clean reopen, real netlist and a minimum behavior test.
Dynamic DIGI1/Logic BB discovery alone is not approval.

Simulation completion is an ordered contract:

`launched → script_started → simulation_started → running → simulation_returned → errors_collected → vectors_exported → validated → complete`

Vectors are scoring-eligible only when the exit/error/status/warning checks, current
group analysis and vector contract, freshness/hash checks, electrical behavior and
required charts all pass. Otherwise they remain `diagnostic_only`.

The compiler never labels a design as simulated.  It distinguishes static validation,
netlisting, fully-ideal designs, and boundary-only opaque-module designs.

`examples/acot_buck_v84.yaml` and its experiment are the public 12 V → 1.2 V,
500 kHz A-COT golden specification. The exact 8.4 comparator and grounded S/R latch
bindings, native one-shot, complementary buffer and asymmetric-delay bindings have
all six catalog proofs. VCVS, VCCS, VPWLR idealized diode, zero-DC `AC 1` source,
two-node Bode probe and grounded DIGI1 AND2 are also approved from public proof
fixtures. `DEVICE_SUMMARY_8_4.md` records the functional-block mapping,
actual netlist models, tunable properties and evidence files. The generator never
substitutes a Generic/HC/vendor wrapper.

Capacitor-voltage and inductor-current initial conditions are declared as typed
`IC` properties and encoded into the native reactive VALUE. Cold-start and POP
use separate parameter sets: POP derives C/L/ripple state from the operating point,
and every load-step source holds its V1 value with `IDLE_IN_POP=YES`. The public
reactive-IC fixture checks value, P-to-N sign and RC/RL decay after clean reopen.

Transient experiments inject `PSP_NPT=10001` unless the experiment declares its own
value. This prevents a SIMPLIS data group from listing continuous vectors whose
sample arrays are empty. The exporter also checks `Length()` before `Show`, because
SIMetrix 8.4 can block when asked to export a present-but-empty vector.

SIMPLIS transient samples are event-dense and are not uniformly spaced. DC current,
voltage and input/output power metrics therefore use time-weighted trapezoidal
integration over the declared steady window. A plain arithmetic mean over exported
sample rows is invalid because it overweights switching edges and on-time samples.
The golden Buck additionally requires `Pout/Pin >= 0.8` before its waveforms pass
electrical validation.

`GetSimulatorStatus() == None` remains incomplete unless doctor has persisted a
`calibrated_none_after_return` result and the current run independently satisfies
every stage, progress, exit/error/warning, `GetSimulationInfo()` and fresh-vector
condition. All other `None` runs remain `diagnostic_only`.

The sole exception is `catalog proof-run`: it may preserve a passed device behavior
proof when `None` is the only completion limitation and all independent stage,
no-error, group, freshness, behavior and chart checks pass. It deliberately stops
at `validated`, reports `normal_simulation_complete=false`, and remains
`scoring_eligible=false`; the exception never applies to normal runs or optimization.
