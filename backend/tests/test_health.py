"""Tests for the /health endpoint."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_health_returns_200(client):
    response = await client.get("/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_health_payload_shape(client):
    data = (await client.get("/health")).json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "whisper_model" in data
    assert "ai_provider" in data
    assert "db" in data


@pytest.mark.asyncio
async def test_health_db_ok(client):
    data = (await client.get("/health")).json()
    assert data["db"] == "ok"


@pytest.mark.asyncio
async def test_health_ai_provider_reflects_settings(client):
    data = (await client.get("/health")).json()
    assert data["ai_provider"] == "null"
