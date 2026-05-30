"""Tests for WebSocket notifications: ConnectionManager, WebSocketNotifier, /ws/client."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.infrastructure.adapters.ws_notifier import (
    ConnectionManager,
    WebSocketNotifier,
)
from backend.tests.conftest import make_wav


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_ws(*, fail_on_send: bool = False) -> MagicMock:
    ws = MagicMock()
    if fail_on_send:
        ws.send_text = AsyncMock(side_effect=RuntimeError("connection lost"))
    else:
        ws.send_text = AsyncMock()
    ws.accept = AsyncMock()
    return ws


# ── ConnectionManager unit tests ──────────────────────────────────────────────

class TestConnectionManager:
    @pytest.mark.asyncio
    async def test_connect_accepts_websocket(self):
        manager = ConnectionManager()
        ws = _mock_ws()
        await manager.connect(ws)
        ws.accept.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_tracks_connection(self):
        manager = ConnectionManager()
        ws = _mock_ws()
        await manager.connect(ws)
        assert manager.connection_count == 1

    @pytest.mark.asyncio
    async def test_disconnect_removes_connection(self):
        manager = ConnectionManager()
        ws = _mock_ws()
        await manager.connect(ws)
        manager.disconnect(ws)
        assert manager.connection_count == 0

    def test_disconnect_unknown_websocket_does_not_raise(self):
        manager = ConnectionManager()
        ws = _mock_ws()
        manager.disconnect(ws)  # should not raise
        assert manager.connection_count == 0

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_all_connections(self):
        manager = ConnectionManager()
        ws1, ws2, ws3 = _mock_ws(), _mock_ws(), _mock_ws()
        for ws in (ws1, ws2, ws3):
            await manager.connect(ws)

        await manager.broadcast("note.created", {"id": "abc"})

        for ws in (ws1, ws2, ws3):
            ws.send_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_sends_correct_json(self):
        manager = ConnectionManager()
        ws = _mock_ws()
        await manager.connect(ws)

        await manager.broadcast("note.created", {"id": "abc", "text": "hello"})

        call_arg = ws.send_text.call_args[0][0]
        payload = json.loads(call_arg)
        assert payload["event"] == "note.created"
        assert payload["data"]["id"] == "abc"
        assert payload["data"]["text"] == "hello"

    @pytest.mark.asyncio
    async def test_broadcast_no_connections_does_not_raise(self):
        manager = ConnectionManager()
        await manager.broadcast("note.created", {"id": "abc"})  # should not raise

    @pytest.mark.asyncio
    async def test_broadcast_removes_dead_connections(self):
        manager = ConnectionManager()
        live_ws = _mock_ws()
        dead_ws = _mock_ws(fail_on_send=True)
        await manager.connect(live_ws)
        await manager.connect(dead_ws)
        assert manager.connection_count == 2

        await manager.broadcast("test.event", {})

        assert manager.connection_count == 1
        live_ws.send_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_continues_after_dead_connection(self):
        manager = ConnectionManager()
        ws_a = _mock_ws(fail_on_send=True)
        ws_b = _mock_ws()
        await manager.connect(ws_a)
        await manager.connect(ws_b)

        await manager.broadcast("test.event", {"key": "value"})

        ws_b.send_text.assert_called_once()
        payload = json.loads(ws_b.send_text.call_args[0][0])
        assert payload["event"] == "test.event"

    @pytest.mark.asyncio
    async def test_multiple_broadcasts_accumulate_removed_dead(self):
        manager = ConnectionManager()
        dead = _mock_ws(fail_on_send=True)
        await manager.connect(dead)

        await manager.broadcast("event1", {})
        assert manager.connection_count == 0

        await manager.broadcast("event2", {})  # no connections, should not raise

    @pytest.mark.asyncio
    async def test_connection_count_reflects_multiple_connects(self):
        manager = ConnectionManager()
        for _ in range(5):
            await manager.connect(_mock_ws())
        assert manager.connection_count == 5

    @pytest.mark.asyncio
    async def test_broadcast_message_is_valid_json(self):
        manager = ConnectionManager()
        ws = _mock_ws()
        await manager.connect(ws)

        await manager.broadcast("note.created", {"id": "x", "tags": ["a", "b"]})

        raw = ws.send_text.call_args[0][0]
        parsed = json.loads(raw)  # raises if invalid JSON
        assert "event" in parsed
        assert "data" in parsed


# ── WebSocketNotifier unit tests ──────────────────────────────────────────────

class TestWebSocketNotifier:
    @pytest.mark.asyncio
    async def test_delegates_to_manager(self):
        manager = MagicMock()
        manager.broadcast = AsyncMock()
        notifier = WebSocketNotifier(manager)

        await notifier.broadcast("note.created", {"id": "abc"})

        manager.broadcast.assert_awaited_once_with("note.created", {"id": "abc"})

    @pytest.mark.asyncio
    async def test_passes_event_and_data_unchanged(self):
        manager = MagicMock()
        manager.broadcast = AsyncMock()
        notifier = WebSocketNotifier(manager)

        data = {"id": "xyz", "type": "task", "tags": ["urgent"]}
        await notifier.broadcast("note.created", data)

        _, call_data = manager.broadcast.call_args[0]
        assert call_data == data


# ── WebSocket endpoint integration tests ─────────────────────────────────────

class TestWsEndpoint:
    def test_can_connect_to_ws_client(self, ws_app):
        from starlette.testclient import TestClient

        with TestClient(ws_app) as client:
            with client.websocket_connect("/ws/client") as ws:
                assert ws is not None  # connection established

    def test_ping_returns_pong(self, ws_app):
        from starlette.testclient import TestClient

        with TestClient(ws_app) as client:
            with client.websocket_connect("/ws/client") as ws:
                ws.send_json({"type": "ping"})
                data = ws.receive_json()
                assert data["type"] == "pong"

    def test_malformed_message_does_not_close_connection(self, ws_app):
        from starlette.testclient import TestClient

        with TestClient(ws_app) as client:
            with client.websocket_connect("/ws/client") as ws:
                ws.send_text("not json at all")
                # Send a valid ping after — if connection is still open, we get pong
                ws.send_json({"type": "ping"})
                data = ws.receive_json()
                assert data["type"] == "pong"

    def test_unknown_message_type_ignored(self, ws_app):
        from starlette.testclient import TestClient

        with TestClient(ws_app) as client:
            with client.websocket_connect("/ws/client") as ws:
                ws.send_json({"type": "subscribe", "filters": {"types": ["task"]}})
                # No crash — subsequent ping still works
                ws.send_json({"type": "ping"})
                data = ws.receive_json()
                assert data["type"] == "pong"

    def test_note_capture_broadcasts_to_connected_client(self, ws_app):
        from starlette.testclient import TestClient

        with TestClient(ws_app) as client:
            with client.websocket_connect("/ws/client") as ws:
                resp = client.post(
                    "/audio/capture",
                    files={"audio": ("audio", make_wav(), "audio/wav")},
                    data={"device_id": "raspi-ws-test", "capture_mode": "wake_word"},
                )
                assert resp.status_code == 202
                note_id = resp.json()["note_id"]

                event = ws.receive_json()
                assert event["event"] == "note.created"
                assert event["data"]["id"] == note_id

    def test_broadcast_data_contains_note_fields(self, ws_app):
        from starlette.testclient import TestClient

        with TestClient(ws_app) as client:
            with client.websocket_connect("/ws/client") as ws:
                client.post(
                    "/audio/capture",
                    files={"audio": ("audio", make_wav(), "audio/wav")},
                    data={"device_id": "raspi-1"},
                )
                event = ws.receive_json()
                data = event["data"]
                required = {"id", "text", "type", "tags", "summary", "capture_mode", "created_at"}
                assert required <= set(data.keys())

    def test_multiple_clients_all_receive_broadcast(self, ws_app):
        from starlette.testclient import TestClient

        with TestClient(ws_app) as client:
            with client.websocket_connect("/ws/client") as ws1:
                with client.websocket_connect("/ws/client") as ws2:
                    client.post(
                        "/audio/capture",
                        files={"audio": ("audio", make_wav(), "audio/wav")},
                        data={"device_id": "raspi-1"},
                    )
                    event1 = ws1.receive_json()
                    event2 = ws2.receive_json()
                    assert event1["event"] == "note.created"
                    assert event2["event"] == "note.created"
                    assert event1["data"]["id"] == event2["data"]["id"]
