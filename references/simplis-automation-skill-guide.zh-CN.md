# SIMPLIS 自动化 Skill 使用说明

这份说明面向使用者和后续维护者，说明当前 `simplis-automation` 能可靠完成什么、应该怎样操作，以及边界在哪里。

## 当前能做什么

1. **检查本机配置**
   - 读取 `config/local_config.json`、环境变量或 CLI 参数。
   - 验证 `SIMetrix.exe` 和 symbol library 目录是否存在。

   ```powershell
   python scripts\simplis_cli.py show-config
   ```

2. **检查原理图结构**
   - 读取文本格式 `.sxsch/.sxcmp`。
   - 统计器件、连线、分析文本、探针和常见 SIMPLIS 元件。

   ```powershell
   python scripts\simplis_cli.py inspect-schematic --input path\to\design.sxsch --out inspection.json --summary-md inspection.md
   ```

3. **运行真实 SIMetrix/SIMPLIS**
   - 通过 `.sxscr` 控制 SIMetrix。
   - 对已有原理图使用 `simplis_run`，行为接近 GUI Run。
   - 建议对用户原始图纸只读打开，并在独立目录运行副本。

   ```text
   Unset EchoOn
   ClearMessageWindow
   OpenSchem /cd /readonly "path/to/copy.sxsch"
   simplis_run
   Let sim_exit_code = GetSIMPLISExitCode()
   Quit
   ```

4. **收集错误证据**
   - 收集 `.deck.err`、`.deck.warn`、`.deck.health`、`.deck.dbg`、netlist、deck、metrics 和向量文件。
   - 注意：`GetSIMPLISExitCode()` 可能是 `0`，但 `.deck.err` 仍然有 POP 失败。

   ```powershell
   python scripts\simplis_cli.py export-agent-evidence --work-dir outputs\case\SIMPLIS_Data --out evidence.json --summary-md evidence.md
   ```

5. **导出波形和 AC 数据**
   - `make-vector-export` 会用只读方式打开原理图，避免污染候选文件。
   - 特殊向量名如 `#VOUT`、数字节点如 `120` 会自动用 `Vec('...')` 引用。

   ```powershell
   python scripts\simplis_cli.py make-vector-export --schematic outputs\case\test.sxsch --out-dir outputs\case\vectors --out outputs\case\export.sxscr --vector simplis_pop1:#VOUT --vector simplis_pop1:#FSW
   python scripts\simplis_cli.py run-script outputs\case\export.sxscr --timeout 420
   python scripts\simplis_cli.py parse-show outputs\case\vectors\pop_vout.txt --out parsed.json
   ```

6. **做优化和报告**
   - `optimize` 支持 spec 驱动、resume、fresh、mock、真实 SIMetrix 运行、CSV/JSON/中文 Markdown 报告和 Matplotlib 图表。
   - 适合已经有稳定候选脚本和明确 metric 的场景。

   ```powershell
   python scripts\simplis_cli.py optimize --spec examples\buck_comp_optimizer\optimizer_spec.json --work-dir outputs\buck_comp_optimizer --max-evals 3 --timeout 240 --batch
   ```

## 推荐工作流

1. **先复制原图**
   - 不直接改 `Downloads` 或用户正在 GUI 中编辑的文件。
   - 每个候选放进独立目录，例如 `outputs\case_a\candidate_001\`。

2. **先跑基线**
   - 记录 `status.txt`。
   - 必查 `SIMPLIS_Data/*.deck.err`、`*.deck.warn`、`*.deck.health`、`*.deck.dbg`。
   - GUI 弹窗只是入口，文件证据才是可复现依据。

3. **POP 不只看收敛**
   - POP 没有 `.err` 不等于电路工作正常。
   - 必须导出 `FSW/CLK/D/VOUT/FB/VC` 等关键波形。
   - 检查是否真实开关、频率是否正确、数字节点是否卡死、输出和反馈 DC 是否合理。

4. **AC 在 POP 波形正常后再看**
   - 从 deck 中读取真实 Bode 表达式，例如 `db(:#FB/:120)`。
   - 不要沿用旧节点号；改线后 Bode probe 节点可能变化。
   - 计算 0 dB 穿越频率和相位裕度，再判断是否满足指标。

5. **补偿参数不要盲扫**
   - 先确认控制方式：谷值比较、峰值比较、平均模式或其他采样结构。
   - 根据碰撞点、纹波幅度、比较器输入和 `VC` DC 值判断工作点。
   - 从基线 plant response 推导补偿零点、极点和中频增益，再少量验证候选。

6. **IC 是工作点的一部分**
   - 这个类电路对电容、电感初始态非常敏感。
   - 可以参考成功 POP 的 `.deck.init`，但不能把所有 `C* / L*` 批量写回。
   - 采样保持或镜像状态电容可能和子模块内部电容耦合，只改外部 IC 会触发 `Error Message ID: 5013` 的 KVL/KCL 冲突。
   - 每次 IC 改动后都要重新跑基线并检查波形。

## 常见误区

- **误区：SIMetrix 进程返回 0 就成功。**  
  正确做法：扫描 `.deck.err/.warn/.health/.dbg`。

- **误区：POP 收敛就说明电路稳定。**  
  正确做法：看 POP 时域波形，尤其开关频率和控制节点是否真实工作。

- **误区：AC probe 的节点号固定。**  
  正确做法：每次改线后从 netlist/deck 读取当前 `.GRAPH` 表达式。

- **误区：参数网格越大越好。**  
  正确做法：先确定 DC 工作点、比较方式、纹波/碰撞点，再算零极点和增益。

- **误区：`.deck.init` 可以全部回填。**  
  正确做法：只回填独立储能状态；对耦合状态先验证 KVL/KCL。

## 当前边界

- 不能保证自动修好任意 SIMPLIS 电路，尤其是控制意图不清、比较器接法不完整、IC 缺失或多稳态的电路。
- 不能替代电源环路设计判断；补偿参数必须结合拓扑、谷值/峰值比较方式、纹波和 DC 工作点。
- 对二进制或非文本 `.sxsch/.sxcmp` 的解析有限，需要先保存为文本格式。
- GUI 截图可以辅助，但可复现诊断以 `SIMPLIS_Data` 文件为准。
- `optimize` 适合已有明确 metric 的闭环搜索，不适合在没有物理约束时盲目探索。

## 交付物建议

一次完整调试应至少保留：

- `inspection.json` / `inspection.md`
- `status.txt`
- `evidence.json` / `evidence.md`
- `vectors/` 下的关键 POP/AC 向量
- 参数候选的变更摘要
- 最终结论：通过项、失败项、仍需人工判断的问题
