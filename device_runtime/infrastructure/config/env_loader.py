"""Environment-backed runtime configuration loader."""

from __future__ import annotations

from collections.abc import Mapping
import os

from device_runtime.application.services.runtime_config import RuntimeConfig


def load_runtime_config(env: Mapping[str, str] | None = None) -> RuntimeConfig:
    values = dict(os.environ if env is None else env)
    config = RuntimeConfig(
        device_id=values.get("DEVICE_ID", "").strip(),
        ws_url=values.get("DEVICE_WS_URL", "").strip(),
        auth_token=values.get("DEVICE_AUTH_TOKEN", "").strip(),
        firmware_version=values.get("DEVICE_FIRMWARE_VERSION", "0.3.0").strip() or "0.3.0",
        transport_adapter=values.get("DEVICE_TRANSPORT_ADAPTER", "websocket").strip() or "websocket",
        display_adapter=values.get("DEVICE_DISPLAY_ADAPTER", "null").strip() or "null",
        button_adapter=values.get("DEVICE_BUTTON_ADAPTER", "null").strip() or "null",
        audio_in_adapter=values.get("DEVICE_AUDIO_IN_ADAPTER", "null").strip() or "null",
        audio_out_adapter=values.get("DEVICE_AUDIO_OUT_ADAPTER", "null").strip() or "null",
        reconnect_initial_ms=_get_int(values, "DEVICE_RECONNECT_INITIAL_MS", 1000),
        reconnect_max_ms=_get_int(values, "DEVICE_RECONNECT_MAX_MS", 6000),
        button_long_press_ms=_get_int(values, "DEVICE_BUTTON_LONG_PRESS_MS", 900),
        button_double_press_ms=_get_int(values, "DEVICE_BUTTON_DOUBLE_PRESS_MS", 350),
        audio_sample_rate=_get_int(values, "DEVICE_AUDIO_SAMPLE_RATE", 16000),
        audio_channels=_get_int(values, "DEVICE_AUDIO_CHANNELS", 1),
        audio_chunk_ms=_get_int(values, "DEVICE_AUDIO_CHUNK_MS", 120),
        diagnostics_enabled=_get_bool(values, "DEVICE_DIAGNOSTICS_ENABLED", True),
        fail_fast_on_missing_transport=_get_bool(values, "DEVICE_FAIL_FAST_ON_MISSING_TRANSPORT", True),
        fail_fast_on_missing_button=_get_bool(values, "DEVICE_FAIL_FAST_ON_MISSING_BUTTON", False),
    )
    config.validate()
    return config


def _get_int(values: Mapping[str, str], key: str, default: int) -> int:
    raw = values.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc


def _get_bool(values: Mapping[str, str], key: str, default: bool) -> bool:
    raw = values.get(key)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{key} must be a boolean")
