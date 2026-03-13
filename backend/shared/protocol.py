"""Common protocol helpers for backend and simulator."""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any


class UiState(str, Enum):
    """Main UI states shared by backend and simulator."""

    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    ERROR = "error"


DEVICE_MESSAGE_TYPES = {
    "device.hello",
    "session.start",
    "agent.select",
    "recording.start",
    "audio.chunk",
    "recording.stop",
    "recording.cancel",
    "assistant.interrupt",
    "ping",
    "debug.user_text",
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
