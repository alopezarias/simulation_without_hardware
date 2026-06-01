"""End-to-end smoke tests — critical happy path from audio capture to the UI.

Each test exercises a complete user scenario at the HTTP boundary without
mocking internal services.  NullStt and NullClassifier replace the real
models so no API keys or GPU are required; the storage layer uses an
in-memory SQLite backed by the shared db_session fixture.

Run as part of the normal test suite:
    pytest backend/tests/test_e2e_smoke.py -v
"""

from __future__ import annotations

import io
import json
import os
import wave

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from backend.tests.conftest import make_wav


# ── local helpers ─────────────────────────────────────────────────────────────

def _wav_upload(duration_s: float = 0.5) -> tuple[str, bytes, str]:
    return ("audio", make_wav(duration_s), "audio/wav")


async def _capture(client: AsyncClient, *, capture_mode: str = "manual", device_id: str = "smoke-device") -> dict:
    resp = await client.post(
        "/audio/capture",
        files={"audio": _wav_upload()},
        data={"device_id": device_id, "capture_mode": capture_mode},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


# ── HTTP smoke tests (async) ──────────────────────────────────────────────────

class TestSmoke:
    """Full happy-path HTTP scenarios."""

    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "version" in body

    @pytest.mark.asyncio
    async def test_full_note_lifecycle(self, client):
        """Capture → retrieve → audio → delete → 404."""

        # 1. Capture audio
        body = await _capture(client, capture_mode="manual")
        note_id = body["note_id"]
        assert body["status"] == "created"
        assert body["duration_s"] > 0

        # 2. Note appears in the feed
        feed = await client.get("/notes")
        assert feed.status_code == 200
        items = feed.json()["items"]
        assert any(n["id"] == note_id for n in items)
        assert feed.json()["total"] >= 1

        # 3. Retrieve the specific note
        detail = await client.get(f"/notes/{note_id}")
        assert detail.status_code == 200
        note = detail.json()
        assert note["id"] == note_id
        assert note["text"]            # NullStt returns fixed text
        assert note["capture_mode"] == "manual"
        assert note["device_id"] == "smoke-device"
        assert note["created_at"]

        # 4. Audio file is a valid WAV
        audio = await client.get(f"/notes/{note_id}/audio")
        assert audio.status_code == 200
        assert "audio" in audio.headers["content-type"]
        with wave.open(io.BytesIO(audio.content)) as wf:
            assert wf.getnchannels() == 1
            assert wf.getframerate() == 16000

        # 5. Delete removes the note
        delete = await client.delete(f"/notes/{note_id}")
        assert delete.status_code == 204

        # 6. Note is gone
        gone = await client.get(f"/notes/{note_id}")
        assert gone.status_code == 404

        # 7. Feed is empty again
        empty = await client.get("/notes")
        assert empty.json()["total"] == 0

    @pytest.mark.asyncio
    async def test_wake_word_capture_mode_stored(self, client):
        body = await _capture(client, capture_mode="wake_word")
        detail = await client.get(f"/notes/{body['note_id']}")
        assert detail.json()["capture_mode"] == "wake_word"

    @pytest.mark.asyncio
    async def test_type_filter_returns_only_matching_notes(self, client):
        """Two notes captured; type filter returns only the queried type."""
        b1 = await _capture(client)
        b2 = await _capture(client)
        # NullClassifier always produces type=note, so filter by a different type returns 0
        filtered = await client.get("/notes?type=task")
        assert filtered.status_code == 200
        assert filtered.json()["total"] == 0

        # Filter by the actual type (note) returns all captured notes
        note_type = (await client.get(f"/notes/{b1['note_id']}")).json()["type"]
        filtered_match = await client.get(f"/notes?type={note_type}")
        assert filtered_match.json()["total"] >= 2

    @pytest.mark.asyncio
    async def test_pagination_limits_results(self, client):
        for _ in range(5):
            await _capture(client)
        page1 = (await client.get("/notes?page=1&limit=3")).json()
        assert len(page1["items"]) == 3
        assert page1["total"] == 5
        assert page1["pages"] >= 2

    @pytest.mark.asyncio
    async def test_capture_response_includes_classification_fields(self, client):
        body = await _capture(client)
        assert "type" in body
        assert "summary" in body
        assert body["type"] in {"task", "idea", "reminder", "note", "dictation"}

    @pytest.mark.asyncio
    async def test_audio_missing_returns_400(self, client):
        resp = await client.post("/audio/capture", data={"capture_mode": "manual"})
        assert resp.status_code in {400, 422}

    @pytest.mark.asyncio
    async def test_delete_nonexistent_note_returns_404(self, client):
        resp = await client.delete("/notes/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_nonexistent_note_returns_404(self, client):
        resp = await client.get("/notes/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


# ── Auth smoke tests ──────────────────────────────────────────────────────────

class TestSmokeAuth:
    """Token enforcement: when a token is configured, all endpoints require it."""

    @pytest.fixture
    def token_app(self, db_session, tmp_path):
        """App variant with auth token enforced."""
        from backend.api.app import create_app
        from backend.api.dependencies import (
            get_audio_store,
            get_classifier,
            get_connection_manager,
            get_notifier,
            get_stt,
        )
        from backend.config.settings import get_settings
        from backend.infrastructure.adapters.local_audio_store import LocalAudioStore
        from backend.infrastructure.adapters.null_classifier import NullClassifier
        from backend.infrastructure.adapters.null_notifier import NullNotifier
        from backend.infrastructure.adapters.null_stt import NullStt
        from backend.infrastructure.adapters.ws_notifier import ConnectionManager
        from backend.infrastructure.db.database import get_db

        os.environ["NOTES_API_TOKEN"] = "smoke-token"
        get_settings.cache_clear()

        _app = create_app()

        async def _override_db():
            yield db_session

        _app.dependency_overrides[get_db] = _override_db
        _app.dependency_overrides[get_stt] = lambda: NullStt()
        _app.dependency_overrides[get_classifier] = lambda: NullClassifier()
        _app.dependency_overrides[get_notifier] = lambda: NullNotifier()
        _app.dependency_overrides[get_connection_manager] = lambda: ConnectionManager()
        _app.dependency_overrides[get_audio_store] = lambda: LocalAudioStore(str(tmp_path))

        yield _app

        os.environ.pop("NOTES_API_TOKEN", None)
        get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_missing_token_returns_401(self, token_app):
        async with AsyncClient(transport=ASGITransport(app=token_app), base_url="http://test") as ac:
            resp = await ac.get("/notes")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_token_returns_401(self, token_app):
        async with AsyncClient(
            transport=ASGITransport(app=token_app),
            base_url="http://test",
            headers={"Authorization": "Bearer wrong-token"},
        ) as ac:
            resp = await ac.get("/notes")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_correct_token_grants_access(self, token_app):
        async with AsyncClient(
            transport=ASGITransport(app=token_app),
            base_url="http://test",
            headers={"Authorization": "Bearer smoke-token"},
        ) as ac:
            resp = await ac.get("/notes")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_health_is_public(self, token_app):
        """Health endpoint must not require auth (needed for Docker healthcheck)."""
        async with AsyncClient(transport=ASGITransport(app=token_app), base_url="http://test") as ac:
            resp = await ac.get("/health")
        assert resp.status_code == 200


# ── WebSocket smoke tests (sync) ──────────────────────────────────────────────

class TestSmokeWebSocket:
    """WebSocket critical path: connect, keepalive, and real-time note broadcast."""

    def test_ws_connect_and_ping_pong(self, ws_app):
        with TestClient(ws_app) as tc:
            with tc.websocket_connect("/ws/client") as ws:
                ws.send_text(json.dumps({"type": "ping"}))
                msg = json.loads(ws.receive_text())
                assert msg["type"] == "pong"

    def test_ws_receives_note_created_on_capture(self, ws_app):
        with TestClient(ws_app) as tc:
            with tc.websocket_connect("/ws/client") as ws:
                resp = tc.post(
                    "/audio/capture",
                    files={"audio": ("audio.wav", make_wav(), "audio/wav")},
                    data={"capture_mode": "wake_word", "device_id": "ws-test"},
                )
                assert resp.status_code == 202
                note_id = resp.json()["note_id"]

                event = json.loads(ws.receive_text())
                assert event["event"] == "note.created"
                assert event["data"]["id"] == note_id
                assert event["data"]["capture_mode"] == "wake_word"

    def test_ws_multiple_clients_all_receive_broadcast(self, ws_app):
        with TestClient(ws_app) as tc:
            with tc.websocket_connect("/ws/client") as ws1, \
                 tc.websocket_connect("/ws/client") as ws2:
                tc.post(
                    "/audio/capture",
                    files={"audio": ("audio.wav", make_wav(), "audio/wav")},
                    data={"capture_mode": "manual", "device_id": "ws-test"},
                )
                e1 = json.loads(ws1.receive_text())
                e2 = json.loads(ws2.receive_text())
                assert e1["event"] == "note.created"
                assert e2["event"] == "note.created"
                assert e1["data"]["id"] == e2["data"]["id"]

    def test_ws_connection_closes_cleanly_on_disconnect(self, ws_app):
        with TestClient(ws_app) as tc:
            with tc.websocket_connect("/ws/client") as ws:
                ws.send_text(json.dumps({"type": "ping"}))
                ws.receive_text()
            # exiting the context manager closes cleanly — no exception means success
