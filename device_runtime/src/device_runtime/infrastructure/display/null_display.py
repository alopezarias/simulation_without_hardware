"""Null display adapter used for degraded or test runtimes."""

from __future__ import annotations

from typing import Any


class NullDisplay:
    def __init__(self) -> None:
        self.rendered: list[Any] = []
        self.diagnostics: list[str] = []

    def render(self, model: Any) -> None:
        self.rendered.append(model)

    def show_diagnostic(self, line: str) -> None:
        self.diagnostics.append(line)
