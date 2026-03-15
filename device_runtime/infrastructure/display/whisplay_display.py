"""Guarded Raspberry Pi display adapter with vendor fallbacks."""

from __future__ import annotations

import importlib
from typing import Any, Callable

from device_runtime.application.services.display_model_service import ScreenViewModel

try:
    _whisplay = importlib.import_module("whisplay")

    WHISPLAY_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - depends on host environment
    _whisplay = None
    WHISPLAY_IMPORT_ERROR = exc


class WhisplayDisplay:
    def __init__(self, *, driver: Any | None = None, driver_factory: Callable[[], Any] | None = None) -> None:
        self._driver_factory = driver_factory
        self._driver = driver
        self.last_model: Any | None = None
        self.last_frame: dict[str, Any] | None = None
        self.diagnostics: list[str] = []
        self._diagnostic_line = ""

    @property
    def available(self) -> bool:
        return self._driver is not None or self._driver_factory is not None or _whisplay is not None

    def render(self, model: ScreenViewModel | Any) -> None:
        driver = self._ensure_driver()
        self.last_model = model
        if hasattr(driver, "render") and not self._looks_like_screen_model(model):
            driver.render(model)
            self.last_frame = None
            return
        frame = self._build_frame(model)
        self.last_frame = frame
        self._render_frame(driver, frame)

    def show_diagnostic(self, line: str) -> None:
        self.diagnostics.append(line)
        self._diagnostic_line = line.strip()
        driver = self._ensure_driver()
        if hasattr(driver, "show_diagnostic"):
            driver.show_diagnostic(line)
            if not self._looks_like_screen_model(self.last_model):
                return
        if self.last_model is not None:
            frame = self._build_frame(self.last_model)
            self.last_frame = frame
            self._render_frame(driver, frame)
            return

    def _ensure_driver(self) -> Any:
        if self._driver is not None:
            return self._driver
        if self._driver_factory is not None:
            self._driver = self._driver_factory()
            return self._driver
        if _whisplay is None:
            detail = ""
            if WHISPLAY_IMPORT_ERROR is not None:
                detail = f": {WHISPLAY_IMPORT_ERROR}"
            raise RuntimeError("Whisplay display adapter requires vendor Python bindings" + detail)
        self._driver = _whisplay.Display()
        return self._driver

    def _build_frame(self, model: ScreenViewModel | Any) -> dict[str, Any]:
        warning = self._diagnostic_line
        if not warning:
            warnings = getattr(model, "warnings", []) or []
            if warnings:
                warning = str(warnings[0]).strip()
        footer = warning or ("connected" if bool(getattr(model, "connected", False)) else "offline")
        lines = [
            str(getattr(model, "local_state", "-")),
            f"remote {str(getattr(model, 'remote_state', '-'))}",
            f"agent  {str(getattr(model, 'active_agent', '-'))}",
            f"focus  {str(getattr(model, 'focus_label', '-'))}",
            f"mic    {'REC' if bool(getattr(model, 'mic_live', False)) else 'OFF'}",
            f"you    {self._one_line(getattr(model, 'transcript_preview', ''))}",
            f"agent  {self._one_line(getattr(model, 'assistant_preview', ''))}",
            footer,
        ]
        return {
            "title": "device-runtime",
            "local_state": str(getattr(model, "local_state", "-")),
            "remote_state": str(getattr(model, "remote_state", "-")),
            "active_agent": str(getattr(model, "active_agent", "-")),
            "focus_label": str(getattr(model, "focus_label", "-")),
            "mic_live": bool(getattr(model, "mic_live", False)),
            "connected": bool(getattr(model, "connected", False)),
            "footer": footer,
            "lines": lines,
        }

    def _render_frame(self, driver: Any, frame: dict[str, Any]) -> None:
        if hasattr(driver, "render"):
            driver.render(frame)
            return
        if hasattr(driver, "display_frame"):
            driver.display_frame(frame)
            return
        if hasattr(driver, "display"):
            driver.display(frame)
            return
        if hasattr(driver, "show"):
            driver.show(frame)
            return
        if hasattr(driver, "clear"):
            driver.clear()
        text_method = None
        for name in ("draw_text", "text", "write_line"):
            candidate = getattr(driver, name, None)
            if candidate is not None:
                text_method = candidate
                break
        if text_method is not None:
            for row, line in enumerate(frame["lines"]):
                try:
                    text_method(row, line)
                except TypeError:
                    text_method(line)
        present = getattr(driver, "present", None) or getattr(driver, "refresh", None)
        if present is not None:
            present()

    def _one_line(self, value: Any, *, limit: int = 28) -> str:
        text = " ".join(str(value or "").split()).strip()
        if not text:
            return "-"
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."

    def _looks_like_screen_model(self, model: Any) -> bool:
        if model is None:
            return False
        return all(hasattr(model, name) for name in ("local_state", "remote_state", "active_agent", "focus_label"))
