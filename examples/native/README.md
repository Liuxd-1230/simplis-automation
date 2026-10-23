# 原生可编辑示例

完整开关电源示例见 [Buck 原生工程](acot_buck/README.md)，包含功率级、反馈、纹波注入、
比较器、锁存器、器件搭建的 ACOT 定时器和驱动，支持真实冷启动、输入跃变及 POP/AC。
定时器根据实际 VIN/VOUT 充电比较，内部没有固定脉宽单稳态。下面保留两个小型基础示例。

将本目录整体复制到任务目录后，用 SIMetrix/SIMPLIS 打开原理图。

|文件|作用|
|---|---|
|`simple_rc.sxsch`|1 V、1 kΩ、1 µF 的 RC 电路|
|`feedback.sxsch`|参考输入 → 控制器 → RC 网络，输出沿底部返回控制器|
|`controller.sxcmp`|压控电压源，输出为 `9 × (VREF − VOUT)`|
|`plant.sxcmp`|独立的 RC 子模块，端口为 DRIVE、VOUT、RTN|

主原理图和两个子模块均为原生对象，器件、参数、导线和端口可编辑。子模块采用相对路径，
需要保存在同一目录。通过主原理图进入子模块编辑，并保留外部符号接口。全部器件定义已嵌入，
依赖合法安装的 SIMetrix/SIMPLIS；无需项目论文电路或额外商业模型文件。

两张主原理图是电路源文件。导出的网表、预处理网表、预览和波形是生成物。
参考器件表 `assets/device-reference/devices.sxsch` 只供选型查阅，不参与连接。

## 原理与验证

RC 从零初始电压开始，时间常数为 `RC = 1 ms`。反馈示例满足
`RC·dVOUT/dt = 9·VREF − 10·VOUT`，因此稳态为 0.9 V，时间常数为 100 µs。
初始条件在原理图仿真指令中明确设置。该示例用于验证层级、端口和负反馈连接。

在 8.40 中实际打开、保存主原理图、导出网表、预处理并运行后：

|检查项|RC|反馈示例|
|---|---:|---:|
|初始输出|0 V|0 V|
|一个时间常数后的输出|0.6321206 V|0.5689085 V|
|仿真结束输出|0.9932621 V，5 ms|0.8999591 V，1 ms|

原生导出的网表检查包括：RC 串并联节点、两个子电路定义、父子端口顺序和反馈极性。
原生预览经过人工查看。现有 v2 的引脚坐标变换被复用，未增加通用自动布线器。

重新生成固定示例需要可选后端依赖：

```powershell
python -m pip install -e .
python scripts/build_native_examples.py --out-dir <new-example-directory>
python tests/validate_v3_native.py --gui <SIMetrix.exe> --solver <simplis.exe> --out-dir <fresh-check-directory>
```

验证命令只打开任务目录副本，并以不带 `/i` 的方式启动独立实例。它不覆盖仓库示例，
不关闭其他用户窗口。生成器只处理这里两个明确布局的示例，不提供任意网表自动还原。
