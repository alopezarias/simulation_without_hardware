"""Tk preview adapter that renders the shared screen view model."""

from __future__ import annotations

import textwrap
from typing import Any, Callable

from device_runtime.application.services.display_model_service import ScreenViewModel


class TkPreviewDisplay:
    def __init__(self, canvas: Any, *, mode_provider: Callable[[], str]) -> None:
        self._canvas = canvas
        self._mode_provider = mode_provider
        self._diagnostic = ""

    def render(self, model: ScreenViewModel) -> None:
        canvas = self._canvas
        canvas.delete("all")
        mode = self._mode_provider().strip().lower()
        shell_fill = "#f8fafc" if mode == "cased" else "#111827"
        shell_outline = "#cbd5e1" if mode == "cased" else "#334155"
        canvas.create_rectangle(0, 0, 360, 520, fill="#08131f", outline="")
        canvas.create_rectangle(58, 24, 302, 496, fill=shell_fill, outline=shell_outline, width=2)
        if mode == "cased":
            canvas.create_rectangle(122, 2, 238, 34, fill="#d97706", outline="#b45309", width=2)
        led_color = self._led_color(model.local_state)
        canvas.create_oval(168, 42, 192, 66, fill=led_color, outline="#1d4ed8")
        canvas.create_rectangle(78, 88, 282, 392, fill="#020617", outline="#1e293b", width=2)
        canvas.create_text(92, 106, anchor="nw", text=model.status_text, fill=led_color, font=("Helvetica", 15, "bold"))
        canvas.create_text(268, 106, anchor="ne", text=model.battery_label, fill="#f8fafc", font=("Helvetica", 10, "bold"))
        canvas.create_text(180, 188, anchor="center", text=self._wrap_text(model.center_title or model.status_detail, 18, 2), fill="#f8fafc", font=("Helvetica", 16, "bold"), width=170)
        canvas.create_text(180, 250, anchor="center", text=self._wrap_text(model.center_body or model.assistant_preview or model.transcript_preview, 22, 3), fill="#dbeafe", font=("Helvetica", 11), width=176)
        canvas.create_text(180, 314, anchor="center", text=self._wrap_text(model.center_hint or model.network_label, 22, 2), fill=led_color, font=("Helvetica", 10, "bold"), width=170)
        footer = self._diagnostic or (model.warnings[0] if model.warnings else ("connected" if model.connected else "offline"))
        canvas.create_text(180, 452, anchor="center", text=footer, fill="#f8fafc", font=("Helvetica", 9), width=220)

    def show_diagnostic(self, line: str) -> None:
        self._diagnostic = line

    def _wrap_text(self, text: str, width: int, lines: int) -> str:
        compact = " ".join(text.split()).strip()
        if not compact:
            return "-"
        wrapped = textwrap.wrap(compact, width=width, break_long_words=True, break_on_hyphens=False)
        if len(wrapped) > lines:
            wrapped = wrapped[:lines]
            wrapped[-1] = wrapped[-1][: max(0, width - 3)] + "..."
        return "\n".join(wrapped)

    def _led_color(self, local_state: str) -> str:
        return {
            "LOCKED": "#475569",
            "READY": "#38e86d",
            "LISTEN": "#ffd60a",
            "MENU": "#ffd60a",
            "MODE": "#4ade80",
            "AGENTS": "#4ade80",
        }.get(local_state, "#40c4ff")
