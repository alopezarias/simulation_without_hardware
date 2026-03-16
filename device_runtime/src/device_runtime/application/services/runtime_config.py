"""Runtime configuration parsed from environment variables."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


DEVICE_WS_URL_ENV = "DEVICE_WS_URL"
DEFAULT_HARDWARE_PROFILE = "auto"
GENERIC_HARDWARE_PROFILE = "generic"
WHISPLAY_HARDWARE_PROFILE = "whisplay"


@dataclass(slots=True)
class RuntimeConfig:
    device_id: str
    ws_url: str
    auth_token: str = ""
    firmware_version: str = "0.3.0"
    transport_adapter: str = "websocket"
    display_adapter: str = "null"
    button_adapter: str = "null"
    audio_in_adapter: str = "null"
    audio_out_adapter: str = "null"
    power_adapter: str = "none"
    rgb_adapter: str = "none"
    pisugar_mode: str = "auto"
    pisugar_host: str = "127.0.0.1"
    pisugar_port: int = 8423
    pisugar_command: str = ""
    rgb_profile: str = "default"
    hardware_profile: str = DEFAULT_HARDWARE_PROFILE
    resolved_hardware_profile: str = GENERIC_HARDWARE_PROFILE
    config_warnings: tuple[str, ...] = ()
    whisplay_driver_path: str = ""
    whisplay_backlight: int = 50
    reconnect_initial_ms: int = 1000
    reconnect_max_ms: int = 6000
    button_long_press_ms: int = 900
    button_double_press_ms: int = 350
    audio_sample_rate: int = 16000
    audio_channels: int = 1
    audio_chunk_ms: int = 120
    diagnostics_enabled: bool = True
    fail_fast_on_missing_transport: bool = True
    fail_fast_on_missing_button: bool = False

    @property
    def whisplay_bundle_active(self) -> bool:
        return self.resolved_hardware_profile == WHISPLAY_HARDWARE_PROFILE

    def validate(self) -> None:
        if not self.device_id.strip():
            raise ValueError("DEVICE_ID is required")
        if not self.ws_url.strip():
            raise ValueError(
                f"{DEVICE_WS_URL_ENV} is required for standalone Raspberry deployment; set it explicitly to the PC backend WebSocket URL"
            )
        parsed = urlparse(self.ws_url)
        if parsed.scheme not in {"ws", "wss"} or not parsed.netloc:
            raise ValueError(f"{DEVICE_WS_URL_ENV} must be a valid ws:// or wss:// URL")
        if self.reconnect_initial_ms <= 0:
            raise ValueError("DEVICE_RECONNECT_INITIAL_MS must be > 0")
        if self.reconnect_max_ms < self.reconnect_initial_ms:
            raise ValueError("DEVICE_RECONNECT_MAX_MS must be >= DEVICE_RECONNECT_INITIAL_MS")
        if self.pisugar_port <= 0:
            raise ValueError("DEVICE_PISUGAR_PORT must be > 0")
        if not 0 <= self.whisplay_backlight <= 100:
            raise ValueError("DEVICE_WHISPLAY_BACKLIGHT must be between 0 and 100")
        if self.button_long_press_ms <= 0:
            raise ValueError("DEVICE_BUTTON_LONG_PRESS_MS must be > 0")
        if self.button_double_press_ms <= 0:
            raise ValueError("DEVICE_BUTTON_DOUBLE_PRESS_MS must be > 0")
        if self.audio_sample_rate <= 0:
            raise ValueError("DEVICE_AUDIO_SAMPLE_RATE must be > 0")
        if self.audio_channels <= 0:
            raise ValueError("DEVICE_AUDIO_CHANNELS must be > 0")
        if self.audio_chunk_ms <= 0:
            raise ValueError("DEVICE_AUDIO_CHUNK_MS must be > 0")
        if self.hardware_profile not in {
            DEFAULT_HARDWARE_PROFILE,
            GENERIC_HARDWARE_PROFILE,
            WHISPLAY_HARDWARE_PROFILE,
        }:
            raise ValueError(
                "DEVICE_HARDWARE_PROFILE must be one of: auto, generic, whisplay"
            )
        if self.resolved_hardware_profile not in {
            GENERIC_HARDWARE_PROFILE,
            WHISPLAY_HARDWARE_PROFILE,
        }:
            raise ValueError("Resolved hardware profile must be generic or whisplay")
