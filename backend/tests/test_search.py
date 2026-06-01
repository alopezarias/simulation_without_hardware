"""Note search tests — GET /notes?q=<text>.

Covers the full stack from HTTP query param through the SQLite LIKE filter.
Uses the shared db_session + client fixtures from conftest (in-memory SQLite,
NullStt, NullClassifier).

Run as part of the normal test suite:
    pytest backend/tests/test_search.py -v
"""

from __future__ import annotations

import pytest

from backend.tests.conftest import make_wav


# ── helpers ───────────────────────────────────────────────────────────────────

async def _capture(client, *, device_id: str = "search-device", capture_mode: str = "manual"):
    resp = await client.post(
        "/audio/capture",
        files={"audio": ("a.wav", make_wav(), "audio/wav")},
        data={"device_id": device_id, "capture_mode": capture_mode},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


# ── NullStt returns fixed text "meeting notes from today" (see conftest) ──────

class TestSearch:

    @pytest.mark.asyncio
    async def test_search_matching_text_returns_note(self, client):
        await _capture(client)
        resp = await client.get("/notes?q=meeting")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert all("meeting" in n["text"].lower() for n in data["items"])

    @pytest.mark.asyncio
    async def test_search_no_match_returns_empty(self, client):
        await _capture(client)
        resp = await client.get("/notes?q=zxqyplonk")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    @pytest.mark.asyncio
    async def test_search_is_case_insensitive(self, client):
        await _capture(client)
        upper = (await client.get("/notes?q=MEETING")).json()
        lower = (await client.get("/notes?q=meeting")).json()
        assert upper["total"] == lower["total"]
        assert upper["total"] >= 1

    @pytest.mark.asyncio
    async def test_search_without_q_returns_all_notes(self, client):
        for _ in range(3):
            await _capture(client)
        all_notes = (await client.get("/notes")).json()
        with_empty_q = (await client.get("/notes?q=")).json()
        assert all_notes["total"] == with_empty_q["total"] == 3

    @pytest.mark.asyncio
    async def test_search_partial_word_matches(self, client):
        await _capture(client)
        resp = await client.get("/notes?q=meet")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    @pytest.mark.asyncio
    async def test_search_combined_with_type_filter(self, client):
        """?q combined with ?type filters both simultaneously."""
        b = await _capture(client)
        note_type = (await client.get(f"/notes/{b['note_id']}")).json()["type"]

        # Matching query + matching type
        hit = (await client.get(f"/notes?q=meeting&type={note_type}")).json()
        assert hit["total"] >= 1

        # Matching query + non-matching type
        miss = (await client.get("/notes?q=meeting&type=task")).json()
        # NullClassifier never returns "task", so this should be 0
        # (unless the note happens to be classified as task, which it won't be)
        assert miss["total"] == 0 or all(n["type"] == "task" for n in miss["items"])

    @pytest.mark.asyncio
    async def test_search_respects_pagination(self, client):
        for _ in range(5):
            await _capture(client)
        page1 = (await client.get("/notes?q=meeting&page=1&limit=2")).json()
        assert len(page1["items"]) == 2
        assert page1["total"] == 5

    @pytest.mark.asyncio
    async def test_search_empty_database_returns_empty(self, client):
        resp = await client.get("/notes?q=anything")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    @pytest.mark.asyncio
    async def test_search_preserves_total_count_accuracy(self, client):
        """total should count ALL matching notes, not just the current page."""
        for _ in range(4):
            await _capture(client)
        data = (await client.get("/notes?q=meeting&page=1&limit=2")).json()
        assert data["total"] == 4
        assert data["pages"] == 2
