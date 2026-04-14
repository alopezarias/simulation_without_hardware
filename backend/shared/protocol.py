"""Common protocol helpers for backend and simulator."""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any


class UiState(str, Enum):
    """Main UI states shared by backend and simulator."""

    STANDBY = "standby"
    LISTENING = "listening"
    CALLING = "calling"
    INCOMING_CALL = "incoming_call"
    CONFIG = "config"


DEVICE_MESSAGE_TYPES = {
    "device.hello",
    "session.start",
    "call.start",
    "agent.select",
    "agents.version.request",
    "agents.list.request",
    "recording.start",
    "audio.chunk",
    "recording.stop",
    "recording.cancel",
    "assistant.interrupt",
    "ping",
    "debug.user_text",
}


BACKEND_MESSAGE_TYPES = {
    "session.ready",
    "ui.state",
    "error",
    "pong",
    "agent.selected",
    "agents.version.response",
    "agents.list.response",
    "transcript.partial",
    "transcript.final",
    "assistant.start",
    "assistant.text.partial",
    "assistant.text.final",
    "assistant.audio.start",
    "assistant.audio.chunk",
    "assistant.audio.end",
    "assistant.audio.file",
    "incoming_call",
}


def now_timestamp() -> int:
    return int(time.time())


def new_turn_id() -> str:
    return f"turn-{uuid.uuid4().hex[:12]}"


def new_session_id() -> str:
    return f"session-{uuid.uuid4().hex[:12]}"


def build_message(message_type: str, **payload: Any) -> dict[str, Any]:
    message: dict[str, Any] = {
        "type": message_type,
        "timestamp": now_timestamp(),
    }
    message.update(payload)
    return message


def validate_device_message(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Message must be a JSON object.")

    message_type = raw.get("type")
    if not isinstance(message_type, str) or not message_type:
        raise ValueError("Message requires a non-empty 'type' field.")

    if message_type not in DEVICE_MESSAGE_TYPES:
        raise ValueError(f"Unsupported message type: {message_type}")

    return raw


def require_fields(message: dict[str, Any], *fields: str) -> None:
    missing = [field for field in fields if field not in message]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")
