# SIMPLIS Automation Skill

[中文说明](README.zh-CN.md)

Codex skill for automating SIMetrix/SIMPLIS 8.4 on Windows. It can create SIMPLIS schematics from structured JSON/YAML specs, run POP/transient jobs, add voltage/current probes, validate generated netlists, and support sweep or closed-loop optimization workflows for buck/PMIC experiments.

## What It Includes

- `SKILL.md`: Codex skill instructions.
- `scripts/simplis_cli.py`: Main CLI entry point.
- `scripts/schematic_generator.py`: JSON/YAML to `.sxscr`, `.sxsch`, `.net`, and `.deck` generator.
- `scripts/smoke_test.py`: Local validation for RC and buck examples.
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

## Agent Install

If you want an AI coding agent to install this skill for you, point it to [`agent.md`](agent.md).
