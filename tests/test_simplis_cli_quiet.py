from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import simplis_cli  # noqa: E402


def test_repair_echo_config_removes_persistent_echoon(tmp_path: Path) -> None:
    config = tmp_path / "Base.sxprj"
    config.write_text("alpha=1\nEchoOn=\nprecision=16\n", encoding="utf-8")

    result = simplis_cli.repair_echo_config(config)

    assert result["changed"] is True
    assert "EchoOn=" not in config.read_text(encoding="utf-8")
    assert Path(str(result["backup"])).exists()


def test_repair_echo_config_is_noop_without_echoon(tmp_path: Path) -> None:
    config = tmp_path / "Base.sxprj"
    config.write_text("alpha=1\nprecision=16\n", encoding="utf-8")

    result = simplis_cli.repair_echo_config(config)

    assert result["changed"] is False
    assert config.read_text(encoding="utf-8") == "alpha=1\nprecision=16\n"
