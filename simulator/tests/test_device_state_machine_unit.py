"""Unit tests for the pure device state machine."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from simulator.application.services import DeviceStateMachine
from simulator.domain.events import DeviceInputEvent, DeviceState, DomainEffect
from simulator.domain.state import DeviceSnapshot


def test_locked_long_press_unlocks() -> None:
    machine = DeviceStateMachine(turn_id_factory=lambda: "turn-1")
    snapshot = DeviceSnapshot(device_id="sim-1")

    result = machine.handle_event(snapshot, DeviceInputEvent.LONG_PRESS, connected=True, now=0.0)

    assert result.snapshot.device_state == DeviceState.READY
    assert result.effects == []


def test_ready_press_starts_listen_when_connected() -> None:
    machine = DeviceStateMachine(turn_id_factory=lambda: "turn-1")
    snapshot = DeviceSnapshot(device_id="sim-1", device_state=DeviceState.READY)

    result = machine.handle_event(snapshot, DeviceInputEvent.PRESS, connected=True, now=0.0)

    assert result.snapshot.device_state == DeviceState.LISTEN
    assert result.snapshot.turn_id == "turn-1"
    assert result.effects[0].kind == DomainEffect.START_LISTEN


def test_ready_press_stays_local_when_disconnected() -> None:
    machine = DeviceStateMachine(turn_id_factory=lambda: "turn-1")
    snapshot = DeviceSnapshot(device_id="sim-1", device_state=DeviceState.READY)

    result = machine.handle_event(snapshot, DeviceInputEvent.PRESS, connected=False, now=0.0)

    assert result.snapshot.device_state == DeviceState.READY
    assert result.effects == []


def test_ready_double_press_opens_menu() -> None:
    machine = DeviceStateMachine(turn_id_factory=lambda: "turn-1")
    snapshot = DeviceSnapshot(device_id="sim-1", device_state=DeviceState.READY)

    result = machine.handle_event(snapshot, DeviceInputEvent.DOUBLE_PRESS, connected=True, now=0.0)

    assert result.snapshot.device_state == DeviceState.MENU
    assert result.effects == []


def test_menu_press_stays_in_menu_and_advances_focus() -> None:
    machine = DeviceStateMachine(turn_id_factory=lambda: "turn-1")
    snapshot = DeviceSnapshot(device_id="sim-1", device_state=DeviceState.MENU)
    snapshot.navigation.menu_options = ["MODE", "MODE"]
    snapshot.navigation.menu_index = 0

    result = machine.handle_event(snapshot, DeviceInputEvent.PRESS, connected=True, now=0.0)

    assert result.snapshot.device_state == DeviceState.MENU
    assert result.snapshot.navigation.menu_index == 1
    assert result.effects == []


def test_menu_double_press_returns_to_ready() -> None:
    machine = DeviceStateMachine(turn_id_factory=lambda: "turn-1")
    snapshot = DeviceSnapshot(device_id="sim-1", device_state=DeviceState.MENU)

    result = machine.handle_event(snapshot, DeviceInputEvent.DOUBLE_PRESS, connected=True, now=0.0)

    assert result.snapshot.device_state == DeviceState.READY
    assert result.effects == []


def test_menu_long_press_enters_mode() -> None:
    machine = DeviceStateMachine(turn_id_factory=lambda: "turn-1")
    snapshot = DeviceSnapshot(device_id="sim-1", device_state=DeviceState.MENU)

    result = machine.handle_event(snapshot, DeviceInputEvent.LONG_PRESS, connected=True, now=0.0)

    assert result.snapshot.device_state == DeviceState.MODE
    assert result.snapshot.navigation.mode_index == 0
    assert result.effects == []


def test_locked_ignores_press_and_double_press() -> None:
    machine = DeviceStateMachine(turn_id_factory=lambda: "turn-1")
    snapshot = DeviceSnapshot(device_id="sim-1")

    press_result = machine.handle_event(snapshot, DeviceInputEvent.PRESS, connected=True, now=0.0)
    double_result = machine.handle_event(snapshot, DeviceInputEvent.DOUBLE_PRESS, connected=True, now=0.0)

    assert press_result.snapshot.device_state == DeviceState.LOCKED
    assert press_result.effects == []
    assert double_result.snapshot.device_state == DeviceState.LOCKED
    assert double_result.effects == []


def test_ready_long_press_locks_device() -> None:
    machine = DeviceStateMachine(turn_id_factory=lambda: "turn-1")
    snapshot = DeviceSnapshot(device_id="sim-1", device_state=DeviceState.READY)

    result = machine.handle_event(snapshot, DeviceInputEvent.LONG_PRESS, connected=True, now=0.0)

    assert result.snapshot.device_state == DeviceState.LOCKED
    assert result.effects == []


def test_listen_double_press_cancels_and_returns_ready() -> None:
    machine = DeviceStateMachine(turn_id_factory=lambda: "turn-1")
    snapshot = DeviceSnapshot(device_id="sim-1", device_state=DeviceState.LISTEN)
    snapshot.turn_id = "turn-1"
    snapshot.listening_active = True

    result = machine.handle_event(snapshot, DeviceInputEvent.DOUBLE_PRESS, connected=True, now=0.0)

    assert result.snapshot.device_state == DeviceState.READY
    assert result.snapshot.turn_id is None
    assert result.snapshot.listening_active is False
    assert [effect.kind for effect in result.effects] == [DomainEffect.STOP_LISTEN_CANCEL]


def test_listen_long_press_uses_warm_agent_cache_without_remote_lookup() -> None:
    machine = DeviceStateMachine(turn_id_factory=lambda: "turn-1")
    snapshot = DeviceSnapshot(device_id="sim-1", device_state=DeviceState.LISTEN)
    snapshot.turn_id = "turn-1"
    snapshot.agent_cache.loaded_at = 10.0
    snapshot.agent_cache.expires_at = 999.0

    result = machine.handle_event(snapshot, DeviceInputEvent.LONG_PRESS, connected=True, now=20.0)

    assert result.snapshot.device_state == DeviceState.AGENTS
    assert [effect.kind for effect in result.effects] == [DomainEffect.STOP_LISTEN_CANCEL]


def test_listen_long_press_requests_version_when_cache_is_stale() -> None:
    machine = DeviceStateMachine(turn_id_factory=lambda: "turn-1")
    snapshot = DeviceSnapshot(device_id="sim-1", device_state=DeviceState.LISTEN)
    snapshot.turn_id = "turn-1"
    snapshot.agents_version = "v1"
    snapshot.agent_cache.loaded_at = 10.0
    snapshot.agent_cache.expires_at = 10.0

    result = machine.handle_event(snapshot, DeviceInputEvent.LONG_PRESS, connected=True, now=20.0)

    assert [effect.kind for effect in result.effects] == [
        DomainEffect.STOP_LISTEN_CANCEL,
        DomainEffect.REQUEST_AGENTS_VERSION,
    ]


def test_listen_long_press_requests_list_when_cache_is_cold() -> None:
    machine = DeviceStateMachine(turn_id_factory=lambda: "turn-1")
    snapshot = DeviceSnapshot(device_id="sim-1", device_state=DeviceState.LISTEN)
    snapshot.turn_id = "turn-1"
    snapshot.agent_cache.loaded_at = None
    snapshot.agent_cache.expires_at = None

    result = machine.handle_event(snapshot, DeviceInputEvent.LONG_PRESS, connected=True, now=20.0)

    assert [effect.kind for effect in result.effects] == [
        DomainEffect.STOP_LISTEN_CANCEL,
        DomainEffect.REQUEST_AGENTS_LIST,
    ]


def test_mode_press_stays_in_mode_and_advances_focus() -> None:
    machine = DeviceStateMachine(turn_id_factory=lambda: "turn-1")
    snapshot = DeviceSnapshot(device_id="sim-1", device_state=DeviceState.MODE)
    snapshot.navigation.available_modes = ["conversation", "briefing"]
    snapshot.navigation.mode_index = 0

    result = machine.handle_event(snapshot, DeviceInputEvent.PRESS, connected=True, now=0.0)

    assert result.snapshot.device_state == DeviceState.MODE
    assert result.snapshot.navigation.mode_index == 1
    assert result.effects == []


def test_mode_double_press_cancels_without_changing_active_mode() -> None:
    machine = DeviceStateMachine(turn_id_factory=lambda: "turn-1")
    snapshot = DeviceSnapshot(device_id="sim-1", device_state=DeviceState.MODE)
    snapshot.navigation.available_modes = ["conversation", "briefing"]
    snapshot.navigation.active_mode = "conversation"
    snapshot.navigation.mode_index = 1

    result = machine.handle_event(snapshot, DeviceInputEvent.DOUBLE_PRESS, connected=True, now=0.0)

    assert result.snapshot.device_state == DeviceState.READY
    assert result.snapshot.navigation.active_mode == "conversation"
    assert result.snapshot.navigation.mode_index == 0
    assert result.effects == []


def test_mode_long_press_confirms_mode_and_returns_ready() -> None:
    machine = DeviceStateMachine(turn_id_factory=lambda: "turn-1")
    snapshot = DeviceSnapshot(device_id="sim-1", device_state=DeviceState.MODE)
    snapshot.navigation.available_modes = ["conversation", "briefing"]
    snapshot.navigation.mode_index = 1

    result = machine.handle_event(snapshot, DeviceInputEvent.LONG_PRESS, connected=True, now=0.0)

    assert result.snapshot.device_state == DeviceState.READY
    assert result.snapshot.navigation.active_mode == "briefing"
    assert result.effects == []


def test_agents_confirm_waits_for_backend_ack() -> None:
    machine = DeviceStateMachine(turn_id_factory=lambda: "turn-1")
    snapshot = DeviceSnapshot(device_id="sim-1", device_state=DeviceState.AGENTS)
    snapshot.agents = ["assistant-general", "assistant-tech"]
    snapshot.navigation.active_agent_id = "assistant-general"
    snapshot.navigation.focused_agent_index = 1

    result = machine.handle_event(snapshot, DeviceInputEvent.LONG_PRESS, connected=True, now=0.0)

    assert result.snapshot.device_state == DeviceState.READY
    assert result.snapshot.active_agent == "assistant-general"
    assert result.snapshot.pending_agent_ack == "assistant-tech"
    assert result.effects[0].kind == DomainEffect.CONFIRM_AGENT


def test_agents_without_catalog_stays_consistent_on_press_and_confirm() -> None:
    machine = DeviceStateMachine(turn_id_factory=lambda: "turn-1")
    snapshot = DeviceSnapshot(device_id="sim-1", device_state=DeviceState.AGENTS)
    snapshot.agents = []
    snapshot.navigation.active_agent_id = "assistant-general"

    press_result = machine.handle_event(snapshot, DeviceInputEvent.PRESS, connected=True, now=0.0)
    confirm_result = machine.handle_event(snapshot, DeviceInputEvent.LONG_PRESS, connected=True, now=0.0)

    assert press_result.snapshot.device_state == DeviceState.AGENTS
    assert press_result.snapshot.active_agent == "assistant-general"
    assert press_result.effects == []
    assert confirm_result.snapshot.device_state == DeviceState.AGENTS
    assert confirm_result.snapshot.active_agent == "assistant-general"
    assert confirm_result.effects == []
