"""Null wake-word adapter — never fires; used when no engine is configured."""

from __future__ import annotations

from typing import Callable


class NullWakeWord:
    """Always available but never emits a wake event."""

    @property
    def available(self) -> bool:
        return True

    def start(self, on_wake: Callable[[], None]) -> None:
        pass

    def stop(self) -> None:
        pass
