# 运行环境、版本与默认数据登记验证

后续状态：8.40 的 POP/AC 阻塞已完成专项修复，并通过完整原生 Buck 验证。
见 [POP/AC 专项结果](pop-ac-buck-validation.md)。下表保留本轮最初验证时的状态。

日期：2026-10-22 至 2026-10-23。基于尚未合并的 PR #8，在独立后续分支修改。
v3.1 继续作为唯一默认 Skill；v2 包名、版本、阶段和结果协议保持原样。

## 实际环境与状态

本机发现两套 8.40 安装，其中一套安装目录含空格。Windows PE 资源分别报告
GUI `8.40.0.2`、solver `8.40.0.25254`、SxCommand `8.40.0.3`。
两套安装的所查库内容均匹配已有记录。代表性完整测试在一套安装上执行，另一套完成
包含中文和空格运行目录的原生控制电路、v2、独立网表验证和最终核验测试。
没有发现 8.30；目录中的历史 8.30 seed 和哈希继续保留，未将它们计为本轮实机验证。

| 项目 | 8.30 | 8.40 本轮实际结果 |
|---|---|---|
| 环境发现、多版本选择、显式版本限制 | 模拟环境通过；缺少实机 | PE 版本确认、两套安装资源匹配通过 |
| C/D/E、自定义、中文及空格目录解析 | 模拟环境通过 | 模拟环境通过；含空格安装目录、中文和空格运行目录真实仿真通过 |
| `.sxsch` 原生打开、保存、图像导出 | 未实机验证 | 参考表、基础 RC、层级反馈通过 |
| `.sxcmp` 与层级端口 | 未实机验证 | 打开/导出、父子模块连接、交叉导线验证通过 |
| 网表及预处理 | 未实机验证 | 原生 RC/反馈网表与端口映射通过；公共 `verify` 在中文/空格目录执行及登记通过 |
| 关键器件 | 未实机验证 | 比较器、SR 锁存器、驱动、反馈的真实控制电路通过 |
| SIMPLIS 瞬态与数据读取 | 未实机验证 | RC/反馈稳态值与时间常数、控制电路输出通过 |
| v2 YAML 默认执行 | 代码支持，高级功能尚未实机验证 | SR 行为验证、原始 YAML 保持、快照和默认登记通过，包含中文/空格目录 |
| 默认原生 CLI 与存储 | 未实机验证 | `simplis native` 自动发现和登记通过 |
| POP / AC | 未实机验证 | 求解器日志显示 POP 收敛及 AC 完成；脚本超时，完整自动化未通过 |
| finalize | 未实机验证 | 原生重新打开、网表、截图、版本记录和最终目录登记通过，包含中文/空格目录；保持原有视觉审阅协议 |
| 原生 SIMetrix 电气分析 | 未实机验证 | 本轮未提供受管理的原生 SIMetrix 分析后端 |

真实中文输出目录测试触发过 Windows 监控 JSON 解码异常，本轮已修复并以真实
PowerShell 中文 JSON 验证。随后单独比较路径：中文无空格的启动脚本通过，含空格的
脚本路径在直接 `/s filename` 调用下失败。依据官方 UsersManual 18.3 和
ScriptReference 4.146，在公共启动函数中通过 `/s` 调用 `Execute(string)`，以完整字符串
传入带空格的脚本名称；未创建另一份源文件、目录别名或修改系统设置。
修复后，含空格安装目录与中文/空格运行目录组合的真实原生控制仿真通过，导出两个
各 10226 点的向量，当前网表、完成状态和源文件映射均通过检查。
相同条件下的 v2 YAML 与 finalize 后续测试也通过。最终核验默认受输入结果的软件版本约束，
核验请求和运行记录同时保存 `runtime_transition`，显示输入版本、执行版本和版本是否变化。
**中文安装目录本身的实际安装/启动仍仅有模拟环境覆盖**，未将中文工作目录测试替代它。

AC 案例的 `.dbg` 明确记录 POP 一次迭代收敛、AC 求解结束；状态窗口显示 Finished，
但 `simplis_run` 后续状态与向量没有返回。该结果记录为 `infrastructure_failed`，
没有把已有数据文件或界面“完成”文字当成可信的 AC 自动化完成证据。
这项问题是否属于既有配置或后端行为，尚未通过 PR #8 的同条件复现实验区分。
测试时还修复了已有模拟器件案例中三个探针的位置；只改图形位置，电路连接未改变。

## 默认管理覆盖范围

| 入口 | 默认登记的位置与范围 |
|---|---|
| `simplis run` / `simplis-v2 run` / `run_experiment()` | 原运行根目录；原结果和阶段保留，增加 inputs、run.json、storage.json |
| 默认 `optimize` evaluator | 每个候选/工况通过上述公共执行函数登记，选中的受管理结果自动保护；本轮未重新实机执行完整优化流程 |
| `verify` / 原有 `sweep` | 每个 build 下的 verification 子目录；记录编译与网表验证，未冒充电气仿真 |
| `simplis native` / `run_native()` | 原生瞬态运行根目录；实际执行复制的 `.sxsch` 及相对子模块 |
| `scripts/run_managed.py` | 继续保留旧命令及 execution/ 路径，复用上述实现 |
| stdlib helper 的 deck run | 原有登记继续保留，增加统一环境信息；省略 --exe 时自动发现 |
| `finalize` | 输入受管理运行被保护；核验输出使用 final 类型登记 |

自定义 evaluator、用户手工启动的任意原生命令、历史目录不自动接管。
原生入口目前处理 SIMPLIS transient Show 导出；外部模型/include 目录需要任务明确准备。
UTF-8 原生文本、项目内相对子模块继续遵守 PR #8 的适用范围。

运行要求新的或空目录；已有文件触发明确错误，避免覆盖人工修改或旧结果。
YAML 输入保留原文快照，运行前后检查身份；编译与独立验证之间也检查源文件变化。
快照属于一次实验的证据，YAML 和原生源文件的权威关系保持不变。
记录包含实际状态、软件/库身份、参数、日志、原始波形、测量关联及文件占用统计。
原始波形默认保留；保存指标不会释放它们。正式结果、保护结果、诊断记录、改动文件
和管理范围外文件的保留策略沿用 PR #8，未执行历史结果迁移或自动清理。

## 已执行的验证与复现

普通测试覆盖配置优先级、C/D/E 绝对路径模拟、中文/空格、多版本、误导目录名、
程序版本冲突、跨安装库拒绝、缺失指定版本、模型内容变化、seed 旧指纹保留、默认失败登记、
人工文件保留和 Windows 中文进程信息，**150 项通过**。新增模拟用于软件环境检查；文中电气数据来自真实仿真。

10 次静态环境发现平均 79.169 ms，最大 104.469 ms，模拟器启动次数为零。
wheel 已完成构建与独立目标目录安装，检查了公共模块、`compatibility.json` 和两个原有 CLI 名称。

```powershell
python -m unittest discover -s tests -p "test_*.py"
python tests/validate_runtime_compatibility.py --out-dir <fresh-directory>
python tests/validate_control_workflow.py --gui <SIMetrix.exe> --install <root> --out-dir <fresh-control-directory>
simplis run examples/catalog_proofs/analog_primitives_v84_experiment.yaml --out-dir <fresh-ac-directory>
```

本轮原始输出保留于工作区的忽略目录：`outputs/runtime-compatibility-verified/`、
`outputs/runtime-portability-control/`、`outputs/runtime-portability-pop-ac2/`、
`outputs/runtime-portability-finalization/`、`outputs/原生 空格验证/`、`outputs/v2 中文空格验证/`、
`outputs/finalize 中文空格验证/` 及失败诊断目录。没有上传私有绝对路径和波形。
控制电路稳态约 1.797–2.203 V，驱动频率 292787.791 Hz；原生负载由 100 kΩ 改为
20 kΩ 后频率 296223.479 Hz，与前次 PR #8 的真实结果一致。

新增 AC 源、双节点波特探针、POP 触发器来自用户更新的参考表，编号 D26–D28。
原有器件定义除编辑器保存的行末空格外保持一致。新增引脚已与安装库逐项核对，
POP 的 `simplis_param.lb` 依赖内容登记在索引中；这些静态/原生检查没有被写成
三种器件在全部分析与两个版本下均已完成电气认证。
