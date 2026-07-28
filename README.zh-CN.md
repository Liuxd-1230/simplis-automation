# simplis-automation

面向 SIMetrix/SIMPLIS 8.3/8.4 的证据优先自动化。v2 已提升为仓库默认实现，原
v1 完整保存在 `legacy/v1/`。

## 安装与默认流程

```powershell
python -m pip install -e .
simplis doctor --catalog catalog/seed_8_4.yaml
simplis run examples/acot_buck_v84_experiment.yaml --out-dir outputs/acot
simplis finalize outputs/acot/experiment-result.json --out-dir outputs/acot-final
```

`doctor` 只检查安装、路径、版本、SxCommand 和 catalog，不启动 SIMetrix，也不运行
RC 校准电路。自动发现优先使用 8.4，找不到时才回退 8.3。

一次分析任务只启动一个 SIMetrix 进程，在同一进程内完成建图、分析卡、真实网表、
仿真和向量导出。扫参和优化候选不做重复重开与截图；只有最终入选结果额外启动一次
执行干净重开、复网表和原生窗口截图。

截图只针对任务所属 HWND，通过 Win32 `PrintWindow` 保存 PNG，不截取桌面。Codex
使用图像识别检查文字、间距、方向和功能分区，再通过 `simplis finalize-review`
写入结构化证据，不需要视觉模型 API 或密钥。

`simplis` 是默认命令，`simplis-v2` 是兼容别名。`compile`、`verify`、`roundtrip`
仅用于诊断。旧版命令只能从 `legacy/v1/` 显式调用。
