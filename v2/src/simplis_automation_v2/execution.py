"""Fail-closed SIMetrix execution evidence and watchdog primitives.

The production runner and unit-test doubles share these pure functions.  A
SIMPLIS process exit, an existing vector file, or a non-empty schematic is not
treated as completion without the ordered stage tokens and simulator evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


JOB_STAGES = (
    "launched",
    "script_started",
    "simulation_started",
    "running",
    "simulation_returned",
    "errors_collected",
    "vectors_exported",
    "validated",
    "complete",
)

FAILED_SIMULATOR_STATES = {"convergencefail", "simerrors", "netlisterrors"}
SUCCESS_SIMULATOR_STATES = {"complete", "warnings"}
COMPLETION_CAPABILITIES = {"explicit_status", "calibrated_none_after_return", "unavailable"}
NONE_AFTER_RETURN_REQUIREMENTS = (
    "watchdog_progress_observed",
    "stage_tokens_complete",
    "simulation_info_matches",
    "vectors_fresh_complete",
)


def _diagnostic(code: str, message: str, **details: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"code": code, "message": message}
    if details:
        item["details"] = details
    return item


def _truthy(value: Any) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes", "ok", "passed"}


def _as_messages(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text or text.casefold() in {"empty", "none", "[]", "<none>"}:
            return []
        return [line.strip() for line in text.splitlines() if line.strip()]
    if isinstance(value, Iterable):
        return [
            str(item).strip()
            for item in value
            if str(item).strip() and str(item).strip().casefold() not in {"empty", "none", "[]", "<none>"}
        ]
    return [str(value)]


@dataclass
class JobStateMachine:
    job_id: str
    events: list[dict[str, Any]] = field(default_factory=list)

    def advance(self, stage: str, *, at: float, evidence: Mapping[str, Any] | None = None) -> None:
        if stage not in JOB_STAGES:
            raise ValueError(f"Unknown job stage: {stage}")
        expected_index = len(self.events)
        if expected_index >= len(JOB_STAGES) or JOB_STAGES[expected_index] != stage:
            expected = JOB_STAGES[expected_index] if expected_index < len(JOB_STAGES) else None
            raise ValueError(f"Out-of-order stage {stage!r}; expected {expected!r}")
        event: dict[str, Any] = {"job_id": self.job_id, "stage": stage, "at": float(at)}
        if evidence:
            event["evidence"] = dict(evidence)
        self.events.append(event)

    @property
    def complete(self) -> bool:
        return len(self.events) == len(JOB_STAGES) and self.events[-1]["stage"] == "complete"

    def as_dict(self) -> dict[str, Any]:
        return {"job_id": self.job_id, "complete": self.complete, "events": list(self.events)}


def assess_simulation_status(
    status_values: Mapping[str, Any],
    *,
    warning_allowlist: Iterable[str],
    recent_error_files: Iterable[str],
    completion_capability: str = "unavailable",
    completion_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Decide whether vectors from a run are eligible for scoring."""

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not _truthy(status_values.get("completion_token")):
        errors.append(_diagnostic("completion_token_missing", "Simulation did not write its completion token"))
    try:
        exit_code = int(str(status_values.get("simplis_exit_code", "-1")).strip())
    except ValueError:
        exit_code = -1
    if exit_code != 0:
        errors.append(_diagnostic("simplis_exit_nonzero", "GetSIMPLISExitCode reported failure", exit_code=exit_code))
    if _truthy(status_values.get("simulation_has_errors")):
        errors.append(_diagnostic("simulation_reported_errors", "SimulationHasErrors reported a failure"))

    simulator_status = str(status_values.get("simulator_status", "None")).strip()
    normalized_status = simulator_status.casefold()
    capability = str(completion_capability).strip().casefold()
    if capability not in COMPLETION_CAPABILITIES:
        errors.append(_diagnostic("completion_capability_invalid", "Runtime completion capability is not recognized", capability=completion_capability))
    if normalized_status in FAILED_SIMULATOR_STATES:
        errors.append(_diagnostic("simulator_status_failed", "GetSimulatorStatus reported failure", status=simulator_status))
    elif normalized_status == "none" and capability == "calibrated_none_after_return":
        evidence = dict(completion_evidence or {})
        missing_evidence = [name for name in NONE_AFTER_RETURN_REQUIREMENTS if not _truthy(evidence.get(name))]
        if missing_evidence:
            errors.append(
                _diagnostic(
                    "none_after_return_evidence_incomplete",
                    "None-after-return lacks the independent evidence required for trusted completion",
                    missing=missing_evidence,
                )
            )
        else:
            warnings.append(
                _diagnostic(
                    "calibrated_none_after_return",
                    "Runtime calibration permits None only after complete watchdog, provenance and vector evidence",
                )
            )
    elif normalized_status not in SUCCESS_SIMULATOR_STATES:
        errors.append(_diagnostic("simulator_status_incomplete", "Simulator status is not a completed state", status=simulator_status))

    simulation_errors = _as_messages(status_values.get("simulation_errors"))
    if simulation_errors and not any(item["code"] == "simulation_reported_errors" for item in errors):
        errors.append(_diagnostic("simulation_reported_errors", "GetSimulationErrors returned messages", messages=simulation_errors))

    error_files = [str(item) for item in recent_error_files]
    if error_files:
        errors.append(_diagnostic("simulator_error_files", "SIMPLIS produced error artifacts", files=error_files))

    allowlist = {str(item).strip() for item in warning_allowlist}
    reported_warnings = _as_messages(status_values.get("simulation_warnings"))
    unknown_warnings = [item for item in reported_warnings if item not in allowlist]
    if normalized_status == "warnings" and not reported_warnings:
        unknown_warnings.append("<warnings status without captured warning text>")
    if unknown_warnings:
        errors.append(_diagnostic("unapproved_simulation_warning", "Simulation produced warning(s) that are not allowlisted", warnings=unknown_warnings))
    elif reported_warnings:
        warnings.append(_diagnostic("allowlisted_simulation_warning", "Simulation produced approved benign warning(s)", warnings=reported_warnings))

    if not _truthy(status_values.get("vector_export_done")):
        errors.append(_diagnostic("vector_export_incomplete", "Vector export did not complete"))
    trusted = not errors
    return {
        "trusted": trusted,
        "classification": "trusted" if trusted else "diagnostic_only",
        "errors": errors,
        "warnings": warnings,
        "simulator_status": simulator_status,
        "completion_capability": capability,
        "completion_evidence": dict(completion_evidence or {}),
        "simulation_errors": simulation_errors,
    }


def validate_group_evidence(
    status_values: Mapping[str, Any],
    contracts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Reject stale/wrong analysis groups before any Show file is trusted."""

    errors: list[dict[str, Any]] = []
    normalized = {str(key).casefold(): value for key, value in status_values.items()}
    for group, contract in contracts.items():
        prefix = f"group.{group}.".casefold()
        vectors_text = str(normalized.get(prefix + "vectors", ""))
        analysis_text = str(normalized.get(prefix + "analysis", ""))
        expected_analysis = str(contract.get("analysis", ""))
        if not analysis_text:
            errors.append(_diagnostic("group_analysis_missing", "Data group analysis metadata was not captured", group=group))
        elif expected_analysis and expected_analysis.casefold() not in analysis_text.casefold():
            errors.append(_diagnostic("group_analysis_mismatch", "Data group has the wrong analysis type", group=group, expected=expected_analysis, actual=analysis_text))
        for vector in contract.get("vectors", []):
            if str(vector).casefold() not in vectors_text.casefold():
                errors.append(_diagnostic("group_vector_missing", "Required vector is absent from the current data group", group=group, vector=vector))
    return {"valid": not errors, "errors": errors}


def classify_watchdog(
    *,
    started_at: float,
    now: float,
    last_progress_at: float,
    process_alive: bool,
    simulator_status: str,
    dialog: Mapping[str, Any] | None,
    stall_timeout: float,
    hard_timeout: float,
) -> dict[str, Any]:
    elapsed = max(0.0, float(now) - float(started_at))
    idle = max(0.0, float(now) - float(last_progress_at))
    normalized = str(simulator_status).strip().casefold()
    dialog_text = " ".join(str(dialog.get(key, "")) for key in ("title", "text") if dialog) if dialog else ""
    dialog_title = str(dialog.get("title", "")).strip().casefold() if dialog else ""
    modal_error = bool(
        dialog
        and any(
            token in dialog_text.casefold()
            for token in ("error", "fatal", "convergence fail", "failed to converge", "netlist failed", "simulation failed")
        )
    )
    modal_dialog = bool(
        dialog
        and (
            _truthy(dialog.get("is_modal"))
            or dialog_title in {"save schematic", "save schematic?"}
        )
    )
    if not process_alive:
        classification = "process_dead"
    elif modal_error:
        classification = "modal_error_blocked"
    elif modal_dialog:
        classification = "modal_dialog_blocked"
    elif normalized in FAILED_SIMULATOR_STATES:
        classification = "simulation_error_blocked"
    elif elapsed >= hard_timeout:
        classification = "hard_timeout"
    elif normalized == "inprogress" and idle >= stall_timeout:
        classification = "stalled_in_progress"
    elif normalized in {"unavailable", "none", ""} and idle >= stall_timeout:
        classification = "stalled_unresponsive"
    else:
        classification = "running"
    return {"classification": classification, "elapsed": elapsed, "idle": idle, "terminal": classification != "running"}


def _quote_path(path: Path) -> str:
    text = str(Path(path).resolve())
    if any(char in text for char in ('"', "\n", "\r")):
        raise ValueError("SIMetrix path contains unsupported characters")
    return f'"{text}"'


def _quote_string(path: Path) -> str:
    text = str(Path(path).resolve())
    if any(char in text for char in ("'", "\n", "\r")):
        raise ValueError("SIMetrix string contains unsupported characters")
    return f"'{text}'"


_PLAIN_VECTOR = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _vector_expr(name: str) -> str:
    if _PLAIN_VECTOR.fullmatch(name):
        return name
    if any(character in name for character in ("'", "\r", "\n")):
        raise ValueError("Vector name contains unsupported characters")
    return f"Vec('{name}')"


def _vector_slug(name: str) -> str:
    label = name[1:] if name.startswith("#") else name
    label = re.sub(r"[^A-Za-z0-9_]+", "_", label).strip("_").lower() or "vector"
    return f"n{label}" if label[0].isdigit() else label


def build_simulation_script(
    *,
    schematic: Path,
    status_file: Path,
    message_log: Path,
    required_vectors: Mapping[str, Iterable[str]],
    vector_dir: Path | None = None,
) -> str:
    """Build a run script that writes provenance/status before exporting data."""

    vector_root = Path(vector_dir or status_file.parent / "vectors").resolve()
    lines = [
        "Set EchoOn",
        "Set precision = 16",
        "Let v2_startup_settle = Sleep(3)",
        f"RedirectMessages dup {_quote_path(message_log)}",
        f"Let v2_status = OpenEchoFile({_quote_string(status_file)}, 'w')",
        "Echo script_started=true",
        "Echo simulation_started=true",
        "Echo running=true",
        "Let v2_close = CloseEchoFile()",
        f"OpenSchem /cd /readonly {_quote_path(schematic)}",
        "simplis_run",
        "Let v2_exit = GetSIMPLISExitCode()",
        "Let v2_has_errors = SimulationHasErrors()",
        "Let v2_errors = GetSimulationErrors()",
        "Let v2_sim_status = GetSimulatorStatus()",
        "Let v2_sim_info = GetSimulationInfo()",
        "Let v2_sim_info_count = Length(v2_sim_info)",
        "Let v2_groups = Groups()",
        "Let v2_errors_safe = JoinStringArray(['<none>'], v2_errors)",
        "Let v2_sim_info_safe = JoinStringArray(['<none>'], v2_sim_info)",
        "Let v2_groups_safe = JoinStringArray(['<none>'], v2_groups)",
        f"Let v2_status = OpenEchoFile({_quote_string(status_file)}, 'a')",
        "Echo simulation_returned=true",
        "Echo simplis_exit_code={v2_exit}",
        "Echo simulation_has_errors={v2_has_errors}",
        "Echo simulator_status={v2_sim_status}",
        "Echo simulation_errors={v2_errors_safe}",
        "Echo simulation_info={v2_sim_info_safe}",
        "Echo simulation_info.count={v2_sim_info_count}",
        "Echo simulation_info.netlist_path={v2_sim_info[0]}",
        "Echo simulation_info.list_path={v2_sim_info[1]}",
        "Echo simulation_info.using_data_file={v2_sim_info[2]}",
        "Echo simulation_info.data_file={v2_sim_info[3]}",
        "Echo simulation_info.options={v2_sim_info[5]}",
        "Echo simulation_info.analysis={v2_sim_info[6]}",
        "Echo simulation_info.title={v2_sim_info[8]}",
        "Echo groups={v2_groups_safe}",
        "Echo errors_collected=true",
        "Let v2_close = CloseEchoFile()",
    ]
    for group, vectors in required_vectors.items():
        lines.extend(
            [
                f"Let v2_group_vectors = VectorsInGroup('{group}')",
                f"Let v2_group_info = GetGroupAnalysisInfo('{group}')",
                "Let v2_group_vectors_safe = JoinStringArray(['<none>'], v2_group_vectors)",
                "Let v2_group_info_safe = JoinStringArray(['<none>'], v2_group_info)",
                f"SetGroup {group}",
                f"Let v2_status = OpenEchoFile({_quote_string(status_file)}, 'a')",
                f"Echo group.{group}.vectors={{v2_group_vectors_safe}}",
                f"Echo group.{group}.analysis={{v2_group_info_safe}}",
            ]
        )
        for vector_index, vector in enumerate(vectors):
            lines.append(f"Echo group.{group}.required={vector}")
            target = vector_root / f"{re.sub(r'[^A-Za-z0-9_]+', '_', str(group)).strip('_').lower()}_{_vector_slug(str(vector))}.txt"
            length_var = f"v2_vector_length_{vector_index}"
            lines.append(f"Let {length_var} = Length({_vector_expr(str(vector))})")
            lines.append(f"Echo group.{group}.vector.{_vector_slug(str(vector))}.length={{{length_var}}}")
            # SIMetrix 8.4 can block indefinitely when Show is asked to export
            # a present-but-empty SIMPLIS vector.  Record the zero length and
            # let provenance validation reject it outside the GUI process.
            lines.append(f"IF {length_var} > 0 THEN")
            lines.append(f'Show /force /names "{_vector_slug(str(vector))}" /file {_quote_path(target)} {_vector_expr(str(vector))}')
            lines.append("ENDIF")
        lines.extend(("Let v2_close = CloseEchoFile()",))
    lines.extend(
        [
            f"Let v2_status = OpenEchoFile({_quote_string(status_file)}, 'a')",
            "Echo vector_export_done=true",
            "Echo simulation_completion_token=true",
            "Echo completion_token=true",
            "Let v2_close = CloseEchoFile()",
            "RedirectMessages flush",
            "RedirectMessages off",
            "Quit",
            "",
        ]
    )
    return "\n".join(lines)
