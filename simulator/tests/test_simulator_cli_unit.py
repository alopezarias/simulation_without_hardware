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

from simulator.entrypoints import cli as simulator
from simulator.shared.protocol import UiState


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


def make_state() -> simulator.SimulatorState:
    return simulator.SimulatorState(device_id="sim-dev")


def test_simulator_state_set_agent_updates_index_and_appends() -> None:
    state = make_state()
    state.set_agent("assistant-tech")
    assert state.active_agent == "assistant-tech"
    state.set_agent("assistant-custom")
    assert state.active_agent == "assistant-custom"
    assert "assistant-custom" in state.agents


@pytest.mark.asyncio
async def test_tap_from_idle_starts_recording(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWs()
    state = make_state()
    monkeypatch.setattr(simulator, "new_turn_id", Mock(return_value="turn-123"))
    monkeypatch.setattr(simulator, "render_screen", Mock())

    await simulator.tap(ws, state)

    assert state.ui_state == UiState.LISTENING
    assert state.turn_id == "turn-123"
    messages = decode_sent(ws)
    assert messages[0]["type"] == "recording.start"
    assert messages[0]["turn_id"] == "turn-123"


@pytest.mark.asyncio
async def test_tap_from_listening_stops_recording(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWs()
    state = make_state()
    state.ui_state = UiState.LISTENING
    state.turn_id = "turn-1"
    monkeypatch.setattr(simulator, "render_screen", Mock())

    await simulator.tap(ws, state)

    assert state.ui_state == UiState.PROCESSING
    assert decode_sent(ws)[0]["type"] == "recording.stop"


@pytest.mark.asyncio
async def test_tap_from_speaking_sends_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWs()
    state = make_state()
    state.ui_state = UiState.SPEAKING
    state.turn_id = "turn-1"
    monkeypatch.setattr(simulator, "render_screen", Mock())

    await simulator.tap(ws, state)

    assert decode_sent(ws)[0]["type"] == "assistant.interrupt"


@pytest.mark.asyncio
async def test_long_press_from_listening_cancels(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWs()
    state = make_state()
    state.ui_state = UiState.LISTENING
    state.turn_id = "turn-1"
    monkeypatch.setattr(simulator, "render_screen", Mock())

    await simulator.long_press(ws, state)

    assert state.ui_state == UiState.IDLE
    assert state.turn_id is None
    assert decode_sent(ws)[0]["type"] == "recording.cancel"


@pytest.mark.asyncio
async def test_double_tap_cycles_agent_and_sends_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWs()
    state = make_state()
    state.agent_index = 0
    monkeypatch.setattr(simulator, "render_screen", Mock())

    await simulator.double_tap(ws, state)

    assert state.agent_index == 1
    assert decode_sent(ws)[0]["type"] == "agent.select"
    assert decode_sent(ws)[0]["agent_id"] == state.active_agent


@pytest.mark.asyncio
async def test_send_debug_text_from_idle_starts_turn_and_sends_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = FakeWs()
    state = make_state()
    monkeypatch.setattr(simulator, "new_turn_id", Mock(return_value="turn-999"))
    monkeypatch.setattr(simulator, "render_screen", Mock())

    await simulator.send_debug_text(ws, state, " hola ")

    messages = decode_sent(ws)
    assert messages[0]["type"] == "recording.start"
    assert messages[1]["type"] == "debug.user_text"
    assert messages[1]["text"] == "hola"


@pytest.mark.asyncio
async def test_send_debug_text_ignores_empty_text(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWs()
    state = make_state()
    render_mock = Mock()
    monkeypatch.setattr(simulator, "render_screen", render_mock)

    await simulator.send_debug_text(ws, state, "   ")

    assert ws.sent == []
    render_mock.assert_called_once()


@pytest.mark.asyncio
async def test_receiver_loop_updates_state_from_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    state = make_state()
    monkeypatch.setattr(simulator, "render_screen", Mock())

    incoming = FakeIncomingWs(
        [
            json.dumps(
                {
                    "type": "session.ready",
                    "session_id": "session-1",
                    "available_agents": ["assistant-general", "assistant-tech"],
                    "active_agent": "assistant-tech",
                }
            ),
            json.dumps({"type": "ui.state", "state": "listening"}),
            json.dumps({"type": "transcript.partial", "text": "hola"}),
            json.dumps({"type": "transcript.final", "text": "hola final"}),
            json.dumps({"type": "assistant.text.partial", "text": "res-"}),
            json.dumps({"type": "assistant.text.final", "text": "respuesta", "interrupted": True}),
            json.dumps({"type": "error", "detail": "boom"}),
        ]
    )

    await simulator.receiver_loop(incoming, state)

    assert state.connected is True
    assert state.session_id == "session-1"
    assert state.active_agent == "assistant-tech"
    assert state.transcript == "hola final"
    assert state.assistant_text.endswith("[interrupted]")
    assert state.ui_state == UiState.ERROR


@pytest.mark.asyncio
async def test_receiver_loop_invalid_ui_state_moves_to_error(monkeypatch: pytest.MonkeyPatch) -> None:
    state = make_state()
    monkeypatch.setattr(simulator, "render_screen", Mock())
    incoming = FakeIncomingWs([json.dumps({"type": "ui.state", "state": "invalid-state"})])
    await simulator.receiver_loop(incoming, state)
    assert state.ui_state == UiState.ERROR


@pytest.mark.asyncio
async def test_ping_loop_sends_ping_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWs()
    sleep_mock = AsyncMock(side_effect=[None, asyncio.CancelledError()])
    monkeypatch.setattr(simulator.asyncio, "sleep", sleep_mock)
    with pytest.raises(asyncio.CancelledError):
        await simulator.ping_loop(ws)
    messages = decode_sent(ws)
    assert messages[0]["type"] == "ping"


@pytest.mark.asyncio
async def test_command_loop_routes_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWs()
    state = make_state()

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

    await simulator.command_loop(ws, state)

    tap_mock.assert_awaited_once_with(ws, state)
    double_mock.assert_awaited_once_with(ws, state)
    long_mock.assert_awaited_once_with(ws, state)
    text_mock.assert_awaited_once_with(ws, state, "hola")
    assert render_mock.called


def test_parse_args_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["simulator.entrypoints.cli"])
    args = simulator.parse_args()
    assert isinstance(args, argparse.Namespace)
    assert args.ws_url.startswith("ws://")
