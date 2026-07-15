# v2 使用的 SIMPLIS 8.4 器件

本文记录 v2 黄金 A-COT Buck 和器件行为 smoke test 实际放置、净表后使用的器件。器件名称以生成净表中的模型为准，不以原理图图标名称猜测。

## 黄金电路功能块

| 功能块 | 展开的叶子器件 | 作用 | 主要可调参数 |
| --- | --- | --- | --- |
| `half_bridge` | 2 x `simplis_prim_vcswitch` + 2 x `VPWLR` | 同步 Buck 高低侧理想开关，以及 `POWER_D_HS_BODY: SW → VIN`、`POWER_D_LS_BODY: 0 → SW` 两只反并联体二极管 | 开关/二极管 `RON`、`ROFF`、阈值、迟滞 |
| `feedback` | 2 x `res` + `ac_source` + `Bode_Probe2` | VOUT 分压；在 divider 与 FB 间串入零直流、`AC 1` 的注入源，并保留断点两侧原始复数向量 | `rtop`、`rbot`、显式 AC 响应符号 |
| `synthetic_ripple` | DC centering source + `vcvs_2` + 2 x `res` + `cap` | 以偏置后的 FB 为共模缩放 `SW−VOUT`，R/C 生成三角纹波，并由并联 restore 电阻独立拉回低频共模；纹波独立于 Cout ESR | VCVS gain、`ripple_offset`、`ripple_r`、`ripple_c`、`ripple_restore_r`、电容 IC |
| `comparator_latch` | `SIMPLIS_DIGI1_COMP_Y` + `SIMPLIS_DIGI1_SRLATCH_Y` | 比较 FB+纹波与基准，并锁存 PWM 请求 | 比较器迟滞/延迟、锁存延迟 |
| `adaptive_ton` | `SIMPLIS_1SHOT_BB` + BUF + asymmetric delay + AND2 | 正沿触发 Ton；由反相、仅延迟下降沿和 AND2 从 Ton 下降沿产生默认 5 ns 的 `TON_DONE` 复位脉冲 | `TH`、逻辑延迟、reset pulse，并受 Ton min/max 限制 |
| `deadtime_driver` | `SIMPLIS_DIGI1_BUF_Y` + 2 x `SIMPLIS_DIGI1_D_ASYMMETRIC_DELAY_Y` | 产生互补 HS/LS，并只延迟各路上升沿以形成死区 | buffer delay、HS/LS rise delay、2 ps fall delay |
| `behavioral_voltage_amplifier` | `vcvs_2` | `INP/INN → OUT/RTN` 的最简有限电压增益模型 | `gain`；不隐式添加带宽、钳位、限流或输出阻抗 |
| `behavioral_ota` | `vccs_2` | `INP/INN → OUT/RTN` 的最简有限跨导模型 | `gm`；不隐式添加带宽、钳位、限流或输出阻抗 |

`adaptive_ton` 当前是“按工况/候选重新计算 TH”，不是仿真过程中连续改变 TH 的模拟除法器。这样能保持电路快速、可编辑，并让优化器显式控制 Ton。

## 功率级、激励与测量器件

| catalog kind | SIMetrix 符号 / 实际模型 | 用途 |
| --- | --- | --- |
| `resistor` | `res` / `R` | 分压、ESR、纹波注入 |
| `capacitor` | `cap` / `C` | 输出/纹波电容；`VALUE` 可组合电压 `IC`，正方向为 P−N |
| `inductor` | `ind` / `L` | Buck 输出电感；`VALUE` 可组合电流 `IC`，正方向为 P→N |
| `dc_voltage_source` | `dc_source` / `V` | VIN 和比较器基准 |
| `pulse_current_source` | `iwave_v2` / `SQU_SOURCE_I` | TRAN 中 5 A→10 A 负载阶跃；POP 中强制 `IDLE_IN_POP=YES`，保持 V1 恒流 |
| `ramp_voltage_source` | `vwave_v2` / `SAW_SOURCE` | 500 kHz 合成纹波 |
| `pwl_voltage_source` | `vpwl` / `PWL` | 仅用于器件行为证明的确定性边沿激励 |
| `pulse_voltage_source` | `vwave_v2` / `SQU_SOURCE` | proof/POP 周期触发 |
| `voltage_controlled_switch` | `simplis_prim_vcswitch` / `SIMPLIS_VC_SWITCH` | 高侧和低侧功率开关 |
| `idealized_diode` | `simplis_pwlr_box` / `VPWLR` | 可调 `RON/ROFF` 的理想化体二极管 |
| `vcvs` / `vccs` | `vcvs_2` / `E`；`vccs_2` / `G` | 合成纹波缩放和最简行为放大器/OTA |
| `ac_injection_source` | `ac_source` / `V AC 1` | 零直流环路注入；已验证 transient DC=0、AC 幅值=1 |
| `bode_probe` | `Bode_Probe2` / `.PRINT` + `.GRAPH` | 保留安装符号默认 `=OUT/IN`，不写受保护的 `TEXT` 属性 |
| `voltage_probe` | `probev_new` / `.PRINT V(...)` | 电压波形 |
| `current_probe` | `InlineCurrentProbe` / 串联 0 V 测量源 | IIN、IL、ICOUT |
| `pop_trigger` | `PERIODIC_OP_V8` | POP 周期触发 |

## 已完成六项证明的原生控制器件

| catalog kind | 实际模型 | 引脚 | 已验证行为 | 证据 |
| --- | --- | --- | --- | --- |
| `digital_comparator_grounded` | `SIMPLIS_DIGI1_COMP_Y` | OUT、OUT_BAR、RTN、INP、INN | 输入越过阈值时输出翻转 | `catalog/evidence/digital_comparator_grounded_8_4.json` |
| `sr_latch_grounded` | `SIMPLIS_DIGI1_SRLATCH_Y` | Q、QN、S、R，模板显式接地 | S/R 动作，Q/QN 全程互补 | `catalog/evidence/sr_latch_grounded_8_4.json` |
| `native_oneshot` | `SIMPLIS_1SHOT_BB` | IN、RTN、OUT、DSCH、RAMP | 4 个 200 ns 脉冲；RAMP、DSCH 有效 | `catalog/evidence/native_oneshot_8_4.json` |
| `digital_buffer_grounded` | `SIMPLIS_DIGI1_BUF_Y` | OUT、OUT_BAR、IN1，模板显式接地 | 2 ps 延迟；正/反输出互补 | `catalog/evidence/digital_buffer_grounded_8_4.json` |
| `asymmetric_delay_grounded` | `SIMPLIS_DIGI1_D_ASYMMETRIC_DELAY_Y` | OUT、IN1，模板显式接地 | 两路上升延迟均为 20 ns；无重叠 | `catalog/evidence/asymmetric_delay_grounded_8_4.json` |
| `vcvs` | `E` | P、N、CP、CN | 670 个有效驱动采样得到精确 `+2.0` 增益 | `catalog/evidence/vcvs_8_4.json` |
| `vccs` | `G` | P、N、CP、CN | 2 mS 驱动 1 kΩ 负载得到 `-2.0` 电压增益，验证电流方向 | `catalog/evidence/vccs_8_4.json` |
| `idealized_diode` | `VPWLR` | P、N | 反向跟随误差 <10 µV；正向钳位 <1 µV | `catalog/evidence/idealized_diode_8_4.json` |
| `ac_injection_source` | `V AC 1` | P、N | transient DC=0；31 个复数 AC 点的输入幅值=1 | `catalog/evidence/ac_injection_source_8_4.json` |
| `bode_probe` | `.PRINT` + `.GRAPH` | OUT、IN | 31 个复数点 `AC_OUT/AC_IN=0.5`；db/ph 指令存在 | `catalog/evidence/bode_probe_8_4.json` |
| `digital_and2_grounded` | `SIMPLIS_DIGI1_AND2_Y` | OUT、OUT_BAR、RTN、IN1、IN2 | 00/10/11/01 全覆盖；997 个保持区采样零误码且互补 | `catalog/evidence/digital_and2_grounded_8_4.json` |

每个 `ideal_native` 条目都具备库哈希、引脚契约、GUI 放置、干净重开、实际净表和最小行为测试六项证据。禁止的新生成器件仍包括 Generic gate、HC 系列、LP311 和 vendor wrapper。

## L/C 初值与 POP 初态

- 冷启动默认使用 `cout_ic=0V`、`lout_ic=0A`、`ripple_ic=0V`，避免把预充电误当成真实启动行为。
- POP/AC 分区按设计指标设置 `COUT IC=Vout_target`、`L1 IC=Iload−ΔIL/2`，并把纹波电容设为 `−ripple_offset`，对应开通前的谷值状态。
- `reactive_value_ic` 编码器把 catalog 的虚拟 `IC` 属性写入原生 `VALUE`，例如 `0.0001 IC=1.2`；未声明 IC 时保持纯 VALUE。
- 公开 RC/RL 行为样例验证了 1.2 V 电容初值、P→N 的 2 A 电感初值和各 1 ms 衰减时间常数。证据见 `catalog/evidence/reactive_ic_8_4.json`；由于本机完成 API 限制，该行为数据仍标记为 `diagnostic_only`。
- POP 负载必须是稳定阻值或稳定电流。黄金样例保留同一只负载阶跃源，但在 POP 中使用 `IDLE_IN_POP=YES` 固定为 5 A；不得仅依赖“阶跃延时尚未到”来假装负载恒定。

## 黄金样例 SIMPLIS 8.4 实测

- 默认 12 V→1.2 V、5 A→10 A TRAN 的所有行为门槛通过：VOUT DC 误差 0.633%、阶跃峰值偏差 3.079%、恢复 0 周期、最小死区 20.0 ns、周期抖动 0.0159%、合成纹波 2.764 mV。
- POP/AC 在固定 5 A 负载和稳态 L/C/ripple IC 下生成完整数据；环路交越 179.862 kHz、相位裕度 87.613°、增益裕度 48.141 dB。
- SIMPLIS 的 transient 时间点在开关事件附近非均匀密集。平均电压、电流和功率必须对时间轴做梯形积分，禁止直接按样本行求算术平均。修正后输入功率 12.765 W、输出功率 12.076 W、功率比 0.946；黄金门槛要求功率比至少 0.8。
- 本机 `GetSimulatorStatus()=None` 且 `GetSimulationInfo()` 关键字段为空，因此上述实测只用于 `diagnostic_only` 电气验收，不可进入优化评分，也没有运行 40 次优化。

## 尚未批准与仅导入边界

| 状态 | catalog kind | 说明 |
| --- | --- | --- |
| pending | `digital_or2`、`digital_xor2`、`summer2` | 已进入证明队列；缺少任一六项证据时不能用于新生成电路 |
| import_only | `ccvs_import_boundary`、`cccs_import_boundary` | 可保留现有原理图中的未知/私有边界，但不能参数化，也不能进入理想性或优化证明 |

## 图面与数据注意事项

- `hybrid` 默认按真实 footprint 保持至少横向 480、纵向 360 SIMetrix units 净距；功率路径在上、控制/反馈在下、分析器件在独立 gutter。
- 左侧输入 pin 的 terminal label 使用 `N180` 向左，右侧输出使用 `N0` 向右；上下 pin 也朝器件外侧。串联二端器件横置，接地支路竖直。
- 每个交付原理图都要在干净重开后用真实 SIMetrix GUI 做 Zoom-to-Fit 截图检查；截图只证明可读性，不代替 branch 附着、净隔离或电气行为。
- SIMetrix 8.4 的 DIGI1 导出常把逻辑波形表示为 0/1，而连续模拟节点仍为实际电压。验证器按每条逻辑波形自己的高低电平计算阈值。
- transient 默认注入 `.OPTIONS` 和 `PSP_NPT=10001`。没有该设置时，PWL、Ton、RAMP 等连续波形可能存在向量名但样本数为 0。
- 本机成功 SIMPLIS run 仍返回 `GetSimulatorStatus()=None`，且 `GetSimulationInfo()` 的 netlist/analysis 字段为空。`catalog proof-run` 只有在阶段 token、无错误/未知 warning、数据组、向量新鲜完整、行为判据和图表全部通过时才可记录器件证明；状态机停在 `validated`，`scoring_eligible=false`，不能进入优化评分。
