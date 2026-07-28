import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import buck_opt  # noqa: E402


def minimal_paths(tmp_path: Path) -> buck_opt.RunPaths:
    return buck_opt.RunPaths(
        run_dir=tmp_path,
        work_dir=tmp_path / "work",
        schematic=tmp_path / "work" / "test.sxsch",
        compensator=None,
        script=tmp_path / "startup.sxscr",
        run_log=tmp_path / "run.log",
        sim_status=tmp_path / "sim_status.txt",
        vectors_dir=tmp_path / "vectors",
        screenshots_dir=tmp_path / "screenshots",
        metrics_json=tmp_path / "metrics.json",
        score_json=tmp_path / "score.json",
        params_json=tmp_path / "params.json",
        result_json=tmp_path / "result.json",
    )


def test_replace_instance_property_by_ref_only_leaves_symbol_defaults() -> None:
    text = """
.Symbol
Property name="REF" value="C1"
Property name="C" value="47u"
.EndSymbol
.Instance
Property name="REF" value="C2"
Property name="C" value="100u"
.EndInstance
.Instance
Property name="REF" value="C1"
Property name="C" value="220u"
.EndInstance
"""

    updated = buck_opt.replace_instance_property(text, "C1", "C", 3.3e-4)

    assert 'Property name="C" value="47u"' in updated
    assert 'Property name="C" value="100u"' in updated
    assert 'Property name="C" value="0.00033"' in updated


def test_replace_instance_value_token_only_touches_requested_token() -> None:
    text = """
.Instance
Property name="REF" value="V2"
Property name="SIMPLIS_VALUE" value="_V1=0 _V2=1 _FREQ=500k"
.EndInstance
"""

    updated = buck_opt.replace_instance_value_token(text, "V2", "SIMPLIS_VALUE", "_V2", 1.2)

    assert "_V2=1.2" in updated
    assert "_FREQ=500k" in updated


def test_parse_metrics_timeout_and_missing_metrics_returns_penalty_reason(tmp_path: Path) -> None:
    paths = minimal_paths(tmp_path)
    paths.work_dir.mkdir()
    paths.sim_status.write_text("simplis_exit_code=\nsimulation_errors=\n", encoding="utf-8")
    config = {
        "nominal_output_v": 1.2,
        "required_metrics": ["fc_hz", "phase_margin_deg"],
        "targets": {},
        "weights": {"failed": 123.0},
    }

    metrics = buck_opt.parse_metrics(config, paths, {}, {"mode": "real", "returncode": 124, "timed_out": True})
    score = buck_opt.evaluate(config, metrics)

    assert metrics["failed"] is True
    assert "timeout" in metrics["failure_reason"].lower()
    assert "missing metrics" in metrics["failure_reason"]
    assert score["score"] == 123.0


def test_validate_real_mode_requires_preplaced_bode_probe(tmp_path: Path) -> None:
    schematic = tmp_path / "user_power_stage.sxsch"
    schematic.write_text(
        "SIMetrixFile type=schematic format=1.0 revision=8\n.EndSchematic\n",
        encoding="utf-8",
    )
    config = {
        "schematic": str(schematic),
        "runs_dir": str(tmp_path / "runs"),
        "bode_probe": {"required": True},
        "required_metrics": ["fc_hz", "phase_margin_deg"],
        "parameters": {},
    }

    issues = buck_opt.validate_config(config, "real")

    assert any("Bode plot probe" in issue for issue in issues)


def test_parse_ac_loop_can_select_crossover_nearest_target(tmp_path: Path) -> None:
    ac_csv = tmp_path / "ac_loop.csv"
    ac_csv.write_text(
        "freq_hz,gain_db,phase_deg\n"
        "1000,5,-100\n"
        "4000,-1,-120\n"
        "8000,-2,-130\n"
        "10000,1,-135\n"
        "12000,-1,-140\n",
        encoding="utf-8",
    )

    metrics = buck_opt.parse_ac_loop_csv(ac_csv, target_fc_hz=10000.0, crossover_policy="nearest_target")

    assert metrics["fc_hz"] == pytest.approx(9333.333333333334)
    assert metrics["crossovers_hz"][0] == pytest.approx(3500.0)
    assert metrics["selected_crossover_policy"] == "nearest_target"


def test_convert_exported_vectors_writes_ac_and_tran_csv(tmp_path: Path) -> None:
    paths = minimal_paths(tmp_path)
    paths.vectors_dir.mkdir(parents=True)
    (paths.vectors_dir / "ac_n7.txt").write_text(
        "freq\t7\n"
        "1000\t(2,0)\n"
        "10000\t(0,2)\n",
        encoding="utf-8",
    )
    (paths.vectors_dir / "ac_n20.txt").write_text(
        "freq\t20\n"
        "1000\t(1,0)\n"
        "10000\t(1,0)\n",
        encoding="utf-8",
    )
    (paths.vectors_dir / "tran_n7.txt").write_text(
        "time\t7\n"
        "0\t1.2\n"
        "1e-6\t1.21\n",
        encoding="utf-8",
    )
    (paths.vectors_dir / "tran_n6.txt").write_text(
        "time\t6\n"
        "0\t2.5\n"
        "1e-6\t2.6\n",
        encoding="utf-8",
    )
    config = {
        "vector_export": {
            "enabled": True,
            "ac": {"group": "simplis_ac1", "output": "7", "input": "20"},
            "tran": {"group": "simplis_tran1", "vout": "7", "vc": "6"},
        }
    }

    result = buck_opt.convert_exported_vectors(config, paths)

    assert result["ac_loop_csv"] == str((tmp_path / "ac_loop.csv").resolve())
    assert result["tran_csv"] == str((tmp_path / "tran.csv").resolve())
    assert "freq_hz,gain_db,phase_deg" in (tmp_path / "ac_loop.csv").read_text(encoding="utf-8")
    assert "time_s,vout_v,vc_v" in (tmp_path / "tran.csv").read_text(encoding="utf-8")


def test_apply_best_backs_up_and_writes_only_whitelist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(buck_opt, "PROJECT_DIR", tmp_path)
    schematic = tmp_path / "buck.sxsch"
    compensator = tmp_path / "3p2zcompensator.sxcmp"
    best = tmp_path / "best_params.json"
    schematic.write_text(
        """
.Instance
Property name="REF" value="L1"
Property name="L" value="680n"
.EndInstance
.Instance
Property name="REF" value="V2"
Property name="SIMPLIS_VALUE" value="_V1=0 _V2=1 _FREQ=500k"
.EndInstance
""",
        encoding="utf-8",
    )
    compensator.write_text(
        'Text value="*** Calculated Parameters - not used in calculations\\n'
        '.VAR FLC = 1\\n'
        '*****************************************************************\\n'
        '*** Debug.\\n"',
        encoding="utf-8",
    )
    best.write_text(
        json.dumps({"params": {"Rz1": 2200.0, "Lout": 8.2e-7, "VRAMP": 1.2}}),
        encoding="utf-8",
    )
    config = {
        "schematic": str(schematic),
        "compensator": str(compensator),
        "runs_dir": str(tmp_path / "runs"),
        "parameters": {
            "Rz1": {"target": buck_opt.COMPENSATION_TARGET, "maps_to": "R5", "initial": 2200.0, "bounds": [1000, 5000]},
            "Lout": {
                "target": buck_opt.SCHEMATIC_PROPERTY_TARGET,
                "refdes": "L1",
                "property": "L",
                "initial": 6.8e-7,
                "bounds": [1e-7, 2e-6],
            },
            "VRAMP": {
                "target": buck_opt.SCHEMATIC_VALUE_TOKEN_TARGET,
                "refdes": "V2",
                "property": "SIMPLIS_VALUE",
                "token": "_V2",
                "initial": 1.0,
                "bounds": [0.5, 2.0],
            },
        },
    }

    result = buck_opt.apply_best(config, best)

    assert Path(result["backups"]["schematic"]).exists()
    assert Path(result["backups"]["compensator"]).exists()
    updated_schematic = schematic.read_text(encoding="utf-8")
    updated_comp = compensator.read_text(encoding="utf-8")
    assert 'Property name="L" value="8.2e-07"' in updated_schematic
    assert "_V2=1.2" in updated_schematic
    assert "_FREQ=500k" in updated_schematic
    assert ".VAR Rz1 = 2200" in updated_comp
    assert ".VAR R5 = {Rz1}" in updated_comp
    assert len(updated_comp.splitlines()) == 1
    assert "\\n.VAR Rz1 = 2200\\n" in updated_comp
