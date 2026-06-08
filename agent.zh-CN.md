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
```

4. 如果本机装了 SIMetrix/SIMPLIS，运行 smoke test。

轻量 RC 测试：

```powershell
python (Join-Path $skillDir "scripts\smoke_test.py") --timeout 90
```

完整 buck POP+60 us 测试：

```powershell
python (Join-Path $skillDir "scripts\smoke_test.py") --include-buck-run --timeout 240
```

5. 提醒用户重启 Codex，让 skill metadata 重新加载。

## 注意事项

- 这个仓库不包含 SIMetrix/SIMPLIS 的专有库文件，也不包含生成出来的仿真输出。
- 脚本默认 SIMetrix 路径是 `D:\Simplis8.4\bin64\SIMetrix.exe`。如果用户安装在其他位置，设置 `SIMETRIX_EXE` 或传入 `--simetrix-exe`。
- 不要把生成的 `.sxsch`、`.net`、`.deck`、`.err` 或 `SIMPLIS_Data` 放进 skill 目录。
- 安装后，用户可以这样调用：`用 simplis-automation 跑 12V buck POP+60us，加 probe 看波形。`

