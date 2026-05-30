from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class NotifierPort(ABC):
    @abstractmethod
    async def broadcast(self, event: str, data: dict[str, Any]) -> None: ...
