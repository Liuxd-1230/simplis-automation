# Agent 安装指南

当用户要求你从 GitHub 安装 `simplis-automation` Codex skill 时，按这个指南执行。

## 目标

把这个仓库安装为 Codex skill，目标路径是：

```text
%CODEX_HOME%\skills\simplis-automation
```

如果没有设置 `CODEX_HOME`，使用：

```text
%USERPROFILE%\.codex
```

## 步骤

1. 确定 Codex home 目录。

```powershell
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" }
$skillsDir = Join-Path $codexHome "skills"
New-Item -ItemType Directory -Force -Path $skillsDir | Out-Null
```

2. clone 或更新 skill。

```powershell
$skillDir = Join-Path $skillsDir "simplis-automation"
if (Test-Path -LiteralPath $skillDir) {
  git -C $skillDir pull --ff-only
} else {
  git clone https://github.com/Liuxd-1230/simplis-automation.git $skillDir
}
```

3. 检查 skill 结构是否完整。

```powershell
Test-Path (Join-Path $skillDir "SKILL.md")
Test-Path (Join-Path $skillDir "scripts\simplis_cli.py")
Test-Path (Join-Path $skillDir "references\generated_buck_open_loop_tran.json")
Test-Path (Join-Path $skillDir "profiles\buck.json")
Test-Path (Join-Path $skillDir "examples\official")
```

4. 创建或检查运行配置。

```powershell
$localConfig = Join-Path $skillDir "config\local_config.json"
if (-not (Test-Path -LiteralPath $localConfig)) {
  Copy-Item (Join-Path $skillDir "config\simplis_automation_config.json") $localConfig
}
```

向用户确认 SIMetrix 可执行文件路径和 symbol library 目录，把它们写入 `config\local_config.json`。不要猜路径。运行仿真前必须验证两个路径都存在。

运行 `show-config` 并检查 JSON 证据，然后才能继续仿真：

```powershell
python (Join-Path $skillDir "scripts\simplis_cli.py") show-config
```

5. 如果本机已经安装并配置好 SIMetrix/SIMPLIS，运行 smoke test。

轻量 RC 测试：

```powershell
python (Join-Path $skillDir "scripts\smoke_test.py") --timeout 90
```

完整 buck POP+60 us 测试：

```powershell
python (Join-Path $skillDir "scripts\smoke_test.py") --include-buck-run --timeout 240
```

6. 检查官方示例并确认 profile 证据。

```powershell
python (Join-Path $skillDir "scripts\simplis_cli.py") inspect-schematic `
  --input (Join-Path $skillDir "examples\official") `
  --out (Join-Path $env:TEMP "simplis_official_examples.json")
```

7. 提醒用户重启 Codex，让 skill metadata 重新加载。

## 注意事项

- 这个仓库不包含 SIMetrix/SIMPLIS 的专有库文件，也不包含生成出来的仿真输出。
- 运行路径必须来自命令行参数、环境变量或 `config/local_config.json`。不要假设 SIMetrix 安装路径。
- 不要把生成的 `.sxsch`、`.net`、`.deck`、`.err` 或 `SIMPLIS_Data` 放进 skill 目录。
- 选择 SIMPLIS 器件时，先查 `profiles/` 和 `examples/official/`。不要把用户的私有研究 schematic 当成公开示例。
- 解释失败或可疑仿真前，先运行 `export-agent-evidence`，结论必须基于导出的报告。
- 安装后，用户可以这样调用：`用 simplis-automation 跑 12V buck POP+60us，加 probe 看波形。`
