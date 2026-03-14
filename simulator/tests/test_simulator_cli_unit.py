"""Unit tests for CLI simulator behaviors."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from simulator.application.services import SimulatorController
from simulator.domain.events import DeviceInputEvent, DeviceState
from simulator.domain.state import SimulatorState
from simulator.entrypoints import cli as simulator


class FakeWs:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent.append(payload)


class FakeIncomingWs:
    def __init__(self, messages: list[str]) -> None:
        self._messages = list(messages)

    def __aiter__(self) -> "FakeIncomingWs":
        return self

    async def __anext__(self) -> str:
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)


def decode_sent(ws: FakeWs) -> list[dict[str, Any]]:
    return [json.loads(raw) for raw in ws.sent]


def make_controller(ws: FakeWs) -> SimulatorController:
    state = SimulatorState(device_id="sim-dev")
    return SimulatorController(
        state,
        gateway=simulator.CliGateway(ws),
        clock=simulator.SystemClock(),
    )


@pytest.mark.asyncio
async def test_tap_from_ready_starts_recording(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWs()
    controller = make_controller(ws)
    controller.snapshot.connected = True
    controller.snapshot.device_state = DeviceState.READY
    monkeypatch.setattr(simulator, "render_screen", Mock())

    await simulator.tap(controller)

    assert controller.snapshot.device_state == DeviceState.LISTEN
    messages = decode_sent(ws)
    assert messages[0]["type"] == "recording.start"
    assert messages[0]["turn_id"] == controller.snapshot.turn_id


@pytest.mark.asyncio
async def test_long_press_from_listen_enters_agents_and_checks_version(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWs()
    controller = make_controller(ws)
    controller.snapshot.connected = True
    controller.snapshot.device_state = DeviceState.LISTEN
    controller.snapshot.listening_active = True
    controller.snapshot.turn_id = "turn-1"
    controller.snapshot.agents_version = "v1"
    controller.snapshot.agent_cache.loaded_at = 10.0
    controller.snapshot.agent_cache.expires_at = 10.0
    monkeypatch.setattr(simulator, "render_screen", Mock())

    await simulator.long_press(controller)

    assert controller.snapshot.device_state == DeviceState.AGENTS
    messages = decode_sent(ws)
    assert [message["type"] for message in messages] == ["recording.cancel", "agents.version.request"]


@pytest.mark.asyncio
async def test_send_debug_text_unlock_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWs()
    controller = make_controller(ws)
    render_mock = Mock()
    monkeypatch.setattr(simulator, "render_screen", render_mock)

    await simulator.send_debug_text(ws, controller, "hola")

    assert ws.sent == []
    render_mock.assert_called_once()


@pytest.mark.asyncio
async def test_receiver_loop_keeps_local_device_state_when_ui_state_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = FakeIncomingWs(
        [
            json.dumps(
                {
                    "type": "session.ready",
                    "session_id": "session-1",
                    "available_agents": ["assistant-general", "assistant-tech"],
                    "active_agent": "assistant-tech",
                    "agents_version": "v1",
                }
            ),
            json.dumps({"type": "ui.state", "state": "listening"}),
        ]
    )
    controller = make_controller(FakeWs())
    controller.snapshot.device_state = DeviceState.AGENTS
    monkeypatch.setattr(simulator, "render_screen", Mock())

    await simulator.receiver_loop(ws, controller)

    assert controller.snapshot.session_id == "session-1"
    assert controller.snapshot.active_agent == "assistant-tech"
    assert controller.snapshot.device_state == DeviceState.AGENTS
    assert controller.snapshot.remote_ui_state.value == "listening"


@pytest.mark.asyncio
async def test_command_loop_routes_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWs()
    controller = make_controller(ws)
    controller.snapshot.connected = True

    commands = iter(["tap", "double", "long", "text hola", "state", "quit"])

    async def fake_to_thread(_fn: Any, _prompt: str) -> str:
        return next(commands)

    tap_mock = AsyncMock()
    double_mock = AsyncMock()
    long_mock = AsyncMock()
    text_mock = AsyncMock()
    render_mock = Mock()

    monkeypatch.setattr(simulator.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(simulator, "tap", tap_mock)
    monkeypatch.setattr(simulator, "double_tap", double_mock)
    monkeypatch.setattr(simulator, "long_press", long_mock)
    monkeypatch.setattr(simulator, "send_debug_text", text_mock)
    monkeypatch.setattr(simulator, "print_help", Mock())
    monkeypatch.setattr(simulator, "render_screen", render_mock)

    await simulator.command_loop(ws, controller)

    tap_mock.assert_awaited_once_with(controller)
    double_mock.assert_awaited_once_with(controller)
    long_mock.assert_awaited_once_with(controller)
    text_mock.assert_awaited_once_with(ws, controller, "hola")
    assert render_mock.called


@pytest.mark.asyncio
async def test_ping_loop_sends_ping_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWs()
    sleep_mock = AsyncMock(side_effect=[None, asyncio.CancelledError()])
    monkeypatch.setattr(simulator.asyncio, "sleep", sleep_mock)
    with pytest.raises(asyncio.CancelledError):
        await simulator.ping_loop(ws)
    assert decode_sent(ws)[0]["type"] == "ping"


def test_parse_args_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["simulator.entrypoints.cli"])
    args = simulator.parse_args()
    assert isinstance(args, argparse.Namespace)
    assert args.ws_url.startswith("ws://")
