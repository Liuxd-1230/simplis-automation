#!/usr/bin/env python3
"""Closed-loop SIMetrix/SIMPLIS parameter optimization scaffold.

The script is intentionally schematic-agnostic. A project-specific .sxscr
template performs parameter injection, runs SIMPLIS, and writes metric JSON.
This Python wrapper proposes candidates, launches SIMetrix, scores metrics,
and resumes from history.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import random
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from runtime_config import resolve_simetrix_exe


OPTIMIZER_OUTPUTS = (
    "optimization_history.json",
    "best_candidate.json",
    "summary.csv",
    "report.zh-CN.md",
)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(data), indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def resolve_spec_relative(spec_path: Path, value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = spec_path.parent / path
    return path.resolve()


def normalize_parameters(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    params: dict[str, dict[str, Any]] = {}
    for name, value in raw.items():
        if isinstance(value, list):
            params[name] = {"values": value, "initial": value[0]}
        elif isinstance(value, dict):
            params[name] = dict(value)
        else:
            raise ValueError(f"Unsupported parameter spec for {name!r}: {value!r}")
    return params


def candidate_key(candidate: dict[str, float]) -> str:
    return json.dumps({k: candidate[k] for k in sorted(candidate)}, sort_keys=True)


def clip(value: float, spec: dict[str, Any]) -> float:
    bounds = spec.get("bounds")
    if bounds:
        value = max(float(bounds[0]), min(float(bounds[1]), value))
    return value


def safe_template_value(value: Any) -> str:
    text = str(value)
    if any(item in text for item in ("{{", "}}", "'", "\r", "\n")):
        raise ValueError(f"Unsafe template value: {text!r}")
    return text


def render_template(
    template: str,
    candidate: dict[str, float],
    result_json: Path,
    candidate_json: Path,
    extra_values: dict[str, Any] | None = None,
) -> str:
    values = {name: safe_template_value(f"{value:.12g}") for name, value in candidate.items()}
    values["RESULT_JSON"] = safe_template_value(result_json)
    values["CANDIDATE_JSON"] = safe_template_value(candidate_json)
    if extra_values:
        for name, value in extra_values.items():
            values[name] = safe_template_value(value)
    text = template
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def grid_candidates(params: dict[str, dict[str, Any]]) -> list[dict[str, float]]:
    names = list(params)
    value_lists = []
    for name in names:
        spec = params[name]
        if "values" not in spec:
            raise ValueError(f"Grid strategy requires parameter {name!r} to define values")
        value_lists.append([float(v) for v in spec["values"]])
    return [dict(zip(names, values)) for values in itertools.product(*value_lists)]


def random_candidate(params: dict[str, dict[str, Any]], rng: random.Random) -> dict[str, float]:
    candidate: dict[str, float] = {}
    for name, spec in params.items():
        if "values" in spec:
            candidate[name] = float(rng.choice(spec["values"]))
            continue
        lo, hi = map(float, spec["bounds"])
        if spec.get("scale") == "log":
            candidate[name] = 10 ** rng.uniform(math.log10(lo), math.log10(hi))
        else:
            candidate[name] = rng.uniform(lo, hi)
    return candidate


def initial_candidate(params: dict[str, dict[str, Any]]) -> dict[str, float]:
    candidate = {}
    for name, spec in params.items():
        if "initial" in spec:
            candidate[name] = float(spec["initial"])
        elif "values" in spec:
            candidate[name] = float(spec["values"][0])
        else:
            lo, hi = map(float, spec["bounds"])
            candidate[name] = math.sqrt(lo * hi) if spec.get("scale") == "log" else 0.5 * (lo + hi)
    return candidate


def coordinate_neighbors(center: dict[str, float], params: dict[str, dict[str, Any]], shrink: float) -> list[dict[str, float]]:
    out = [dict(center)]
    for name, spec in params.items():
        step = float(spec.get("step", 0.1))
        if spec.get("scale") == "log":
            factor = float(spec.get("factor", 10 ** step)) ** shrink
            for value in (center[name] / factor, center[name] * factor):
                cand = dict(center)
                cand[name] = clip(value, spec)
                out.append(cand)
        else:
            delta = step * shrink
            for value in (center[name] - delta, center[name] + delta):
                cand = dict(center)
                cand[name] = clip(value, spec)
                out.append(cand)
    unique: dict[str, dict[str, float]] = {}
    for cand in out:
        unique[candidate_key(cand)] = cand
    return list(unique.values())


def score_metrics(metrics: dict[str, Any], weights: dict[str, float], constraints: dict[str, dict[str, float]]) -> float:
    if metrics.get("failed"):
        return math.inf
    penalty = 0.0
    for name, rule in constraints.items():
        if name not in metrics:
            return math.inf
        value = float(metrics[name])
        if "min" in rule and value < float(rule["min"]):
            penalty += 1e9 + (float(rule["min"]) - value) ** 2
        if "max" in rule and value > float(rule["max"]):
            penalty += 1e9 + (value - float(rule["max"])) ** 2
    score = penalty
    for name, weight in weights.items():
        if name not in metrics:
            return math.inf
        score += float(weight) * abs(float(metrics[name]))
    return score


def score_sort_value(row: dict[str, Any]) -> float:
    score = row.get("score")
    if score is None:
        return math.inf
    try:
        value = float(score)
    except (TypeError, ValueError):
        return math.inf
    return value if math.isfinite(value) else math.inf


def best_row(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not history:
        return None
    return min(history, key=score_sort_value)


def read_metrics(path: Path, returncode: int, dry_run: bool) -> dict[str, Any]:
    if dry_run:
        return {"failed": False, "dry_run": True}
    if returncode != 0:
        return {"failed": True, "returncode": returncode}
    if not path.exists():
        return {"failed": True, "missing_metrics": True}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {"failed": True, "empty_metrics": True}
    try:
        raw = json.loads(text)
        metrics = raw.get("metrics", raw)
    except json.JSONDecodeError:
        metrics = parse_key_value_metrics(text)
    metrics.setdefault("failed", False)
    return metrics


def parse_key_value_metrics(text: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if value.lower() in {"true", "false"}:
            metrics[name] = value.lower() == "true"
            continue
        try:
            metrics[name] = float(value)
        except ValueError:
            metrics[name] = value
    if not metrics:
        return {"failed": True, "unparsed_metrics": True}
    return metrics


def mock_metrics(candidate: dict[str, float], spec: dict[str, Any]) -> dict[str, float | bool]:
    target = spec.get("mock_target", {})
    metrics = {"failed": False}
    error = 0.0
    for name, value in candidate.items():
        center = float(target.get(name, value))
        error += (float(value) - center) ** 2
    metrics["objective"] = error
    metrics["vout_dc_error_mv"] = 1000.0 * error
    metrics["loadstep_undershoot_mv"] = 100.0 * math.sqrt(error)
    return metrics


def run_candidate(
    idx: int,
    candidate: dict[str, float],
    template: str,
    work: Path,
    spec: dict[str, Any],
    args: argparse.Namespace,
    template_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result_json = work / f"candidate_{idx:04d}_metrics.json"
    candidate_json = work / f"candidate_{idx:04d}_candidate.json"
    script = work / f"candidate_{idx:04d}.sxscr"
    save_json(candidate_json, {"candidate": candidate})
    script.write_text(render_template(template, candidate, result_json, candidate_json, template_values), encoding="utf-8")

    if args.mock:
        metrics = mock_metrics(candidate, spec)
        save_json(result_json, {"candidate": candidate, "metrics": metrics})
        rc = 0
    elif args.dry_run:
        metrics = read_metrics(result_json, 0, dry_run=True)
        rc = 0
    else:
        cmd = [args.simetrix_exe]
        if args.interactive:
            cmd.append("/i")
        cmd += ["/s", str(script)]
        try:
            rc = subprocess.run(cmd, cwd=str(work), check=False, timeout=args.timeout).returncode
        except subprocess.TimeoutExpired:
            rc = 124
        metrics = read_metrics(result_json, rc, dry_run=False)

    weights = {k: float(v) for k, v in spec.get("weights", {}).items()}
    constraints = spec.get("constraints", {})
    score = score_metrics(metrics, weights, constraints)
    return {
        "index": idx,
        "candidate": candidate,
        "script": str(script),
        "result_json": str(result_json),
        "returncode": rc,
        "metrics": metrics,
        "score": score,
    }


def next_candidates(
    strategy: str,
    params: dict[str, dict[str, Any]],
    history: list[dict[str, Any]],
    spec: dict[str, Any],
    rng: random.Random,
    seen: set[str],
) -> list[dict[str, float]]:
    if strategy == "grid":
        return [cand for cand in grid_candidates(params) if candidate_key(cand) not in seen]
    if strategy == "random":
        out = []
        attempts = 0
        while len(out) < int(spec.get("batch_size", 1)) and attempts < 1000:
            attempts += 1
            cand = random_candidate(params, rng)
            key = candidate_key(cand)
            if key not in seen:
                out.append(cand)
                seen.add(key)
        return out
    if strategy == "coordinate":
        completed = [row for row in history if math.isfinite(score_sort_value(row))]
        if completed:
            center = min(completed, key=score_sort_value)["candidate"]
        else:
            center = initial_candidate(params)
        rounds = max(0, len(history) // max(1, 1 + 2 * len(params)))
        shrink = float(spec.get("shrink", 0.5)) ** rounds
        return [cand for cand in coordinate_neighbors(center, params, shrink) if candidate_key(cand) not in seen]
    raise ValueError(f"Unknown strategy: {strategy}")


def format_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return "inf"
        return f"{value:.12g}"
    return str(value)


def ordered_candidate_names(history: list[dict[str, Any]], spec: dict[str, Any]) -> list[str]:
    names = list(spec.get("parameters", {}).keys())
    seen = set(names)
    for row in history:
        for name in row.get("candidate", {}):
            if name not in seen:
                names.append(name)
                seen.add(name)
    return names


def ordered_metric_names(history: list[dict[str, Any]], spec: dict[str, Any]) -> list[str]:
    report = spec.get("report", {})
    names = list(report.get("chart_metrics", []))
    seen = set(names)
    for row in history:
        for name in row.get("metrics", {}):
            if name == "failed":
                continue
            if name not in seen:
                names.append(name)
                seen.add(name)
    return names


def sorted_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(history, key=lambda row: (score_sort_value(row), int(row.get("index", 0))))


def write_summary_csv(path: Path, history: list[dict[str, Any]], spec: dict[str, Any]) -> None:
    candidate_names = ordered_candidate_names(history, spec)
    metric_names = ordered_metric_names(history, spec)
    fieldnames = ["index", "score", "failed", "returncode"]
    fieldnames.extend(f"candidate_{name}" for name in candidate_names)
    fieldnames.extend(f"metric_{name}" for name in metric_names)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted_history(history):
            metrics = row.get("metrics", {})
            out = {
                "index": format_scalar(row.get("index")),
                "score": format_scalar(row.get("score")),
                "failed": format_scalar(bool(metrics.get("failed", False))),
                "returncode": format_scalar(row.get("returncode")),
            }
            candidate = row.get("candidate", {})
            for name in candidate_names:
                out[f"candidate_{name}"] = format_scalar(candidate.get(name))
            for name in metric_names:
                out[f"metric_{name}"] = format_scalar(metrics.get(name))
            writer.writerow(out)


def metric_label(name: str, spec: dict[str, Any]) -> str:
    labels = spec.get("report", {}).get("metric_labels_zh", {})
    return str(labels.get(name, name))


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def write_markdown_report(path: Path, history: list[dict[str, Any]], spec: dict[str, Any]) -> None:
    report = spec.get("report", {})
    title = str(report.get("title", "SIMPLIS 闭环优化报告"))
    best = best_row(history)
    failed_count = sum(1 for row in history if row.get("metrics", {}).get("failed") or not math.isfinite(score_sort_value(row)))
    lines = [
        f"# {title}",
        "",
        "## 概览",
        "",
        f"- 候选总数：{len(history)}",
        f"- 失败候选：{failed_count} / {len(history)}",
    ]
    if best:
        lines.append(f"- 最佳得分：{format_scalar(best.get('score'))}")
        lines.append(f"- 最佳候选序号：{format_scalar(best.get('index'))}")

    if best:
        lines.extend(["", "## 最佳参数", ""])
        candidate_rows = [[name, format_scalar(value)] for name, value in best.get("candidate", {}).items()]
        lines.append(markdown_table(["参数", "值"], candidate_rows or [["无", ""]]))

        lines.extend(["", "## 关键指标", ""])
        metric_names = ordered_metric_names(history, spec)
        metric_rows = [
            [metric_label(name, spec), name, format_scalar(best.get("metrics", {}).get(name))]
            for name in metric_names
            if name in best.get("metrics", {})
        ]
        lines.append(markdown_table(["指标", "字段", "值"], metric_rows or [["无", "", ""]]))

    lines.extend([
        "",
        "## 图表",
        "",
        "![得分收敛](charts/score_convergence.png)",
        "",
        "![关键指标](charts/key_metrics.png)",
        "",
        "![参数轨迹](charts/parameter_trace.png)",
        "",
        "## 输出文件",
        "",
        "- `optimization_history.json`：完整候选历史。",
        "- `best_candidate.json`：当前最佳候选。",
        "- `summary.csv`：按得分排序的表格摘要。",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def numeric_series(history: list[dict[str, Any]], getter: Any) -> tuple[list[int], list[float]]:
    xs: list[int] = []
    ys: list[float] = []
    for pos, row in enumerate(history):
        raw = getter(row)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            xs.append(int(row.get("index", pos)))
            ys.append(value)
    return xs, ys


def save_empty_chart(path: Path, title: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.set_title(title)
    ax.text(0.5, 0.5, "No finite data", ha="center", va="center", transform=ax.transAxes)
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def write_charts(work: Path, history: list[dict[str, Any]], spec: dict[str, Any]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    charts = work / "charts"
    charts.mkdir(parents=True, exist_ok=True)

    ordered = sorted(history, key=lambda row: int(row.get("index", 0)))
    xs, scores = numeric_series(ordered, lambda row: row.get("score"))
    if scores:
        best_scores: list[float] = []
        current = math.inf
        for score in scores:
            current = min(current, score)
            best_scores.append(current)
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.plot(xs, scores, marker="o", label="score")
        ax.plot(xs, best_scores, marker=".", label="best so far")
        ax.set_title("Score convergence")
        ax.set_xlabel("candidate index")
        ax.set_ylabel("score")
        ax.grid(True, alpha=0.3)
        ax.legend()
        fig.tight_layout()
        fig.savefig(charts / "score_convergence.png", dpi=140)
        plt.close(fig)
    else:
        save_empty_chart(charts / "score_convergence.png", "Score convergence")

    metric_names = spec.get("report", {}).get("chart_metrics") or ordered_metric_names(ordered, spec)[:4]
    fig, ax = plt.subplots(figsize=(7, 4))
    plotted = False
    for name in metric_names:
        mx, my = numeric_series(ordered, lambda row, metric=name: row.get("metrics", {}).get(metric))
        if my:
            ax.plot(mx, my, marker="o", label=name)
            plotted = True
    if plotted:
        ax.set_title("Key metrics")
        ax.set_xlabel("candidate index")
        ax.grid(True, alpha=0.3)
        ax.legend()
        fig.tight_layout()
        fig.savefig(charts / "key_metrics.png", dpi=140)
        plt.close(fig)
    else:
        plt.close(fig)
        save_empty_chart(charts / "key_metrics.png", "Key metrics")

    param_names = ordered_candidate_names(ordered, spec)
    fig, ax = plt.subplots(figsize=(7, 4))
    plotted = False
    for name in param_names:
        px, py = numeric_series(ordered, lambda row, param=name: row.get("candidate", {}).get(param))
        if py:
            ax.plot(px, py, marker="o", label=name)
            plotted = True
    if plotted:
        ax.set_title("Parameter trace")
        ax.set_xlabel("candidate index")
        ax.grid(True, alpha=0.3)
        ax.legend()
        fig.tight_layout()
        fig.savefig(charts / "parameter_trace.png", dpi=140)
        plt.close(fig)
    else:
        plt.close(fig)
        save_empty_chart(charts / "parameter_trace.png", "Parameter trace")


def write_optimizer_outputs(work: Path, history: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, str]:
    work.mkdir(parents=True, exist_ok=True)
    history_path = work / "optimization_history.json"
    best_path = work / "best_candidate.json"
    summary_path = work / "summary.csv"
    report_path = work / "report.zh-CN.md"

    save_json(history_path, history)
    best = best_row(history)
    if best is not None:
        save_json(best_path, best)
    write_summary_csv(summary_path, history, spec)
    write_charts(work, history, spec)
    write_markdown_report(report_path, history, spec)
    return {
        "history": str(history_path),
        "best": str(best_path),
        "summary": str(summary_path),
        "report": str(report_path),
        "charts": str(work / "charts"),
    }


def reset_optimizer_state(work: Path) -> None:
    for name in OPTIMIZER_OUTPUTS:
        path = work / name
        if path.exists():
            path.unlink()
    charts = work / "charts"
    if charts.exists():
        shutil.rmtree(charts)
    for pattern in ("candidate_*.sxscr", "candidate_*_candidate.json", "candidate_*_metrics.json"):
        for path in work.glob(pattern):
            if path.is_file():
                path.unlink()


def optimize_from_spec(
    spec_path: Path,
    work: Path,
    args: argparse.Namespace,
    *,
    max_evals_override: int | None = None,
    fresh: bool = False,
) -> dict[str, Any]:
    spec_path = spec_path.resolve()
    spec = load_json(spec_path)
    if not isinstance(spec, dict):
        raise SystemExit(f"Optimization spec root must be an object: {spec_path}")
    work = work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    if fresh:
        reset_optimizer_state(work)

    template_path = resolve_spec_relative(spec_path, spec["script_template"])
    template = template_path.read_text(encoding="utf-8")
    template_values = {
        "SPEC_DIR": str(spec_path.parent),
        "TEMPLATE_DIR": str(template_path.parent),
        "WORK_DIR": str(work),
    }
    template_values.update(spec.get("template_values", {}))
    params = normalize_parameters(spec["parameters"])
    strategy = spec.get("strategy", "coordinate")
    max_evals = int(max_evals_override if max_evals_override is not None else spec.get("max_evals", spec.get("iterations", 20)))
    rng = random.Random(int(spec.get("seed", 1)))

    history_path = work / "optimization_history.json"
    loaded = load_json(history_path) if history_path.exists() else []
    if not isinstance(loaded, list):
        raise SystemExit(f"Optimization history must be a list: {history_path}")
    history: list[dict[str, Any]] = loaded
    seen = {candidate_key(row["candidate"]) for row in history if "candidate" in row}

    while len(history) < max_evals:
        batch = next_candidates(strategy, params, history, spec, rng, seen)
        if not batch:
            break
        for cand in batch:
            if len(history) >= max_evals:
                break
            key = candidate_key(cand)
            if key in seen:
                continue
            seen.add(key)
            row = run_candidate(len(history), cand, template, work, spec, args, template_values)
            history.append(row)
            write_optimizer_outputs(work, history, spec)
            best = best_row(history)
            print(json.dumps(json_safe({"latest": row, "best": best}), indent=2, ensure_ascii=False, allow_nan=False))

    outputs = write_optimizer_outputs(work, history, spec) if history else {}
    return {
        "evaluations": len(history),
        "best": best_row(history),
        "outputs": outputs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Closed-loop SIMPLIS optimizer")
    parser.add_argument("spec", help="JSON optimization spec")
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--simetrix-exe", help="Path to SIMetrix.exe; overrides runtime config")
    parser.add_argument("--runtime-config", help="JSON runtime config with simetrix_exe")
    parser.add_argument("--max-evals", type=int, help="Override spec max_evals for this run")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--interactive", action="store_true", help="Keep SIMetrix interactive while running candidates")
    parser.add_argument("--batch", dest="interactive", action="store_false", default=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mock", action="store_true", help="Generate synthetic metrics for optimizer validation")
    parser.add_argument("--fresh", action="store_true", help="Remove optimizer state in the work directory before running")
    args = parser.parse_args(argv)
    if not args.mock and not args.dry_run:
        args.simetrix_exe = str(resolve_simetrix_exe(args.simetrix_exe, config_path=args.runtime_config))

    result = optimize_from_spec(
        Path(args.spec),
        Path(args.work_dir),
        args,
        max_evals_override=args.max_evals,
        fresh=args.fresh,
    )
    print(json.dumps(json_safe({"result": result}), indent=2, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
