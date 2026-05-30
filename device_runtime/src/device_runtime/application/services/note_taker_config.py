"""Configuration dataclass for the note-taker runtime."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(slots=True)
class NoteTakerConfig:
    # ── identity ──────────────────────────────────────────────────────────────
    device_id: str
    note_api_url: str                   # HTTP(S) backend URL, e.g. http://localhost:8000
    note_api_token: str = ""

    # ── adapters ──────────────────────────────────────────────────────────────
    display_adapter: str = "null"
    button_adapter: str = "null"
    audio_in_adapter: str = "null"
    power_adapter: str = "none"
    rgb_adapter: str = "none"
    wake_word_engine: str = "null"      # "null" | "openwakeword" (future)
    wake_word_model: str = "hey_jarvis"

    # ── hardware tweaks ───────────────────────────────────────────────────────
    hardware_profile: str = "auto"
    resolved_hardware_profile: str = "generic"
    whisplay_driver_path: str = ""
    whisplay_backlight: int = 50
    whisplay_bundle_active: bool = False

    # ── button ────────────────────────────────────────────────────────────────
    button_long_press_ms: int = 1000    # 1 s default for note-taker
    button_double_press_ms: int = 350

    # ── audio ─────────────────────────────────────────────────────────────────
    audio_sample_rate: int = 16000
    audio_channels: int = 1
    audio_chunk_ms: int = 120
    audio_in_alsa_device: str = "default"
    audio_in_alsa_period_size: int = 0
    audio_in_alsa_nonblock: bool = False

    # ── recording limits ──────────────────────────────────────────────────────
    silence_timeout_s: float = 3.0     # seconds of silence that ends a wake-word recording
    max_recording_s: float = 60.0      # hard cap on any single recording

    # ── misc ──────────────────────────────────────────────────────────────────
    config_warnings: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.device_id.strip():
            raise ValueError("DEVICE_ID is required")
        if not self.note_api_url.strip():
            raise ValueError(
                "DEVICE_NOTE_API_URL is required — set it to the backend HTTP URL"
            )
        parsed = urlparse(self.note_api_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("DEVICE_NOTE_API_URL must be a valid http:// or https:// URL")
        if self.button_long_press_ms <= 0:
            raise ValueError("DEVICE_BUTTON_LONG_PRESS_MS must be > 0")
        if self.audio_sample_rate <= 0:
            raise ValueError("DEVICE_AUDIO_SAMPLE_RATE must be > 0")
        if self.audio_channels <= 0:
            raise ValueError("DEVICE_AUDIO_CHANNELS must be > 0")
        if self.audio_chunk_ms <= 0:
            raise ValueError("DEVICE_AUDIO_CHUNK_MS must be > 0")
        if self.silence_timeout_s <= 0:
            raise ValueError("DEVICE_SILENCE_TIMEOUT_S must be > 0")
        if self.max_recording_s <= 0:
            raise ValueError("DEVICE_MAX_RECORDING_S must be > 0")
        if not 0 <= self.whisplay_backlight <= 100:
            raise ValueError("DEVICE_WHISPLAY_BACKLIGHT must be between 0 and 100")
