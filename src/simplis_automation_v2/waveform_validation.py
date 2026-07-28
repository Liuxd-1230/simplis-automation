"""Waveform provenance and electrical-behaviour validation for buck runs."""

from __future__ import annotations

import math
from pathlib import Path
from statistics import mean
from typing import Any, Mapping, Sequence


def _diagnostic(code: str, message: str, **details: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"code": code, "message": message}
    if details:
        item["details"] = details
    return item


def _finite(values: Sequence[Any]) -> bool:
    try:
        return all(math.isfinite(float(value)) for value in values)
    except (TypeError, ValueError):
        return False


def validate_vector(
    vector: Mapping[str, Any],
    *,
    expected_analysis: str,
    expected_source_hash: str,
    run_started_at: float,
    min_samples: int,
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    x_values = list(vector.get("x", []))
    y_values = list(vector.get("y", []))
    if str(vector.get("analysis", "")).casefold() != expected_analysis.casefold():
        errors.append(_diagnostic("analysis_mismatch", "Vector came from the wrong analysis", expected=expected_analysis, actual=vector.get("analysis")))
    if str(vector.get("source_hash", "")) != str(expected_source_hash):
        errors.append(_diagnostic("source_hash_mismatch", "Vector provenance does not match the current run"))
    if float(vector.get("created_at", 0.0) or 0.0) < float(run_started_at):
        errors.append(_diagnostic("stale_vector", "Vector predates the current run"))
    if len(x_values) != len(y_values) or len(y_values) < min_samples:
        errors.append(_diagnostic("insufficient_samples", "Vector is empty, partial, or has mismatched axes", x=len(x_values), y=len(y_values), minimum=min_samples))
    if not _finite(x_values) or not _finite(y_values):
        errors.append(_diagnostic("nonfinite_vector", "Vector contains NaN, infinity, or non-numeric samples"))
    if len(x_values) > 1 and any(float(right) < float(left) for left, right in zip(x_values, x_values[1:])):
        errors.append(_diagnostic("nonmonotonic_axis", "Vector x-axis decreases"))
    return {"valid": not errors, "errors": errors, "samples": len(y_values)}


def _rising_edges(time: Sequence[float], values: Sequence[float], threshold: float = 2.5) -> list[float]:
    return [float(time[index]) for index in range(1, min(len(time), len(values))) if float(values[index - 1]) <= threshold < float(values[index])]


def _falling_edges(time: Sequence[float], values: Sequence[float], threshold: float = 2.5) -> list[float]:
    return [float(time[index]) for index in range(1, min(len(time), len(values))) if float(values[index - 1]) > threshold >= float(values[index])]


def _next_after(values: Sequence[float], moment: float) -> float | None:
    return next((value for value in values if value >= moment), None)


def _p2p(values: Sequence[float]) -> float:
    return max(values) - min(values) if values else 0.0


def _logic_threshold(values: Sequence[float], preferred: float = 2.5) -> float:
    low = min(values)
    high = max(values)
    if high == low:
        return preferred
    return preferred if low < preferred < high else low + (high - low) / 2.0


def _steady(values: Sequence[float], fraction: float = 0.25) -> list[float]:
    if not values:
        return []
    start = max(0, len(values) - max(2, int(len(values) * fraction)))
    return [float(value) for value in values[start:]]


def _time_weighted_mean(time: Sequence[float], values: Sequence[float]) -> float:
    """Average a SIMPLIS waveform without bias from event-dense samples."""

    if not values:
        return 0.0
    if len(time) != len(values):
        raise ValueError("Time and value vectors must have equal lengths")
    area = 0.0
    duration = 0.0
    for left_time, right_time, left_value, right_value in zip(time, time[1:], values, values[1:]):
        delta = float(right_time) - float(left_time)
        if delta < 0.0:
            raise ValueError("Time vector must be monotonic")
        if delta == 0.0:
            continue
        area += 0.5 * (float(left_value) + float(right_value)) * delta
        duration += delta
    return area / duration if duration > 0.0 else mean(float(value) for value in values)


def _gain_through_origin(inputs: Sequence[float], outputs: Sequence[float]) -> tuple[float | None, int]:
    peak = max((abs(float(value)) for value in inputs), default=0.0)
    threshold = max(peak * 0.2, 1e-12)
    pairs = [(float(left), float(right)) for left, right in zip(inputs, outputs) if abs(float(left)) >= threshold]
    denominator = sum(left * left for left, _right in pairs)
    if not pairs or denominator <= 1e-30:
        return None, len(pairs)
    return sum(left * right for left, right in pairs) / denominator, len(pairs)


def _relative_error_pct(actual: float, expected: float) -> float:
    return abs(float(actual) - float(expected)) / max(abs(float(expected)), 1e-30) * 100.0


def validate_buck_waveforms(vectors: Mapping[str, Sequence[float]], spec: Mapping[str, Any]) -> dict[str, Any]:
    required = ("time", "VOUT", "VIN", "PWM_HS", "PWM_LS", "SW", "IL", "ILOAD", "ICOUT", "RIPPLE")
    errors: list[dict[str, Any]] = []
    missing = [name for name in required if name not in vectors]
    if missing:
        return {"valid": False, "errors": [_diagnostic("required_vector_missing", "Required buck vector is missing", vectors=missing)], "metrics": {}}
    lengths = {name: len(vectors[name]) for name in required}
    if len(set(lengths.values())) != 1 or next(iter(lengths.values())) < 8:
        return {"valid": False, "errors": [_diagnostic("vector_length_mismatch", "Buck vectors have inconsistent or insufficient samples", lengths=lengths)], "metrics": {}}
    if any(not _finite(vectors[name]) for name in required):
        return {"valid": False, "errors": [_diagnostic("nonfinite_waveforms", "Buck vectors contain non-finite values")], "metrics": {}}

    time = [float(value) for value in vectors["time"]]
    vout = [float(value) for value in vectors["VOUT"]]
    vin = [float(value) for value in vectors["VIN"]]
    hs = [float(value) for value in vectors["PWM_HS"]]
    ls = [float(value) for value in vectors["PWM_LS"]]
    sw = [float(value) for value in vectors["SW"]]
    il = [float(value) for value in vectors["IL"]]
    iload = [float(value) for value in vectors["ILOAD"]]
    icout = [float(value) for value in vectors["ICOUT"]]
    ripple = [float(value) for value in vectors["RIPPLE"]]
    if any(right < left for left, right in zip(time, time[1:])) or time[-1] <= time[0]:
        return {
            "valid": False,
            "errors": [_diagnostic("invalid_time_axis", "Buck time axis must be monotonic and span a positive duration")],
            "metrics": {},
        }
    target = float(spec["vout_target"])
    expected_vin = float(spec["vin"])
    fsw_target = float(spec["fsw_target"])
    steady_start = spec.get("steady_window_start_s")

    hs_threshold = _logic_threshold(hs)
    ls_threshold = _logic_threshold(ls)
    hs_edges = _rising_edges(time, hs, hs_threshold)
    if _p2p(hs) < 0.5 or len(hs_edges) < 2:
        errors.append(_diagnostic("pwm_hs_stuck", "High-side PWM is constant or has too few cycles"))
    if _p2p(ls) < 0.5 or len(_rising_edges(time, ls, ls_threshold)) < 2:
        errors.append(_diagnostic("pwm_ls_stuck", "Low-side PWM is constant or has too few cycles"))
    overlap = sum(1 for h, l in zip(hs, ls) if h > hs_threshold and l > ls_threshold)
    if overlap:
        errors.append(_diagnostic("shoot_through", "High-side and low-side PWM overlap", samples=overlap))
    hs_fall = _falling_edges(time, hs, hs_threshold)
    ls_fall = _falling_edges(time, ls, ls_threshold)
    ls_rise = _rising_edges(time, ls, ls_threshold)
    deadtimes = [next_edge - edge for edge in hs_fall if (next_edge := _next_after(ls_rise, edge)) is not None]
    deadtimes.extend(next_edge - edge for edge in ls_fall if (next_edge := _next_after(hs_edges, edge)) is not None)
    minimum_deadtime = min(deadtimes) if deadtimes else 0.0
    required_deadtime = float(spec.get("deadtime_min_s", 0.0))
    if required_deadtime and (not deadtimes or minimum_deadtime < required_deadtime):
        errors.append(_diagnostic("deadtime_too_short", "PWM interlock does not meet the required deadtime", measured=minimum_deadtime, required=required_deadtime))

    frequency_edges = [edge for edge in hs_edges if steady_start is None or edge >= float(steady_start)]
    periods = [right - left for left, right in zip(frequency_edges, frequency_edges[1:]) if right > left]
    fsw = 1.0 / mean(periods) if periods else 0.0
    if fsw and abs(fsw - fsw_target) / fsw_target > 0.25:
        errors.append(_diagnostic("switching_frequency_out_of_range", "Measured switching frequency is outside the 25% sanity window", measured=fsw, target=fsw_target))
    if max(sw) < 0.7 * expected_vin or min(sw) > 0.3 * expected_vin:
        errors.append(_diagnostic("switch_node_not_switching", "SW does not span plausible low and high levels", minimum=min(sw), maximum=max(sw)))
    hs_falls = _falling_edges(time, hs, hs_threshold)
    on_times = [fall - rise for rise in hs_edges for fall in [_next_after(hs_falls, rise)] if fall is not None and fall > rise]
    ton_mean = mean(on_times) if on_times else 0.0
    nominal_ton = target / max(expected_vin * fsw_target, 1e-30)
    if ton_mean and abs(ton_mean - nominal_ton) / nominal_ton > float(spec.get("ton_tolerance_pct", 35.0)) / 100.0:
        errors.append(_diagnostic("on_time_out_of_range", "Measured high-side on-time is implausible for adaptive Ton", measured=ton_mean, nominal=nominal_ton))

    if steady_start is not None:
        effective_steady_start = float(steady_start)
        steady_indices = [index for index, moment in enumerate(time) if moment >= effective_steady_start]
        if len(steady_indices) < 2:
            errors.append(_diagnostic("steady_window_missing", "Declared steady-state window is outside or too short", start_s=steady_start))
            steady_indices = list(range(max(0, len(time) - max(2, len(time) // 4)), len(time)))
    else:
        effective_steady_start = time[-1] - 0.25 * (time[-1] - time[0])
        steady_indices = [index for index, moment in enumerate(time) if moment >= effective_steady_start]
        if len(steady_indices) < 2:
            steady_indices = list(range(max(0, len(time) - max(2, len(time) // 4)), len(time)))
    steady_time = [time[index] for index in steady_indices]
    steady_vout = [vout[index] for index in steady_indices]
    steady_vin = [vin[index] for index in steady_indices]
    steady_il = [il[index] for index in steady_indices]
    steady_load = [iload[index] for index in steady_indices]
    steady_icout = [icout[index] for index in steady_indices]
    steady_ripple = [ripple[index] for index in steady_indices]
    vout_mean = _time_weighted_mean(steady_time, steady_vout)
    vin_mean = _time_weighted_mean(steady_time, steady_vin)
    il_mean = _time_weighted_mean(steady_time, steady_il)
    load_mean = _time_weighted_mean(steady_time, steady_load)
    icout_mean = _time_weighted_mean(steady_time, steady_icout)
    dc_error_pct = abs(vout_mean - target) / max(abs(target), 1e-15) * 100.0
    if dc_error_pct > float(spec.get("vout_error_limit_pct", 5.0)):
        errors.append(_diagnostic("vout_not_regulated", "Steady-state output is outside its regulation sanity limit", error_pct=dc_error_pct))
    if max(abs(value) for value in vout) > abs(target) * float(spec.get("vout_bounded_multiplier", 2.0)):
        errors.append(_diagnostic("vout_unbounded", "Output voltage exceeds its configured bounded-startup range", peak=max(abs(value) for value in vout)))

    startup_metrics: dict[str, float] = {}
    startup_check_requested = any(
        name in spec
        for name in (
            "initial_vout_limit_pct",
            "startup_overshoot_limit_pct",
            "startup_inductor_min_a",
        )
    )
    if startup_check_requested:
        startup_end = spec.get("startup_window_end_s", spec.get("load_step_time_s", steady_start))
        startup_indices = [
            index
            for index, moment in enumerate(time)
            if startup_end is None or moment < float(startup_end)
        ]
        if not startup_indices:
            errors.append(_diagnostic("startup_window_missing", "Declared startup window is outside the waveform range", end_s=startup_end))
        else:
            startup_vout = [vout[index] for index in startup_indices]
            startup_il = [il[index] for index in startup_indices]
            initial_vout_pct = abs(startup_vout[0]) / max(abs(target), 1e-15) * 100.0
            startup_peak = max(startup_vout)
            startup_overshoot_pct = max(0.0, startup_peak - target) / max(abs(target), 1e-15) * 100.0
            startup_min_il = min(startup_il)
            startup_metrics = {
                "initial_vout_pct": initial_vout_pct,
                "startup_vout_peak": startup_peak,
                "startup_overshoot_pct": startup_overshoot_pct,
                "startup_inductor_min_a": startup_min_il,
                "startup_window_end_s": float(startup_end) if startup_end is not None else time[startup_indices[-1]],
            }
            if initial_vout_pct > float(spec.get("initial_vout_limit_pct", math.inf)):
                errors.append(
                    _diagnostic(
                        "startup_initial_vout_invalid",
                        "Output does not start near the declared zero-voltage state",
                        measured_pct=initial_vout_pct,
                        limit_pct=spec.get("initial_vout_limit_pct"),
                    )
                )
            if startup_overshoot_pct > float(spec.get("startup_overshoot_limit_pct", math.inf)):
                errors.append(
                    _diagnostic(
                        "startup_overshoot_excessive",
                        "Startup output overshoot exceeds its limit",
                        measured_pct=startup_overshoot_pct,
                        limit_pct=spec.get("startup_overshoot_limit_pct"),
                    )
                )
            if startup_min_il < float(spec.get("startup_inductor_min_a", -math.inf)):
                errors.append(
                    _diagnostic(
                        "startup_inductor_current_reversed",
                        "Inductor current crosses the declared startup floor",
                        measured=startup_min_il,
                        floor=spec.get("startup_inductor_min_a"),
                    )
                )
    if min(steady_il) <= float(spec.get("ccm_min_current", 0.0)):
        errors.append(_diagnostic("ccm_violation", "Inductor current crosses the configured CCM floor", minimum=min(steady_il)))
    if _p2p(steady_il) <= max(1e-9, abs(il_mean) * 1e-5):
        errors.append(_diagnostic("inductor_current_flat", "Inductor current has no measurable ripple"))
    current_balance_pct = abs(il_mean - load_mean) / max(abs(load_mean), 1e-12) * 100.0
    if current_balance_pct > float(spec.get("current_balance_limit_pct", 10.0)):
        errors.append(_diagnostic("inductor_load_mismatch", "Average inductor and load currents disagree", error_pct=current_balance_pct))
    if abs(icout_mean) > max(0.05, abs(load_mean) * 0.05):
        errors.append(_diagnostic("capacitor_dc_current", "Output capacitor has implausible average DC current", mean=icout_mean))
    ripple_pp = _p2p(steady_ripple)
    if ripple_pp <= float(spec.get("ripple_min", 1e-6)):
        errors.append(_diagnostic("synthetic_ripple_missing", "Synthetic ripple is absent or flat", peak_to_peak=ripple_pp))
    if spec.get("require_comparator_crossing"):
        if "CMP_P" not in vectors or "CMP_N" not in vectors:
            errors.append(_diagnostic("comparator_vectors_missing", "Comparator input vectors are required for this experiment"))
        else:
            difference = [float(left) - float(right) for left, right in zip(vectors["CMP_P"], vectors["CMP_N"])]
            crossings = sum(1 for left, right in zip(difference, difference[1:]) if left == 0 or left * right < 0)
            if crossings < 2:
                errors.append(_diagnostic("comparator_not_crossing", "Comparator inputs do not produce repeated valid crossings", crossings=crossings))

    reset_metrics: dict[str, float] = {}
    if spec.get("require_reset_once_per_cycle"):
        reset_name = str(spec.get("reset_vector", "RESET"))
        if reset_name not in vectors:
            errors.append(_diagnostic("reset_vector_missing", "One reset pulse per switching cycle is required", vector=reset_name))
        else:
            reset = [float(value) for value in vectors[reset_name]]
            reset_edges = _rising_edges(time, reset, _logic_threshold(reset))
            cycle_counts = [sum(1 for edge in reset_edges if left <= edge < right) for left, right in zip(hs_edges, hs_edges[1:])]
            bad_cycles = [index for index, count in enumerate(cycle_counts) if count != 1]
            reset_metrics = {"reset_pulse_count": float(len(reset_edges)), "reset_checked_cycles": float(len(cycle_counts)), "reset_bad_cycles": float(len(bad_cycles))}
            if not cycle_counts or bad_cycles:
                errors.append(_diagnostic("reset_not_once_per_cycle", "RESET must pulse exactly once in each complete switching cycle", bad_cycles=bad_cycles, counts=cycle_counts))

    ripple_sync_metrics: dict[str, float] = {}
    if spec.get("require_ripple_edge_sync"):
        sw_threshold = (min(sw) + max(sw)) / 2.0
        switch_edges = sorted(_rising_edges(time, sw, sw_threshold) + _falling_edges(time, sw, sw_threshold))
        sync_window = float(spec.get("ripple_edge_sync_window_s", 0.15 / max(fsw_target, 1e-30)))
        minimum_delta = max(
            float(spec.get("ripple_edge_delta_min", 0.0)),
            float(spec.get("ripple_min", 1e-6)) * 0.1,
            ripple_pp * 0.05,
            1e-12,
        )
        synchronized = 0
        for edge in switch_edges:
            indices = [index for index, moment in enumerate(time) if abs(moment - edge) <= sync_window]
            if len(indices) >= 2 and _p2p([ripple[index] for index in indices]) >= minimum_delta:
                synchronized += 1
        ratio = synchronized / len(switch_edges) if switch_edges else 0.0
        ripple_sync_metrics = {"ripple_edge_sync_ratio": ratio, "ripple_switch_edge_count": float(len(switch_edges))}
        if not switch_edges or ratio < float(spec.get("ripple_edge_sync_min_ratio", 0.8)):
            errors.append(_diagnostic("ripple_not_edge_synchronous", "Synthetic ripple does not respond at enough SW transitions", ratio=ratio, edges=len(switch_edges)))

    power_metrics: dict[str, float] = {}
    if "IIN" in vectors:
        raw_iin = vectors["IIN"]
        if len(raw_iin) != len(time) or not _finite(raw_iin):
            errors.append(_diagnostic("input_current_vector_invalid", "Input-current vector is missing samples or contains non-finite values"))
        else:
            steady_iin = [float(raw_iin[index]) for index in steady_indices]
            input_power = [left * right for left, right in zip(steady_vin, steady_iin)]
            output_power = [left * right for left, right in zip(steady_vout, steady_load)]
            pin = _time_weighted_mean(steady_time, input_power)
            pout = _time_weighted_mean(steady_time, output_power)
            power_ratio = pout / pin if pin > 0.0 else 0.0
            minimum_ratio = float(spec.get("min_power_ratio", 0.1))
            maximum_ratio = float(spec.get("max_power_gain", 1.05))
            power_metrics = {"input_power_w": pin, "output_power_w": pout, "power_ratio": power_ratio}
            if pin <= 0.0 or pout <= 0.0 or power_ratio < minimum_ratio or power_ratio > maximum_ratio:
                errors.append(
                    _diagnostic(
                        "power_balance_invalid",
                        "Input/output power direction or magnitude is implausible",
                        input_power=pin,
                        output_power=pout,
                        power_ratio=power_ratio,
                        minimum_ratio=minimum_ratio,
                        maximum_ratio=maximum_ratio,
                    )
                )

    load_step_metrics: dict[str, float] = {}
    if spec.get("load_step_time_s") is not None:
        step_time = float(spec["load_step_time_s"])
        before = [index for index, moment in enumerate(time) if moment < step_time]
        after = [index for index, moment in enumerate(time) if moment >= step_time]
        if not before or not after:
            errors.append(_diagnostic("load_step_window_missing", "Declared load-step time is outside the waveform range", step_time=step_time))
        else:
            pre_window = before[-max(2, min(len(before), len(time) // 20)) :]
            post_window = after[: max(2, min(len(after), len(time) // 20))]
            load_delta = mean(iload[index] for index in post_window) - mean(iload[index] for index in pre_window)
            expected_delta = float(spec.get("load_step_delta_a", load_delta))
            if expected_delta and load_delta * expected_delta <= 0:
                errors.append(_diagnostic("load_step_wrong_direction", "Measured load step has the wrong direction", measured=load_delta, expected=expected_delta))
            post_values = [vout[index] for index in after]
            peak_deviation = max(abs(value - target) for value in post_values)
            recovery_band = abs(target) * float(spec.get("recovery_band_pct", 5.0)) / 100.0
            recovery_time = None
            hold_time = 1.0 / max(fsw_target, 1e-30)
            for candidate_index in after:
                if time[candidate_index] < step_time:
                    continue
                hold_indices = [i for i in after if time[candidate_index] <= time[i] <= time[candidate_index] + hold_time]
                if hold_indices and all(abs(vout[i] - target) <= recovery_band for i in hold_indices):
                    recovery_time = time[candidate_index] - step_time
                    break
            recovery_limit = float(spec.get("recovery_limit_cycles", 20.0))
            recovery_cycles = recovery_time * fsw_target if recovery_time is not None else recovery_limit + 1.0
            load_step_metrics = {
                "load_step_delta_a": load_delta,
                "load_step_peak_deviation_pct": peak_deviation / max(abs(target), 1e-15) * 100.0,
                "load_step_recovery_s": recovery_time,
                "recovery_cycles": recovery_cycles,
            }
            if load_step_metrics["load_step_peak_deviation_pct"] > float(spec.get("load_step_deviation_limit_pct", 5.0)):
                errors.append(_diagnostic("load_step_deviation_excessive", "Load-step output deviation exceeds its limit", value=load_step_metrics["load_step_peak_deviation_pct"]))
            if recovery_cycles > recovery_limit:
                errors.append(_diagnostic("load_step_recovery_too_slow", "Load-step response does not recover within its cycle limit", cycles=recovery_cycles))

    pop_metrics: dict[str, float] = {}
    if len(hs_edges) >= 4:
        cycle_means: list[float] = []
        cycle_il_means: list[float] = []
        for left, right in zip(hs_edges[-4:-1], hs_edges[-3:]):
            indices = [index for index, moment in enumerate(time) if left <= moment <= right]
            if len(indices) >= 2:
                cycle_time = [time[index] for index in indices]
                cycle_means.append(_time_weighted_mean(cycle_time, [vout[index] for index in indices]))
                cycle_il_means.append(_time_weighted_mean(cycle_time, [il[index] for index in indices]))
        if len(cycle_means) >= 2:
            vout_cycle_delta = (max(cycle_means) - min(cycle_means)) / max(abs(mean(cycle_means)), 1e-15) * 100.0
            il_cycle_delta = (max(cycle_il_means) - min(cycle_il_means)) / max(abs(mean(cycle_il_means)), 1e-15) * 100.0
            pop_metrics = {"pop_vout_cycle_delta_pct": vout_cycle_delta, "pop_il_cycle_delta_pct": il_cycle_delta}
            limit = float(spec.get("pop_cycle_delta_limit_pct", 1.0))
            if max(vout_cycle_delta, il_cycle_delta) > limit:
                errors.append(_diagnostic("periodic_steady_state_failed", "Last switching cycles are not periodic-steady", vout_delta_pct=vout_cycle_delta, il_delta_pct=il_cycle_delta, limit_pct=limit))

    jitter_pct = 0.0
    if len(periods) >= 2:
        average_period = mean(periods)
        jitter_pct = (max(periods) - min(periods)) / average_period * 100.0
    metrics = {
        "vout_mean": vout_mean,
        "vout_dc_error_pct": dc_error_pct,
        "vout_ripple_pp": _p2p(steady_vout),
        "switching_frequency_hz": fsw,
        "period_jitter_pct": jitter_pct,
        "inductor_current_mean": il_mean,
        "load_current_mean": load_mean,
        "current_balance_error_pct": current_balance_pct,
        "capacitor_current_mean": icout_mean,
        "synthetic_ripple_pp": ripple_pp,
        "vin_mean": vin_mean,
        "minimum_deadtime_s": minimum_deadtime,
        "ton_mean_s": ton_mean,
        "steady_window_start_s": effective_steady_start,
        **reset_metrics,
        **ripple_sync_metrics,
        **power_metrics,
        **startup_metrics,
        **load_step_metrics,
        **pop_metrics,
    }
    return {"valid": not errors, "errors": errors, "metrics": metrics}


def validate_catalog_analog_waveforms(vectors: Mapping[str, Sequence[float]], spec: Mapping[str, Any]) -> dict[str, Any]:
    """Prove VCVS/VCCS sign and gain plus both VPWLR diode regions."""

    required = ("time", "CTRL", "VCVS_OUT", "VCCS_OUT", "DIODE_IN", "DIODE_OUT", "AC_IN_DC", "AC_OUT_DC")
    missing = [name for name in required if name not in vectors]
    if missing:
        return {
            "valid": False,
            "errors": [_diagnostic("required_vector_missing", "Catalog analog proof vector is missing", vectors=missing)],
            "metrics": {},
        }
    lengths = {name: len(vectors[name]) for name in required}
    if len(set(lengths.values())) != 1 or next(iter(lengths.values())) < 8:
        return {
            "valid": False,
            "errors": [_diagnostic("vector_length_mismatch", "Catalog analog proof vectors are inconsistent or too short", lengths=lengths)],
            "metrics": {},
        }
    if any(not _finite(vectors[name]) for name in required):
        return {"valid": False, "errors": [_diagnostic("nonfinite_waveforms", "Catalog analog proof contains non-finite samples")], "metrics": {}}

    control = [float(value) for value in vectors["CTRL"]]
    vcvs_out = [float(value) for value in vectors["VCVS_OUT"]]
    vccs_out = [float(value) for value in vectors["VCCS_OUT"]]
    diode_in = [float(value) for value in vectors["DIODE_IN"]]
    diode_out = [float(value) for value in vectors["DIODE_OUT"]]
    ac_in_dc = [float(value) for value in vectors["AC_IN_DC"]]
    ac_out_dc = [float(value) for value in vectors["AC_OUT_DC"]]
    errors: list[dict[str, Any]] = []

    peak_control = max((abs(value) for value in control), default=0.0)
    if peak_control <= 1e-9 or min(control) >= -0.2 * peak_control or max(control) <= 0.2 * peak_control:
        errors.append(_diagnostic("catalog_control_states_missing", "Analog proof control must visit positive and negative driven states"))
    vcvs_gain, vcvs_samples = _gain_through_origin(control, vcvs_out)
    vccs_gain, vccs_samples = _gain_through_origin(control, vccs_out)
    expected_vcvs = float(spec.get("vcvs_gain", 2.0))
    expected_vccs = float(spec.get("vccs_voltage_gain", -2.0))
    gain_tolerance = float(spec.get("gain_tolerance_pct", 2.0))
    if vcvs_gain is None or _relative_error_pct(vcvs_gain, expected_vcvs) > gain_tolerance:
        errors.append(
            _diagnostic(
                "vcvs_gain_failed",
                "VCVS output does not have the declared positive voltage gain",
                measured=vcvs_gain,
                expected=expected_vcvs,
                tolerance_pct=gain_tolerance,
            )
        )
    if vccs_gain is None or _relative_error_pct(vccs_gain, expected_vccs) > gain_tolerance:
        errors.append(
            _diagnostic(
                "vccs_gain_failed",
                "VCCS plus proof load does not have the declared signed voltage gain",
                measured=vccs_gain,
                expected=expected_vccs,
                tolerance_pct=gain_tolerance,
            )
        )

    diode_peak = max((abs(value) for value in diode_in), default=0.0)
    diode_threshold = max(diode_peak * 0.2, 1e-9)
    stable_indices = [
        index
        for index in range(1, len(diode_in) - 1)
        if (diode_in[index - 1] < -diode_threshold and diode_in[index] < -diode_threshold and diode_in[index + 1] < -diode_threshold)
        or (diode_in[index - 1] > diode_threshold and diode_in[index] > diode_threshold and diode_in[index + 1] > diode_threshold)
    ]
    reverse_indices = [index for index in stable_indices if diode_in[index] < -diode_threshold]
    forward_indices = [index for index in stable_indices if diode_in[index] > diode_threshold]
    reverse_error = max((abs(diode_out[index] - diode_in[index]) for index in reverse_indices), default=math.inf)
    forward_clamp = max((abs(diode_out[index]) for index in forward_indices), default=math.inf)
    reverse_limit = float(spec.get("diode_off_tracking_tolerance_v", 0.02))
    forward_limit = float(spec.get("diode_on_clamp_max_v", 0.02))
    if not reverse_indices or reverse_error > reverse_limit:
        errors.append(
            _diagnostic(
                "diode_reverse_tracking_failed",
                "Reverse-biased VPWLR proof output does not track its source through the load",
                measured_error_v=reverse_error,
                tolerance_v=reverse_limit,
                samples=len(reverse_indices),
            )
        )
    if not forward_indices or forward_clamp > forward_limit:
        errors.append(
            _diagnostic(
                "diode_forward_clamp_failed",
                "Forward-biased VPWLR proof output is not clamped near zero",
                measured_abs_v=forward_clamp,
                maximum_v=forward_limit,
                samples=len(forward_indices),
            )
        )
    ac_dc_offset = max((abs(value) for value in ac_in_dc + ac_out_dc), default=math.inf)
    ac_dc_limit = float(spec.get("ac_dc_offset_max_v", 1e-9))
    if ac_dc_offset > ac_dc_limit:
        errors.append(
            _diagnostic(
                "ac_injection_dc_offset",
                "AC injection source must be zero in the transient/DC operating circuit",
                measured_abs_v=ac_dc_offset,
                maximum_v=ac_dc_limit,
            )
        )
    metrics = {
        "vcvs_gain": vcvs_gain,
        "vccs_voltage_gain": vccs_gain,
        "vcvs_gain_samples": vcvs_samples,
        "vccs_gain_samples": vccs_samples,
        "diode_reverse_max_tracking_error_v": reverse_error,
        "diode_forward_max_abs_v": forward_clamp,
        "diode_reverse_samples": len(reverse_indices),
        "diode_forward_samples": len(forward_indices),
        "ac_injection_max_dc_abs_v": ac_dc_offset,
    }
    return {"valid": not errors, "errors": errors, "metrics": metrics}


def _decay_crossing_time(time: Sequence[float], normalized: Sequence[float], level: float) -> float | None:
    for index, (left, right) in enumerate(zip(normalized, normalized[1:])):
        if left >= level >= right:
            t_left = float(time[index])
            t_right = float(time[index + 1])
            if right == left:
                return t_left
            fraction = (level - left) / (right - left)
            return t_left + fraction * (t_right - t_left)
    return None


def validate_catalog_reactive_ic_waveforms(vectors: Mapping[str, Sequence[float]], spec: Mapping[str, Any]) -> dict[str, Any]:
    """Prove capacitor-voltage and inductor-current IC value, sign, and decay."""

    required = ("time", "CAP_V", "IND_I")
    missing = [name for name in required if name not in vectors]
    if missing:
        return {
            "valid": False,
            "errors": [_diagnostic("required_vector_missing", "Reactive IC proof vector is missing", vectors=missing)],
            "metrics": {},
        }
    lengths = {name: len(vectors[name]) for name in required}
    if len(set(lengths.values())) != 1 or next(iter(lengths.values())) < 8:
        return {
            "valid": False,
            "errors": [_diagnostic("vector_length_mismatch", "Reactive IC proof vectors are inconsistent or too short", lengths=lengths)],
            "metrics": {},
        }
    if any(not _finite(vectors[name]) for name in required):
        return {"valid": False, "errors": [_diagnostic("nonfinite_waveforms", "Reactive IC proof contains non-finite samples")], "metrics": {}}

    time = [float(value) for value in vectors["time"]]
    if any(right <= left for left, right in zip(time, time[1:])):
        return {"valid": False, "errors": [_diagnostic("nonmonotonic_axis", "Reactive IC proof time axis must increase")], "metrics": {}}

    expected = {
        "capacitor": {
            "values": [float(value) for value in vectors["CAP_V"]],
            "initial": float(spec.get("capacitor_initial_v", 1.2)),
            "tau": float(spec.get("capacitor_tau_s", 1e-3)),
        },
        "inductor": {
            "values": [float(value) for value in vectors["IND_I"]],
            "initial": float(spec.get("inductor_initial_a", 2.0)),
            "tau": float(spec.get("inductor_tau_s", 1e-3)),
        },
    }
    errors: list[dict[str, Any]] = []
    metrics: dict[str, float | None] = {"initial_time_s": time[0]}
    initial_tolerance = float(spec.get("initial_tolerance_pct", 3.0))
    tau_tolerance = float(spec.get("tau_tolerance_pct", 10.0))
    final_fraction_limit = float(spec.get("final_fraction_max", 0.02))
    monotonic_rise_limit = float(spec.get("monotonic_rise_fraction_max", 0.01))
    initial_time_limit = float(spec.get("initial_time_tolerance_s", min(float(item["tau"]) for item in expected.values()) * 0.01))
    if abs(time[0]) > initial_time_limit:
        errors.append(
            _diagnostic(
                "reactive_ic_initial_sample_missing",
                "Reactive IC proof does not begin close enough to t=0",
                first_time_s=time[0],
                maximum_abs_time_s=initial_time_limit,
            )
        )

    for name, record in expected.items():
        values = record["values"]
        initial = float(record["initial"])
        tau = float(record["tau"])
        if initial == 0 or tau <= 0:
            errors.append(_diagnostic("reactive_ic_contract_invalid", "Reactive IC proof requires non-zero initial values and positive time constants", quantity=name))
            continue
        normalized = [float(value) / initial for value in values]
        initial_error = abs(normalized[0] - 1.0) * 100.0
        final_fraction = abs(normalized[-1])
        maximum_rise = max((right - left for left, right in zip(normalized, normalized[1:])), default=0.0)
        minimum_normalized = min(normalized)
        crossing = _decay_crossing_time(time, normalized, 1.0 / math.e)
        tau_error = abs((crossing - time[0]) - tau) / tau * 100.0 if crossing is not None else None
        prefix = "capacitor" if name == "capacitor" else "inductor"
        unit_suffix = "v" if name == "capacitor" else "a"
        metrics[f"{prefix}_initial_{unit_suffix}"] = float(values[0])
        metrics[f"{prefix}_initial_error_pct"] = initial_error
        metrics[f"{prefix}_final_fraction"] = final_fraction
        metrics[f"{prefix}_estimated_tau_s"] = (crossing - time[0]) if crossing is not None else None
        metrics[f"{prefix}_tau_error_pct"] = tau_error
        metrics[f"{prefix}_maximum_normalized_rise"] = maximum_rise
        if normalized[0] <= 0:
            errors.append(
                _diagnostic(
                    f"{prefix}_ic_sign_failed",
                    f"{prefix.capitalize()} initial condition has the wrong P-to-N sign",
                    measured=values[0],
                    expected=initial,
                )
            )
        if initial_error > initial_tolerance:
            errors.append(
                _diagnostic(
                    f"{prefix}_ic_value_failed",
                    f"{prefix.capitalize()} initial condition is outside tolerance",
                    measured=values[0],
                    expected=initial,
                    error_pct=initial_error,
                    tolerance_pct=initial_tolerance,
                )
            )
        if minimum_normalized < -initial_tolerance / 100.0 or maximum_rise > monotonic_rise_limit:
            errors.append(
                _diagnostic(
                    f"{prefix}_ic_decay_nonmonotonic",
                    f"{prefix.capitalize()} state does not decay monotonically toward zero",
                    minimum_normalized=minimum_normalized,
                    maximum_normalized_rise=maximum_rise,
                    allowed_rise=monotonic_rise_limit,
                )
            )
        if final_fraction > final_fraction_limit:
            errors.append(
                _diagnostic(
                    f"{prefix}_ic_decay_incomplete",
                    f"{prefix.capitalize()} state did not decay far enough",
                    final_fraction=final_fraction,
                    maximum=final_fraction_limit,
                )
            )
        if crossing is None or tau_error is None or tau_error > tau_tolerance:
            errors.append(
                _diagnostic(
                    f"{prefix}_ic_tau_failed",
                    f"{prefix.capitalize()} decay does not match the declared time constant",
                    estimated_tau_s=(crossing - time[0]) if crossing is not None else None,
                    expected_tau_s=tau,
                    error_pct=tau_error,
                    tolerance_pct=tau_tolerance,
                )
            )
    return {"valid": not errors, "errors": errors, "metrics": metrics}


def validate_catalog_ac_probe(
    frequency: Sequence[float],
    response: Sequence[Mapping[str, float]],
    spec: Mapping[str, Any],
    *,
    input_response: Sequence[Mapping[str, float]] | None = None,
) -> dict[str, Any]:
    """Validate a raw-complex AC divider ratio used to prove source and probe behavior."""

    if len(frequency) != len(response) or len(frequency) < 2:
        return {"valid": False, "errors": [_diagnostic("ac_probe_samples_invalid", "Catalog AC proof has inconsistent or insufficient samples")], "metrics": {}}
    if not _finite(frequency) or any(float(value) <= 0 for value in frequency) or any(
        float(right) <= float(left) for left, right in zip(frequency, frequency[1:])
    ):
        return {"valid": False, "errors": [_diagnostic("ac_probe_frequency_invalid", "Catalog AC proof frequency axis must be positive and increasing")], "metrics": {}}
    try:
        values = [complex(float(item["real"]), float(item["imag"])) for item in response]
    except (KeyError, TypeError, ValueError):
        return {"valid": False, "errors": [_diagnostic("ac_probe_not_complex", "Catalog AC proof must use raw complex input and output vectors")], "metrics": {}}
    if any(not (math.isfinite(value.real) and math.isfinite(value.imag)) for value in values):
        return {"valid": False, "errors": [_diagnostic("ac_probe_nonfinite", "Catalog AC proof contains non-finite complex samples")], "metrics": {}}

    expected_gain = float(spec.get("expected_gain", 0.5))
    expected_phase = float(spec.get("expected_phase_deg", 0.0))
    gain_tolerance = float(spec.get("gain_tolerance_pct", 2.0))
    phase_tolerance = float(spec.get("phase_tolerance_deg", 2.0))
    magnitudes = [abs(value) for value in values]
    phases = [math.degrees(math.atan2(value.imag, value.real)) for value in values]
    gain_errors = [_relative_error_pct(value, expected_gain) for value in magnitudes]
    phase_errors = [abs(value - expected_phase) for value in phases]
    errors: list[dict[str, Any]] = []
    if max(gain_errors) > gain_tolerance:
        errors.append(
            _diagnostic(
                "ac_probe_gain_failed",
                "AC injection and divider ratio is outside its declared gain tolerance",
                maximum_error_pct=max(gain_errors),
                tolerance_pct=gain_tolerance,
            )
        )
    if max(phase_errors) > phase_tolerance:
        errors.append(
            _diagnostic(
                "ac_probe_phase_failed",
                "AC injection and divider ratio has an unexpected phase shift",
                maximum_error_deg=max(phase_errors),
                tolerance_deg=phase_tolerance,
            )
        )
    input_magnitudes: list[float] = []
    if input_response is None or len(input_response) != len(frequency):
        errors.append(_diagnostic("ac_source_vector_missing", "Catalog AC source proof requires its raw complex input vector"))
    else:
        try:
            input_values = [complex(float(item["real"]), float(item["imag"])) for item in input_response]
        except (KeyError, TypeError, ValueError):
            errors.append(_diagnostic("ac_source_not_complex", "Catalog AC source vector is not complex"))
        else:
            if any(not (math.isfinite(value.real) and math.isfinite(value.imag)) for value in input_values):
                errors.append(_diagnostic("ac_source_nonfinite", "Catalog AC source contains non-finite complex samples"))
            else:
                input_magnitudes = [abs(value) for value in input_values]
                expected_input = float(spec.get("expected_input_magnitude", 1.0))
                input_tolerance = float(spec.get("input_magnitude_tolerance_pct", gain_tolerance))
                input_errors = [_relative_error_pct(value, expected_input) for value in input_magnitudes]
                if max(input_errors) > input_tolerance:
                    errors.append(
                        _diagnostic(
                            "ac_source_magnitude_failed",
                            "AC injection source does not produce the declared small-signal magnitude",
                            maximum_error_pct=max(input_errors),
                            tolerance_pct=input_tolerance,
                        )
                    )
    return {
        "valid": not errors,
        "errors": errors,
        "metrics": {
            "mean_gain": mean(magnitudes),
            "maximum_gain_error_pct": max(gain_errors),
            "mean_phase_deg": mean(phases),
            "maximum_phase_error_deg": max(phase_errors),
            "sample_count": len(values),
            "mean_input_magnitude": mean(input_magnitudes) if input_magnitudes else None,
        },
    }


def validate_catalog_and2_waveforms(vectors: Mapping[str, Sequence[float]], spec: Mapping[str, Any]) -> dict[str, Any]:
    """Check all four AND2 input states, the primary output, and its complement."""

    required = ("time", "A", "B", "OUT", "OUT_BAR")
    missing = [name for name in required if name not in vectors]
    if missing:
        return {"valid": False, "errors": [_diagnostic("required_vector_missing", "Catalog AND2 proof vector is missing", vectors=missing)], "metrics": {}}
    lengths = {name: len(vectors[name]) for name in required}
    if len(set(lengths.values())) != 1 or next(iter(lengths.values())) < 8:
        return {
            "valid": False,
            "errors": [_diagnostic("vector_length_mismatch", "Catalog AND2 proof vectors are inconsistent or too short", lengths=lengths)],
            "metrics": {},
        }
    if any(not _finite(vectors[name]) for name in required):
        return {"valid": False, "errors": [_diagnostic("nonfinite_waveforms", "Catalog AND2 proof contains non-finite samples")], "metrics": {}}

    time = [float(value) for value in vectors["time"]]
    waves = {name: [float(value) for value in vectors[name]] for name in required if name != "time"}
    if any(right < left for left, right in zip(time, time[1:])):
        return {"valid": False, "errors": [_diagnostic("nonmonotonic_axis", "Catalog AND2 proof time axis decreases")], "metrics": {}}
    preferred = float(spec.get("logic_threshold", 2.5))
    thresholds = {name: _logic_threshold(waves[name], preferred) for name in waves}
    transitions = sorted(
        _rising_edges(time, waves["A"], thresholds["A"])
        + _falling_edges(time, waves["A"], thresholds["A"])
        + _rising_edges(time, waves["B"], thresholds["B"])
        + _falling_edges(time, waves["B"], thresholds["B"])
    )
    positive_steps = [right - left for left, right in zip(time, time[1:]) if right > left]
    default_ignore = min(positive_steps) * 2.0 if positive_steps else 0.0
    ignore_window = float(spec.get("propagation_ignore_s", default_ignore))
    eligible_indices = [
        index
        for index, moment in enumerate(time)
        if not any(0.0 <= moment - transition <= ignore_window for transition in transitions)
    ]
    states: set[tuple[bool, bool]] = set()
    output_mismatches = 0
    complement_mismatches = 0
    for index in eligible_indices:
        a = waves["A"][index] > thresholds["A"]
        b = waves["B"][index] > thresholds["B"]
        output = waves["OUT"][index] > thresholds["OUT"]
        output_bar = waves["OUT_BAR"][index] > thresholds["OUT_BAR"]
        states.add((a, b))
        expected = a and b
        if output != expected:
            output_mismatches += 1
        if output_bar == output:
            complement_mismatches += 1
    checked = len(eligible_indices)
    output_ratio = output_mismatches / checked if checked else 1.0
    complement_ratio = complement_mismatches / checked if checked else 1.0
    limit = float(spec.get("max_mismatch_ratio", 0.02))
    errors: list[dict[str, Any]] = []
    missing_states = sorted({(False, False), (True, False), (True, True), (False, True)} - states)
    if missing_states:
        errors.append(_diagnostic("and2_truth_table_states_missing", "AND2 proof did not hold every input state", states=missing_states))
    if checked == 0 or output_ratio > limit:
        errors.append(
            _diagnostic(
                "and2_truth_table_failed",
                "AND2 primary output does not match A AND B",
                mismatches=output_mismatches,
                checked=checked,
                ratio=output_ratio,
                maximum=limit,
            )
        )
    if checked == 0 or complement_ratio > limit:
        errors.append(
            _diagnostic(
                "and2_complement_failed",
                "AND2 complementary output is not the inverse of the primary output",
                mismatches=complement_mismatches,
                checked=checked,
                ratio=complement_ratio,
                maximum=limit,
            )
        )
    if _p2p(waves["OUT"]) < 0.5 or _p2p(waves["OUT_BAR"]) < 0.5:
        errors.append(_diagnostic("and2_output_stuck", "AND2 primary or complementary output never visits both logic levels"))
    return {
        "valid": not errors,
        "errors": errors,
        "metrics": {
            "truth_table_states_covered": len(states),
            "checked_samples": checked,
            "output_mismatch_samples": output_mismatches,
            "output_mismatch_ratio": output_ratio,
            "complement_mismatch_samples": complement_mismatches,
            "complement_mismatch_ratio": complement_ratio,
            "propagation_ignore_s": ignore_window,
        },
    }


def _unwrap_phase_degrees(phases: Sequence[float]) -> list[float]:
    if not phases:
        return []
    output = [float(phases[0])]
    for raw in phases[1:]:
        value = float(raw)
        while value - output[-1] > 180.0:
            value -= 360.0
        while value - output[-1] <= -180.0:
            value += 360.0
        output.append(value)
    return output


def validate_ac_response(frequency: Sequence[float], response: Sequence[Mapping[str, float]]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    if len(frequency) != len(response) or len(frequency) < 3:
        return {"valid": False, "errors": [_diagnostic("ac_samples_invalid", "AC response has inconsistent or insufficient samples")], "metrics": {}}
    if (
        not _finite(frequency)
        or any(float(value) <= 0 for value in frequency)
        or any(float(right) <= float(left) for left, right in zip(frequency, frequency[1:]))
    ):
        return {"valid": False, "errors": [_diagnostic("ac_frequency_invalid", "AC frequency axis must be finite, positive, and increasing")], "metrics": {}}
    complex_values: list[complex] = []
    try:
        complex_values = [complex(float(value["real"]), float(value["imag"])) for value in response]
    except (KeyError, TypeError, ValueError):
        return {"valid": False, "errors": [_diagnostic("ac_not_complex", "AC loop response must contain real and imaginary values")], "metrics": {}}
    magnitudes = [abs(value) for value in complex_values]
    wrapped_phases = [math.degrees(math.atan2(value.imag, value.real)) for value in complex_values]
    phases = _unwrap_phase_degrees(wrapped_phases)
    crossing_index = next((index for index in range(1, len(magnitudes)) if magnitudes[index - 1] >= 1.0 > magnitudes[index]), None)
    if crossing_index is None:
        errors.append(_diagnostic("zero_db_crossing_missing", "AC loop response has no descending 0 dB crossing"))
        return {"valid": False, "errors": errors, "metrics": {}}
    index = crossing_index
    left_mag, right_mag = magnitudes[index - 1], magnitudes[index]
    fraction = (left_mag - 1.0) / max(left_mag - right_mag, 1e-30)
    log_left = math.log10(float(frequency[index - 1]))
    log_right = math.log10(float(frequency[index]))
    crossover = 10 ** (log_left + fraction * (log_right - log_left))
    phase = phases[index - 1] + fraction * (phases[index] - phases[index - 1])
    phase_margin = 180.0 + phase
    gain_margin_db: float | None = None
    phase_crossing_frequency: float | None = None
    phase_crossing = next((i for i in range(1, len(phases)) if phases[i - 1] > -180.0 >= phases[i]), None)
    if phase_crossing is not None:
        left_phase, right_phase = phases[phase_crossing - 1], phases[phase_crossing]
        phase_fraction = (left_phase + 180.0) / max(left_phase - right_phase, 1e-30)
        phase_log_left = math.log10(float(frequency[phase_crossing - 1]))
        phase_log_right = math.log10(float(frequency[phase_crossing]))
        phase_crossing_frequency = 10 ** (phase_log_left + phase_fraction * (phase_log_right - phase_log_left))
        magnitude_db = [20.0 * math.log10(max(value, 1e-30)) for value in magnitudes]
        magnitude_at_phase_db = magnitude_db[phase_crossing - 1] + phase_fraction * (
            magnitude_db[phase_crossing] - magnitude_db[phase_crossing - 1]
        )
        gain_margin_db = -magnitude_at_phase_db
    return {
        "valid": phase_margin > 0.0,
        "errors": [] if phase_margin > 0.0 else [_diagnostic("phase_margin_nonpositive", "Interpolated phase margin is not positive", value=phase_margin)],
        "metrics": {
            "crossover_frequency_hz": crossover,
            "phase_margin_deg": phase_margin,
            "gain_margin_db": gain_margin_db,
            "phase_crossing_frequency_hz": phase_crossing_frequency,
            "gain_margin_unbounded_in_sweep": phase_crossing is None,
        },
    }


def write_validation_charts(
    vectors: Mapping[str, Sequence[float]],
    validation: Mapping[str, Any],
    output_dir: Path,
    spec: Mapping[str, Any] | None = None,
    name: str = "buck",
) -> list[str]:
    """Write compact, reviewable switching and regulation charts."""

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    time = vectors["time"]
    spec = dict(spec or {})
    outputs: list[str] = []

    fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)
    axes[0].plot(time, vectors["VOUT"], label="VOUT")
    axes[0].plot(time, vectors["RIPPLE"], label="RIPPLE", alpha=0.7)
    if "vout_target" in spec:
        axes[0].axhline(float(spec["vout_target"]), color="black", linestyle="--", linewidth=0.8, label="VOUT target")
    axes[0].legend(loc="best")
    axes[0].set_ylabel("Voltage (V)")
    axes[1].plot(time, vectors["PWM_HS"], label="PWM_HS")
    axes[1].plot(time, vectors["PWM_LS"], label="PWM_LS", alpha=0.8)
    reset_name = str(spec.get("reset_vector", "RESET"))
    if reset_name in vectors:
        axes[1].plot(time, vectors[reset_name], label=reset_name, alpha=0.7)
    axes[1].legend(loc="best")
    axes[1].set_ylabel("Logic (V)")
    axes[2].plot(time, vectors["IL"], label="IL")
    axes[2].plot(time, vectors["ILOAD"], label="ILOAD")
    axes[2].legend(loc="best")
    axes[2].set_ylabel("Current (A)")
    axes[2].set_xlabel("Time (s)")
    for marker, label in ((spec.get("steady_window_start_s"), "steady window"), (spec.get("load_step_time_s"), "load step")):
        if marker is not None:
            for axis in axes:
                axis.axvline(float(marker), linestyle=":" if label == "steady window" else "--", linewidth=0.9, label=label if axis is axes[0] else None)
    edge_times = _rising_edges([float(value) for value in time], [float(value) for value in vectors["PWM_HS"]], _logic_threshold(vectors["PWM_HS"]))
    for edge in edge_times[:20]:
        axes[1].axvline(edge, color="grey", linewidth=0.35, alpha=0.35)
    metrics = validation.get("metrics", {})
    fig.suptitle(
        "Buck validation: "
        + ("PASS" if validation.get("valid") else "FAIL")
        + f" | min deadtime={metrics.get('minimum_deadtime_s', 'n/a')} s"
    )
    fig.tight_layout()
    path = root / f"{name}-validation.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    outputs.append(str(path))

    if len(edge_times) >= 2:
        window_start = edge_times[-min(4, len(edge_times))]
        detail_indices = [index for index, moment in enumerate(time) if float(moment) >= window_start]
        if detail_indices:
            fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
            detail_time = [time[index] for index in detail_indices]
            axes[0].plot(detail_time, [vectors["PWM_HS"][index] for index in detail_indices], label="PWM_HS")
            axes[0].plot(detail_time, [vectors["PWM_LS"][index] for index in detail_indices], label="PWM_LS")
            if reset_name in vectors:
                axes[0].plot(detail_time, [vectors[reset_name][index] for index in detail_indices], label=reset_name)
            axes[1].plot(detail_time, [vectors["SW"][index] for index in detail_indices], label="SW")
            axes[1].plot(detail_time, [vectors["RIPPLE"][index] for index in detail_indices], label="RIPPLE")
            for axis in axes:
                axis.legend(loc="best")
            axes[1].set_xlabel("Time (s)")
            fig.suptitle("Switching edge, deadtime, RESET and ripple detail")
            fig.tight_layout()
            detail_path = root / f"{name}-switching-detail.png"
            fig.savefig(detail_path, dpi=160)
            plt.close(fig)
            outputs.append(str(detail_path))

    if spec.get("load_step_time_s") is not None:
        step_time = float(spec["load_step_time_s"])
        period = 1.0 / max(float(spec.get("fsw_target", 1.0)), 1e-30)
        indices = [index for index, moment in enumerate(time) if step_time - 5 * period <= float(moment) <= step_time + 25 * period]
        if indices:
            fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
            step_axis = [time[index] for index in indices]
            axes[0].plot(step_axis, [vectors["VOUT"][index] for index in indices], label="VOUT")
            axes[0].axhline(float(spec.get("vout_target", 0.0)), color="black", linestyle="--", linewidth=0.8)
            axes[1].plot(step_axis, [vectors["ILOAD"][index] for index in indices], label="ILOAD")
            for axis in axes:
                axis.axvline(step_time, color="red", linestyle="--", linewidth=0.9)
                axis.legend(loc="best")
            axes[1].set_xlabel("Time (s)")
            fig.suptitle("Declared load-step direction and recovery window")
            fig.tight_layout()
            step_path = root / f"{name}-load-step.png"
            fig.savefig(step_path, dpi=160)
            plt.close(fig)
            outputs.append(str(step_path))
    return outputs


def write_ac_chart(
    frequency: Sequence[float],
    response: Sequence[Mapping[str, float]],
    validation: Mapping[str, Any],
    output_dir: Path,
) -> str:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    values = [complex(float(item["real"]), float(item["imag"])) for item in response]
    magnitude = [20.0 * math.log10(max(abs(item), 1e-30)) for item in values]
    phase = _unwrap_phase_degrees([math.degrees(math.atan2(item.imag, item.real)) for item in values])
    fig, axes = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
    axes[0].semilogx(frequency, magnitude)
    axes[0].axhline(0.0, color="black", linestyle="--", linewidth=0.8)
    axes[0].set_ylabel("Gain (dB)")
    axes[1].semilogx(frequency, phase)
    axes[1].axhline(-180.0, color="black", linestyle="--", linewidth=0.8)
    axes[1].set_ylabel("Phase (deg)")
    axes[1].set_xlabel("Frequency (Hz)")
    metrics = validation.get("metrics", {})
    fig.suptitle(f"Loop validation: {'PASS' if validation.get('valid') else 'FAIL'} | PM={metrics.get('phase_margin_deg', 'n/a')} | GM={metrics.get('gain_margin_db', 'n/a')}")
    fig.tight_layout()
    path = root / "ac-loop-validation.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return str(path)


def write_catalog_proof_chart(
    vectors: Mapping[str, Sequence[Any]],
    validation: Mapping[str, Any],
    output_dir: Path,
    *,
    behavior: str,
) -> str:
    """Write one compact chart for a catalog-only behavior fixture."""

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    verdict = "PASS" if validation.get("valid") else "FAIL"
    if behavior == "catalog_analog_primitives":
        fig, axes = plt.subplots(4, 1, figsize=(10, 10), sharex=True)
        axes[0].plot(vectors["time"], vectors["CTRL"], label="CTRL")
        axes[0].plot(vectors["time"], vectors["VCVS_OUT"], label="VCVS_OUT")
        axes[0].legend(loc="best")
        axes[1].plot(vectors["time"], vectors["CTRL"], label="CTRL")
        axes[1].plot(vectors["time"], vectors["VCCS_OUT"], label="VCCS_OUT")
        axes[1].legend(loc="best")
        axes[2].plot(vectors["time"], vectors["DIODE_IN"], label="DIODE_IN")
        axes[2].plot(vectors["time"], vectors["DIODE_OUT"], label="DIODE_OUT")
        axes[2].legend(loc="best")
        axes[3].plot(vectors["time"], vectors["AC_IN_DC"], label="AC_IN (transient)")
        axes[3].plot(vectors["time"], vectors["AC_OUT_DC"], label="AC_OUT (transient)")
        axes[3].legend(loc="best")
        axes[3].set_xlabel("Time (s)")
        fig.suptitle(f"Catalog analog primitives: {verdict}")
        filename = "catalog-analog-primitives-validation.png"
    elif behavior == "catalog_ac_probe":
        frequency = [float(value) for value in vectors["frequency"]]
        response = [complex(float(item["real"]), float(item["imag"])) for item in vectors["response"]]  # type: ignore[index]
        magnitude = [20.0 * math.log10(max(abs(value), 1e-30)) for value in response]
        phase = [math.degrees(math.atan2(value.imag, value.real)) for value in response]
        fig, axes = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
        axes[0].semilogx(frequency, magnitude, label="AC_OUT / AC_IN")
        axes[0].legend(loc="best")
        axes[0].set_ylabel("Gain (dB)")
        axes[1].semilogx(frequency, phase)
        axes[1].set_ylabel("Phase (deg)")
        axes[1].set_xlabel("Frequency (Hz)")
        fig.suptitle(f"Catalog AC source and Bode probe: {verdict}")
        filename = "catalog-ac-probe-validation.png"
    elif behavior == "catalog_and2":
        fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
        axes[0].plot(vectors["time"], vectors["A"], label="A")
        axes[0].plot(vectors["time"], vectors["B"], label="B")
        axes[0].legend(loc="best")
        axes[1].plot(vectors["time"], vectors["OUT"], label="OUT")
        axes[1].plot(vectors["time"], vectors["OUT_BAR"], label="OUT_BAR")
        axes[1].legend(loc="best")
        axes[1].set_xlabel("Time (s)")
        fig.suptitle(f"Catalog DIGI1 AND2 truth table: {verdict}")
        filename = "catalog-and2-validation.png"
    elif behavior == "catalog_reactive_ic":
        fig, axes = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
        axes[0].plot(vectors["time"], vectors["CAP_V"], label="V(CAP_IC)")
        axes[0].axhline(0.0, color="black", linestyle="--", linewidth=0.8)
        axes[0].set_ylabel("Capacitor voltage (V)")
        axes[0].legend(loc="best")
        axes[1].plot(vectors["time"], vectors["IND_I"], label="I(L_IC), P→N")
        axes[1].axhline(0.0, color="black", linestyle="--", linewidth=0.8)
        axes[1].set_ylabel("Inductor current (A)")
        axes[1].set_xlabel("Time (s)")
        axes[1].legend(loc="best")
        fig.suptitle(f"Catalog reactive initial conditions: {verdict}")
        filename = "catalog-reactive-ic-validation.png"
    else:
        raise ValueError(f"Unsupported catalog proof behavior: {behavior}")
    fig.tight_layout()
    path = root / filename
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path)


def validate_sr_latch_waveforms(
    vectors: Mapping[str, Sequence[float]],
    threshold: float = 2.5,
    spec: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    required = ("time", "S", "R", "Q", "QN")
    missing = [name for name in required if name not in vectors]
    if missing:
        return {"valid": False, "errors": [_diagnostic("required_vector_missing", "S/R latch vector is missing", vectors=missing)], "metrics": {}}
    if len({len(vectors[name]) for name in required}) != 1:
        return {"valid": False, "errors": [_diagnostic("vector_length_mismatch", "S/R latch vectors have inconsistent lengths")], "metrics": {}}
    s = [float(value) for value in vectors["S"]]
    r = [float(value) for value in vectors["R"]]
    q = [float(value) for value in vectors["Q"]]
    qn = [float(value) for value in vectors["QN"]]
    errors: list[dict[str, Any]] = []
    if _p2p(s) < 2 or _p2p(r) < 2:
        errors.append(_diagnostic("latch_stimulus_missing", "Set and reset inputs must both toggle"))
    # A node used only by DIGITAL1 devices is exported as logical 0/1, while
    # the same output loaded by an analog-domain device is exported at VOL/VOH
    # (typically 0/5 V). Accept both representations without weakening the
    # requirement that each output visits two distinct states.
    if _p2p(q) < 0.5 or _p2p(qn) < 0.5:
        errors.append(_diagnostic("latch_output_stuck", "Latch outputs do not visit both logic states"))
    thresholds = [_logic_threshold(values, threshold) for values in (s, r, q, qn)]
    active = [
        (sv > thresholds[0], rv > thresholds[1], qv > thresholds[2], qnv > thresholds[3])
        for sv, rv, qv, qnv in zip(s, r, q, qn)
    ]
    expected_initial = (spec or {}).get("initial_q")
    initial_q = int(active[0][2]) if active else None
    if expected_initial is not None and initial_q != int(expected_initial):
        errors.append(_diagnostic("latch_initial_state_failed", "Q initial state disagrees with the declared IC contract", measured=initial_q, expected=int(expected_initial)))
    complement_violations = sum(1 for _sv, _rv, qv, qnv in active if qv == qnv)
    if complement_violations > max(2, len(active) // 100):
        errors.append(_diagnostic("latch_outputs_not_complementary", "Q and QN are not complementary", samples=complement_violations))
    set_samples = [qv for sv, rv, qv, _qnv in active if sv and not rv]
    reset_samples = [qv for sv, rv, qv, _qnv in active if rv and not sv]
    if not set_samples or sum(set_samples) / len(set_samples) < 0.8:
        errors.append(_diagnostic("latch_set_failed", "Q does not assert during set stimulus"))
    if not reset_samples or sum(reset_samples) / len(reset_samples) > 0.2:
        errors.append(_diagnostic("latch_reset_failed", "Q does not clear during reset stimulus"))
    return {
        "valid": not errors,
        "errors": errors,
        "metrics": {
            "initial_q": initial_q,
            "q_peak_to_peak": _p2p(q),
            "qn_peak_to_peak": _p2p(qn),
            "complement_violation_samples": complement_violations,
        },
    }


def validate_timing_primitives_waveforms(vectors: Mapping[str, Sequence[float]], spec: Mapping[str, Any]) -> dict[str, Any]:
    required = ("time", "PWM", "TON_OUT", "BUF", "BUF_BAR", "PWM_HS", "PWM_LS", "RAMP", "DSCH")
    missing = [name for name in required if name not in vectors]
    if missing:
        return {"valid": False, "errors": [_diagnostic("required_vector_missing", "Timing primitive vector is missing", vectors=missing)], "metrics": {}}
    lengths = {name: len(vectors[name]) for name in required}
    if len(set(lengths.values())) != 1 or next(iter(lengths.values())) < 8:
        return {"valid": False, "errors": [_diagnostic("vector_length_mismatch", "Timing primitive vectors have inconsistent or insufficient samples", lengths=lengths)], "metrics": {}}

    time = [float(value) for value in vectors["time"]]
    waves = {name: [float(value) for value in vectors[name]] for name in required if name != "time"}
    errors: list[dict[str, Any]] = []
    thresholds = {name: _logic_threshold(waves[name]) for name in ("PWM", "TON_OUT", "BUF", "BUF_BAR", "PWM_HS", "PWM_LS")}
    edges = {name: _rising_edges(time, waves[name], thresholds[name]) for name in thresholds}
    falls = {name: _falling_edges(time, waves[name], thresholds[name]) for name in thresholds}
    if len(edges["PWM"]) < 2:
        errors.append(_diagnostic("timing_stimulus_missing", "Timing stimulus has too few rising edges"))
    for name in ("TON_OUT", "BUF", "BUF_BAR", "PWM_HS", "PWM_LS"):
        if _p2p(waves[name]) < 0.5 or len(edges[name]) < 2:
            errors.append(_diagnostic("timing_output_stuck", "Timing output is constant or incomplete", vector=name))

    ton_widths = [fall - rise for rise in edges["TON_OUT"] if (fall := _next_after(falls["TON_OUT"], rise)) is not None and fall > rise]
    ton_mean = mean(ton_widths) if ton_widths else 0.0
    ton_target = float(spec.get("ton_s", ton_mean))
    ton_tolerance = float(spec.get("ton_tolerance_pct", 5.0)) / 100.0
    if not ton_widths or ton_target and abs(ton_mean - ton_target) / ton_target > ton_tolerance:
        errors.append(_diagnostic("oneshot_duration_failed", "One-shot high time is outside tolerance", measured=ton_mean, target=ton_target))

    complement_violations = sum(
        1
        for direct, inverse in zip(waves["BUF"], waves["BUF_BAR"])
        if (direct > thresholds["BUF"]) == (inverse > thresholds["BUF_BAR"])
    )
    if complement_violations > max(2, len(time) // 100):
        errors.append(_diagnostic("buffer_not_complementary", "Buffer direct and inverse outputs are not complementary", samples=complement_violations))
    overlap = sum(
        1
        for hs, ls in zip(waves["PWM_HS"], waves["PWM_LS"])
        if hs > thresholds["PWM_HS"] and ls > thresholds["PWM_LS"]
    )
    if overlap:
        errors.append(_diagnostic("deadtime_overlap", "Delayed high-side and low-side outputs overlap", samples=overlap))

    hs_delays = [edge - source for source in edges["BUF"] if (edge := _next_after(edges["PWM_HS"], source)) is not None]
    ls_delays = [edge - source for source in edges["BUF_BAR"] if (edge := _next_after(edges["PWM_LS"], source)) is not None]
    expected_deadtime = float(spec.get("deadtime_s", 0.0))
    deadtime_tolerance = float(spec.get("deadtime_tolerance_pct", 5.0)) / 100.0
    for name, delays in (("PWM_HS", hs_delays), ("PWM_LS", ls_delays)):
        measured = mean(delays) if delays else 0.0
        if not delays or expected_deadtime and abs(measured - expected_deadtime) / expected_deadtime > deadtime_tolerance:
            errors.append(_diagnostic("asymmetric_delay_failed", "Asymmetric rising delay is outside tolerance", vector=name, measured=measured, target=expected_deadtime))
    if _p2p(waves["RAMP"]) <= 1e-6 or _p2p(waves["DSCH"]) <= 1e-6:
        errors.append(_diagnostic("oneshot_internal_activity_missing", "One-shot RAMP or DSCH output is flat"))

    return {
        "valid": not errors,
        "errors": errors,
        "metrics": {
            "ton_mean_s": ton_mean,
            "ton_pulse_count": len(ton_widths),
            "hs_rise_delay_s": mean(hs_delays) if hs_delays else 0.0,
            "ls_rise_delay_s": mean(ls_delays) if ls_delays else 0.0,
            "buffer_complement_violation_samples": complement_violations,
            "deadtime_overlap_samples": overlap,
            "ramp_peak_to_peak": _p2p(waves["RAMP"]),
            "dsch_peak_to_peak": _p2p(waves["DSCH"]),
        },
    }


def write_timing_primitives_chart(vectors: Mapping[str, Sequence[float]], validation: Mapping[str, Any], output_dir: Path) -> str:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)
    axes[0].plot(vectors["time"], vectors["PWM"], label="PWM")
    axes[0].plot(vectors["time"], vectors["TON_OUT"], label="TON_OUT")
    axes[1].plot(vectors["time"], vectors["BUF"], label="BUF")
    axes[1].plot(vectors["time"], vectors["BUF_BAR"], label="BUF_BAR")
    axes[1].plot(vectors["time"], vectors["PWM_HS"], label="PWM_HS", alpha=0.8)
    axes[1].plot(vectors["time"], vectors["PWM_LS"], label="PWM_LS", alpha=0.8)
    axes[2].plot(vectors["time"], vectors["RAMP"], label="RAMP")
    axes[2].plot(vectors["time"], vectors["DSCH"], label="DSCH")
    for axis in axes:
        axis.legend(loc="best")
    axes[2].set_xlabel("Time (s)")
    fig.suptitle("Timing primitives: " + ("PASS" if validation.get("valid") else "FAIL"))
    fig.tight_layout()
    path = root / "timing-primitives-validation.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return str(path)


def write_sr_latch_chart(vectors: Mapping[str, Sequence[float]], validation: Mapping[str, Any], output_dir: Path) -> str:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    axes[0].plot(vectors["time"], vectors["S"], label="S")
    axes[0].plot(vectors["time"], vectors["R"], label="R")
    axes[0].legend(loc="best")
    axes[1].plot(vectors["time"], vectors["Q"], label="Q")
    axes[1].plot(vectors["time"], vectors["QN"], label="QN")
    axes[1].legend(loc="best")
    axes[1].set_xlabel("Time (s)")
    fig.suptitle("S/R latch behavior: " + ("PASS" if validation.get("valid") else "FAIL"))
    fig.tight_layout()
    path = root / "sr-latch-validation.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return str(path)
