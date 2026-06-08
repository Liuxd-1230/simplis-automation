# Parameter Injection and Metric Export

The closed-loop optimizer needs a project-specific `.sxscr` template. The template should:

1. Open or create the schematic.
2. Inject parameter values from placeholders such as `{{ramp_gain}}`.
3. Run `simplis_run` or a netlist/deck flow.
4. Calculate scalar metrics.
5. Write `{{RESULT_JSON}}`.
6. End with `Quit` for batch jobs.

Confirmed local command/function names worth using or checking:

```text
OpenEchoFile
CloseEchoFile
ChangeSymbolProperty
Prop
SetComponentValue
SetInstanceParamValue
SetModelParamValue
GetCurveVector
Maximum
Minimum
Mean1
RMS1
WriteRawData
WriteSchemProp
GetSIMPLISExitCode
GetSimulationErrors
```

Use these as a search list in `D:\Simplis8.4\support\docs\ScriptReference.pdf` and installed examples before finalizing a real measurement script.

## Template Shape

Example skeleton:

```text
OpenSchem "D:\path\to\buck_controller.sxsch"

; Project-specific injection. Replace with the verified command for the symbol/property.
; SetComponentValue "R_RAMP_GAIN" "{{ramp_gain}}"
; SetInstanceParamValue "X_SERVO_OTA" "gm" "{{servo_gm}}"

simplis_run

; Project-specific measurement section. Export JSON compatible with closed_loop_optimize.py.
; OpenEchoFile redirects Echo output to a file.
Let echo_file = OpenEchoFile('{{RESULT_JSON}}', 'w')
Echo vout_dc_error_mv=0.5
Echo loadstep_undershoot_mv=30.0
Let close_result = CloseEchoFile()

Quit
```

## Metric Contract

Write key-value metrics, which are easiest from SIMetrix scripts:

```text
vout_dc_error_mv=0.5
loadstep_undershoot_mv=30.0
phase_margin_deg=58.0
```

or direct JSON metrics:

```json
{"vout_dc_error_mv": 0.5, "loadstep_undershoot_mv": 30.0, "phase_margin_deg": 58.0}
```

or wrapped metrics:

```json
{
  "candidate": {"ramp_gain": 1.0},
  "metrics": {
    "vout_dc_error_mv": 0.5,
    "loadstep_undershoot_mv": 30.0,
    "phase_margin_deg": 58.0
  }
}
```

Missing metric files, empty metric files, nonzero SIMetrix return codes, and explicit `failed=true` or `"failed": true` are treated as failed candidates.

## Practical Advice for Buck PMIC Work

- Prefer changing named global variables or small behavioral blocks over editing many component values.
- Put every tuned variable in one schematic-visible parameter table or controller subcircuit so the script can change values by name.
- During early development, make a postprocess block that writes only 3-5 scalars: DC error, startup overshoot, load-step undershoot, recovery time, and jitter.
- Add phase margin/POP metrics later; they often need different analysis setup.
