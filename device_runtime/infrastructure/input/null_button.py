"""Null button adapter used for degraded or test runtimes."""

from __future__ import annotations

from typing import Callable


class NullButton:
    def __init__(self) -> None:
        self._handler: Callable[[str], None] | None = None
        self.started = False

    def start(self, on_event: Callable[[str], None]) -> None:
        self._handler = on_event
        self.started = True

    def stop(self) -> None:
        self.started = False
        self._handler = None
