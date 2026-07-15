"""SIMetrix Show parsing and immutable vector provenance manifests."""

from __future__ import annotations

import math
import re
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from .io import sha256_file, write_json


_COMPLEX = re.compile(
    r"^\(?\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*,\s*"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*\)?$"
)


def _scalar(text: str) -> float | dict[str, float]:
    match = _COMPLEX.fullmatch(text.strip())
    if match:
        return {"real": float(match.group(1)), "imag": float(match.group(2))}
    return float(text.strip())


def parse_show_file(path: str | Path) -> dict[str, Any]:
    source = Path(path).resolve()
    rows: list[tuple[float, float | dict[str, float]]] = []
    with source.open("r", encoding="utf-8", errors="replace") as handle:
        header = handle.readline().split()
        if len(header) < 2:
            raise ValueError(f"SIMetrix Show file has no x/y header: {source}")
        for line in handle:
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                continue
            rows.append((float(parts[0]), _scalar(parts[1])))
    return {
        "path": str(source),
        "x_name": header[0],
        "y_name": header[1],
        "x": [item[0] for item in rows],
        "y": [item[1] for item in rows],
        "sha256": sha256_file(source),
        "created_at": source.stat().st_mtime,
    }


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
            parsed = parse_show_file(path)
        except (OSError, ValueError) as exc:
            errors.append({"code": "vector_parse_failed", "group": group, "vector": vector, "path": str(path), "reason": str(exc)})
            continue
        x = parsed["x"]
        y = parsed["y"]
        finite_y = all(
            math.isfinite(float(item)) if not isinstance(item, dict) else math.isfinite(float(item["real"])) and math.isfinite(float(item["imag"]))
            for item in y
        )
        record = {
            "run_id": run_id,
            "candidate_hash": candidate_hash,
            "source_hash": source_hash,
            "group": group,
            "analysis": analysis,
            "vector": vector,
            "path": str(path),
            "samples": len(y),
            "x_name": parsed["x_name"],
            "x_min": min(x) if x else None,
            "x_max": max(x) if x else None,
            "finite": bool(x and all(math.isfinite(float(item)) for item in x) and finite_y),
            # SIMPLIS event vectors intentionally repeat the transition time
            # to encode the value immediately before and after a discontinuity.
            "monotonic": all(right >= left for left, right in zip(x, x[1:])),
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
