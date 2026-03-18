"""Environment-backed runtime configuration loader."""

from __future__ import annotations

from collections.abc import Mapping
import os

from device_runtime.application.services.runtime_config import (
    DEFAULT_HARDWARE_PROFILE,
    GENERIC_HARDWARE_PROFILE,
    WHISPLAY_HARDWARE_PROFILE,
    RuntimeConfig,
)


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
        power_adapter=values.get("DEVICE_POWER_ADAPTER", "none").strip() or "none",
        rgb_adapter=values.get("DEVICE_RGB_ADAPTER", "none").strip() or "none",
        pisugar_mode=values.get("DEVICE_PISUGAR_MODE", "auto").strip() or "auto",
        pisugar_host=values.get("DEVICE_PISUGAR_HOST", "127.0.0.1").strip() or "127.0.0.1",
        pisugar_port=_get_int(values, "DEVICE_PISUGAR_PORT", 8423),
        pisugar_command=values.get("DEVICE_PISUGAR_COMMAND", "").strip(),
        rgb_profile=values.get("DEVICE_RGB_PROFILE", "default").strip() or "default",
        hardware_profile=values.get("DEVICE_HARDWARE_PROFILE", DEFAULT_HARDWARE_PROFILE).strip().lower()
        or DEFAULT_HARDWARE_PROFILE,
        whisplay_driver_path=values.get("DEVICE_WHISPLAY_DRIVER_PATH", "").strip(),
        whisplay_backlight=_get_int(values, "DEVICE_WHISPLAY_BACKLIGHT", 50),
        reconnect_initial_ms=_get_int(values, "DEVICE_RECONNECT_INITIAL_MS", 1000),
        reconnect_max_ms=_get_int(values, "DEVICE_RECONNECT_MAX_MS", 6000),
        button_long_press_ms=_get_int(values, "DEVICE_BUTTON_LONG_PRESS_MS", 900),
        button_double_press_ms=_get_int(values, "DEVICE_BUTTON_DOUBLE_PRESS_MS", 350),
        audio_sample_rate=_get_int(values, "DEVICE_AUDIO_SAMPLE_RATE", 16000),
        audio_channels=_get_int(values, "DEVICE_AUDIO_CHANNELS", 1),
        audio_chunk_ms=_get_int(values, "DEVICE_AUDIO_CHUNK_MS", 120),
        audio_in_alsa_device=values.get("DEVICE_AUDIO_IN_ALSA_DEVICE", "default").strip() or "default",
        audio_out_alsa_device=values.get("DEVICE_AUDIO_OUT_ALSA_DEVICE", "default").strip() or "default",
        audio_in_alsa_period_size=_get_int(values, "DEVICE_AUDIO_IN_ALSA_PERIOD_SIZE", 0),
        audio_out_alsa_period_size=_get_int(values, "DEVICE_AUDIO_OUT_ALSA_PERIOD_SIZE", 0),
        audio_out_chunk_ms=_get_int(values, "DEVICE_AUDIO_OUT_CHUNK_MS", 200),
        audio_in_alsa_nonblock=_get_bool(values, "DEVICE_AUDIO_IN_ALSA_NONBLOCK", False),
        audio_out_start_buffer_ms=_get_int_with_alias(
            values,
            "DEVICE_AUDIO_OUT_START_BUFFER_MS",
            "DEVICE_AUDIO_OUT_BUFFER_MS",
            1000,
        ),
        diagnostics_enabled=_get_bool(values, "DEVICE_DIAGNOSTICS_ENABLED", True),
        fail_fast_on_missing_transport=_get_bool(values, "DEVICE_FAIL_FAST_ON_MISSING_TRANSPORT", True),
        fail_fast_on_missing_button=_get_bool(values, "DEVICE_FAIL_FAST_ON_MISSING_BUTTON", False),
    )
    _resolve_hardware_profile(config)
    config.validate()
    return config


def _resolve_hardware_profile(config: RuntimeConfig) -> None:
    resolved_profile = config.hardware_profile
    if resolved_profile == DEFAULT_HARDWARE_PROFILE:
        if config.display_adapter.strip().lower() == WHISPLAY_HARDWARE_PROFILE:
            resolved_profile = WHISPLAY_HARDWARE_PROFILE
        else:
            resolved_profile = GENERIC_HARDWARE_PROFILE

    warnings: list[str] = []
    if resolved_profile == WHISPLAY_HARDWARE_PROFILE:
        warnings.extend(_apply_whisplay_bundle_resolution(config))

    config.resolved_hardware_profile = resolved_profile
    config.config_warnings = tuple(warnings)


def _apply_whisplay_bundle_resolution(config: RuntimeConfig) -> list[str]:
    warnings: list[str] = []
    wm8960_device = "plughw:wm8960soundcard,0"
    if config.display_adapter.strip().lower() != WHISPLAY_HARDWARE_PROFILE:
        config.display_adapter = WHISPLAY_HARDWARE_PROFILE
        warnings.append(
            "Whisplay hardware profile forced DEVICE_DISPLAY_ADAPTER=whisplay to keep the integrated screen/RGB bundle on the vendor stack"
        )

    button_adapter = config.button_adapter.strip().lower()
    if button_adapter != "whisplay":
        config.button_adapter = "whisplay"
        if button_adapter == "gpio":
            warnings.append(
                "Whisplay hardware profile replaced DEVICE_BUTTON_ADAPTER=gpio with DEVICE_BUTTON_ADAPTER=whisplay because the vendor bundle already owns the button and separate GPIO17 access conflicts on Raspberry Pi"
            )
        else:
            warnings.append(
                "Whisplay hardware profile forced DEVICE_BUTTON_ADAPTER=whisplay so button clicks come from the integrated vendor bundle"
            )

    if config.rgb_adapter.strip().lower() in {"", "none", "null", "disabled"}:
        config.rgb_adapter = "hardware"
        warnings.append(
            "Whisplay hardware profile defaulted DEVICE_RGB_ADAPTER=hardware so RGB stays on the integrated vendor controller"
        )

    audio_in = config.audio_in_adapter.strip().lower()
    if audio_in not in {"", "none", "null", "disabled"}:
        if audio_in == "alsa" and config.audio_in_alsa_device == "default":
            config.audio_in_alsa_device = wm8960_device
            warnings.append(
                "Whisplay hardware profile defaulted DEVICE_AUDIO_IN_ALSA_DEVICE=plughw:wm8960soundcard,0 for the integrated WM8960 codec"
            )
        warnings.append(
            f"Whisplay hardware profile kept DEVICE_AUDIO_IN_ADAPTER={config.audio_in_adapter}; verify it does not conflict with the integrated microphone path"
        )

    audio_out = config.audio_out_adapter.strip().lower()
    if audio_out not in {"", "none", "null", "disabled"}:
        if audio_out == "alsa" and config.audio_out_alsa_device == "default":
            config.audio_out_alsa_device = wm8960_device
            warnings.append(
                "Whisplay hardware profile defaulted DEVICE_AUDIO_OUT_ALSA_DEVICE=plughw:wm8960soundcard,0 for the integrated WM8960 codec"
            )
        warnings.append(
            f"Whisplay hardware profile kept DEVICE_AUDIO_OUT_ADAPTER={config.audio_out_adapter}; verify it does not conflict with the integrated speaker path"
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


def _get_int_with_alias(values: Mapping[str, str], key: str, alias: str, default: int) -> int:
    if key in values and values[key].strip():
        return _get_int(values, key, default)
    if alias in values and values[alias].strip():
        return _get_int(values, alias, default)
    return default


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
