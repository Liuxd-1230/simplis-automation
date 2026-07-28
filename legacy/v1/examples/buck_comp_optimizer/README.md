# SIMPLIS Loop Compensation Optimizer

这个目录是一个最小可运行的 SIMetrix/SIMPLIS 环路补偿优化框架。它可以用于当前示例 buck，也可以通过 JSON 配置接入用户自己的 SIMPLIS 原理图。

核心约束：

- sweep 和 optimize 只修改 `runs/run_xxxx/work/` 里的 run-local 副本。
- 默认只改补偿参数、白名单数值参数、仿真脚本和 Python 自动化代码。
- 不删除、不短接、不重连主功率级，不改反馈极性、比较器极性或电流采样拓扑。
- 找到 best 后，只有显式执行 `apply-best` 才写回目标 schematic，并且会先备份。

## 必要前提

用户自己的 schematic 需要提前准备好两件事：

1. 补偿元件已经参数化。二型可以是 `Rz/Cz/Cp`，三型可以是 `Rz1/Cz1/Rz2/Cz2/Cp1`，名字由配置决定。
2. 环路里已经放好 SIMPLIS Bode Plot Probe。没有 Bode probe 时，`validate --mode real` 会提醒并停止 real AC-loop 优化准备。

runner 不会自动插入 Bode probe，因为 probe 的接入方向会影响环路符号和相位裕度定义。用户需要先在 SIMPLIS GUI 中确认 probe 的位置和方向。

## 默认示例映射

当前示例是三型补偿，补偿子电路 `Modeling_Blocks/3p2zcompensator.sxcmp` 中的变量映射为：

| 优化名 | schematic 变量 | 元件 |
| --- | --- | --- |
| `Rz1` | `R5` | `R5` |
| `Cz1` | `C2` | `C2` |
| `Rz2` | `R4` | `R4` |
| `Cz2` | `C4` | `C4` |
| `Cp1` | `C3` | `C3` |

当前启用的电路参数白名单：

| 优化名 | 目标 |
| --- | --- |
| `Lout` | `L1.L` |
| `Cout` | `C1.C` |
| `CoutESR` | `C1.ESR` |
| `VRAMP` | `V2.SIMPLIS_VALUE` 的 `_V2` token |

## 配置自己的 schematic

复制 `optimizer_config.json` 到你的工程目录，然后修改这些字段：

```json
{
  "simetrix_exe": "D:/SIMetrix830/bin64/SIMetrix.exe",
  "schematic": "your_converter.sxsch",
  "compensator": "Modeling_Blocks/your_compensator.sxcmp",
  "runs_dir": "runs",
  "targets": {
    "fc_hz": 10000,
    "fc_hz_min": 9000,
    "fc_hz_max": 11000,
    "phase_margin_deg": 60,
    "gain_margin_db": 10
  },
  "crossover_policy": "nearest_target"
}
```

路径可以是绝对路径，也可以相对配置文件所在目录。real run 会复制 schematic 到 `runs/run_xxxx/work/` 再修改。

如果补偿变量在顶层 schematic 的 F11 analysis text 中，使用 `schematic_var`：

```json
"Rz": {
  "group": "compensation",
  "target": "schematic_var",
  "maps_to": "RZ",
  "initial": 10000,
  "bounds": [1000, 100000],
  "coarse": [4700, 10000, 22000],
  "step_factor": 1.35
}
```

如果补偿变量在单独 `.sxcmp` 子模块中，使用默认的 `compensator_alias`：

```json
"Rz1": {
  "group": "compensation",
  "target": "compensator_alias",
  "maps_to": "R5",
  "initial": 2848.377,
  "bounds": [800, 12000],
  "coarse": [1500, 2848.377, 5600],
  "step_factor": 1.35
}
```

二型补偿通常启用 `Rz/Cz/Cp` 三个参数即可。三型补偿通常启用 `Rz1/Cz1/Rz2/Cz2/Cp1` 五个参数。名字本身不重要，`maps_to` 必须对应原理图里已经参数化好的变量名。

## 配置 Bode vector 导出

示例配置里已经有：

```json
"vector_export": {
  "enabled": true,
  "ac": {
    "group": "simplis_ac1",
    "output": "7",
    "input": "20"
  },
  "tran": {
    "group": "simplis_tran1",
    "vout": "7",
    "vc": "6"
  }
}
```

这些 vector 名称来自当前示例 schematic。换成用户自己的 schematic 后，必须确认 `ac.output/ac.input/tran.vout/tran.vc` 对应你的数据组和 vector。可以先做一次 real run，打开 `runs/run_xxxx/export_vectors.sxscr` 和 `runs/run_xxxx/vectors/` 验证导出文件。

runner 会把 AC 复数 vector 比值转成：

- `ac_loop.csv`: `freq_hz,gain_db,phase_deg`
- `bode.svg`: 自动画出的 Bode 图

TRAN vector 会转成：

- `tran.csv`: `time_s,vout_v,vc_v`

## 常用命令

先验证配置：

```powershell
cd E:\Workspace\Codex\simplis-automation\examples\buck_comp_optimizer
python .\buck_opt.py --config .\optimizer_config.json validate --source provided --mode real
```

如果没有预放 Bode Plot Probe，real mode 会报出明确提醒。先回 SIMPLIS GUI 放好 probe，再继续。

先跑 mock 闭环，确认 optimizer 和文件输出：

```powershell
python .\buck_opt.py run-once --source provided --mode mock
python .\buck_opt.py optimize --source provided --mode mock --max-evals 8 --coordinate-rounds 1
```

分阶段诊断真实 schematic：

```powershell
python .\buck_opt.py diagnose-real --source provided --mode real --stage-timeout-s 60
```

跑一次真实仿真并自动导出 AC/TRAN：

```powershell
python .\buck_opt.py --timeout-s 240 run-once --source provided --mode real
```

指定参数跑一次：

```powershell
python .\buck_opt.py --config .\optimizer_config_10k.json --timeout-s 240 run-once --source provided --mode real --param Rz1=154 --param Cz1=1.058e-7 --param Cp1=4.134e-9
```

真实粗扫加坐标下降：

```powershell
python .\buck_opt.py optimize --source provided --mode real --max-evals 30 --coordinate-rounds 2
```

确认 `best_params.json` 后写回目标 schematic：

```powershell
python .\buck_opt.py apply-best --target provided --best .\best_params.json
```

`apply-best` 会先生成 `.bak-YYYYMMDD-HHMMSS` 备份，然后只写配置白名单中的参数。

## 输出文件

每个 run 会保存：

- `params.json`
- `startup.sxscr`
- `export_vectors.sxscr`
- `simetrix_stdout.txt` / `simetrix_stderr.txt`
- `vector_export_stdout.txt` / `vector_export_stderr.txt`
- `sim_status.txt`
- `vectors/*.txt`
- `ac_loop.csv`
- `tran.csv`
- `bode.svg`
- `metrics.json`
- `score.json`
- `result.json`

汇总文件：

- `summary.csv`
- `best_params.json`
- `optimization_history.json`

仿真失败、超时、缺 vector、缺 metrics 时不会崩溃。对应 run 会记录 `failure_reason`，score 返回 penalty，优化继续。

## Command shell 滚动

如果 SIMetrix command shell 一直滚动脚本行，运行：

```powershell
python E:\Workspace\Codex\simplis-automation\scripts\simplis_cli.py --simetrix-exe "D:\SIMetrix830\bin64\SIMetrix.exe" quiet-shell --out "$env:TEMP\quiet_shell.sxscr" --run --timeout 15
```

这个命令会备份并移除用户 `Base.sxprj` 中的持久化 `EchoOn=`，然后发送 `Unset EchoOn`、`ClearMessageWindow` 和 `CloseSimplisStatusBox`。它不会修改 schematic 或电路数据。
