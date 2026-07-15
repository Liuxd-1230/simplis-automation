# 本机器件目录（SIMetrix/SIMPLIS 8.3）

[`seed_8_3.yaml`](seed_8_3.yaml) 是本机 `D:\SIMetrix830` 安装的**证据种子**，不是跨版本的厂家器件库。它锁定了实际读取过的 `.sxslb` / `.lb` 文件路径和 SHA-256；如果 `doctor` 发现路径或哈希不匹配，应重新导入并审批，不能静默沿用旧结论。

## 导入与审批

1. 用 `simplis-v2 catalog import` 从本机符号库产生候选记录。
2. 检查候选符号、可见引脚、模型 `.NODE_MAP`、属性默认值和实际 `Netlist /simplis` 结果。
3. 只有确认了引脚顺序、属性白名单和理想性声明后，使用 `simplis-v2 catalog approve` 生成项目使用的锁定 catalog。
4. `approval: pending` 的记录可用于说明缺口或 GUI 回导诊断，不能用于可验证设计。

`approved` 只表示 v2 可以生成并验证该受控接口；它不等于已经完成项目级瞬态、POP 或效率验证。`fully_ideal` 表示目录中没有不透明厂商模块，`static_valid` / `netlisted` 仍由每一次编译和实际网表化决定。

## 域边界与不透明模块

无地参考的 `SIMPLIS_DIGI1_BUF_N` / `D_PULSE_N_N` 只能留在数字域。若连到模拟节点，必须选带 `RTN` 的已批准设备，例如 `digital_inverter_grounded` 或 `digital_comparator_grounded`，并把 `RTN` 接到明确的模拟参考网（通常是 `0`）。

导入 `.sxcmp`、厂商模型或没有受控理想性证据的符号时，v2 应把它记为 opaque module：保留文件哈希、端口和实例属性，只验证边界连接，并把整个设计最高标成 `boundary_verified`。它永远不能把设计升级为 `fully_ideal`。

`ideal_diode`、`ideal_opamp`、通用 `a_d` / `d_a` 和若干逻辑显示符号被特意保留为 pending：当前安装中虽能观察到相应符号或模型，却没有足够证据证明其是正确可生成的理想 SIMPLIS 结构。
