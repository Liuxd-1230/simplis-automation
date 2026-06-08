# DVM Notes

DVM is a testplan/report automation layer, not a separate SIMPLIS command-line simulator.

Use DVM when the task is a validation matrix:

- line/load corners
- startup
- load transient
- AC/POP stability
- efficiency
- temperature/model corners
- candidate topology comparison

Minimum DVM ingredients:

- a working SIMetrix/SIMPLIS schematic
- a DVM control symbol
- a `.testplan` file
- optional `preprocess.sxscr` and `postprocess.sxscr`

Example installed files:

```text
<SIMPLIS_INSTALL_DIR>\support\dvm\syncbuck_1in_1out.testplan
<SIMPLIS_INSTALL_DIR>\support\dvm\dcdc_1in_1out.testplan
<SIMPLIS_INSTALL_DIR>\support\dvm\efficiency_dcdc_1in_1out.testplan
<SIMPLIS_INSTALL_DIR>\support\dvm\preprocess.sxscr
<SIMPLIS_INSTALL_DIR>\support\dvm\postprocess.sxscr
<SIMPLIS_INSTALL_DIR>\support\symbollibs\SIMPLIS_DVM_ADVANCED.sxslb
```

Typical `.testplan` row shape:

```text
*?@ analysis	objective	source	load	label
Ac	BodePlot(OUTPUT:1)	"SOURCE(INPUT:1, Nominal)"	"LOAD(OUTPUT:1, Light)"	Ac Analysis|Bode Plot|Vin Nominal|Light Load
Transient	"StepLoad(OUTPUT:1, Light, 100%)"	"SOURCE(INPUT:1, Nominal)"		Transient|Step Load|Vin Nominal|Light Load to 100% Load
```

Preprocess scripts can inspect labels and DVM properties:

```text
Arguments @retval label report_dir log_file controlhandle
PropValues2('OUTPUT1_NOM', -1, 'handle', controlhandle)
```

Current uncertainty: a public, stable `RunDVM testplan` command has not been confirmed. When asked to automate DVM, first test on an installed DVM tutorial schematic, then identify the menu/internal command or script that launches the DVM run in the local installation.
