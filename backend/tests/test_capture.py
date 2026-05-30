"""Tests for POST /audio/capture."""

from __future__ import annotations

import io
import os

import pytest

from backend.tests.conftest import make_wav


def _wav_file(duration_s: float = 0.5) -> tuple[str, bytes, str]:
    return ("audio", make_wav(duration_s), "audio/wav")


# ── Happy path ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_capture_returns_202(client):
    resp = await client.post(
        "/audio/capture",
        files={"audio": _wav_file()},
        data={"device_id": "raspi-1", "capture_mode": "wake_word"},
    )
    assert resp.status_code == 202


@pytest.mark.asyncio
async def test_capture_response_shape(client):
    resp = await client.post(
        "/audio/capture",
        files={"audio": _wav_file()},
        data={"device_id": "raspi-1"},
    )
    body = resp.json()
    assert "note_id" in body
    assert body["status"] == "created"
    assert "type" in body
    assert "summary" in body
    assert "duration_s" in body


@pytest.mark.asyncio
async def test_capture_note_persisted_in_db(client):
    resp = await client.post(
        "/audio/capture",
        files={"audio": _wav_file()},
        data={"device_id": "raspi-home", "capture_mode": "manual"},
    )
    note_id = resp.json()["note_id"]
    note_resp = await client.get(f"/notes/{note_id}")
    assert note_resp.status_code == 200
    note = note_resp.json()
    assert note["id"] == note_id
    assert note["device_id"] == "raspi-home"
    assert note["capture_mode"] == "manual"


@pytest.mark.asyncio
async def test_capture_transcription_stored(client):
    resp = await client.post(
        "/audio/capture",
        files={"audio": _wav_file()},
        data={"device_id": "raspi-1"},
    )
    note_id = resp.json()["note_id"]
    note = (await client.get(f"/notes/{note_id}")).json()
    # NullStt returns "meeting notes from today"
    assert note["text"] == "meeting notes from today"


@pytest.mark.asyncio
async def test_capture_audio_file_saved(client, tmp_path):
    resp = await client.post(
        "/audio/capture",
        files={"audio": _wav_file()},
        data={"device_id": "raspi-1"},
    )
    note_id = resp.json()["note_id"]
    note = (await client.get(f"/notes/{note_id}")).json()
    assert note["audio_path"]
    import pathlib
    assert pathlib.Path(note["audio_path"]).exists()


@pytest.mark.asyncio
async def test_capture_default_capture_mode_is_wake_word(client):
    resp = await client.post(
        "/audio/capture",
        files={"audio": _wav_file()},
        data={"device_id": "raspi-1"},
    )
    note_id = resp.json()["note_id"]
    note = (await client.get(f"/notes/{note_id}")).json()
    assert note["capture_mode"] == "wake_word"


# ── Validation errors ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_capture_missing_audio_field_returns_422(client):
    resp = await client.post(
        "/audio/capture",
        data={"device_id": "raspi-1"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_capture_missing_device_id_returns_422(client):
    resp = await client.post(
        "/audio/capture",
        files={"audio": _wav_file()},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_capture_empty_audio_returns_422(client):
    resp = await client.post(
        "/audio/capture",
        files={"audio": ("audio", b"", "audio/wav")},
        data={"device_id": "raspi-1"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_capture_invalid_capture_mode_returns_422(client):
    resp = await client.post(
        "/audio/capture",
        files={"audio": _wav_file()},
        data={"device_id": "raspi-1", "capture_mode": "invalid_mode"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_capture_oversized_audio_returns_413(client):
    # ~2MB = well over the 60s limit
    big_audio = b"\x00" * (2 * 1024 * 1024)
    resp = await client.post(
        "/audio/capture",
        files={"audio": ("audio", big_audio, "audio/wav")},
        data={"device_id": "raspi-1"},
    )
    assert resp.status_code == 413


# ── Auth ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_capture_no_auth_required_when_token_empty(client):
    # Default test settings have no token → auth disabled
    resp = await client.post(
        "/audio/capture",
        files={"audio": _wav_file()},
        data={"device_id": "raspi-1"},
    )
    assert resp.status_code == 202


@pytest.mark.asyncio
async def test_capture_wrong_token_returns_401(app):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer wrong-token"},
    ) as c:
        # Temporarily enable auth
        os.environ["NOTES_API_TOKEN"] = "correct-token"
        from backend.config.settings import get_settings
        get_settings.cache_clear()
        resp = await c.post(
            "/audio/capture",
            files={"audio": _wav_file()},
            data={"device_id": "raspi-1"},
        )
    os.environ.pop("NOTES_API_TOKEN", None)
    get_settings.cache_clear()
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_capture_correct_token_allowed(authed_client):
    os.environ["NOTES_API_TOKEN"] = "test-token"
    from backend.config.settings import get_settings
    get_settings.cache_clear()
    resp = await authed_client.post(
        "/audio/capture",
        files={"audio": _wav_file()},
        data={"device_id": "raspi-1"},
    )
    os.environ.pop("NOTES_API_TOKEN", None)
    get_settings.cache_clear()
    assert resp.status_code == 202
