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
        canvas.create_rectangle(0, 0, 360, 520, fill="#0f172a", outline="")
        canvas.create_rectangle(58, 24, 302, 496, fill=shell_fill, outline=shell_outline, width=2)
        if mode == "cased":
            canvas.create_rectangle(122, 2, 238, 34, fill="#d97706", outline="#b45309", width=2)
        led_color = self._led_color(model.local_state)
        canvas.create_oval(168, 42, 192, 66, fill=led_color, outline="#1d4ed8")
        canvas.create_rectangle(78, 88, 282, 392, fill="#020617", outline="#1e293b", width=2)
        canvas.create_text(92, 104, anchor="nw", text=model.local_state, fill="#e2e8f0", font=("Helvetica", 15, "bold"))
        canvas.create_text(268, 104, anchor="ne", text=model.remote_state, fill="#93c5fd", font=("Helvetica", 10, "bold"))
        canvas.create_text(92, 132, anchor="nw", text=f"agent: {model.active_agent}", fill="#94a3b8", font=("Helvetica", 9))
        canvas.create_text(92, 148, anchor="nw", text=f"focus: {model.focus_label}", fill="#94a3b8", font=("Helvetica", 9))
        canvas.create_text(92, 174, anchor="nw", text="mic", fill="#f8fafc", font=("Helvetica", 10, "bold"))
        canvas.create_oval(128, 175, 140, 187, fill="#ef4444" if model.mic_live else "#475569", outline="")
        canvas.create_text(146, 174, anchor="nw", text="REC" if model.mic_live else "OFF", fill="#fca5a5" if model.mic_live else "#94a3b8", font=("Helvetica", 9, "bold"))
        canvas.create_text(92, 212, anchor="nw", text="YOU", fill="#e2e8f0", font=("Helvetica", 9, "bold"))
        canvas.create_text(92, 230, anchor="nw", text=self._wrap_text(model.transcript_preview or "Tap/Press to speak", 22, 5), fill="#e2e8f0", font=("Helvetica", 10), width=166)
        canvas.create_text(92, 306, anchor="nw", text="AGENT", fill="#86efac", font=("Helvetica", 9, "bold"))
        canvas.create_text(92, 324, anchor="nw", text=self._wrap_text(model.assistant_preview or "Waiting for backend response", 22, 5), fill="#86efac", font=("Helvetica", 10), width=166)
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
            "READY": "#38bdf8",
            "LISTEN": "#ef4444",
            "MENU": "#f59e0b",
            "MODE": "#a855f7",
            "AGENTS": "#22c55e",
        }.get(local_state, "#ef4444")
