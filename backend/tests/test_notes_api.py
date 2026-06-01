"""Tests for GET /notes, GET /notes/{id}, PUT /notes/{id}, DELETE /notes/{id}, GET /notes/{id}/audio."""

from __future__ import annotations

import pytest

from backend.tests.conftest import make_wav


async def _create_note(client, device_id: str = "raspi-1", capture_mode: str = "wake_word") -> str:
    resp = await client.post(
        "/audio/capture",
        files={"audio": ("audio", make_wav(), "audio/wav")},
        data={"device_id": device_id, "capture_mode": capture_mode},
    )
    assert resp.status_code == 202
    return resp.json()["note_id"]


# ── GET /notes ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_notes_empty(client):
    resp = await client.get("/notes")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["page"] == 1


@pytest.mark.asyncio
async def test_list_notes_returns_created_note(client):
    note_id = await _create_note(client)
    resp = await client.get("/notes")
    assert resp.status_code == 200
    ids = [n["id"] for n in resp.json()["items"]]
    assert note_id in ids


@pytest.mark.asyncio
async def test_list_notes_total_count(client):
    await _create_note(client)
    await _create_note(client)
    resp = await client.get("/notes")
    assert resp.json()["total"] == 2


@pytest.mark.asyncio
async def test_list_notes_ordered_newest_first(client):
    id1 = await _create_note(client)
    id2 = await _create_note(client)
    items = (await client.get("/notes")).json()["items"]
    # Newest first
    assert items[0]["id"] == id2
    assert items[1]["id"] == id1


@pytest.mark.asyncio
async def test_list_notes_filter_by_type(client):
    await _create_note(client)  # NullClassifier always returns type=note
    resp = await client.get("/notes", params={"type": "note"})
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1
    for item in resp.json()["items"]:
        assert item["type"] == "note"


@pytest.mark.asyncio
async def test_list_notes_filter_no_match(client):
    await _create_note(client)
    resp = await client.get("/notes", params={"type": "task"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_list_notes_invalid_type_returns_422(client):
    resp = await client.get("/notes", params={"type": "invalid"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_notes_pagination(client):
    for _ in range(5):
        await _create_note(client)
    page1 = (await client.get("/notes", params={"page": 1, "limit": 3})).json()
    page2 = (await client.get("/notes", params={"page": 2, "limit": 3})).json()
    assert len(page1["items"]) == 3
    assert len(page2["items"]) == 2
    assert page1["total"] == 5
    assert page1["pages"] == 2
    ids_p1 = {n["id"] for n in page1["items"]}
    ids_p2 = {n["id"] for n in page2["items"]}
    assert ids_p1.isdisjoint(ids_p2)


@pytest.mark.asyncio
async def test_list_notes_limit_capped_at_100(client):
    resp = await client.get("/notes", params={"limit": 200})
    assert resp.status_code == 422


# ── GET /notes/{id} ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_note_existing(client):
    note_id = await _create_note(client, device_id="raspi-desk")
    resp = await client.get(f"/notes/{note_id}")
    assert resp.status_code == 200
    note = resp.json()
    assert note["id"] == note_id
    assert note["device_id"] == "raspi-desk"
    assert "text" in note
    assert "type" in note
    assert "tags" in note
    assert "created_at" in note


@pytest.mark.asyncio
async def test_get_note_not_found(client):
    resp = await client.get("/notes/nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_note_has_all_fields(client):
    note_id = await _create_note(client)
    note = (await client.get(f"/notes/{note_id}")).json()
    required_fields = {"id", "device_id", "text", "type", "tags", "entities",
                       "summary", "audio_path", "duration_s", "annotation", "capture_mode", "created_at"}
    assert required_fields <= set(note.keys())


# ── DELETE /notes/{id} ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_note_returns_204(client):
    note_id = await _create_note(client)
    resp = await client.delete(f"/notes/{note_id}")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_note_removes_from_list(client):
    note_id = await _create_note(client)
    await client.delete(f"/notes/{note_id}")
    items = (await client.get("/notes")).json()["items"]
    assert all(n["id"] != note_id for n in items)


@pytest.mark.asyncio
async def test_delete_note_get_returns_404_after(client):
    note_id = await _create_note(client)
    await client.delete(f"/notes/{note_id}")
    resp = await client.get(f"/notes/{note_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_note_not_found_returns_404(client):
    resp = await client.delete("/notes/does-not-exist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_note_removes_audio_file(client, tmp_path):
    import pathlib
    note_id = await _create_note(client)
    note = (await client.get(f"/notes/{note_id}")).json()
    audio_path = pathlib.Path(note["audio_path"])
    assert audio_path.exists()
    await client.delete(f"/notes/{note_id}")
    assert not audio_path.exists()


# ── PUT /notes/{id} ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_note_text(client):
    note_id = await _create_note(client)
    resp = await client.put(f"/notes/{note_id}", json={"text": "updated text"})
    assert resp.status_code == 200
    assert resp.json()["text"] == "updated text"


@pytest.mark.asyncio
async def test_update_note_annotation(client):
    note_id = await _create_note(client)
    resp = await client.put(f"/notes/{note_id}", json={"annotation": "my note about this"})
    assert resp.status_code == 200
    assert resp.json()["annotation"] == "my note about this"


@pytest.mark.asyncio
async def test_update_note_both_fields(client):
    note_id = await _create_note(client)
    resp = await client.put(f"/notes/{note_id}", json={"text": "revised", "annotation": "context"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "revised"
    assert body["annotation"] == "context"


@pytest.mark.asyncio
async def test_update_note_not_found_returns_404(client):
    resp = await client.put("/notes/nonexistent", json={"text": "nope"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_note_no_fields_returns_422(client):
    note_id = await _create_note(client)
    resp = await client.put(f"/notes/{note_id}", json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_note_persists_on_get(client):
    note_id = await _create_note(client)
    await client.put(f"/notes/{note_id}", json={"annotation": "persisted"})
    note = (await client.get(f"/notes/{note_id}")).json()
    assert note["annotation"] == "persisted"


@pytest.mark.asyncio
async def test_update_note_response_has_annotation_field(client):
    note_id = await _create_note(client)
    note = (await client.get(f"/notes/{note_id}")).json()
    assert "annotation" in note


# ── GET /notes/{id}/audio ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_audio_returns_wav(client):
    note_id = await _create_note(client)
    resp = await client.get(f"/notes/{note_id}/audio")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"


@pytest.mark.asyncio
async def test_get_audio_content_matches_upload(client):
    wav_bytes = make_wav(duration_s=1.0)
    resp = await client.post(
        "/audio/capture",
        files={"audio": ("audio", wav_bytes, "audio/wav")},
        data={"device_id": "raspi-1"},
    )
    note_id = resp.json()["note_id"]
    audio_resp = await client.get(f"/notes/{note_id}/audio")
    assert audio_resp.content == wav_bytes


@pytest.mark.asyncio
async def test_get_audio_not_found_returns_404(client):
    resp = await client.get("/notes/nonexistent-id/audio")
    assert resp.status_code == 404
