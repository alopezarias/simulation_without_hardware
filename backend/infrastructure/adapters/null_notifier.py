from __future__ import annotations

from typing import Any

from backend.application.ports.notifier_port import NotifierPort


class NullNotifier(NotifierPort):
    """No-op notifier. Feature 4 replaces this with the WebSocket fan-out."""

    async def broadcast(self, event: str, data: dict[str, Any]) -> None:
        pass
