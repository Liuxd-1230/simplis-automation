from __future__ import annotations

import hashlib
from itertools import pairwise
import math
import re
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from .io import write_json


_COMPLEX = re.compile(
    r"^\(?\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*,\s*"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*\)?$"
)


def _scalar(text: str) -> float | dict[str, float]:
    match = _COMPLEX.fullmatch(text.strip())
    if match:
        return {"real": float(match.group(1)), "imag": float(match.group(2))}
    return float(text.strip())


def _read_show_file(path: str | Path, *, keep_samples: bool) -> dict[str, Any]:
    source = Path(path).resolve()
    x_values, y_values = [], []
    digest = hashlib.sha256()
    count, previous, minimum, maximum = 0, None, None, None
    finite, monotonic, positive = True, True, True
    with source.open("rb") as handle:
        raw_header = handle.readline()
        digest.update(raw_header)
        header = raw_header.decode("utf-8", errors="replace").split()
        if len(header) < 2:
            raise ValueError(f"SIMetrix Show file has no x/y header: {source}")
        for raw_line in handle:
            digest.update(raw_line)
            parts = raw_line.decode("utf-8", errors="replace").split(maxsplit=1)
            if len(parts) != 2:
                continue
            x, y = float(parts[0]), _scalar(parts[1])
            count += 1
            finite = finite and math.isfinite(x) and (all(math.isfinite(v) for v in y.values()) if isinstance(y, dict) else math.isfinite(y))
            monotonic = monotonic and (previous is None or x >= previous)
            positive = positive and x > 0
            minimum = x if minimum is None else min(minimum, x)
            maximum = x if maximum is None else max(maximum, x)
            previous = x
            if keep_samples:
                x_values.append(x)
                y_values.append(y)
    result = dict(path=str(source), x_name=header[0], y_name=header[1],
                  sha256=digest.hexdigest(), created_at=source.stat().st_mtime,
                  samples=count, finite=bool(count and finite), monotonic=monotonic,
                  x_min=minimum, x_max=maximum, x_positive=positive)
    if keep_samples:
        result.update(x=x_values, y=y_values)
    return result


def parse_show_file(path: str | Path, *, include_summary: bool = False) -> dict[str, Any]:
    parsed = _read_show_file(path, keep_samples=True)
    if include_summary:
        return parsed
    return {key: parsed[key] for key in ("path", "x_name", "y_name", "x", "y", "sha256", "created_at")}


def summarize_show_file(path: str | Path) -> dict[str, Any]:
    return _read_show_file(path, keep_samples=False)


def build_vector_manifest(
    *,
    run_id: str,
    candidate_hash: str,
    source_hash: str,
    run_started_at: float,
    exports: Iterable[Mapping[str, Any]],
    output_path: str | Path,
    trusted: bool,
    simulation_info_hash: str | None = None,
    artifact_hashes: Mapping[str, str] | None = None,
    parsed_vectors: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for export in exports:
        path = Path(str(export["path"])).resolve()
        group = str(export["group"])
        analysis = str(export["analysis"])
        vector = str(export["vector"])
        if not path.is_file() or path.stat().st_size == 0:
            errors.append({"code": "vector_file_missing", "group": group, "vector": vector, "path": str(path)})
            continue
        try:
            parsed = parsed_vectors[str(path)] if parsed_vectors is not None else summarize_show_file(path)
        except (OSError, ValueError) as exc:
            errors.append({"code": "vector_parse_failed", "group": group, "vector": vector, "path": str(path), "reason": str(exc)})
            continue
        if "samples" not in parsed:
            x, y = parsed["x"], parsed["y"]
            summary = dict(samples=len(y), x_min=min(x) if x else None, x_max=max(x) if x else None,
                           finite=bool(x and all(math.isfinite(v) for v in x) and all(
                               all(math.isfinite(v) for v in item.values()) if isinstance(item, dict) else math.isfinite(item) for item in y)),
                           monotonic=all(right >= left for left, right in pairwise(x)))
        else:
            summary = parsed
        record = {
            "run_id": run_id,
            "candidate_hash": candidate_hash,
            "source_hash": source_hash,
            "group": group,
            "analysis": analysis,
            "vector": vector,
            "path": str(path),
            "samples": summary["samples"],
            "x_name": parsed["x_name"],
            "x_min": summary["x_min"],
            "x_max": summary["x_max"],
            "finite": summary["finite"],
            # SIMPLIS event vectors intentionally repeat the transition time
            # to encode the value immediately before and after a discontinuity.
            "monotonic": summary["monotonic"],
            "fresh": parsed["created_at"] >= float(run_started_at) - 1.0,
            "sha256": parsed["sha256"],
        }
        records.append(record)
        if not record["finite"]:
            errors.append({"code": "vector_nonfinite", "group": group, "vector": vector})
        if not record["monotonic"]:
            errors.append({"code": "vector_axis_nonmonotonic", "group": group, "vector": vector})
        if not record["fresh"]:
            errors.append({"code": "vector_stale", "group": group, "vector": vector})
    payload = {
        "schema_version": "simplis-automation/v2/vector-manifest",
        "created_at": time.time(),
        "run_id": run_id,
        "candidate_hash": candidate_hash,
        "source_hash": source_hash,
        "simulation_info_hash": simulation_info_hash,
        "artifact_hashes": dict(artifact_hashes or {}),
        "trusted_simulation": bool(trusted),
        "classification": "scoring_eligible" if trusted and not errors else "diagnostic_only",
        "vectors": records,
        "errors": errors,
    }
    write_json(output_path, payload)
    return payload
