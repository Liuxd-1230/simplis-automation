# SIMPLIS Automation Skill

[English README](README.md)

这是一个用于 Codex 的 SIMetrix/SIMPLIS 8.4 自动化 skill，主要面向 Windows 下的 buck/PMIC 仿真开发。它可以从 JSON/YAML 电路描述生成 SIMPLIS 原理图脚本、原理图、netlist 和 deck，运行 POP/瞬态仿真，自动加电压/电流 probe，并做 netlist 连通性检查。

## 包含内容

- `SKILL.md`：Codex 触发和使用这个 skill 时读取的核心说明。
- `scripts/simplis_cli.py`：主要命令行入口。
- `scripts/simetrix_waveforms.py`：生成波形导出 `.sxscr`，并解析 SIMetrix `Show` 文本输出。
- `scripts/schematic_generator.py`：把 JSON/YAML 配置转换成 `.sxscr`、`.sxsch`、`.net`、`.deck`。
- `scripts/smoke_test.py`：本地验证脚本，支持 RC 小测试和 buck 完整测试。
- `references/generated_buck_open_loop_tran.json`：当前默认 12 V buck 示例，包含体二极管、由 `PWM_HS` 反相得到的 `PWM_LS`、POP trigger、60 us 瞬态、电压 probe 和串联电流 probe。
- `references/`：SIMetrix/SIMPLIS 命令、DVM、优化流程、已验证本机行为等参考资料。

## 环境要求

- Windows。
- 已安装 SIMetrix/SIMPLIS 8.4。
- Python 3.10 或更新版本。
- 如果从 GitHub 安装，需要 Git。

安装后创建本机运行配置：

```powershell
Copy-Item %CODEX_HOME%\skills\simplis-automation\config\simplis_automation_config.json `
  %CODEX_HOME%\skills\simplis-automation\config\local_config.json
```

编辑 `config\local_config.json`，把 `simetrix_exe` 和 `symbol_lib_dir` 指到你本机安装的 SIMetrix/SIMPLIS 文件。也可以设置 `SIMETRIX_EXE` 和 `SIMPLIS_SYMBOL_LIB_DIR`，或者在命令里传入 `--simetrix-exe` 和 `--symbol-lib-dir`。

检查最终解析到的配置：

```powershell
python %CODEX_HOME%\skills\simplis-automation\scripts\simplis_cli.py show-config
```

## 安装

把仓库 clone 到 Codex 的 skills 目录：

```powershell
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" }
New-Item -ItemType Directory -Force -Path (Join-Path $codexHome "skills") | Out-Null
git clone https://github.com/Liuxd-1230/simplis-automation.git (Join-Path $codexHome "skills\simplis-automation")
```

安装后重启 Codex，让它重新加载 skill metadata。

## 验证

运行轻量 RC smoke test：

```powershell
python %CODEX_HOME%\skills\simplis-automation\scripts\smoke_test.py --timeout 90
```

运行完整 buck POP+60 us 示例：

```powershell
python %CODEX_HOME%\skills\simplis-automation\scripts\smoke_test.py --include-buck-run --timeout 240
```

完整 buck 测试会检查关键网络，以及电压/电流 probe 是否真的进入生成的 deck。

## 示例

生成并运行默认的 12 V buck testbench：

```powershell
python %CODEX_HOME%\skills\simplis-automation\scripts\simplis_cli.py generate-schematic `
  --config %CODEX_HOME%\skills\simplis-automation\references\generated_buck_open_loop_tran.json `
  --out-dir path\to\outputs\generated_buck_open_loop_tran `
  --run --netlist-check --timeout 240 --batch
```

也可以直接这样对 Codex 说：

```text
用 simplis-automation 跑 12V buck POP+60us，加 probe，看 VOUT、SW、IL 和 PWM 波形。
```

或者：

```text
用 simplis-automation，把 L 改成 1u、Cout 改成 100u、负载改成 0.5ohm，然后跑 buck POP+60us。
```

从已有 schematic 导出 POP/AC 向量：

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

## 让 Agent 安装

如果你想让另一个 AI coding agent 帮你安装这个 skill，把 [`agent.zh-CN.md`](agent.zh-CN.md) 发给它即可。英文版安装说明见 [`agent.md`](agent.md)。
