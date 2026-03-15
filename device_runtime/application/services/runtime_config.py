"""Runtime configuration parsed from environment variables."""

from __future__ import annotations

from dataclasses import dataclass


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

    def validate(self) -> None:
        if not self.device_id.strip():
            raise ValueError("DEVICE_ID is required")
        if not self.ws_url.strip():
            raise ValueError("DEVICE_WS_URL is required")
        if self.reconnect_initial_ms <= 0:
            raise ValueError("DEVICE_RECONNECT_INITIAL_MS must be > 0")
        if self.reconnect_max_ms < self.reconnect_initial_ms:
            raise ValueError("DEVICE_RECONNECT_MAX_MS must be >= DEVICE_RECONNECT_INITIAL_MS")
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
