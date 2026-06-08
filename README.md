# SIMPLIS Automation Skill

[中文说明](README.zh-CN.md)

Codex skill for automating SIMetrix/SIMPLIS 8.4 on Windows. It can create SIMPLIS schematics from structured JSON/YAML specs, run POP/transient jobs, add voltage/current probes, validate generated netlists, and support sweep or closed-loop optimization workflows for buck/PMIC experiments.

## What It Includes

- `SKILL.md`: Codex skill instructions.
- `scripts/simplis_cli.py`: Main CLI entry point.
- `scripts/simetrix_waveforms.py`: Waveform export-script generation and SIMetrix `Show` text parsing helpers.
- `scripts/schematic_generator.py`: JSON/YAML to `.sxscr`, `.sxsch`, `.net`, and `.deck` generator.
- `scripts/smoke_test.py`: Local validation for RC and buck examples.
- `references/generated_feedback_divider_hybrid.json`: Small feedback-divider example that uses hybrid local-wire routing.
- `references/generated_buck_open_loop_tran.json`: 12 V buck example with body diodes, inverter-derived `PWM_LS`, POP trigger, 60 us transient, voltage probes, and inline current probes.
- `references/`: SIMetrix/SIMPLIS command notes, DVM notes, optimization guidance, and verified local behavior.

## Requirements

- Windows.
- SIMetrix/SIMPLIS 8.4 installed.
- Python 3.10 or newer.
- Git, if installing from GitHub.

Create a local runtime config after installation:

```powershell
Copy-Item %CODEX_HOME%\skills\simplis-automation\config\simplis_automation_config.json `
  %CODEX_HOME%\skills\simplis-automation\config\local_config.json
```

Edit `config\local_config.json` so `simetrix_exe` and `symbol_lib_dir` point to your installed SIMetrix/SIMPLIS files. You can also set `SIMETRIX_EXE` and `SIMPLIS_SYMBOL_LIB_DIR`, or pass `--simetrix-exe` and `--symbol-lib-dir`.

Check the resolved configuration:

```powershell
python %CODEX_HOME%\skills\simplis-automation\scripts\simplis_cli.py show-config
```

## Install

Clone this repository into your Codex skills folder:

```powershell
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" }
New-Item -ItemType Directory -Force -Path (Join-Path $codexHome "skills") | Out-Null
git clone https://github.com/Liuxd-1230/simplis-automation.git (Join-Path $codexHome "skills\simplis-automation")
```

Restart Codex after installation so the skill metadata is reloaded.

## Validate

Run the lightweight RC smoke test:

```powershell
python %CODEX_HOME%\skills\simplis-automation\scripts\smoke_test.py --timeout 90
```

Run the full buck POP+60 us example:

```powershell
python %CODEX_HOME%\skills\simplis-automation\scripts\smoke_test.py --include-buck-run --timeout 240
```

## Example Use

Generate and run the default probed 12 V buck:

```powershell
python %CODEX_HOME%\skills\simplis-automation\scripts\simplis_cli.py generate-schematic `
  --config %CODEX_HOME%\skills\simplis-automation\references\generated_buck_open_loop_tran.json `
  --out-dir path\to\outputs\generated_buck_open_loop_tran `
  --run --netlist-check --timeout 240 --batch
```

Or ask Codex naturally:

```text
Use simplis-automation to run the 12 V buck POP+60 us example with probes and check VOUT, SW, IL, and PWM waveforms.
```

Generate a cleaner hand-drawn-style feedback block:

```powershell
python %CODEX_HOME%\skills\simplis-automation\scripts\simplis_cli.py generate-schematic `
  --config %CODEX_HOME%\skills\simplis-automation\references\generated_feedback_divider_hybrid.json `
  --out-dir path\to\outputs\generated_feedback_divider_hybrid `
  --netlist-check --timeout 180 --batch
```

Set `routing.mode` to `hybrid` when a schematic should use short local Manhattan wires and only a few boundary `term` labels. Leave it unset for the older, most robust one-label-per-pin behavior.

Export POP/AC vectors from an existing schematic:

```powershell
python %CODEX_HOME%\skills\simplis-automation\scripts\simplis_cli.py make-vector-export `
  --schematic path\to\VRAMPValley.sxsch `
  --out-dir path\to\vectors `
  --out path\to\export_vectors.sxscr `
  --vector simplis_pop1:#VOUT `
  --vector simplis_pop1:#V_SERVO `
  --vector simplis_ac1:46

python %CODEX_HOME%\skills\simplis-automation\scripts\simplis_cli.py run-script path\to\export_vectors.sxscr
python %CODEX_HOME%\skills\simplis-automation\scripts\simplis_cli.py parse-show path\to\vectors\pop_vout.txt --out parsed_vectors.json
```

## Agent Install

If you want an AI coding agent to install this skill for you, point it to [`agent.md`](agent.md).
