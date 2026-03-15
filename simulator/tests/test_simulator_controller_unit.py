"""Unit tests for the shared simulator controller and protocol handling."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from simulator.application.services import SimulatorController
from simulator.domain.events import DeviceInputEvent, DeviceState
from simulator.domain.state import DeviceSnapshot


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object | None]] = []

    async def start_listen(self, turn_id: str) -> None:
        self.calls.append(("start_listen", turn_id))

    async def stop_listen(self, turn_id: str) -> None:
        self.calls.append(("stop_listen", turn_id))

    async def cancel_listen(self, turn_id: str | None) -> None:
        self.calls.append(("cancel_listen", turn_id))

    async def send_audio_chunk(self, turn_id: str, chunk: dict[str, object]) -> None:
        self.calls.append(("send_audio_chunk", {"turn_id": turn_id, **chunk}))

    async def request_agents_version(self) -> None:
        self.calls.append(("request_agents_version", None))

    async def request_agents_list(self) -> None:
        self.calls.append(("request_agents_list", None))

    async def confirm_agent(self, agent_id: str) -> None:
        self.calls.append(("confirm_agent", agent_id))


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.current = now

    def now(self) -> float:
        return self.current


class FakeObserver:
    def __init__(self) -> None:
        self.snapshots: list[DeviceSnapshot] = []

    def publish(self, snapshot: DeviceSnapshot) -> None:
        self.snapshots.append(snapshot)


@pytest.mark.asyncio
async def test_controller_maps_start_listen_effect_to_gateway() -> None:
    gateway = FakeGateway()
    clock = FakeClock()
    observer = FakeObserver()
    snapshot = DeviceSnapshot(device_id="sim-1", device_state=DeviceState.READY)
    snapshot.connected = True
    snapshot.session_id = "session-1"
    controller = SimulatorController(snapshot, gateway=gateway, clock=clock, observer=observer)

    await controller.handle_input(DeviceInputEvent.PRESS)

    assert gateway.calls[0][0] == "start_listen"
    assert controller.snapshot.device_state == DeviceState.LISTEN
    assert observer.snapshots[-1].device_state == DeviceState.LISTEN


@pytest.mark.asyncio
async def test_controller_navigation_in_agents_does_not_send_remote_traffic() -> None:
    gateway = FakeGateway()
    clock = FakeClock()
    snapshot = DeviceSnapshot(device_id="sim-1", device_state=DeviceState.AGENTS)
    snapshot.agents = ["assistant-general", "assistant-tech"]
    controller = SimulatorController(snapshot, gateway=gateway, clock=clock)

    await controller.handle_input(DeviceInputEvent.PRESS)

    assert gateway.calls == []
    assert controller.snapshot.focused_agent == "assistant-tech"


@pytest.mark.asyncio
async def test_controller_canceling_agents_does_not_emit_agent_select() -> None:
    gateway = FakeGateway()
    clock = FakeClock()
    snapshot = DeviceSnapshot(device_id="sim-1", device_state=DeviceState.AGENTS)
    snapshot.agents = ["assistant-general", "assistant-tech"]
    snapshot.navigation.active_agent_id = "assistant-general"
    snapshot.navigation.focused_agent_index = 1
    controller = SimulatorController(snapshot, gateway=gateway, clock=clock)

    await controller.handle_input(DeviceInputEvent.DOUBLE_PRESS)

    assert controller.snapshot.device_state == DeviceState.READY
    assert controller.snapshot.active_agent == "assistant-general"
    assert ("confirm_agent", "assistant-tech") not in gateway.calls


@pytest.mark.asyncio
async def test_controller_only_promotes_active_agent_after_ack() -> None:
    gateway = FakeGateway()
    clock = FakeClock()
    snapshot = DeviceSnapshot(device_id="sim-1", device_state=DeviceState.AGENTS)
    snapshot.agents = ["assistant-general", "assistant-tech"]
    snapshot.navigation.active_agent_id = "assistant-general"
    snapshot.navigation.focused_agent_index = 1
    controller = SimulatorController(snapshot, gateway=gateway, clock=clock)

    await controller.handle_input(DeviceInputEvent.LONG_PRESS)

    assert controller.snapshot.active_agent == "assistant-general"
    assert controller.snapshot.pending_agent_ack == "assistant-tech"
    assert gateway.calls[-1] == ("confirm_agent", "assistant-tech")

    await controller.handle_backend_message({"type": "agent.selected", "agent_id": "assistant-tech"})

    assert controller.snapshot.active_agent == "assistant-tech"
    assert controller.snapshot.pending_agent_ack is None


@pytest.mark.asyncio
async def test_controller_refreshes_catalog_only_when_version_changes() -> None:
    gateway = FakeGateway()
    clock = FakeClock(now=100.0)
    snapshot = DeviceSnapshot(device_id="sim-1")
    snapshot.agents = ["assistant-general", "assistant-tech"]
    snapshot.agents_version = "v1"
    snapshot.agent_cache.loaded_at = 10.0
    snapshot.agent_cache.expires_at = 20.0
    controller = SimulatorController(snapshot, gateway=gateway, clock=clock)

    await controller.handle_backend_message(
        {"type": "agents.version.response", "version": "v1", "active_agent": "assistant-general"}
    )

    assert gateway.calls == []
    assert controller.snapshot.agent_cache.expires_at == pytest.approx(400.0)

    await controller.handle_backend_message(
        {"type": "agents.version.response", "version": "v2", "active_agent": "assistant-general"}
    )

    assert gateway.calls[-1] == ("request_agents_list", None)


@pytest.mark.asyncio
async def test_controller_keeps_local_device_state_when_remote_ui_state_changes() -> None:
    gateway = FakeGateway()
    clock = FakeClock()
    snapshot = DeviceSnapshot(device_id="sim-1", device_state=DeviceState.AGENTS)
    controller = SimulatorController(snapshot, gateway=gateway, clock=clock)

    await controller.handle_backend_message({"type": "ui.state", "state": "processing"})

    assert controller.snapshot.device_state == DeviceState.AGENTS
    assert controller.snapshot.remote_ui_state.value == "processing"


@pytest.mark.asyncio
async def test_controller_blocks_listen_until_session_ready() -> None:
    gateway = FakeGateway()
    clock = FakeClock()
    snapshot = DeviceSnapshot(device_id="sim-1", device_state=DeviceState.READY)
    snapshot.connected = True
    controller = SimulatorController(snapshot, gateway=gateway, clock=clock)

    result = await controller.handle_input(DeviceInputEvent.PRESS)

    assert result.note == "backend not ready"
    assert controller.snapshot.device_state == DeviceState.READY
    assert gateway.calls == []
