# 可编辑 Buck 原生工程

`buck_startup.sxsch` 用于冷启动和负载跃变，`buck_pop_ac.sxsch` 用于预充电后的 POP/AC，
`buck_line_step.sxsch` 用于 5→18→5 V 输入跃变。
两个测试入口共用 `buck_core.sxcmp` 及其四个控制子模块。修改电路时编辑这些原生文件。
YAML 示例用于核对初始拓扑及参数，后续运行不会重新生成或覆盖本目录。

## 打开与修改

1. 在 SIMetrix/SIMPLIS 8.40 打开一个 `.sxsch`。
2. 进入 `XBUCK` 查看功率级与实际连接；进入 `XSENSE`、`XCTRL`、`XTON`、`XDRV` 编辑内部器件。
3. 在 SIMetrix 中执行同目录 `run_startup.sxscr`、`run_pop_ac.sxscr` 或 `run_line_step.sxscr`。
   脚本使用自身所在目录寻找工程，临时关闭会阻塞批处理的厂商 HTML 健康报告；求解器错误和警告保留。
   源文件保持可编辑，仿真输出位于 SIMetrix 创建的 `SIMPLIS_Data`。

直接打开 `.sxcmp` 查看时，保存操作可能丢失外部符号接口。修改子模块请通过父电路进入层级。

## 参数与连接

| 项目 | 设置 |
|---|---|
| 输入 / 输出目标 | 12 V / 1.2 V |
| 理想标称频率 | 500 kHz；实际频率见下方验证记录 |
| 功率电感 / 输出电容 | 1 µH / 100 µF |
| 电容 ESR / 开关导通电阻 | 5 mΩ / 1 mΩ |
| 比较器迟滞 / 延时 | 2 mV / 2 ns |
| 定时跨导 / 定时电容 | 5 µS / 10 pF |
| 定时比较器延迟 / 迟滞 | 2 ns / 100 µV |
| 电容复位开关 | Ron=1 Ω、Roff=1 TΩ |
| 上升沿死区 | 20 ns |
| 启动定时门限下限 | 0.3 V |
| 冷启动 | 电感、电容初始状态为零，参考电压在 150 µs 内上升 |
| 瞬态负载 | 初始 5 A，200 µs 时升至 10 A |
| POP 工况 | 5 A，输出预充电 1.2 V，电感初始电流 4.028 A |
| POP | 实际高侧驱动触发，最大周期 5 µs，收敛要求 1e-12 |
| AC | 1 kHz–5 MHz，50 点/十倍频 |

## ACOT 怎样工作

`timer.sxcmp` 由压控电流源 GTON、CTON、复位开关 SRESET、比较器 UCMP 和反相缓冲 UBUF
构成，门限支路另有 RTH、DMIN 和 VMIN。SR 锁存器继续使用 `control.sxcmp` 中的器件。
VIN 和 VOUT 都直接接到运行中的功率级。

1. 输出反馈比较器请求导通，SR 锁存器置位，驱动产生实际高侧信号 GH。
2. GH 上升后，UBUF 关闭 SRESET，电流 `5 µS × VIN` 给 10 pF 电容充电。
3. RAMP 达到 VTH 时，UCMP 输出 DONE，复位控制锁存器并关断高侧。
4. GH 下降后，SRESET 接通，清除定时电容上的电荷。

高侧实际导通沿决定充电起点，驱动的上升沿死区发生在计时之前。
各次开关由反馈与电容比较产生；电路内部没有预设的周期复位脉冲。
测试入口中的 VRST 模拟启动时释放的上电复位；它不参与后续各周期的电容复位。

稳态门限跟随 VOUT，忽略延迟和复位残压时：

`Ton = Cton × VOUT / (gm × VIN)`，`fsw ≈ gm / Cton = 500 kHz`。

例如理想 VOUT=1.2 V 时，VIN=5/12/18 V 分别对应 480/200/133.333 ns。
比较器、缓冲、锁存器和驱动的有限延迟会增加实际导通时间。
输入变化时 VIN 直接改变充电斜率，运行不需要重新计算定时参数。

启动时 VOUT 接近零，DMIN 把门限钳位在约 0.3 V，保证输出能够建立。
VOUT 高于该值后，DMIN 关断，门限经 RTH 跟随 VOUT。
GTON、比较器和开关采用 SIMPLIS 功能模型；本例未实现晶体管级跨导、输入耐压转换或 PVT 验证。

功率级和输出滤波器通过导线连接。内部反馈采用 90 kΩ / 10 kΩ 分压，
AC 1 电压源串联在 `FB_DIV` 和 `FB` 之间，直流时压降为零。
返回比定义为 `T=-V(FB_DIV)/V(FB)`。波特探针的 OUT 接 `FB_DIV`、IN 接 `FB`，
`PHASE_VERTICAL=1` 用于返回比的相位符号。

POP 的触发门在实际导出网表中为 `XBUCK.XPOP.!D_CYCLE`。分析指令明确使用这个名称。
本次原生层级采用 `<ref>` 网表模板，GUI 自动推测的 `X$XBUCK...` 名称与实际调用名不同。
修改层级参考名后，应同时核对触发门和 `.INIT` 中的层级名称。

## 真实验证

```powershell
python tests/validate_pop_ac_buck.py --out-dir outputs/my-buck-validation
python tests/validate_acot_timer.py --out-dir outputs/my-acot-validation
```

这是按需执行的专项集成测试，会启动真实模拟器；普通单元测试不会启动它。
测试保存启动、POP、AC、错误触发副本、独立求解器对照、原生网表检查及 CSV 数据。
原始波形和最终结果受到保护。当前 ACOT 测量见 [器件搭建验证](../../../docs/acot-timer-validation.md)；
此前固定单稳态版本的排障记录见 [POP/AC 专项诊断](../../../docs/pop-ac-buck-validation.md)。

本目录选用用户器件参考表及匹配安装库的符号，复用公开 YAML 示例的电气拓扑。
运行时使用安装中的模型库；未附带厂商商业示例工程或模型库文件。
