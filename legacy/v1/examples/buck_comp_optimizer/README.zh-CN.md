# Buck 补偿器闭环优化示例

这个目录提供 `simplis_cli.py optimize` 的真实 SIMetrix/SIMPLIS 示例。默认模板会打开 `generated/ideal_buck_wide/ideal_buck_wide.sxsch`，运行 `simplis_run`，并把每个候选的标量指标写入 optimizer 期望的 `{{RESULT_JSON}}`。

先安装报告依赖：

```powershell
pip install -r requirements.txt
```

确认本机配置可用：

```powershell
python scripts\simplis_cli.py show-config
```

运行 3 个真实候选：

```powershell
python scripts\simplis_cli.py optimize `
  --spec examples\buck_comp_optimizer\optimizer_spec.json `
  --work-dir outputs\buck_comp_optimizer `
  --max-evals 3 `
  --timeout 240 `
  --batch
```

输出会写入 `outputs/buck_comp_optimizer/`：

- `optimization_history.json`
- `best_candidate.json`
- `summary.csv`
- `report.zh-CN.md`
- `charts/score_convergence.png`
- `charts/key_metrics.png`
- `charts/parameter_trace.png`

当前 `candidate_template.sxscr` 重点验证真实 SIMPLIS 启动、候选渲染、指标回传和报告生成链路。若要做严格的补偿器参数优化，请把模板中的占位指标替换为经过验证的 `SetComponentValue`/`SetInstanceParamValue` 参数注入和真实波形/AC 后处理表达式。
