from __future__ import annotations

import json
import logging
from typing import Any

from starlette.websockets import WebSocket

from backend.application.ports.notifier_port import NotifierPort

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Tracks active WebSocket connections and fans out broadcast messages."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)
        logger.debug("WS client connected. active=%d", len(self._connections))

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws)
        logger.debug("WS client disconnected. active=%d", len(self._connections))

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    async def broadcast(self, event: str, data: dict[str, Any]) -> None:
        if not self._connections:
            return
        message = json.dumps({"event": event, "data": data})
        dead: set[WebSocket] = set()
        for ws in list(self._connections):
            try:
                await ws.send_text(message)
            except Exception as exc:
                logger.debug("WS send failed, dropping connection: %s", exc)
                dead.add(ws)
        self._connections -= dead


class WebSocketNotifier(NotifierPort):
    def __init__(self, manager: ConnectionManager) -> None:
        self._manager = manager

    async def broadcast(self, event: str, data: dict[str, Any]) -> None:
        await self._manager.broadcast(event, data)


# Process-level singleton — shared between the WS endpoint and the notifier dep.
_default_manager = ConnectionManager()
