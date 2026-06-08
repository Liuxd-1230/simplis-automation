# Agent Installation Guide

Use this guide when a user asks you to install the `simplis-automation` Codex skill from GitHub.

## Goal

Install this repository as a Codex skill at:

```text
%CODEX_HOME%\skills\simplis-automation
```

If `CODEX_HOME` is not set, use:

```text
%USERPROFILE%\.codex
```

## Steps

1. Determine the Codex home directory.

```powershell
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" }
$skillsDir = Join-Path $codexHome "skills"
New-Item -ItemType Directory -Force -Path $skillsDir | Out-Null
```

2. Clone or update the skill.

```powershell
$skillDir = Join-Path $skillsDir "simplis-automation"
if (Test-Path -LiteralPath $skillDir) {
  git -C $skillDir pull --ff-only
} else {
  git clone https://github.com/Liuxd-1230/simplis-automation..git $skillDir
}
```

3. Verify the skill shape.

```powershell
Test-Path (Join-Path $skillDir "SKILL.md")
Test-Path (Join-Path $skillDir "scripts\simplis_cli.py")
Test-Path (Join-Path $skillDir "references\generated_buck_open_loop_tran.json")
```

4. If SIMetrix/SIMPLIS is installed, run the smoke test.

```powershell
python (Join-Path $skillDir "scripts\smoke_test.py") --timeout 90
```

For the full buck example:

```powershell
python (Join-Path $skillDir "scripts\smoke_test.py") --include-buck-run --timeout 240
```

5. Tell the user to restart Codex so skill metadata is reloaded.

## Notes

- This repository intentionally does not include proprietary SIMetrix/SIMPLIS libraries or generated simulation output files.
- The scripts default to `D:\Simplis8.4\bin64\SIMetrix.exe`. If the user installed SIMetrix elsewhere, set `SIMETRIX_EXE` or pass `--simetrix-exe`.
- Do not put generated `.sxsch`, `.net`, `.deck`, `.err`, or `SIMPLIS_Data` output inside the skill directory.
- The GitHub repository name currently ends with a dot, so the clone URL contains two dots before `git`: `simplis-automation..git`.

