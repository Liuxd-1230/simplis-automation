from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from simplis_automation_v2.waveform_validation import (
    validate_ac_response,
    validate_buck_waveforms,
    validate_catalog_ac_probe,
    validate_catalog_analog_waveforms,
    validate_catalog_and2_waveforms,
    validate_catalog_reactive_ic_waveforms,
    validate_sr_latch_waveforms,
    validate_timing_primitives_waveforms,
    validate_vector,
    write_catalog_proof_chart,
    write_validation_charts,
)


def _square_wave(times: list[float], period: float, duty: float, delay: float = 0.0) -> list[float]:
    return [5.0 if ((time - delay) % period) < duty * period else 0.0 for time in times]


class WaveformValidationTests(unittest.TestCase):
    def test_catalog_analog_proof_checks_gain_sign_and_diode_regions(self) -> None:
        times = [index * 0.1e-6 for index in range(30)]
        ctrl = [0.0] * 5 + [1.0] * 10 + [-1.0] * 10 + [0.0] * 5
        diode_in = [-1.0] * 15 + [1.0] * 15
        vectors = {
            "time": times,
            "CTRL": ctrl,
            "VCVS_OUT": [2.0 * value for value in ctrl],
            "VCCS_OUT": [-2.0 * value for value in ctrl],
            "DIODE_IN": diode_in,
            "DIODE_OUT": [-1.0] * 15 + [1e-3] * 15,
            "AC_IN_DC": [0.0] * 30,
            "AC_OUT_DC": [0.0] * 30,
        }
        spec = {
            "vcvs_gain": 2.0,
            "vccs_voltage_gain": -2.0,
            "gain_tolerance_pct": 2.0,
            "diode_off_tracking_tolerance_v": 0.02,
            "diode_on_clamp_max_v": 0.02,
        }
        valid = validate_catalog_analog_waveforms(vectors, spec)
        self.assertTrue(valid["valid"], valid)
        self.assertAlmostEqual(valid["metrics"]["vcvs_gain"], 2.0)
        self.assertAlmostEqual(valid["metrics"]["vccs_voltage_gain"], -2.0)

        invalid = validate_catalog_analog_waveforms(
            vectors | {"VCCS_OUT": [2.0 * value for value in ctrl], "DIODE_OUT": [-0.7] * 15 + [0.5] * 15},
            spec,
        )
        codes = {item["code"] for item in invalid["errors"]}
        self.assertIn("vccs_gain_failed", codes)
        self.assertIn("diode_reverse_tracking_failed", codes)
        self.assertIn("diode_forward_clamp_failed", codes)
        dc_invalid = validate_catalog_analog_waveforms(vectors | {"AC_IN_DC": [0.1] * 30}, spec)
        self.assertIn("ac_injection_dc_offset", {item["code"] for item in dc_invalid["errors"]})

    def test_catalog_ac_probe_requires_complex_half_gain(self) -> None:
        frequency = [1e3, 1e4, 1e5, 1e6]
        response = [{"real": 0.5, "imag": 0.0} for _ in frequency]
        input_response = [{"real": 1.0, "imag": 0.0} for _ in frequency]
        valid = validate_catalog_ac_probe(
            frequency,
            response,
            {"expected_gain": 0.5, "gain_tolerance_pct": 2.0},
            input_response=input_response,
        )
        self.assertTrue(valid["valid"], valid)
        self.assertAlmostEqual(valid["metrics"]["mean_gain"], 0.5)
        self.assertAlmostEqual(valid["metrics"]["mean_input_magnitude"], 1.0)
        invalid = validate_catalog_ac_probe(frequency, [{"real": 0.8, "imag": 0.2} for _ in frequency], {"expected_gain": 0.5})
        self.assertFalse(invalid["valid"])
        self.assertIn("ac_probe_gain_failed", {item["code"] for item in invalid["errors"]})
        bad_input = validate_catalog_ac_probe(
            frequency,
            response,
            {"expected_gain": 0.5},
            input_response=[{"real": 2.0, "imag": 0.0} for _ in frequency],
        )
        self.assertIn("ac_source_magnitude_failed", {item["code"] for item in bad_input["errors"]})

    def test_catalog_and2_proof_covers_truth_table_and_complement(self) -> None:
        times = [index * 0.1e-6 for index in range(40)]
        states = [(False, False)] * 10 + [(True, False)] * 10 + [(True, True)] * 10 + [(False, True)] * 10
        a = [5.0 if left else 0.0 for left, _right in states]
        b = [5.0 if right else 0.0 for _left, right in states]
        out = [5.0 if left and right else 0.0 for left, right in states]
        out_bar = [0.0 if value else 5.0 for value in out]
        vectors = {"time": times, "A": a, "B": b, "OUT": out, "OUT_BAR": out_bar}
        valid = validate_catalog_and2_waveforms(vectors, {"logic_threshold": 2.5, "max_mismatch_ratio": 0.02})
        self.assertTrue(valid["valid"], valid)
        self.assertEqual(valid["metrics"]["truth_table_states_covered"], 4)

        broken = list(out)
        broken[22] = 0.0
        invalid = validate_catalog_and2_waveforms(vectors | {"OUT": broken}, {"logic_threshold": 2.5, "max_mismatch_ratio": 0.0})
        self.assertFalse(invalid["valid"])
        self.assertIn("and2_truth_table_failed", {item["code"] for item in invalid["errors"]})

    def test_catalog_reactive_ic_proof_checks_value_sign_and_decay(self) -> None:
        times = [index * 50e-6 for index in range(101)]
        capacitor = [1.2 * math.exp(-time / 1e-3) for time in times]
        inductor = [2.0 * math.exp(-time / 1e-3) for time in times]
        spec = {
            "capacitor_initial_v": 1.2,
            "inductor_initial_a": 2.0,
            "capacitor_tau_s": 1e-3,
            "inductor_tau_s": 1e-3,
            "initial_tolerance_pct": 2,
            "tau_tolerance_pct": 5,
            "final_fraction_max": 0.01,
        }
        valid = validate_catalog_reactive_ic_waveforms(
            {"time": times, "CAP_V": capacitor, "IND_I": inductor},
            spec,
        )
        self.assertTrue(valid["valid"], valid)
        self.assertAlmostEqual(valid["metrics"]["capacitor_initial_v"], 1.2)
        self.assertAlmostEqual(valid["metrics"]["inductor_initial_a"], 2.0)

        invalid = validate_catalog_reactive_ic_waveforms(
            {"time": times, "CAP_V": [1.2] * len(times), "IND_I": [-value for value in inductor]},
            spec,
        )
        codes = {item["code"] for item in invalid["errors"]}
        self.assertIn("capacitor_ic_decay_incomplete", codes)
        self.assertIn("capacitor_ic_tau_failed", codes)
        self.assertIn("inductor_ic_sign_failed", codes)

    def test_catalog_proof_chart_is_written_for_each_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            analog = write_catalog_proof_chart(
                {
                    "time": [0.0, 1.0],
                    "CTRL": [0.0, 1.0],
                    "VCVS_OUT": [0.0, 2.0],
                    "VCCS_OUT": [0.0, -2.0],
                    "DIODE_IN": [-1.0, 1.0],
                    "DIODE_OUT": [-1.0, 0.0],
                    "AC_IN_DC": [0.0, 0.0],
                    "AC_OUT_DC": [0.0, 0.0],
                },
                {"valid": True, "metrics": {}},
                Path(tmp),
                behavior="catalog_analog_primitives",
            )
            ac = write_catalog_proof_chart(
                {"frequency": [1e3, 1e4], "response": [{"real": 0.5, "imag": 0.0}] * 2},
                {"valid": True, "metrics": {}},
                Path(tmp),
                behavior="catalog_ac_probe",
            )
            and2 = write_catalog_proof_chart(
                {"time": [0.0, 1.0], "A": [0.0, 5.0], "B": [0.0, 5.0], "OUT": [0.0, 5.0], "OUT_BAR": [5.0, 0.0]},
                {"valid": True, "metrics": {}},
                Path(tmp),
                behavior="catalog_and2",
            )
            reactive = write_catalog_proof_chart(
                {"time": [0.0, 1.0], "CAP_V": [1.2, 0.0], "IND_I": [2.0, 0.0]},
                {"valid": True, "metrics": {}},
                Path(tmp),
                behavior="catalog_reactive_ic",
            )
            self.assertTrue(all(Path(path).is_file() for path in (analog, ac, and2, reactive)))

    def test_timing_primitives_require_oneshot_complement_and_deadtime(self) -> None:
        times = [index * 10e-9 for index in range(600)]
        pwm = _square_wave(times, 2e-6, 0.2, 1e-6)
        ton = [5.0 if any(edge <= time < edge + 200e-9 for edge in (1e-6, 3e-6, 5e-6)) else 0.0 for time in times]
        buf = [1.0 if value > 2.5 else 0.0 for value in pwm]
        buf_bar = [1.0 - value for value in buf]
        hs = [1.0 if any(edge + 20e-9 <= time < edge + 400e-9 for edge in (1e-6, 3e-6, 5e-6)) else 0.0 for time in times]
        ls = [1.0 if any(edge + 420e-9 <= time < edge + 2e-6 for edge in (1e-6, 3e-6)) else 0.0 for time in times]
        vectors = {
            "time": times,
            "PWM": pwm,
            "TON_OUT": ton,
            "BUF": buf,
            "BUF_BAR": buf_bar,
            "PWM_HS": hs,
            "PWM_LS": ls,
            "RAMP": [(time % 200e-9) / 200e-9 for time in times],
            "DSCH": [1.0 if value else 0.0 for value in ton],
        }
        result = validate_timing_primitives_waveforms(vectors, {"ton_s": 200e-9, "deadtime_s": 20e-9, "ton_tolerance_pct": 10, "deadtime_tolerance_pct": 30})
        self.assertTrue(result["valid"], result)

    def test_vector_rejects_stale_wrong_analysis_partial_and_nonfinite_data(self) -> None:
        base = {
            "x": [0.0, 1.0, 2.0],
            "y": [0.0, 1.0, 0.0],
            "analysis": "tran",
            "source_hash": "abc",
            "created_at": 20.0,
        }
        stale = validate_vector(base, expected_analysis="tran", expected_source_hash="abc", run_started_at=21.0, min_samples=3)
        self.assertFalse(stale["valid"])
        wrong = validate_vector(base | {"created_at": 22.0}, expected_analysis="ac", expected_source_hash="abc", run_started_at=21.0, min_samples=3)
        self.assertFalse(wrong["valid"])
        partial = validate_vector(base | {"created_at": 22.0}, expected_analysis="tran", expected_source_hash="abc", run_started_at=21.0, min_samples=4)
        self.assertFalse(partial["valid"])
        bad = validate_vector(base | {"created_at": 22.0, "y": [0.0, math.nan, 0.0]}, expected_analysis="tran", expected_source_hash="abc", run_started_at=21.0, min_samples=3)
        self.assertFalse(bad["valid"])

    def test_buck_rejects_constant_pwm_and_shoot_through_even_with_numeric_waveforms(self) -> None:
        times = [index * 20e-9 for index in range(1000)]
        vectors = {
            "time": times,
            "VOUT": [1.2 for _ in times],
            "VIN": [12.0 for _ in times],
            "PWM_HS": [5.0 for _ in times],
            "PWM_LS": [5.0 for _ in times],
            "SW": [12.0 for _ in times],
            "IL": [7.5 for _ in times],
            "ILOAD": [7.5 for _ in times],
            "ICOUT": [0.0 for _ in times],
            "RIPPLE": [0.02 for _ in times],
        }
        result = validate_buck_waveforms(vectors, {"vout_target": 1.2, "vin": 12.0, "fsw_target": 500e3, "deadtime_min": 10e-9})
        self.assertFalse(result["valid"])
        codes = {item["code"] for item in result["errors"]}
        self.assertIn("pwm_hs_stuck", codes)
        self.assertIn("shoot_through", codes)

    def test_valid_switching_waveforms_produce_metrics_and_charts(self) -> None:
        step = 20e-9
        times = [index * step for index in range(2000)]
        period = 2e-6
        hs = _square_wave(times, period, 0.1)
        ls = [5.0 if 0.3e-6 <= (time % period) < 1.9e-6 else 0.0 for time in times]
        sw = [12.0 if value > 2.5 else 0.0 for value in hs]
        ripple = [0.02 * math.sin(2 * math.pi * time / period) for time in times]
        il = [7.5 + 0.5 * math.sin(2 * math.pi * time / period) for time in times]
        vectors = {
            "time": times,
            "VOUT": [1.2 + 0.004 * math.sin(2 * math.pi * time / period) for time in times],
            "VIN": [12.0 for _ in times],
            "PWM_HS": hs,
            "PWM_LS": ls,
            "SW": sw,
            "IL": il,
            "ILOAD": [7.5 for _ in times],
            "ICOUT": [value - 7.5 for value in il],
            "RIPPLE": ripple,
        }
        result = validate_buck_waveforms(vectors, {"vout_target": 1.2, "vin": 12.0, "fsw_target": 500e3, "deadtime_min": 10e-9})
        self.assertTrue(result["valid"], result)
        self.assertLess(result["metrics"]["vout_dc_error_pct"], 1.0)
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_validation_charts(vectors, result, Path(tmp))
            self.assertTrue(paths)
            self.assertTrue(all(Path(path).is_file() for path in paths))

    def test_buck_power_uses_time_weighting_and_rejects_low_ratio(self) -> None:
        period = 2e-6
        cycle_states = [(0.0, False, False), (0.1e-6, False, False), (0.1e-6, True, False)]
        cycle_states.extend((0.1e-6 + index * 0.01e-6, True, False) for index in range(1, 20))
        cycle_states.extend(
            [
                (0.3e-6, True, False),
                (0.3e-6, False, False),
                (0.32e-6, False, False),
                (0.32e-6, False, True),
                (1.9e-6, False, True),
                (1.9e-6, False, False),
                (period, False, False),
            ]
        )
        samples = [
            (cycle * period + phase, high_side, low_side)
            for cycle in range(8)
            for phase, high_side, low_side in cycle_states
        ]
        times = [sample[0] for sample in samples]
        hs = [5.0 if sample[1] else 0.0 for sample in samples]
        ls = [5.0 if sample[2] else 0.0 for sample in samples]
        il = [10.2 if sample[1] else 9.977777777777778 for sample in samples]
        vectors = {
            "time": times,
            "VOUT": [1.2 for _sample in samples],
            "VIN": [12.0 for _sample in samples],
            "PWM_HS": hs,
            "PWM_LS": ls,
            "SW": [12.0 if sample[1] else 0.0 for sample in samples],
            "IL": il,
            "ILOAD": [10.0 for _sample in samples],
            "ICOUT": [value - 10.0 for value in il],
            "RIPPLE": [0.002 if sample[1] else -0.00022222222222222223 for sample in samples],
            "IIN": [10.0 if sample[1] else 0.0 for sample in samples],
        }
        spec = {
            "vout_target": 1.2,
            "vin": 12.0,
            "fsw_target": 500e3,
            "deadtime_min_s": 10e-9,
            "min_power_ratio": 0.8,
        }

        result = validate_buck_waveforms(vectors, spec)
        self.assertTrue(result["valid"], result)
        self.assertAlmostEqual(result["metrics"]["input_power_w"], 12.0, places=6)
        self.assertAlmostEqual(result["metrics"]["output_power_w"], 12.0, places=6)
        self.assertAlmostEqual(result["metrics"]["power_ratio"], 1.0, places=6)

        inefficient = validate_buck_waveforms(
            vectors | {"IIN": [20.0 if sample[1] else 0.0 for sample in samples]},
            spec,
        )
        self.assertFalse(inefficient["valid"])
        self.assertIn("power_balance_invalid", {item["code"] for item in inefficient["errors"]})

    def test_buck_startup_checks_reject_stale_high_output_overshoot_and_reverse_current(self) -> None:
        step = 20e-9
        times = [index * step for index in range(2000)]
        period = 2e-6
        hs = _square_wave(times, period, 0.1)
        ls = [5.0 if 0.3e-6 <= (time % period) < 1.9e-6 else 0.0 for time in times]
        il = [7.5 + 0.5 * math.sin(2 * math.pi * time / period) for time in times]
        il[0] = -1.0
        vout = [1.2 for _ in times]
        vout[0] = 12.0
        vout[100] = 1.3
        vectors = {
            "time": times,
            "VOUT": vout,
            "VIN": [12.0 for _ in times],
            "PWM_HS": hs,
            "PWM_LS": ls,
            "SW": [12.0 if value > 2.5 else 0.0 for value in hs],
            "IL": il,
            "ILOAD": [7.5 for _ in times],
            "ICOUT": [value - 7.5 for value in il],
            "RIPPLE": [0.02 * math.sin(2 * math.pi * time / period) for time in times],
        }
        result = validate_buck_waveforms(
            vectors,
            {
                "vout_target": 1.2,
                "vin": 12.0,
                "fsw_target": 500e3,
                "steady_window_start_s": 20e-6,
                "startup_window_end_s": 10e-6,
                "initial_vout_limit_pct": 5.0,
                "startup_overshoot_limit_pct": 5.0,
                "startup_inductor_min_a": 0.0,
            },
        )
        codes = {item["code"] for item in result["errors"]}
        self.assertIn("startup_initial_vout_invalid", codes)
        self.assertIn("startup_overshoot_excessive", codes)
        self.assertIn("startup_inductor_current_reversed", codes)
        self.assertAlmostEqual(result["metrics"]["startup_vout_peak"], 12.0)
        self.assertAlmostEqual(result["metrics"]["startup_inductor_min_a"], -1.0)

    def test_ac_response_requires_complex_data_and_zero_db_crossing(self) -> None:
        no_cross = validate_ac_response(
            [1.0, 10.0, 100.0],
            [{"real": 0.1, "imag": 0.0}] * 3,
        )
        self.assertFalse(no_cross["valid"])
        response = validate_ac_response(
            [1.0, 10.0, 100.0, 1000.0],
            [
                {"real": 10.0, "imag": 0.0},
                {"real": 2.0, "imag": -0.5},
                {"real": 0.5, "imag": -0.5},
                {"real": 0.05, "imag": -0.1},
            ],
        )
        self.assertTrue(response["valid"], response)
        self.assertGreater(response["metrics"]["phase_margin_deg"], 0.0)
        self.assertTrue(response["metrics"]["gain_margin_unbounded_in_sweep"])

        wrapped = validate_ac_response(
            [1.0, 10.0, 100.0, 1000.0, 10000.0],
            [
                {"real": 0.0, "imag": -10.0},
                {"real": -1.0, "imag": -1.7320508075688772},
                {"real": -0.4330127018922193, "imag": -0.25},
                {"real": -0.0984807753012208, "imag": 0.017364817766693033},
                {"real": -0.008660254037844387, "imag": 0.005},
            ],
        )
        self.assertTrue(wrapped["valid"], wrapped)
        self.assertFalse(wrapped["metrics"]["gain_margin_unbounded_in_sweep"])
        self.assertGreater(wrapped["metrics"]["gain_margin_db"], 6.0)
        self.assertIsNotNone(wrapped["metrics"]["phase_crossing_frequency_hz"])

        nonmonotonic = validate_ac_response(
            [1.0, 100.0, 10.0],
            [{"real": 2.0, "imag": 0.0}, {"real": 0.5, "imag": 0.0}, {"real": 0.1, "imag": 0.0}],
        )
        self.assertIn("ac_frequency_invalid", {item["code"] for item in nonmonotonic["errors"]})

    def test_sr_latch_requires_complementary_set_and_reset_behavior(self) -> None:
        vectors = {
            "time": [0, 1, 2, 3, 4, 5],
            "S": [0, 5, 0, 0, 0, 0],
            "R": [0, 0, 0, 5, 0, 0],
            "Q": [0, 5, 5, 0, 0, 0],
            "QN": [5, 0, 0, 5, 5, 5],
        }
        valid = validate_sr_latch_waveforms(vectors)
        self.assertTrue(valid["valid"], valid)
        self.assertTrue(validate_sr_latch_waveforms(vectors, spec={"initial_q": 0})["valid"])
        wrong_initial = validate_sr_latch_waveforms(vectors, spec={"initial_q": 1})
        self.assertIn("latch_initial_state_failed", {item["code"] for item in wrong_initial["errors"]})
        invalid = validate_sr_latch_waveforms(vectors | {"QN": vectors["Q"]})
        self.assertFalse(invalid["valid"])
        self.assertIn("latch_outputs_not_complementary", {item["code"] for item in invalid["errors"]})

    def test_buck_rejects_missing_deadtime_and_fake_periodic_steady_state(self) -> None:
        step = 20e-9
        times = [index * step for index in range(1200)]
        period = 2e-6
        hs = _square_wave(times, period, 0.1)
        # LS rises immediately when HS falls: no requested deadtime.
        ls = [5.0 if 0.2e-6 <= (time % period) < 1.9e-6 else 0.0 for time in times]
        vectors = {
            "time": times,
            "VOUT": [1.18 + 1000.0 * time for time in times],
            "VIN": [12.0 for _ in times],
            "PWM_HS": hs,
            "PWM_LS": ls,
            "SW": [12.0 if value > 2.5 else 0.0 for value in hs],
            "IL": [7.5 + 0.2 * math.sin(2 * math.pi * time / period) for time in times],
            "ILOAD": [7.5 for _ in times],
            "ICOUT": [0.0 for _ in times],
            "RIPPLE": [0.02 * math.sin(2 * math.pi * time / period) for time in times],
        }
        result = validate_buck_waveforms(
            vectors,
            {"vout_target": 1.2, "vin": 12.0, "fsw_target": 500e3, "deadtime_min_s": 10e-9, "pop_cycle_delta_limit_pct": 0.01},
        )
        codes = {item["code"] for item in result["errors"]}
        self.assertIn("deadtime_too_short", codes)
        self.assertIn("periodic_steady_state_failed", codes)

    def test_buck_requires_one_reset_per_cycle_and_edge_synchronous_ripple(self) -> None:
        step = 20e-9
        times = [index * step for index in range(1400)]
        period = 2e-6
        hs = _square_wave(times, period, 0.1)
        ls = [5.0 if 0.3e-6 <= (time % period) < 1.9e-6 else 0.0 for time in times]
        reset = [5.0 if 0.2e-6 <= (time % period) < 0.24e-6 else 0.0 for time in times]
        ripple = [0.02 * math.sin(2 * math.pi * time / period) for time in times]
        il = [7.5 + 0.3 * math.sin(2 * math.pi * time / period) for time in times]
        vectors = {
            "time": times,
            "VOUT": [1.2 for _ in times],
            "VIN": [12.0 for _ in times],
            "PWM_HS": hs,
            "PWM_LS": ls,
            "SW": [12.0 if value > 2.5 else 0.0 for value in hs],
            "IL": il,
            "ILOAD": [7.5 for _ in times],
            "ICOUT": [value - 7.5 for value in il],
            "RIPPLE": ripple,
            "RESET": reset,
        }
        spec = {
            "vout_target": 1.2,
            "vin": 12.0,
            "fsw_target": 500e3,
            "deadtime_min_s": 10e-9,
            "require_reset_once_per_cycle": True,
            "reset_vector": "RESET",
            "require_ripple_edge_sync": True,
        }
        valid = validate_buck_waveforms(vectors, spec)
        self.assertTrue(valid["valid"], valid)
        double_reset = [5.0 if (0.2e-6 <= (time % period) < 0.24e-6 or 1.0e-6 <= (time % period) < 1.04e-6) else 0.0 for time in times]
        invalid = validate_buck_waveforms(vectors | {"RESET": double_reset, "RIPPLE": [0.01 for _ in times]}, spec)
        codes = {item["code"] for item in invalid["errors"]}
        self.assertIn("reset_not_once_per_cycle", codes)
        self.assertIn("ripple_not_edge_synchronous", codes)


if __name__ == "__main__":
    unittest.main()
