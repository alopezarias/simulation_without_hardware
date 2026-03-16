"""Inbound protocol coercion helpers for runtime-owned state."""

from __future__ import annotations

from typing import Any

from device_runtime.protocol.types import UiState


def normalize_message_type(raw: Any) -> str:
    if raw is None:
        return ""
    return str(raw).strip()


def coerce_ui_state(raw: Any, *, default: UiState = UiState.IDLE) -> tuple[UiState, str | None]:
    state_value = normalize_message_type(raw)
    if not state_value:
        return default, None
    try:
        return UiState(state_value), None
    except ValueError:
        return UiState.ERROR, f"ui.state invalid: {state_value}"
