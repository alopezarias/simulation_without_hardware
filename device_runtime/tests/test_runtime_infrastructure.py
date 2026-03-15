"""Infrastructure tests for shared runtime transport and audio helpers."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.shared.protocol import build_message
from device_runtime.infrastructure.audio.pcm_chunker import PcmChunker
from device_runtime.infrastructure.transport.websocket_client import SessionNotReadyError, WebSocketTransport


class FakeWs:
    def __init__(self, incoming: list[str]) -> None:
        self.sent: list[str] = []
        self._incoming = list(incoming)
        self.closed = False

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self) -> "FakeWs":
        return self

    async def __anext__(self) -> str:
        if not self._incoming:
            raise StopAsyncIteration
        return self._incoming.pop(0)


class FakeConnect:
    def __init__(self, ws: FakeWs) -> None:
        self._ws = ws

    async def __aenter__(self) -> FakeWs:
        return self._ws

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class SequenceConnect:
    def __init__(self, sockets: list[FakeWs]) -> None:
        self._sockets = list(sockets)

    def __call__(self, _url: str) -> FakeConnect:
        if not self._sockets:
            raise RuntimeError("no more sockets")
        return FakeConnect(self._sockets.pop(0))


def test_pcm_chunker_builds_protocol_metadata() -> None:
    chunker = PcmChunker(sample_rate=16000, channels=1, chunk_ms=120)

    chunk = chunker.build_chunk(b"\x00\x01" * 400, seq=3, timestamp_ms=25)

    assert chunk is not None
    assert chunk["seq"] == 3
    assert chunk["timestamp_ms"] == 25
    assert chunk["codec"] == "pcm16"
    assert chunk["size_bytes"] == 800


@pytest.mark.asyncio
async def test_transport_blocks_functional_messages_before_session_ready() -> None:
    transport = WebSocketTransport("ws://localhost/ws", hello_payload=build_message("device.hello", device_id="dev"))

    with pytest.raises(SessionNotReadyError, match="recording.start"):
        await transport.send(build_message("recording.start", turn_id="turn-1"))


@pytest.mark.asyncio
async def test_transport_sends_hello_and_marks_session_ready() -> None:
    fake_ws = FakeWs([json.dumps({"type": "session.ready", "session_id": "session-1"})])
    transport = WebSocketTransport(
        "ws://localhost/ws",
        hello_payload=build_message("device.hello", device_id="dev"),
        keepalive_interval_s=60.0,
        connect_factory=lambda _url: FakeConnect(fake_ws),
    )
    received: list[dict[str, Any]] = []

    def on_message(message: dict[str, Any]) -> None:
        received.append(message)
        transport.close()

    transport.set_message_handler(on_message)

    await transport.connect()

    assert json.loads(fake_ws.sent[0])["type"] == "device.hello"
    assert received[0]["type"] == "session.ready"
    assert transport.session_ready is True


@pytest.mark.asyncio
async def test_transport_reconnects_and_replays_hello() -> None:
    first_ws = FakeWs([json.dumps({"type": "session.ready", "session_id": "session-1"})])
    second_ws = FakeWs([json.dumps({"type": "session.ready", "session_id": "session-2"})])
    transport = WebSocketTransport(
        "ws://localhost/ws",
        hello_payload=build_message("device.hello", device_id="dev"),
        keepalive_interval_s=60.0,
        reconnect_initial_ms=5,
        reconnect_max_ms=5,
        connect_factory=SequenceConnect([first_ws, second_ws]),
    )
    ready_ids: list[str] = []

    def on_message(message: dict[str, Any]) -> None:
        if message.get("type") == "session.ready":
            ready_ids.append(str(message.get("session_id", "")))
            if len(ready_ids) == 2:
                transport.close()

    transport.set_message_handler(on_message)
    await asyncio.wait_for(transport.connect(), timeout=1.0)

    assert ready_ids == ["session-1", "session-2"]
    assert json.loads(first_ws.sent[0])["type"] == "device.hello"
    assert json.loads(second_ws.sent[0])["type"] == "device.hello"


@pytest.mark.asyncio
async def test_transport_close_closes_active_socket() -> None:
    fake_ws = FakeWs([])
    transport = WebSocketTransport(
        "ws://localhost/ws",
        hello_payload=build_message("device.hello", device_id="dev"),
        keepalive_interval_s=60.0,
        connect_factory=lambda _url: FakeConnect(fake_ws),
    )
    transport._active_ws = fake_ws

    transport.close()
    await asyncio.sleep(0)

    assert fake_ws.closed is True
