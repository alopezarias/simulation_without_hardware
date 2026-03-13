"""Device output adapter over FastAPI WebSocket."""

from __future__ import annotations

from typing import Any

from fastapi import WebSocket


class WebSocketOutput:
    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def send_json(self, message: dict[str, Any]) -> None:
        await self._websocket.send_json(message)
