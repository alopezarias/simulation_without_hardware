"""Runtime configuration for the backend application."""

from __future__ import annotations

import os
from hashlib import sha1
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class BackendSettings:
    enable_fake_audio: bool
    loopback_audio_enabled: bool
    loopback_chunk_ms: int
    audio_reply_mode: str
    device_auth_token: str
    available_agents: list[str]
    allowed_device_ids: set[str]
    log_level: str

    @property
    def agent_catalog_version(self) -> str:
        payload = "\n".join(self.available_agents).encode("utf-8")
        return sha1(payload).hexdigest()[:12]

    @classmethod
    def from_env(cls) -> "BackendSettings":
        available_agents = [
            value.strip()
            for value in os.getenv(
                "SIM_AVAILABLE_AGENTS",
                "assistant-general,assistant-tech,assistant-ops",
            ).split(",")
            if value.strip()
        ]
        if not available_agents:
            available_agents = ["assistant-general"]

        audio_reply_mode = os.getenv("AUDIO_REPLY_MODE", "assistant").strip().lower()
        if audio_reply_mode not in {"assistant", "echo"}:
            audio_reply_mode = "assistant"

        return cls(
            enable_fake_audio=_env_bool("ENABLE_FAKE_AUDIO", False),
            loopback_audio_enabled=_env_bool("LOOPBACK_AUDIO_ENABLED", True),
            loopback_chunk_ms=max(20, int(os.getenv("LOOPBACK_CHUNK_MS", "120"))),
            audio_reply_mode=audio_reply_mode,
            device_auth_token=os.getenv("SIM_DEVICE_AUTH_TOKEN", "").strip(),
            available_agents=available_agents,
            allowed_device_ids={
                value.strip()
                for value in os.getenv("SIM_ALLOWED_DEVICE_IDS", "").split(",")
                if value.strip()
            },
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
