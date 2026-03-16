"""Guarded Raspberry Pi display adapter with vendor fallbacks."""

from __future__ import annotations

import importlib
from typing import Any, Callable

from device_runtime.application.services.display_model_service import ScreenViewModel
from device_runtime.infrastructure.whisplay_vendor import load_whisplay_vendor


class WhisplayDisplay:
    def __init__(
        self,
        *,
        driver: Any | None = None,
        driver_factory: Callable[[], Any] | None = None,
        driver_path: str = "",
        backlight: int = 50,
    ) -> None:
        self._driver_factory = driver_factory
        self._driver = driver
        self._driver_path = driver_path
        self._backlight = backlight
        self.last_model: Any | None = None
        self.last_frame: dict[str, Any] | None = None
        self.diagnostics: list[str] = []
        self._diagnostic_line = ""

    @property
    def available(self) -> bool:
        if self._driver is not None or self._driver_factory is not None:
            return True
        return load_whisplay_vendor(self._driver_path).available

    def get_rgb_controller(self) -> Any | None:
        try:
            driver = self.get_board()
        except RuntimeError:
            return None
        if hasattr(driver, "set_rgb"):
            return driver
        return None

    def get_board(self) -> Any:
        return self._ensure_driver()

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
        vendor = load_whisplay_vendor(self._driver_path)
        self._driver = vendor.create_board()
        set_backlight = getattr(self._driver, "set_backlight", None)
        if callable(set_backlight):
            set_backlight(self._backlight)
        return self._driver

    def _build_frame(self, model: ScreenViewModel | Any) -> dict[str, Any]:
        warning = self._diagnostic_line
        if not warning:
            warnings = getattr(model, "warnings", []) or []
            if warnings:
                warning = str(warnings[0]).strip()
        top_row = self._compose_top_row(
            getattr(model, "status_text", getattr(model, "local_state", "-")),
            getattr(model, "battery_label", "BAT --"),
        )
        footer = self._one_line(
            warning
            or getattr(model, "diagnostics_label", "")
            or getattr(model, "network_label", "")
            or ("connected" if bool(getattr(model, "connected", False)) else "offline"),
            limit=30,
        )
        center_title = self._one_line(
            getattr(model, "center_title", getattr(model, "status_detail", "")),
            limit=40,
        )
        center_body = self._one_line(
            getattr(model, "center_body", getattr(model, "assistant_preview", "")),
            limit=52,
        )
        center_hint = self._one_line(
            getattr(model, "center_hint", getattr(model, "network_label", "")),
            limit=40,
        )
        body_lines = self._wrap_lines(
            center_body,
            limit=24,
            max_lines=2,
        )
        lines = [
            top_row,
            center_title,
            *body_lines,
            center_hint,
            footer,
        ]
        return {
            "scene": str(getattr(model, "scene", "ready")),
            "status_text": str(getattr(model, "status_text", getattr(model, "local_state", "-"))),
            "status_detail": str(getattr(model, "status_detail", "")),
            "center_title": center_title,
            "center_body": center_body,
            "center_hint": center_hint,
            "local_state": str(getattr(model, "local_state", "-")),
            "remote_state": str(getattr(model, "remote_state", "-")),
            "active_agent": str(getattr(model, "active_agent", "-")),
            "focus_label": str(getattr(model, "focus_label", "-")),
            "mic_live": bool(getattr(model, "mic_live", False)),
            "connected": bool(getattr(model, "connected", False)),
            "network_label": str(getattr(model, "network_label", "NET --")),
            "battery_label": str(getattr(model, "battery_label", "BAT --")),
            "diagnostics_label": str(getattr(model, "diagnostics_label", "")),
            "header_badges": list(getattr(model, "header_badges", [])),
            "footer": footer,
            "top_row": top_row,
            "lines": lines[:6],
        }

    def _render_frame(self, driver: Any, frame: dict[str, Any]) -> None:
        if hasattr(driver, "render"):
            driver.render(frame)
            return
        if self._render_vendor_image(driver, frame):
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

    def _render_vendor_image(self, driver: Any, frame: dict[str, Any]) -> bool:
        if not hasattr(driver, "draw_image"):
            return False
        if not hasattr(driver, "LCD_WIDTH") or not hasattr(driver, "LCD_HEIGHT"):
            return False
        try:
            image_module = importlib.import_module("PIL.Image")
            draw_module = importlib.import_module("PIL.ImageDraw")
            font_module = importlib.import_module("PIL.ImageFont")
        except Exception:
            return False
        width = int(getattr(driver, "LCD_WIDTH", 240))
        height = int(getattr(driver, "LCD_HEIGHT", 280))
        scene = str(frame.get("scene", "ready"))
        accent = self._scene_accent(scene)
        image = image_module.new("RGB", (width, height), self._scene_background(scene))
        draw = draw_module.Draw(image)
        font = font_module.load_default()
        draw.text((10, 10), str(frame.get("status_text", "-")), fill=accent, font=font)
        battery_text = str(frame.get("battery_label", "BAT --"))
        battery_x = max(10, width - self._text_width(draw, battery_text, font) - 10)
        draw.text((battery_x, 10), battery_text, fill=(245, 245, 245), font=font)
        center_title = str(frame.get("center_title", "-"))
        center_body = self._wrap_lines(str(frame.get("center_body", "-")), limit=24, max_lines=2)
        center_hint = str(frame.get("center_hint", ""))
        title_y = max(60, (height // 2) - 34)
        self._draw_centered_text(draw, width, title_y, center_title, font, fill=(255, 255, 255))
        body_y = title_y + 26
        for index, line in enumerate(center_body):
            self._draw_centered_text(draw, width, body_y + (index * 16), line, font, fill=(225, 235, 240))
        hint_y = body_y + (len(center_body) * 16) + 8
        if center_hint and center_hint != "-":
            self._draw_centered_text(draw, width, hint_y, center_hint, font, fill=accent)
        footer = str(frame.get("footer", ""))
        if footer and footer != "-":
            self._draw_centered_text(draw, width, height - 18, footer, font, fill=(214, 223, 228))
        pixel_data = self._rgb565_pixels(image)
        driver.draw_image(0, 0, width, height, pixel_data)
        return True

    def _rgb565_pixels(self, image: Any) -> list[int]:
        pixels: list[int] = []
        rgb_image = image.convert("RGB")
        width, height = rgb_image.size
        for y in range(height):
            for x in range(width):
                red, green, blue = rgb_image.getpixel((x, y))
                rgb565 = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
                pixels.extend(((rgb565 >> 8) & 0xFF, rgb565 & 0xFF))
        return pixels

    def _scene_background(self, scene: str) -> tuple[int, int, int]:
        palette = {
            "ready": (8, 34, 19),
            "connected": (8, 34, 19),
            "listening": (58, 48, 4),
            "processing": (29, 39, 79),
            "speaking": (6, 34, 74),
            "agent-selection": (9, 53, 50),
            "menu": (43, 37, 5),
            "mode-selection": (18, 52, 18),
            "error": (84, 12, 20),
            "disconnected": (10, 28, 64),
        }
        return palette.get(str(scene), (24, 24, 28))

    def _scene_accent(self, scene: str) -> tuple[int, int, int]:
        palette = {
            "ready": (56, 231, 109),
            "connected": (56, 231, 109),
            "listening": (255, 214, 10),
            "processing": (92, 182, 255),
            "speaking": (64, 196, 255),
            "agent-selection": (74, 222, 128),
            "menu": (255, 214, 10),
            "mode-selection": (74, 222, 128),
            "error": (255, 84, 84),
            "disconnected": (92, 182, 255),
        }
        return palette.get(str(scene), (245, 245, 245))

    def _compose_top_row(self, status_text: Any, battery_label: Any) -> str:
        left = self._one_line(status_text, limit=12)
        right = self._one_line(battery_label, limit=12)
        return self._one_line(f"{left}   {right}", limit=30)

    def _draw_centered_text(self, draw: Any, width: int, y: int, text: str, font: Any, *, fill: tuple[int, int, int]) -> None:
        compact = self._one_line(text, limit=30)
        x = max(8, (width - self._text_width(draw, compact, font)) // 2)
        draw.text((x, y), compact, fill=fill, font=font)

    def _text_width(self, draw: Any, text: str, font: Any) -> int:
        textbbox = getattr(draw, "textbbox", None)
        if callable(textbbox):
            bbox = textbbox((0, 0), text, font=font)
            if isinstance(bbox, tuple) and len(bbox) == 4:
                return int(bbox[2]) - int(bbox[0])
        textlength = getattr(draw, "textlength", None)
        if callable(textlength):
            length = textlength(text, font=font)
            if isinstance(length, (int, float)):
                return int(length)
        return len(text) * 6

    def _one_line(self, value: Any, *, limit: int = 28) -> str:
        text = " ".join(str(value or "").split()).strip()
        if not text:
            return "-"
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."

    def _wrap_lines(self, value: str, *, limit: int, max_lines: int) -> list[str]:
        text = " ".join(str(value or "").split()).strip()
        if not text:
            return ["-"]
        words = text.split(" ")
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                lines.append(current)
                if len(lines) == max_lines:
                    break
            current = word
        if current and len(lines) < max_lines:
            lines.append(current)
        rendered = " ".join(lines)
        if rendered != text and lines:
            lines[-1] = self._one_line(lines[-1], limit=limit - 3) + "..."
        return lines[:max_lines]

    def _looks_like_screen_model(self, model: Any) -> bool:
        if model is None:
            return False
        return all(hasattr(model, name) for name in ("local_state", "remote_state", "active_agent", "focus_label"))
