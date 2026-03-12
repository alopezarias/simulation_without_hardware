"""Domain entity representing the state of a connected simulated device."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from protocol import UiState, new_session_id

from app.application.ports import DeviceOutputPort


@dataclass
class DeviceSession:
    output: DeviceOutputPort
    session_id: str = field(default_factory=new_session_id)
    device_id: str = "unknown-device"
    active_agent: str = "assistant-general"
    ui_state: UiState = UiState.IDLE
    recording: bool = False
    turn_id: str | None = None
    text_fragments: list[str] = field(default_factory=list)
    response_task: asyncio.Task[None] | None = None
    interrupted: asyncio.Event = field(default_factory=asyncio.Event)
    authenticated: bool = False
    turn_started_monotonic: float | None = None
    recording_config: dict[str, Any] = field(default_factory=dict)
    audio_chunks_received: int = 0
    audio_bytes_received: int = 0
    audio_file_path: str | None = None
    audio_file_handle: Any | None = None
