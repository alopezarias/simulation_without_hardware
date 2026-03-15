"""Unit tests for scenario runner orchestration helpers."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from simulator.qa import scenario_runner


def test_build_runtime_env_uses_safe_null_defaults() -> None:
    args = argparse.Namespace(
        ws_url="ws://localhost/ws",
        device_id="sim-1",
        auth_token="token-1",
        runtime_device_id="",
        runtime_display_adapter="null",
        runtime_button_adapter="null",
        runtime_audio_in_adapter="null",
        runtime_audio_out_adapter="null",
    )

    env = scenario_runner._build_runtime_env(args)

    assert env["DEVICE_ID"] == "sim-1-raspi"
    assert env["DEVICE_WS_URL"] == "ws://localhost/ws"
    assert env["DEVICE_AUTH_TOKEN"] == "token-1"
    assert env["DEVICE_AUDIO_IN_ADAPTER"] == "null"


def test_build_runtime_env_applies_overrides() -> None:
    args = argparse.Namespace(
        ws_url="ws://localhost/ws",
        device_id="sim-1",
        auth_token="",
        runtime_device_id="",
        runtime_display_adapter="whisplay",
        runtime_button_adapter="gpio",
        runtime_audio_in_adapter="alsa",
        runtime_audio_out_adapter="alsa",
    )

    env = scenario_runner._build_runtime_env(args, overrides={"DEVICE_AUDIO_IN_ADAPTER": "null"})

    assert env["DEVICE_AUDIO_IN_ADAPTER"] == "null"
    assert env["DEVICE_DISPLAY_ADAPTER"] == "whisplay"


@pytest.mark.asyncio
async def test_run_scenarios_all_includes_raspi_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    args = argparse.Namespace(scenario="all")
    called: list[str] = []

    async def fake_run_named(name: str, _args: argparse.Namespace) -> scenario_runner.ScenarioResult:
        called.append(name)
        return scenario_runner.ScenarioResult(name=name, passed=True, duration_ms=1, details="ok")

    monkeypatch.setattr(scenario_runner, "run_named_scenario", fake_run_named)

    results = await scenario_runner.run_scenarios(args)

    assert [result.name for result in results] == called
    assert "raspi-bootstrap" in called
    assert "raspi-reconnect" in called
    assert "raspi-no-mic" in called
