"""Environment-backed configuration loader for the note-taker runtime."""

from __future__ import annotations

from collections.abc import Mapping
import os

from device_runtime.application.services.note_taker_config import NoteTakerConfig


_WHISPLAY_PROFILE = "whisplay"
_GENERIC_PROFILE = "generic"
_WM8960_DEVICE = "plughw:wm8960soundcard,0"


def load_note_taker_config(env: Mapping[str, str] | None = None) -> NoteTakerConfig:
    values = dict(os.environ if env is None else env)
    config = NoteTakerConfig(
        device_id=values.get("DEVICE_ID", "").strip(),
        note_api_url=values.get("DEVICE_NOTE_API_URL", "").strip(),
        note_api_token=values.get("DEVICE_NOTE_API_TOKEN", "").strip(),
        display_adapter=values.get("DEVICE_DISPLAY_ADAPTER", "null").strip() or "null",
        button_adapter=values.get("DEVICE_BUTTON_ADAPTER", "null").strip() or "null",
        audio_in_adapter=values.get("DEVICE_AUDIO_IN_ADAPTER", "null").strip() or "null",
        power_adapter=values.get("DEVICE_POWER_ADAPTER", "none").strip() or "none",
        rgb_adapter=values.get("DEVICE_RGB_ADAPTER", "none").strip() or "none",
        wake_word_engine=values.get("DEVICE_WAKE_WORD_ENGINE", "null").strip() or "null",
        wake_word_model=values.get("DEVICE_WAKE_WORD_MODEL", "hey_jarvis").strip() or "hey_jarvis",
        hardware_profile=values.get("DEVICE_HARDWARE_PROFILE", "auto").strip().lower() or "auto",
        whisplay_driver_path=values.get("DEVICE_WHISPLAY_DRIVER_PATH", "").strip(),
        whisplay_backlight=_get_int(values, "DEVICE_WHISPLAY_BACKLIGHT", 50),
        button_long_press_ms=_get_int(values, "DEVICE_BUTTON_LONG_PRESS_MS", 1000),
        button_double_press_ms=_get_int(values, "DEVICE_BUTTON_DOUBLE_PRESS_MS", 350),
        audio_sample_rate=_get_int(values, "DEVICE_AUDIO_SAMPLE_RATE", 16000),
        audio_channels=_get_int(values, "DEVICE_AUDIO_CHANNELS", 1),
        audio_chunk_ms=_get_int(values, "DEVICE_AUDIO_CHUNK_MS", 120),
        audio_in_alsa_device=values.get("DEVICE_AUDIO_IN_ALSA_DEVICE", "default").strip() or "default",
        audio_in_alsa_period_size=_get_int(values, "DEVICE_AUDIO_IN_ALSA_PERIOD_SIZE", 0),
        audio_in_alsa_nonblock=_get_bool(values, "DEVICE_AUDIO_IN_ALSA_NONBLOCK", False),
        silence_timeout_s=_get_float(values, "DEVICE_SILENCE_TIMEOUT_S", 3.0),
        max_recording_s=_get_float(values, "DEVICE_MAX_RECORDING_S", 60.0),
    )
    _resolve_hardware_profile(config)
    config.validate()
    return config


def _resolve_hardware_profile(config: NoteTakerConfig) -> None:
    profile = config.hardware_profile
    if profile == "auto":
        profile = _WHISPLAY_PROFILE if config.display_adapter == _WHISPLAY_PROFILE else _GENERIC_PROFILE

    warnings: list[str] = []
    if profile == _WHISPLAY_PROFILE:
        warnings.extend(_apply_whisplay_bundle(config))

    config.resolved_hardware_profile = profile
    config.whisplay_bundle_active = profile == _WHISPLAY_PROFILE
    config.config_warnings = tuple(warnings)


def _apply_whisplay_bundle(config: NoteTakerConfig) -> list[str]:
    warnings: list[str] = []
    if config.display_adapter != _WHISPLAY_PROFILE:
        config.display_adapter = _WHISPLAY_PROFILE
        warnings.append("Whisplay profile forced DEVICE_DISPLAY_ADAPTER=whisplay")

    btn = config.button_adapter
    if btn != "whisplay":
        config.button_adapter = "whisplay"
        if btn == "gpio":
            warnings.append(
                "Whisplay profile replaced DEVICE_BUTTON_ADAPTER=gpio with whisplay "
                "(GPIO17 conflicts with vendor button on hardware)"
            )
        else:
            warnings.append("Whisplay profile forced DEVICE_BUTTON_ADAPTER=whisplay")

    if config.rgb_adapter in {"", "none", "null", "disabled"}:
        config.rgb_adapter = "hardware"
        warnings.append("Whisplay profile defaulted DEVICE_RGB_ADAPTER=hardware")

    if config.audio_in_adapter not in {"", "none", "null", "disabled"}:
        if config.audio_in_adapter == "alsa" and config.audio_in_alsa_device == "default":
            config.audio_in_alsa_device = _WM8960_DEVICE
            warnings.append(
                "Whisplay profile defaulted DEVICE_AUDIO_IN_ALSA_DEVICE=plughw:wm8960soundcard,0"
            )
    return warnings


def _get_int(values: Mapping[str, str], key: str, default: int) -> int:
    raw = values.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc


def _get_float(values: Mapping[str, str], key: str, default: float) -> float:
    raw = values.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be a number") from exc


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
