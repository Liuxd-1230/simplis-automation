"""Fail-closed records for GUI screenshots inspected by an agent or reviewer."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .errors import ValidationError
from .io import sha256_file, write_json


VISUAL_CHECKS = (
    "readable_text",
    "symbol_spacing",
    "label_spacing",
    "terminal_orientation",
    "series_orientation",
    "ground_orientation",
    "functional_bands",
    "analysis_gutter",
)
CHECK_STATUSES = {"pass", "fail", "not_applicable"}


def parse_visual_checks(values: Iterable[str]) -> dict[str, str]:
    checks: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValidationError("Visual check must use NAME=STATUS", value=value)
        name, status = (item.strip() for item in value.split("=", 1))
        if name not in VISUAL_CHECKS:
            raise ValidationError("Unknown visual check", check=name, allowed=list(VISUAL_CHECKS))
        if status not in CHECK_STATUSES:
            raise ValidationError("Unknown visual check status", check=name, status=status, allowed=sorted(CHECK_STATUSES))
        if name in checks:
            raise ValidationError("Visual check is duplicated", check=name)
        checks[name] = status
    missing = [name for name in VISUAL_CHECKS if name not in checks]
    if missing:
        raise ValidationError("Visual evidence is missing required checks", missing=missing)
    return checks


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValidationError("Cannot read build manifest", path=str(path), reason=str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise ValidationError("Build manifest is not valid JSON", path=str(path), reason=str(exc)) from exc
    if not isinstance(data, dict):
        raise ValidationError("Build manifest root must be an object", path=str(path))
    return data


def record_visual_evidence(
    manifest_path: str | Path,
    output_path: str | Path,
    *,
    capture_method: str,
    capture_id: str | None,
    image_path: str | Path | None,
    window_title: str,
    active_document: str,
    width: int,
    height: int,
    role: str,
    clean_reopen_token: str,
    responsive: bool,
    modal_free: bool,
    canvas_nonblank: bool,
    verdict: str,
    checks: Mapping[str, str],
    findings: Iterable[str] = (),
) -> dict[str, Any]:
    manifest = Path(manifest_path).resolve()
    data = _load_manifest(manifest)
    artifacts = data.get("artifacts", {})
    if not isinstance(artifacts, Mapping) or not artifacts.get("schematic"):
        raise ValidationError("Build manifest does not identify a schematic", path=str(manifest))
    schematic = Path(str(artifacts["schematic"])).resolve()
    if not schematic.is_file():
        raise ValidationError("Visual-evidence schematic is missing", path=str(schematic))
    if schematic.name.casefold() not in active_document.casefold():
        raise ValidationError(
            "Active GUI document does not identify the manifest schematic",
            expected=schematic.name,
            active_document=active_document,
        )
    normalized_method = capture_method.strip().casefold()
    if normalized_method not in {"computer_use", "native_file"}:
        raise ValidationError("Unsupported screenshot capture method", capture_method=capture_method)
    resolved_image: Path | None = None
    if normalized_method == "computer_use":
        if not capture_id:
            raise ValidationError("Computer Use evidence requires a capture id")
    else:
        if image_path is None:
            raise ValidationError("Native-file evidence requires an image path")
        resolved_image = Path(image_path).resolve()
        if not resolved_image.is_file():
            raise ValidationError("Screenshot file is missing", path=str(resolved_image))
    if width <= 0 or height <= 0:
        raise ValidationError("Screenshot dimensions must be positive", width=width, height=height)
    if role not in {"whole_sheet", "detail"}:
        raise ValidationError("Visual evidence role must be whole_sheet or detail", role=role)
    normalized_verdict = verdict.strip().casefold()
    if normalized_verdict not in {"pass", "fail"}:
        raise ValidationError("Visual verdict must be pass or fail", verdict=verdict)
    normalized_checks = parse_visual_checks(f"{name}={status}" for name, status in checks.items())
    gate_failures = [
        name
        for name, passed in (
            ("clean_reopen_token", bool(clean_reopen_token.strip())),
            ("responsive", responsive),
            ("modal_free", modal_free),
            ("canvas_nonblank", canvas_nonblank),
        )
        if not passed
    ]
    gate_failures.extend(name for name, status in normalized_checks.items() if status == "fail")
    if normalized_verdict == "pass" and gate_failures:
        raise ValidationError("Passing visual evidence has failed prerequisites", failures=gate_failures)

    netlist_value = artifacts.get("netlist")
    netlist = Path(str(netlist_value)).resolve() if netlist_value else None
    record = {
        "schema_version": "simplis-automation/v2/visual-evidence",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(manifest),
        "schematic": {"path": str(schematic), "sha256": sha256_file(schematic)},
        "netlist": {
            "path": str(netlist) if netlist else None,
            "sha256": sha256_file(netlist) if netlist and netlist.is_file() else None,
        },
        "clean_reopen_token": clean_reopen_token,
        "capture": {
            "method": normalized_method,
            "id": capture_id,
            "role": role,
            "window_title": window_title,
            "active_document": active_document,
            "width": width,
            "height": height,
            "image_path": str(resolved_image) if resolved_image else None,
            "image_sha256": sha256_file(resolved_image) if resolved_image else None,
            "persistent_image": resolved_image is not None,
        },
        "preconditions": {
            "responsive": bool(responsive),
            "modal_free": bool(modal_free),
            "canvas_nonblank": bool(canvas_nonblank),
        },
        "checks": normalized_checks,
        "verdict": normalized_verdict,
        "findings": [str(item) for item in findings],
        "scoring_eligible": False,
        "limitations": [
            "Visual evidence does not prove pin attachment or net isolation.",
            "Visual evidence does not prove simulation completion or electrical correctness.",
        ],
    }
    write_json(output_path, record)
    return record
