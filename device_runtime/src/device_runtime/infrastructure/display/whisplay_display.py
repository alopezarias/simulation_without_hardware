"""Guarded Raspberry Pi display adapter with vendor fallbacks."""

from __future__ import annotations

import importlib
from typing import Any, Callable

from device_runtime.application.services.display_model_service import ScreenViewModel
from device_runtime.infrastructure.whisplay_vendor import load_whisplay_vendor


class WhisplayDisplay:
    _HORIZONTAL_SAFE_MARGIN = 16

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
        self._fonts: dict[tuple[str, int], Any] = {}

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
        if frame == self.last_frame:
            return
        previous_frame = self.last_frame
        self.last_frame = frame
        self._render_frame(driver, frame, previous_frame=previous_frame)

    def show_diagnostic(self, line: str) -> None:
        self.diagnostics.append(line)
        normalized = line.strip()
        if normalized == self._diagnostic_line:
            return
        self._diagnostic_line = normalized
        driver = self._ensure_driver()
        if hasattr(driver, "show_diagnostic"):
            driver.show_diagnostic(line)
            if not self._looks_like_screen_model(self.last_model):
                return
        if self.last_model is not None:
            frame = self._build_frame(self.last_model)
            if frame == self.last_frame:
                return
            previous_frame = self.last_frame
            self.last_frame = frame
            self._render_frame(driver, frame, previous_frame=previous_frame)
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
        status_icon = str(getattr(model, "status_icon", "•"))
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
        body_lines = self._wrap_lines(center_body, limit=24, max_lines=2)
        lines = [
            top_row,
            f"[{status_icon}] {center_title}",
            *body_lines,
            center_hint,
            footer,
        ]
        regions = {
            "header": {
                "scene": str(getattr(model, "scene", "ready")),
                "status_text": str(getattr(model, "status_text", getattr(model, "local_state", "-"))),
                "battery_label": str(getattr(model, "battery_label", "--")),
                "battery_percent": getattr(model, "battery_percent", None),
                "battery_charging": bool(getattr(model, "battery_charging", False)),
            },
            "body": {
                "scene": str(getattr(model, "scene", "ready")),
                "status_icon": status_icon,
                "center_title": center_title,
                "body_lines": body_lines,
                "center_hint": center_hint,
            },
            "footer": {
                "scene": str(getattr(model, "scene", "ready")),
                "footer": footer,
            },
        }
        return {
            "scene": str(getattr(model, "scene", "ready")),
            "status_icon": status_icon,
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
            "battery_label": str(getattr(model, "battery_label", "--")),
            "battery_percent": getattr(model, "battery_percent", None),
            "battery_charging": bool(getattr(model, "battery_charging", False)),
            "diagnostics_label": str(getattr(model, "diagnostics_label", "")),
            "header_badges": list(getattr(model, "header_badges", [])),
            "footer": footer,
            "top_row": top_row,
            "lines": lines[:6],
            "regions": regions,
        }

    def _render_frame(self, driver: Any, frame: dict[str, Any], *, previous_frame: dict[str, Any] | None = None) -> None:
        if hasattr(driver, "render"):
            driver.render(frame)
            return
        if self._render_vendor_image(driver, frame, previous_frame=previous_frame):
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

    def _render_vendor_image(self, driver: Any, frame: dict[str, Any], *, previous_frame: dict[str, Any] | None = None) -> bool:
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
        previous_regions = (previous_frame or {}).get("regions", {})
        regions = frame.get("regions", {})
        if width < 80 or height < 120:
            layout = {"header": (0, 0, width, height)}
        else:
            header_height = min(44, height)
            footer_height = min(52, max(0, height - header_height - 120))
            body_height = max(0, height - header_height - footer_height)
            layout = {
                "header": (0, 0, width, header_height),
                "body": (0, header_height, width, body_height),
                "footer": (0, header_height + body_height, width, footer_height),
            }
        for name, box in layout.items():
            region_payload = regions.get(name)
            if region_payload == previous_regions.get(name):
                continue
            x, y, region_width, region_height = box
            if region_width <= 0 or region_height <= 0:
                continue
            image = image_module.new("RGB", (region_width, region_height), self._scene_background(str(frame.get("scene", "ready"))))
            draw = draw_module.Draw(image)
            if name == "header":
                self._draw_header_region(draw, frame, region_width, region_height, font_module)
            elif name == "body":
                self._draw_body_region(draw, frame, region_width, region_height, font_module)
            else:
                self._draw_footer_region(draw, frame, region_width, region_height, font_module)
            driver.draw_image(x, y, region_width, region_height, self._rgb565_pixels(image))
        return True

    def _rgb565_pixels(self, image: Any) -> list[int]:
        raw = image.convert("RGB").tobytes()
        pixels = bytearray(len(raw) // 3 * 2)
        target_index = 0
        for index in range(0, len(raw), 3):
            red = raw[index]
            green = raw[index + 1]
            blue = raw[index + 2]
            rgb565 = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
            pixels[target_index] = (rgb565 >> 8) & 0xFF
            pixels[target_index + 1] = rgb565 & 0xFF
            target_index += 2
        return list(pixels)

    def _scene_background(self, scene: str) -> tuple[int, int, int]:
        return (16, 18, 24)

    def _scene_accent(self, scene: str) -> tuple[int, int, int]:
        palette = {
            "ready": (56, 231, 109),
            "connected": (56, 231, 109),
            "standby": (56, 231, 109),
            "listening": (255, 214, 10),
            "calling": (92, 182, 255),
            "incoming-call": (244, 114, 182),
            "config": (168, 85, 247),
            "error": (255, 84, 84),
            "disconnected": (92, 182, 255),
        }
        return palette.get(str(scene), (245, 245, 245))

    def _compose_top_row(self, status_text: Any, battery_label: Any) -> str:
        left = self._one_line(status_text, limit=12)
        right = self._one_line(battery_label, limit=12)
        return self._one_line(f"{left}   {right}", limit=30)

    def _draw_centered_text(self, draw: Any, width: int, y: int, text: str, font: Any, *, fill: tuple[int, int, int]) -> None:
        safe_margin = self._safe_margin(width, minimum=8)
        compact = self._fit_text(draw, text, font, max_width=max(24, width - (safe_margin * 2)))
        text_width = self._text_width(draw, compact, font)
        x = max(safe_margin, (width - text_width) // 2)
        max_x = max(safe_margin, width - text_width - safe_margin)
        x = min(x, max_x)
        draw.text((x, y), compact, fill=fill, font=font)

    def _draw_header_region(self, draw: Any, frame: dict[str, Any], width: int, height: int, font_module: Any) -> None:
        scene = str(frame.get("scene", "ready"))
        accent = self._scene_accent(scene)
        label_font = self._font(font_module, 16, bold=True)
        text_font = self._font(font_module, 14)
        left_margin = self._safe_margin(width)
        pill_width = min(108, max(80, width // 2 - left_margin))
        status = self._fit_text(draw, frame.get("status_text", "-"), label_font, max_width=max(24, pill_width - 20))
        draw.rounded_rectangle((left_margin, 8, left_margin + pill_width, height - 8), radius=14, outline=(56, 64, 78), fill=(24, 28, 37))
        draw.text((left_margin + 10, 14), status, fill=accent, font=label_font)
        self._draw_battery(draw, width, height, frame, font=text_font)

    def _draw_body_region(self, draw: Any, frame: dict[str, Any], width: int, height: int, font_module: Any) -> None:
        scene = str(frame.get("scene", "ready"))
        accent = self._scene_accent(scene)
        title_font = self._font(font_module, 28, bold=True)
        body_font = self._font(font_module, 18)
        hint_font = self._font(font_module, 14)
        icon_font = self._font(font_module, 16, bold=True)
        center_x = width // 2
        badge_size = 54
        badge_x0 = center_x - (badge_size // 2)
        badge_y0 = 16
        draw.ellipse((badge_x0, badge_y0, badge_x0 + badge_size, badge_y0 + badge_size), fill=(24, 28, 37), outline=accent, width=3)
        icon = self._one_line(frame.get("status_icon", "•"), limit=4)
        icon_x = max(8, center_x - (self._text_width(draw, icon, icon_font) // 2))
        draw.text((icon_x, badge_y0 + 18), icon, fill=accent, font=icon_font)
        title_y = badge_y0 + badge_size + 18
        self._draw_centered_text(draw, width, title_y, str(frame.get("center_title", "-")), title_font, fill=(248, 250, 252))
        safe_margin = self._safe_margin(width)
        body_lines = self._wrap_text_pixels(
            draw,
            str(frame.get("center_body", "-")),
            body_font,
            max_width=max(36, width - (safe_margin * 2)),
            max_lines=2,
        )
        body_y = title_y + 40
        for index, line in enumerate(body_lines):
            self._draw_centered_text(draw, width, body_y + (index * 24), str(line), body_font, fill=(203, 213, 225))
        hint = str(frame.get("center_hint", "")).strip()
        if hint and hint != "-":
            self._draw_centered_text(draw, width, body_y + (len(body_lines) * 24) + 12, hint, hint_font, fill=accent)

    def _draw_footer_region(self, draw: Any, frame: dict[str, Any], width: int, height: int, font_module: Any) -> None:
        footer = str(frame.get("footer", "")).strip()
        if not footer or footer == "-":
            return
        font = self._font(font_module, 12)
        self._draw_centered_text(draw, width, max(8, (height // 2) - 8), footer, font, fill=(148, 163, 184))

    def _draw_battery(self, draw: Any, width: int, height: int, frame: dict[str, Any], *, font: Any) -> None:
        percent = frame.get("battery_percent")
        charging = bool(frame.get("battery_charging", False))
        scene = str(frame.get("scene", "ready"))
        accent = self._scene_accent(scene)
        body_width = 28
        body_height = 14
        safe_margin = self._safe_margin(width)
        label = str(frame.get("battery_label", "--"))
        label = self._fit_text(draw, label, font, max_width=max(18, width // 4))
        label_width = self._text_width(draw, label, font)
        body_x = max(safe_margin, width - label_width - body_width - 22 - safe_margin)
        body_y = max(10, (height - body_height) // 2)
        draw.rounded_rectangle((body_x, body_y, body_x + body_width, body_y + body_height), radius=3, outline=(226, 232, 240), width=2)
        draw.rectangle((body_x + body_width, body_y + 4, body_x + body_width + 3, body_y + 10), fill=(226, 232, 240))
        if isinstance(percent, int):
            fill_width = max(3, int((body_width - 4) * min(100, max(0, percent)) / 100))
            draw.rounded_rectangle((body_x + 2, body_y + 2, body_x + 2 + fill_width, body_y + body_height - 2), radius=2, fill=accent)
            label = f"{percent}%"
        if charging:
            draw.text((body_x + body_width + 8, body_y - 1), "+", fill=accent, font=font)
        text_x = max(safe_margin, width - self._text_width(draw, label, font) - safe_margin)
        draw.text((text_x, body_y - 1), label, fill=(241, 245, 249), font=font)

    def _font(self, font_module: Any, size: int, *, bold: bool = False) -> Any:
        key = (("bold" if bold else "regular"), size)
        cached = self._fonts.get(key)
        if cached is not None:
            return cached
        candidates = [
            "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/Library/Fonts/Arial Unicode.ttf",
        ]
        for candidate in candidates:
            try:
                font = font_module.truetype(candidate, size=size)
                self._fonts[key] = font
                return font
            except Exception:
                continue
        font = font_module.load_default()
        self._fonts[key] = font
        return font

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

    def _safe_margin(self, width: int, *, minimum: int = 10) -> int:
        return min(self._HORIZONTAL_SAFE_MARGIN, max(minimum, width // 12))

    def _fit_text(self, draw: Any, value: Any, font: Any, *, max_width: int) -> str:
        text = " ".join(str(value or "").split()).strip()
        if not text:
            return "-"
        if self._text_width(draw, text, font) <= max_width:
            return text
        ellipsis = "..."
        candidate = text
        while candidate:
            candidate = candidate[:-1].rstrip()
            compact = (candidate + ellipsis).strip()
            if compact and self._text_width(draw, compact, font) <= max_width:
                return compact
        return ellipsis

    def _wrap_text_pixels(self, draw: Any, value: Any, font: Any, *, max_width: int, max_lines: int) -> list[str]:
        text = " ".join(str(value or "").split()).strip()
        if not text:
            return ["-"]
        words = text.split(" ")
        lines: list[str] = []
        current = ""
        index = 0
        while index < len(words):
            word = words[index]
            fitted_word = self._fit_text(draw, word, font, max_width=max_width)
            candidate = fitted_word if not current else f"{current} {fitted_word}"
            if self._text_width(draw, candidate, font) <= max_width:
                current = candidate
                index += 1
                continue
            if current:
                lines.append(current)
                if len(lines) == max_lines:
                    break
                current = ""
                continue
            lines.append(self._fit_text(draw, fitted_word, font, max_width=max_width))
            index += 1
            if len(lines) == max_lines:
                break
        if current and len(lines) < max_lines:
            lines.append(current)
        consumed = " ".join(lines)
        if consumed != text and lines:
            lines[-1] = self._fit_text(draw, lines[-1] + " ...", font, max_width=max_width)
        return lines[:max_lines]

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
