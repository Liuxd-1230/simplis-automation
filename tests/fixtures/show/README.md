# 真实输出来源

- `rc_transient.txt`：SIMPLIS 8.40 实际运行 `examples/native/simple_rc.sxsch` 的 Show 导出，1001 点。
- `control_q.txt`：SIMPLIS 8.40 实际运行 `examples/hysteretic_control_experiment.yaml` 的数字 Q 导出，包含重复事件时间。
- `feedback_ac.txt`：从仓库已有真实 Buck AC 原始文件 `../pop_ac/native_buck.f2` 提取 FB 的实部和虚部，逐频点转换为 Show 的两列表示，186 点。
- `script_error.log`：真实 SIMetrix 脚本引用不存在的向量后产生的错误日志；任务路径已替换为 `<TASK>`。

这些文件用于检查读取、事件点、复数和错误检测。性能测量不用于宣称电路指标。
