"""Battery calibration helpers for on-device battery display."""

from __future__ import annotations

from dataclasses import dataclass
import math

from device_runtime.application.ports import PowerStatus


DEFAULT_BATTERY_DISPLAY_CALIBRATION = "0:0,1:25,2:50,20:75,60:100"


@dataclass(frozen=True, slots=True)
class BatteryCalibrationPoint:
    raw_percent: float
    display_percent: float


@dataclass(frozen=True, slots=True)
class BatteryDisplayState:
    raw_percent: float | None
    display_percent: int | None
    percent_text: str
    label: str
    icon: str
    bars: int
    bar_capacity: int
    charging: bool


class BatteryDisplayService:
    def __init__(
        self,
        *,
        calibration: str = DEFAULT_BATTERY_DISPLAY_CALIBRATION,
        bar_count: int = 4,
    ) -> None:
        self._bar_count = max(1, bar_count)
        self._points = self._parse_points(calibration)

    def build(self, power: PowerStatus) -> BatteryDisplayState:
        raw_percent = self._raw_percent(power)
        display_percent = self._display_percent(raw_percent) if raw_percent is not None else None
        charging = bool(power.charging)
        percent_text = self._percent_text(display_percent, charging=charging)
        icon = self._icon(display_percent)
        label = self._label(icon, percent_text)
        bars = self._bars(display_percent)
        return BatteryDisplayState(
            raw_percent=raw_percent,
            display_percent=display_percent,
            percent_text=percent_text,
            label=label,
            icon=icon,
            bars=bars,
            bar_capacity=self._bar_count,
            charging=charging,
        )

    def _raw_percent(self, power: PowerStatus) -> float | None:
        if not power.available or power.battery_percent is None:
            return None
        return max(0.0, min(100.0, float(power.battery_percent)))

    def _display_percent(self, raw_percent: float) -> int:
        points = self._points
        if raw_percent <= points[0].raw_percent:
            return int(round(points[0].display_percent))
        if raw_percent >= points[-1].raw_percent:
            return int(round(points[-1].display_percent))
        for left, right in zip(points, points[1:], strict=False):
            if left.raw_percent <= raw_percent <= right.raw_percent:
                span = right.raw_percent - left.raw_percent
                if span <= 0:
                    return int(round(right.display_percent))
                ratio = (raw_percent - left.raw_percent) / span
                value = left.display_percent + ((right.display_percent - left.display_percent) * ratio)
                return int(round(max(0.0, min(100.0, value))))
        return int(round(max(0.0, min(100.0, raw_percent))))

    def _percent_text(self, display_percent: int | None, *, charging: bool) -> str:
        if display_percent is None:
            return "--"
        suffix = "+" if charging else ""
        return f"{display_percent}%{suffix}"

    def _label(self, icon: str, percent_text: str) -> str:
        if percent_text == "--":
            return "--"
        return f"{icon} {percent_text}"

    def _icon(self, display_percent: int | None) -> str:
        if display_percent is None:
            return "[----]"
        filled = self._bars(display_percent)
        return "[" + ("#" * filled) + ("-" * (self._bar_count - filled)) + "]"

    def _bars(self, display_percent: int | None) -> int:
        if display_percent is None or display_percent <= 0:
            return 0
        percent = max(0, min(100, display_percent))
        return min(self._bar_count, max(1, math.ceil((percent / 100) * self._bar_count)))

    def _parse_points(self, calibration: str) -> tuple[BatteryCalibrationPoint, ...]:
        raw_points = [segment.strip() for segment in calibration.split(",") if segment.strip()]
        if len(raw_points) < 2:
            raise ValueError("DEVICE_BATTERY_DISPLAY_CALIBRATION must define at least two raw:display pairs")
        points: list[BatteryCalibrationPoint] = []
        previous_raw: float | None = None
        previous_display: float | None = None
        for segment in raw_points:
            if ":" not in segment:
                raise ValueError(
                    "DEVICE_BATTERY_DISPLAY_CALIBRATION entries must use raw:display format"
                )
            raw_text, display_text = segment.split(":", 1)
            try:
                raw_value = float(raw_text.strip())
                display_value = float(display_text.strip())
            except ValueError as exc:
                raise ValueError(
                    "DEVICE_BATTERY_DISPLAY_CALIBRATION entries must be numeric raw:display pairs"
                ) from exc
            if not 0.0 <= raw_value <= 100.0:
                raise ValueError("Battery calibration raw values must stay between 0 and 100")
            if not 0.0 <= display_value <= 100.0:
                raise ValueError("Battery calibration display values must stay between 0 and 100")
            if previous_raw is not None and raw_value <= previous_raw:
                raise ValueError("Battery calibration raw values must be strictly increasing")
            if previous_display is not None and display_value < previous_display:
                raise ValueError("Battery calibration display values must be monotonic")
            points.append(BatteryCalibrationPoint(raw_percent=raw_value, display_percent=display_value))
            previous_raw = raw_value
            previous_display = display_value
        return tuple(points)
