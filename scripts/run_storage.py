from __future__ import annotations

import argparse
import json
from pathlib import Path
import stat
import sys


KINDS = ("temporary", "candidate", "final", "diagnostic")
KEEP_ROLES = {"input", "log", "record", "metrics", "evidence"}


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def register_run(root: Path, generated: dict[str, str], *, kind: str,
                 completed: bool, environment: dict | None = None) -> dict:
    root = root.resolve()
    if kind not in KINDS:
        raise ValueError(f"Unknown run kind: {kind}")
    manifest = root / "storage.json"
    if manifest.exists():
        raise ValueError("Run storage is already registered")
    files = []
    for name, role in generated.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"Generated file must exist inside this run: {name}")
        stat = path.stat()
        files.append(dict(path=path.relative_to(root).as_posix(), role=role,
                          bytes=stat.st_size, mtime_ns=stat.st_mtime_ns))
    record = dict(schema=1, root=str(root), kind=kind, completed=completed,
                  protected=kind == "final", environment=environment or {}, files=files)
    write_json(manifest, record)
    return record


def load_run(root: Path) -> dict:
    root = root.resolve()
    record = json.loads((root / "storage.json").read_text(encoding="utf-8"))
    if record["schema"] != 1 or Path(record["root"]).resolve() != root:
        raise ValueError("Storage record belongs to another run directory")
    return record


def protect(root: Path, reason: str) -> dict:
    if not reason.strip():
        raise ValueError("Provide the reason for retaining this run")
    record = load_run(root)
    record.update(protected=True, retention_reason=reason)
    write_json(root / "storage.json", record)
    return record


def register_metrics(root: Path, metrics: dict) -> None:
    record = load_run(root)
    path = root / "metrics.json"
    # 已有人工作业的测量文件不能被覆盖
    old = next((f for f in record["files"] if f["path"] == path.name), None)
    if path.exists() and (old is None or not unchanged(path, old)):
        raise ValueError("Existing metrics were edited or are not owned by this run")
    write_json(path, metrics)
    stat = path.stat()
    record["files"] = [f for f in record["files"] if f["path"] != path.name]
    record["files"].append(dict(path=path.name, role="metrics", bytes=stat.st_size,
                                mtime_ns=stat.st_mtime_ns))
    write_json(root / "storage.json", record)


def unchanged(path: Path, item: dict) -> bool:
    stat = path.stat()
    return stat.st_size == item["bytes"] and stat.st_mtime_ns == item["mtime_ns"]


def cleanup(root: Path, *, apply: bool = False) -> dict:
    root = root.resolve()
    record = load_run(root)
    if any(not (root / f["path"]).resolve().is_relative_to(root) for f in record["files"]):
        raise ValueError("Storage entry points outside this run")
    measured = any(f["role"] == "metrics" and (root / f["path"]).is_file()
                   and unchanged(root / f["path"], f) for f in record["files"])
    entries = []
    for item in record["files"]:
        path = (root / item["path"]).resolve()
        if not path.is_file():
            entries.append(dict(path=item["path"], action="absent", bytes=0))
            continue
        reason = None
        if record["protected"] or record["kind"] == "final":
            reason = "protected run"
        elif item["role"] in KEEP_ROLES:
            reason = "input, diagnostics or extracted evidence"
        elif not unchanged(path, item):
            reason = "file changed after registration"
        elif item["role"] == "raw" and record["completed"] and not measured:
            reason = "extract and save metrics before pruning successful raw data"
        elif item["role"] not in {"raw", "scratch"}:
            reason = "unrecognized role"
        size = path.stat().st_size
        action = "keep" if reason else "delete" if apply else "would_delete"
        if action == "delete":
            # SIMPLIS 8.40 会将部分状态输出设成只读；仅处理已登记且没有改动的生成文件
            if not path.stat().st_mode & stat.S_IWRITE:
                path.chmod(path.stat().st_mode | stat.S_IWRITE)
            path.unlink()
        entries.append(dict(path=item["path"], action=action, reason=reason, bytes=size))
    result = dict(run=str(root), dry_run=not apply, files=entries,
                  total_remaining_bytes=sum(p.stat().st_size for p in root.rglob("*") if p.is_file()),
                  reclaimable_bytes=sum(e["bytes"] for e in entries if e["action"] in {"delete", "would_delete"}),
                  registered_remaining_bytes=sum(e["bytes"] for e in entries if e["action"] not in {"delete", "absent"}),
                  unregistered_files=[p.relative_to(root).as_posix() for p in root.rglob("*")
                                      if p.is_file() and p.relative_to(root).as_posix() not in
                                      {f["path"] for f in record["files"]} | {"storage.json", "cleanup.json"}])
    if apply:
        write_json(root / "cleanup.json", result)
    return result


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Manage registered task-generated run files; preview by default")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("protect")
    p.add_argument("run", type=Path)
    p.add_argument("--reason", required=True)
    p = commands.add_parser("cleanup")
    p.add_argument("runs", type=Path, nargs="+")
    p.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.command == "protect":
        result = protect(args.run.resolve(), args.reason)
    else:
        reports = [cleanup(p) for p in args.runs]
        if args.apply:
            reports = [cleanup(p, apply=True) for p in args.runs]
        result = dict(runs=reports, reclaimable_bytes=sum(r["reclaimable_bytes"] for r in reports))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
