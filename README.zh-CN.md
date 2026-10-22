# SIMPLIS 自动化技能 v3

运行环境发现已统一，默认仿真入口自动登记数据。使用 `simplis doctor --format text`
检查环境，使用原有 `simplis run` 或新增 `simplis native` 运行。旧命令和参数继续保留。
配置方式见[运行环境](references/runtime.md)，实际通过项目和限制见
[兼容验证记录](docs/runtime-compatibility-validation.md)。8.30 与 8.40 的实机验证状态分别记录。

宗旨：**灵活、不臃肿、参考有依据**。入口是 [SKILL.md](SKILL.md)。

v3.1 整合了用户的[中文器件参考](assets/device-reference/README.md)，目前包含 27 种器件。
增加按功能查询、官方资料定位、两个[可编辑原生示例](examples/native/README.md)，以及
[运行数据保存和清理](references/run-storage.md)。清理默认预览，正式证据可以标记保留。

后续修正保持现有范围：原生与 v2 运行可使用同一套登记和保留机制；原始数据默认保留，
清理必须明确选择文件。`.t2` 均值和概要逐行读取，减少大波形的内存占用。
使用包含比较器、锁存器、驱动和反馈的控制电路检查了自动布局、层级、交叉导线及原生参数编辑。
实际测量和适用范围见[验证记录](docs/control-storage-validation.md)。

```powershell
python scripts/simplis_tools.py find --query "需要测量支路电流"
python scripts/simplis_tools.py find --query transformer --install <安装目录>
python scripts/simplis_tools.py locate --install <安装目录或程序路径> --query ScriptReference
python scripts/run_storage.py cleanup <本次任务运行目录>
```

器件查询优先参考表；缺少匹配时查找官方库。参考器件不适合时，使用 `--official` 继续查询。
输出包含引脚、属性、实际来源和验证范围。查询工具依靠双语功能词，未匹配时需要调整关键词。
参考中的部分数字器件由嵌入符号和本机模型提供，索引中记录了依赖。

仿真可以直接使用网表。原生绘图流程以 `.sxsch` 及子模块为准；使用 v2 后端时以 YAML 为准，
图形界面的改动必须先导回 YAML 再生成。生成物与源文件分开保存，交付时说明文件位置和可编辑性。

按需要选择器件查询、原生绘图、子模块、仿真或结果分析；不要求依次走完整流程。
可以直接查安装库、编写原生命令或小脚本，也可以按需使用已有 v2 执行器。
不强制转换 YAML，不自动运行 golden case，不把论文电路当默认模板。

## 默认加载什么

根技能只保留任务路由和必要边界。详细内容分为：

- [器件参考](references/devices.md)：引脚、实例属性、模型来源和版本差异。
- [原生原理图](references/native-schematics.md)：层级端口、布线、网表与显示检查。
- [仿真与数据](references/simulation.md)：命令行运行、波形新鲜度、非均匀采样积分。
- [数据保存与清理](references/run-storage.md)：四类运行记录、测量保存、保留标记和空间统计。
- [来源与适用范围](references/evidence.md)：手册章节与实际观察，区分事实和推断。

`scripts/simplis_tools.py` 是可选的 Python 标准库工具，提供符号查询、自包含网表运行、
`.t2` 读取及时间加权均值。运行 `--help` 查看参数；只使用它不需要安装完整后端依赖。

## 保留兼容性

v3 是技能版本。现有 `simplis` / `simplis-v2` CLI、Python 包版本及严格验证合同保持不变，
作为[可选后端](references/v2-workflow.md)保留。选择该后端后仍遵守其输入与验证要求。
旧 v1 的 `SKILL.md` 改名为 `WORKFLOW.md`，避免同名技能重复注册。

实际任务按目标收尾：查器件不启动仿真；画图不自动扩大为参数扫描；仿真必须检查真实波形，
但不自动附加所有角点、POP、截图或 finalize。开关替代 MOS 时应保留研究依赖的非理想性；
例如 VDS 电流检测需要有限导通电阻。

安装技能时选仓库根目录。开发与命令示例见 [README.md](README.md)。本次版本没有收录私人
论文电路、绝对安装路径、商业手册正文或生成波形。

实际验证了原生打开/保存/导出、层级反馈连接、真实仿真与失败诊断、批量运行清理。
结果和适用范围见[验证记录](docs/v3.1-validation.md)。现有 v2 后端保持兼容，
其输出不会自动加入新清理工具的管理范围。
