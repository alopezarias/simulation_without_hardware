"""Null diagnostics adapter used for degraded or test runtimes."""

from __future__ import annotations

from typing import Any


class NullDiagnostics:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def record(self, event: str, **data: Any) -> None:
        self.events.append((event, data))
