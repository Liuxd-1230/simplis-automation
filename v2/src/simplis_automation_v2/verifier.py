"""Fail-closed, netlist-level verification for v2 build manifests.

Verification intentionally has a narrower job than simulation: it proves that
the emitted SIMetrix script can create an editable schematic and that SIMetrix
can emit a non-empty SIMPLIS netlist.  A successful compile alone is therefore
never reported as a verified circuit.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from .catalog import load_catalog
from .io import load_yaml, sha256_file, write_json
from .runtime import resolve_runtime


MANIFEST_SCHEMA = "simplis-automation/v2/build-manifest"
_ERROR_PATTERN = re.compile(
    r"(?im)(?:^\s*(?:\*\*\*\s*)?(?:fatal|error|exception)\b|\berror\s+message\s+id\s*:|\bsimetrix\b[^\n]{0,80}\berror\b)"
)
_GROUND_NAMES = {"0", "gnd", "ground", "rtn"}


Runner = Callable[..., Any]


def _wait_for_nonempty(paths: tuple[Path, ...], timeout: float = 30.0) -> bool:
    """Wait for artifacts written by SIMetrix's single-instance command server.

    On SIMetrix 8.3 the process started with ``/s`` can return after handing the
    script to the GUI process, several seconds before the requested files are
    flushed.  Process exit is therefore not an artifact-completion signal.
    """

    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if all(_is_nonempty(path) for path in paths):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def _diagnostic(code: str, message: str, **details: Any) -> dict[str, Any]:
    record: dict[str, Any] = {"code": code, "message": message}
    if details:
        record["details"] = details
    return record


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raw = load_yaml(path)
    except OSError as exc:
        raise ValueError(f"Cannot read manifest: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("Manifest root must be an object")
    return raw


def _resolve_path(value: Any, *, base: Path, fallback_base: Path | None = None) -> Path | None:
    if value is None or not str(value).strip():
        return None
    path = Path(str(value)).expanduser()
    if path.is_absolute():
        return path
    preferred = base / path
    if fallback_base is not None and not preferred.exists() and (fallback_base / path).exists():
        return fallback_base / path
    return preferred


def _artifact_value(artifacts: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in artifacts and artifacts[name] is not None:
            return artifacts[name]
    return None


def _quote_sxscr(value: Path) -> str:
    text = str(value.resolve())
    if any(char in text for char in ('\n', '\r', '"')):
        raise ValueError("SIMetrix artifact path contains unsupported script characters")
    return f'"{text}"'


def _quote_simetrix_string(value: Path) -> str:
    text = str(value.resolve())
    if any(char in text for char in ("\n", "\r", "'")):
        raise ValueError("SIMetrix string path contains unsupported script characters")
    return f"'{text}'"


def _parse_status(path: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    if not path.is_file():
        return parsed
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lower()
        if key:
            parsed[key] = value.strip()
    return parsed


def _status_true(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "passed", "ok"}


def _is_nonempty(path: Path | None) -> bool:
    return bool(path and path.is_file() and path.stat().st_size > 0)


def _normalize_runner_result(raw: Any) -> dict[str, Any]:
    if isinstance(raw, int):
        return {"returncode": raw, "stdout": "", "stderr": "", "timed_out": False}
    if isinstance(raw, Mapping):
        return {
            "returncode": int(raw.get("returncode", raw.get("code", 0))),
            "stdout": str(raw.get("stdout", "") or ""),
            "stderr": str(raw.get("stderr", "") or ""),
            "timed_out": bool(raw.get("timed_out", False)),
        }
    return {
        "returncode": int(getattr(raw, "returncode", 0)),
        "stdout": str(getattr(raw, "stdout", "") or ""),
        "stderr": str(getattr(raw, "stderr", "") or ""),
        "timed_out": False,
    }


def _invoke_runner(
    executable: Path,
    script: Path,
    *,
    timeout: float | None,
    interactive: bool,
    stage: str,
    runner: Runner | None,
) -> dict[str, Any]:
    command = [str(executable)]
    if interactive:
        command.append("/i")
    command.extend(["/s", str(script)])
    try:
        if runner is None:
            completed = subprocess.run(
                command,
                cwd=str(script.parent),
                check=False,
                timeout=timeout,
                text=True,
                capture_output=not interactive,
            )
            result = _normalize_runner_result(completed)
        else:
            try:
                completed = runner(
                    command,
                    cwd=script.parent,
                    timeout=timeout,
                    interactive=interactive,
                    stage=stage,
                )
            except TypeError:
                # A small positional runner is convenient in external tests and
                # in downstream integrations.  It still receives only a
                # SIMetrix command that points at an emitted .sxscr file.
                completed = runner(command, script.parent, timeout)
            result = _normalize_runner_result(completed)
    except subprocess.TimeoutExpired as exc:
        result = {
            "returncode": 124,
            "stdout": str(exc.stdout or ""),
            "stderr": str(exc.stderr or ""),
            "timed_out": True,
        }
    except Exception as exc:  # pragma: no cover - defensive integration boundary
        result = {"returncode": 125, "stdout": "", "stderr": repr(exc), "timed_out": False}
    result["command"] = command
    result["script"] = str(script)
    return result


def _write_runner_logs(directory: Path, stage: str, result: Mapping[str, Any]) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    stdout_path = directory / f"{stage}.stdout.log"
    stderr_path = directory / f"{stage}.stderr.log"
    stdout_path.write_text(str(result.get("stdout", "")), encoding="utf-8")
    stderr_path.write_text(str(result.get("stderr", "")), encoding="utf-8")
    return {"stdout": str(stdout_path), "stderr": str(stderr_path)}


def _error_text_messages(text: str, source: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for match in _ERROR_PATTERN.finditer(text):
        line_end = text.find("\n", match.start())
        line = text[match.start() : line_end if line_end >= 0 else None].strip()
        messages.append(_diagnostic("simetrix_reported_error", "SIMetrix reported an error", source=source, excerpt=line[:500]))
    return messages


def _recent_error_files(root: Path, started_at: float) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    findings: list[dict[str, Any]] = []
    patterns = ("*.err", "*.error", "*.deck.err")
    seen: set[Path] = set()
    for pattern in patterns:
        for path in root.rglob(pattern):
            if path in seen or not path.is_file() or path.stat().st_size == 0:
                continue
            seen.add(path)
            # A stale error from a previous verification must not poison a new
            # manifest, but errors produced during this launch are terminal.
            if path.stat().st_mtime < started_at - 1.0:
                continue
            excerpt = path.read_text(encoding="utf-8", errors="replace")[:500]
            findings.append(
                _diagnostic(
                    "simetrix_error_file",
                    "SIMetrix produced a non-empty error file",
                    path=str(path),
                    excerpt=" ".join(excerpt.split()),
                )
            )
    return findings


def _manifest_catalog_status(manifest: Mapping[str, Any], manifest_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return catalog evidence plus terminal errors and non-terminal warnings."""

    raw = manifest.get("catalog")
    if not isinstance(raw, Mapping):
        return ({"status": "not_provided", "verified": False}, [], [_diagnostic("catalog_not_provided", "Manifest has no catalog fingerprint; ideality cannot be elevated")])
    path = _resolve_path(raw.get("path"), base=manifest_dir)
    expected = raw.get("fingerprint") or raw.get("sha256")
    record: dict[str, Any] = {
        "path": str(path) if path else None,
        "expected_fingerprint": str(expected) if expected else None,
        "actual_fingerprint": None,
        "verified": False,
        "status": "invalid",
    }
    if path is None:
        return record, [_diagnostic("catalog_path_missing", "Manifest catalog does not include a path")], []
    if not path.is_file():
        return record, [_diagnostic("catalog_missing", "Catalog lock does not exist", path=str(path))], []
    if not expected:
        return record, [_diagnostic("catalog_fingerprint_missing", "Manifest catalog does not include a fingerprint", path=str(path))], []
    try:
        loaded_catalog = load_catalog(path)
    except Exception as exc:
        return record, [_diagnostic("catalog_invalid", "Catalog lock cannot be validated", path=str(path), reason=str(exc))], []
    actual = str(loaded_catalog["fingerprint"])
    record["actual_fingerprint"] = actual
    record["file_sha256"] = sha256_file(path)
    if actual != str(expected):
        return record, [
            _diagnostic(
                "catalog_fingerprint_mismatch",
                "Catalog lock changed after compilation",
                path=str(path),
                expected=str(expected),
                actual=actual,
            )
        ], []
    record.update({"verified": True, "status": "matched"})
    return record, [], []


def _validate_expected_netlist(netlist: Path, expected: Any) -> list[dict[str, Any]]:
    if not isinstance(expected, Mapping):
        return []
    text = netlist.read_text(encoding="utf-8", errors="replace")
    errors: list[dict[str, Any]] = []
    components = expected.get("components", [])
    if isinstance(components, list):
        for component in components:
            if not isinstance(component, Mapping):
                continue
            ref = component.get("ref") or component.get("id")
            if ref and not re.search(rf"(?i)(?<![A-Za-z0-9_]){re.escape(str(ref))}(?![A-Za-z0-9_])", text):
                errors.append(
                    _diagnostic(
                        "netlist_component_missing",
                        "Expected component is absent from generated netlist",
                        ref=str(ref),
                    )
                )
    nets = expected.get("nets", {})
    if isinstance(nets, Mapping):
        for name in sorted(str(key) for key in nets):
            if name.lower() in _GROUND_NAMES:
                continue
            if not re.search(rf"(?i)(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text):
                errors.append(
                    _diagnostic(
                        "netlist_net_missing",
                        "Expected net is absent from generated netlist",
                        net=name,
                    )
                )
    return errors


def _classification(manifest: Mapping[str, Any], catalog_verified: bool) -> str:
    raw = manifest.get("classification")
    classification = raw if isinstance(raw, Mapping) else {}
    opaque = classification.get("opaque_modules", [])
    if isinstance(opaque, (str, bytes)):
        opaque = [opaque]
    if opaque:
        return "boundary_verified"
    if classification.get("fully_ideal") is True and catalog_verified:
        return "fully_ideal"
    return "netlisted"


def _artifact_hashes(paths: Mapping[str, Path | None]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for name, path in paths.items():
        if _is_nonempty(path):
            hashes[name] = sha256_file(path)  # type: ignore[arg-type]
    return hashes


def _write_reports(status_path: Path, evidence_path: Path, result: dict[str, Any]) -> None:
    try:
        write_json(status_path, result)
        write_json(
            evidence_path,
            {
                "schema_version": 1,
                "manifest_path": result.get("manifest_path"),
                "status": result.get("status"),
                "classification": result.get("classification"),
                "artifacts": result.get("artifacts"),
                "catalog": result.get("catalog"),
                "stage_evidence": result.get("stages"),
                "artifact_sha256": result.get("artifact_sha256"),
            },
        )
    except OSError:
        # The primary diagnostic is still returned to the caller if the output
        # directory itself is unwritable.
        pass


def verify_manifest(
    manifest_path: str | Path,
    runtime: Mapping[str, Any] | str | Path | None = None,
    timeout: float | None = 90.0,
    interactive: bool = False,
    *,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Create a schematic and require a real SIMPLIS netlist for one manifest.

    ``runner`` is an optional test/integration seam.  It receives a command
    whose final argument is always an emitted ``.sxscr`` script; production
    callers omit it and use :func:`subprocess.run` with the discovered runtime.
    """

    source = Path(manifest_path).expanduser().resolve()
    manifest_dir = source.parent
    status_path = manifest_dir / "verification-status.json"
    evidence_path = manifest_dir / "verification-evidence.json"
    verification_dir = manifest_dir / "verification"
    result: dict[str, Any] = {
        "schema_version": 1,
        "ok": False,
        "status": "failed",
        "classification": "static_valid",
        "manifest_path": str(source),
        "status_path": str(status_path),
        "evidence_path": str(evidence_path),
        "artifacts": {},
        "stages": {},
        "catalog": {"status": "not_checked", "verified": False},
        "errors": [],
        "warnings": [],
        "artifact_sha256": {},
    }
    if not source.is_file():
        result["errors"].append(_diagnostic("manifest_missing", "Build manifest does not exist", path=str(source)))
        _write_reports(status_path, evidence_path, result)
        return result

    try:
        manifest = _read_manifest(source)
    except Exception as exc:
        result["errors"].append(_diagnostic("manifest_invalid", "Cannot parse build manifest", reason=str(exc)))
        _write_reports(status_path, evidence_path, result)
        return result

    if manifest.get("schema_version") not in {None, MANIFEST_SCHEMA}:
        result["errors"].append(
            _diagnostic(
                "manifest_schema_unsupported",
                "Unsupported build manifest schema",
                schema_version=manifest.get("schema_version"),
            )
        )
    artifacts_raw = manifest.get("artifacts")
    if not isinstance(artifacts_raw, Mapping):
        artifacts_raw = {}
        result["errors"].append(_diagnostic("manifest_artifacts_missing", "Manifest does not define artifacts"))
    build_dir = _resolve_path(manifest.get("build_dir"), base=manifest_dir) or manifest_dir
    script = _resolve_path(
        _artifact_value(artifacts_raw, "script", "sxscr", "script_path"),
        base=build_dir,
        fallback_base=manifest_dir,
    )
    schematic = _resolve_path(
        _artifact_value(artifacts_raw, "schematic", "schematic_path", "sxsch"),
        base=build_dir,
        fallback_base=manifest_dir,
    )
    netlist = _resolve_path(
        _artifact_value(artifacts_raw, "netlist", "netlist_path", "net"),
        base=build_dir,
        fallback_base=manifest_dir,
    )
    result["artifacts"] = {
        "script": str(script) if script else None,
        "schematic": str(schematic) if schematic else None,
        "netlist": str(netlist) if netlist else None,
    }
    for name, path, suffix in (("script", script, ".sxscr"), ("schematic", schematic, ".sxsch"), ("netlist", netlist, ".net")):
        if path is None:
            result["errors"].append(_diagnostic("artifact_missing", "Manifest does not declare required artifact", artifact=name))
        elif path.suffix.lower() != suffix:
            result["errors"].append(
                _diagnostic("artifact_suffix_invalid", "Artifact has an unexpected file suffix", artifact=name, path=str(path), expected=suffix)
            )
    if script is not None and not _is_nonempty(script):
        result["errors"].append(_diagnostic("script_missing", "Emitted SIMetrix script is absent or empty", path=str(script)))
    expected_script_hash = manifest.get("script_sha256")
    if expected_script_hash and script is not None and script.is_file():
        actual_script_hash = sha256_file(script)
        if actual_script_hash != str(expected_script_hash):
            result["errors"].append(
                _diagnostic(
                    "script_fingerprint_mismatch",
                    "Emitted SIMetrix script changed after compilation",
                    path=str(script),
                    expected=str(expected_script_hash),
                    actual=actual_script_hash,
                )
            )

    classification_raw = manifest.get("classification")
    if isinstance(classification_raw, Mapping) and classification_raw.get("static_valid") is False:
        result["errors"].append(_diagnostic("manifest_not_static_valid", "Compiler marked this manifest as statically invalid"))
    elif not isinstance(classification_raw, Mapping) or classification_raw.get("static_valid") is not True:
        result["warnings"].append(_diagnostic("static_valid_not_asserted", "Manifest does not explicitly assert static validity"))

    catalog, catalog_errors, catalog_warnings = _manifest_catalog_status(manifest, manifest_dir)
    result["catalog"] = catalog
    result["errors"].extend(catalog_errors)
    result["warnings"].extend(catalog_warnings)

    supplied_runner = runner
    if supplied_runner is None and isinstance(runtime, Mapping):
        candidate = runtime.get("runner") or runtime.get("_runner")
        if callable(candidate):
            supplied_runner = candidate
    runtime_status = resolve_runtime(runtime)
    result["runtime"] = runtime_status
    if not runtime_status.get("ok"):
        result["errors"].append(_diagnostic("runtime_invalid", "SIMetrix runtime preflight failed", diagnostics=runtime_status.get("errors", [])))
    executable = Path(str(runtime_status["executable"])) if runtime_status.get("executable") else None

    if result["errors"]:
        _write_reports(status_path, evidence_path, result)
        return result
    assert script is not None and schematic is not None and netlist is not None and executable is not None

    verification_dir.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    create_result = _invoke_runner(
        executable,
        script,
        timeout=timeout,
        interactive=interactive,
        stage="create_schematic",
        runner=supplied_runner,
    )
    if supplied_runner is None and not create_result.get("timed_out"):
        _wait_for_nonempty((schematic,), timeout=min(30.0, float(timeout or 30.0)))
    create_logs = _write_runner_logs(verification_dir, "create_schematic", create_result)
    create_errors = _error_text_messages(str(create_result.get("stdout", "")), create_logs["stdout"])
    create_errors.extend(_error_text_messages(str(create_result.get("stderr", "")), create_logs["stderr"]))
    if create_result.get("timed_out"):
        create_errors.append(_diagnostic("create_timeout", "SIMetrix timed out while creating the schematic", timeout=timeout))
    if create_result.get("returncode") != 0:
        create_errors.append(
            _diagnostic("create_process_failed", "SIMetrix failed while creating the schematic", returncode=create_result.get("returncode"))
        )
    if not _is_nonempty(schematic):
        create_errors.append(_diagnostic("schematic_missing", "SIMetrix did not create a non-empty editable schematic", path=str(schematic)))
    create_errors.extend(_recent_error_files(build_dir, started_at))
    result["stages"]["create_schematic"] = {
        "status": "passed" if not create_errors else "failed",
        "process": create_result,
        "logs": create_logs,
    }
    result["errors"].extend(create_errors)
    if result["errors"]:
        result["artifact_sha256"] = _artifact_hashes({"script": script, "schematic": schematic, "netlist": netlist})
        _write_reports(status_path, evidence_path, result)
        return result

    netlist_status = verification_dir / "netlist-status.txt"
    netlist_script = verification_dir / "verify-netlist.sxscr"
    try:
        netlist_script.write_text(
            "\n".join(
                (
                    f"OpenSchem {_quote_sxscr(schematic)}",
                    f"Netlist /simplis {_quote_sxscr(netlist)}",
                    f"Let v2_echo = OpenEchoFile({_quote_simetrix_string(netlist_status)}, 'w')",
                    "Echo completion_token=true",
                    "Echo netlist_completed=true",
                    "Let v2_close = CloseEchoFile()",
                    "Quit",
                    "",
                )
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        result["errors"].append(_diagnostic("verification_script_unwritable", "Cannot write emitted netlist verification script", reason=str(exc)))
        _write_reports(status_path, evidence_path, result)
        return result

    netlist_result = _invoke_runner(
        executable,
        netlist_script,
        timeout=timeout,
        interactive=interactive,
        stage="netlist",
        runner=supplied_runner,
    )
    if supplied_runner is None and not netlist_result.get("timed_out"):
        _wait_for_nonempty((netlist, netlist_status), timeout=min(30.0, float(timeout or 30.0)))
    netlist_logs = _write_runner_logs(verification_dir, "netlist", netlist_result)
    netlist_errors = _error_text_messages(str(netlist_result.get("stdout", "")), netlist_logs["stdout"])
    netlist_errors.extend(_error_text_messages(str(netlist_result.get("stderr", "")), netlist_logs["stderr"]))
    if netlist_result.get("timed_out"):
        netlist_errors.append(_diagnostic("netlist_timeout", "SIMetrix timed out while producing the netlist", timeout=timeout))
    if netlist_result.get("returncode") != 0:
        netlist_errors.append(
            _diagnostic("netlist_process_failed", "SIMetrix failed while producing the netlist", returncode=netlist_result.get("returncode"))
        )
    if not _is_nonempty(netlist):
        netlist_errors.append(_diagnostic("netlist_missing", "SIMetrix did not create a non-empty SIMPLIS netlist", path=str(netlist)))
    status_values = _parse_status(netlist_status)
    if not _status_true(status_values.get("completion_token")):
        netlist_errors.append(_diagnostic("netlist_completion_missing", "Netlist script did not write its completion token", path=str(netlist_status)))
    if not _status_true(status_values.get("netlist_completed")):
        netlist_errors.append(_diagnostic("netlist_status_missing", "Netlist script did not confirm completion", path=str(netlist_status)))
    netlist_errors.extend(_recent_error_files(build_dir, started_at))
    if _is_nonempty(netlist):
        netlist_text = netlist.read_text(encoding="utf-8", errors="replace")
        netlist_errors.extend(_error_text_messages(netlist_text, str(netlist)))
        netlist_errors.extend(_validate_expected_netlist(netlist, manifest.get("expected")))
    result["stages"]["netlist"] = {
        "status": "passed" if not netlist_errors else "failed",
        "process": netlist_result,
        "logs": netlist_logs,
        "script": str(netlist_script),
        "status_file": str(netlist_status),
        "status_values": status_values,
    }
    result["errors"].extend(netlist_errors)
    artifacts = {
        "script": script,
        "schematic": schematic,
        "netlist": netlist,
        "netlist_script": netlist_script,
        "netlist_status": netlist_status,
    }
    result["artifact_sha256"] = _artifact_hashes(artifacts)
    if not result["errors"]:
        result["ok"] = True
        result["status"] = "passed"
        result["classification"] = _classification(manifest, bool(catalog.get("verified")))
    _write_reports(status_path, evidence_path, result)
    return result
