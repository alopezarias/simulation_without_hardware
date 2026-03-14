"""Unit tests for the new local simulator domain foundation."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from simulator.application.ports import BackendGateway, Clock, StateObserver
from simulator.domain.events import DeviceInputEvent, DeviceState, DomainEffect, EffectPayload, MenuOption
from simulator.domain.state import DeviceSnapshot
from simulator.shared.protocol import UiState


def test_device_snapshot_defaults_separate_local_and_remote_state() -> None:
    snapshot = DeviceSnapshot(device_id="sim-1")

    assert snapshot.device_state == DeviceState.LOCKED
    assert snapshot.ui_state == UiState.IDLE
    assert snapshot.navigation.menu_options == [MenuOption.MODE.value]
    assert snapshot.agents == ["assistant-general", "assistant-tech", "assistant-ops"]
    assert snapshot.active_agent == "assistant-general"


def test_device_snapshot_preserves_agent_cache_and_focus() -> None:
    snapshot = DeviceSnapshot(device_id="sim-1")
    snapshot.agents = ["assistant-general", "assistant-tech"]
    snapshot.set_agent("assistant-tech")

    assert snapshot.active_agent == "assistant-tech"
    assert snapshot.agent_index == 1
    assert snapshot.agents == ["assistant-general", "assistant-tech"]


def test_device_snapshot_ui_state_is_remote_metadata() -> None:
    snapshot = DeviceSnapshot(device_id="sim-1")
    snapshot.device_state = DeviceState.AGENTS
    snapshot.ui_state = UiState.PROCESSING

    assert snapshot.device_state == DeviceState.AGENTS
    assert snapshot.remote_ui_state == UiState.PROCESSING


def test_domain_types_expose_phase_one_contract() -> None:
    effect = EffectPayload(DomainEffect.REQUEST_AGENTS_VERSION, {"reason": "stale-cache"})

    assert DeviceInputEvent.DOUBLE_PRESS.value == "double_press"
    assert DomainEffect.CONFIRM_AGENT.value == "confirm_agent"
    assert effect.data["reason"] == "stale-cache"


def test_ports_are_runtime_checkable_protocol_shapes() -> None:
    assert BackendGateway is not None
    assert Clock is not None
    assert StateObserver is not None
