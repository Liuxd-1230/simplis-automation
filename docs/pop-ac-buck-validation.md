# POP / AC 原始故障与原生 Buck 验证

日期：2026-10-23。实际模拟器：SIMetrix GUI 8.40.0.2，SIMPLIS 8.40.0.25254。
PR #8 已合并至 main；PR #9 合并至其原开发分支。本次从已合并的 #9 改动继续调查。

## 故障对照

| 项目 | 原始现象和证据 | 确认原因及修复 | 本轮结果 |
|---|---|---|---|
| 原始 POP 求解 | `.dbg`：`POP analysis succeeded after 1 iteration(s)` | 求解已成功；不存在可归因于 POP 不收敛的错误编号 | PASS |
| 原始 AC 求解 | `.f2` 声明 31 个频点；求解器日志结束，状态窗口 Finished | 数值求解与脚本后处理分开判断 | PASS |
| 脚本返回 | 被动监控的调用标记停在 `simplis_run`；消息日志进入 `simplis_health_report_parse_deck` | 当前 8.40 安装的附加 HTML 健康报告处理阻塞；在本次脚本内临时关闭该报告 | PASS |
| 向量导出 | 原始脚本没有返回标记、没有导出 | 同一电路、版本和分析条件，仅关闭附加报告后约 12 s 返回并导出 31 点复数向量；默认自动化入口复测通过 | PASS |
| 原生完整 Buck | 原生层级和器件模板需实际核对 | 核对导出的端口、功率支路、反馈和触发门，使用明确的真实层级名 | PASS |
| 负向 POP 触发 | 独立副本将触发阈值设为 50 V | 5 V 驱动不能越过阈值，保存真实错误 5020、原文和 POP 日志 | 预期 FAIL，诊断 PASS |

外部 `SxCommand` 查询停止后，原始脚本仍在同一位置阻塞；本轮没有据此禁用正常监控。
逐调用标记证明状态查询位于阻塞点之后。附加报告内部的消息轨迹可定位到其处理过程，
尚未修改厂商内置脚本，也未将某一条内部表达式认定为已证明的软件缺陷。

公共脚本现在在 `simplis_run` 返回后立即写入标记，随后记录退出码、错误查询、状态查询、
数据组读取和导出进度。选择数据组前检查其存在，避免缺失数据组进入交互等待。
`.dbg` 纳入故障包，错误编号及相邻原文单独列出，保留原始文件。

## 真实 POP 错误

独立副本使用 50 V 触发阈值，正式电路保持 2.5 V。实际 `.err` 原文：

```text
<<<<<<<< Error Message ID: 5020 >>>>>>>>
Periodic Operating-Pt Analysis:
    Reaching a time duration equal to
    `1.00000e-05' without registering the
    triggering condition that defines
    the start of a period. Check your
    circuit and/or initial conditions.
```

`.dbg` 同时记录 `POP analysis failed. Max. period exceeded.`。
错误发生在周期起点搜索阶段：输入驱动在 0–5 V 变化，触发阈值被明确设置为 50 V。
这次失败具有已知电路原因，自动化报告 `simulator_error_artifact`，没有等待到通用超时。

整理原生工程时还捕获并修复了 1021（重复大小写参考名导致 `U?`）、1075（电容实际名称带
`C$` 前缀，初始条件引用不符）、1077/1128（自动推测触发路径与实际层级不符）。
这些原始失败目录继续保留。正式测试在修复后的独立新目录执行。

## 完整原生 Buck

权威源文件在 [examples/native/acot_buck](../examples/native/acot_buck/README.md)。
冷启动与 POP/AC 两个测试入口共用全部功率及控制子模块。电气参数沿用原 YAML 示例：
12 V、1.2 V 目标、1 µH、100 µF、5 mΩ ESR、5–10 A 负载、20 ns 死区。
定时模型采用原示例固定的 200 ns 单稳态；未增加实时 Vin 前馈电路。

冷启动验证与预充电 POP 分别执行。启动参考在 150 µs 内上升，200 µs 时负载从 5 A 升至
10 A；POP 工况使用 5 A，输出初始 1.2 V、电感初始 4.028 A，纹波电容按原工况初始化。
求解使用原收敛要求 1e-12，未放宽收敛条件或人为固定开关信号。

| 结果 | 本轮数值 |
|---|---:|
| 冷启动峰值 | 1.216398 V |
| 10 A 稳态平均输出 | 1.207597 V |
| 10 A 实际开关频率 | 555.487 kHz |
| POP 迭代 / 最后收敛误差 | 3 / 7.323375e-15 |
| POP 5 A 平均输出 | 1.207658 V |
| POP 平均电感电流 | 5.000012 A |
| POP 实际频率 | 554.224 kHz |
| 5 个周期末端电压差 | 0 V（按导出精度） |
| 5 个周期末端电流差 | -3.997e-13 A |
| POP 导出点数 | 2070 |
| AC 频率 / 点数 | 1 kHz–5 MHz / 186 |
| 0 dB 交越 / 相位裕度 | 179.862 kHz / 87.613° |
| 扫描插值获得的最小增益裕度 | 18.636 dB，3.677440 MHz |
| GUI / 独立求解器最大相对复数返回信号差 | 4.994424e-7 |

交越频率为当前 50 点/十倍频数据之间的插值结果。扫描只有一个下降的 0 dB 交越。
MHz 区间存在多个负实轴穿越；程序列出全部穿越，并取其中最小的增益裕度。
原先仅取第一个相位穿越会得到 48.141 dB，本轮更正为 18.636 dB。
完整精度和所有交越记录保存在 `validation.json` 和 CSV 中。

返回比采用 `T=-V(FB_DIV)/V(FB)`。注入源串联在反馈分压点与控制器输入之间，直流压降为零。
纹波支路和其余闭环保持连接。波特探针使用对应端口和相位符号，
Python 再由两个原始复数向量计算同一定义。高频开关系统响应的起伏保留在完整扫频图中。
冷启动在零初始储能下接入 5 A 电流负载，初始输出约为 -25 mV；启动图保留了这段负电压。

GUI 与独立求解器均重新求解同一本轮导出的网表，使用独立目录及 fresh 参数。
GUI 中的 `.t2/.f2` 可能只含文件头，数据通过本次数据组的 Show 导出；独立求解器的
完整 `.f2` 逐行检查频点、实虚部配对和有效数值。只有文件头的表会被拒绝。

## 连接与源文件检查

专项测试针对真实原生网表检查：输入电流探针的两端、上下桥臂及体二极管、
电感及输出电容 ESR、采样分压、零直流 AC 注入源、SW−VOUT 纹波支路、
比较器至锁存器、定时复位、死区驱动和 POP 触发输入。
模块内部使用实际导线，跨区域及层级边界使用端口和必要标签。
原生预览来自 SIMetrix 的 CopyClipSchem，子模块外部符号保持完整。

## 复现与保留

```powershell
python -m unittest discover -s tests -p "test_*.py"
python tests/validate_pop_ac_buck.py --out-dir outputs/new-pop-ac-check
python scripts/plot_pop_ac_buck.py --results outputs/new-pop-ac-check --eeplot-assets <pe-plots-paper>/assets
```

本轮主要记录位于忽略目录 `outputs/pop-ac-investigation/`：
`reproduce-original`、`probe-passive`、`probe-health-off`、`fixed-original`、
`buck-original-fixed-automation`、`final-regression-02` 及原生绘图失败记录。
原始错误、输入快照、原生网表、求解日志、Show 向量、独立求解器表和绘图 CSV 均保留。

另以 SIMetrix 直接执行本目录的 `run_pop_ac.sxscr`，在约 11.514 s 内完成，返回退出码 0。
该脚本保持工程窗口供人工编辑；测试只在完成标记出现后关闭自己启动的实例。
脚本动态文件路径采用 `{buck_schematic}` 表达式。
早期把变量写在引号内部时停在打开原理图阶段，相应副本和进度记录已保留。

启动、POP 周期及 Bode 三张图均通过 `pe-plots-paper` 检查：`RESULT: PASS`，`LINTS: 0`。
已查看 IEEEtran 页面预览，字号可读，无文字重叠，坐标单位和波形范围可核对。
POP 图的输出电压采用相对实际周期平均值的偏差，原始绝对值保留在 CSV。
最终成功结果已标记保护。没有清理原始实验数据。

普通单元测试共 153 项通过；其中新增数据解析测试使用本轮真实 Buck 输出和 5020 原始错误。
本轮覆盖 8.40 的该 Buck 工况；8.30、其他工作模式及 PVT 仍需对应实机或模型验证。
