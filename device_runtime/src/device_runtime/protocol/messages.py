"""Message builders and identifiers for the runtime protocol."""

from __future__ import annotations

import time
import uuid
from typing import Any

from device_runtime.protocol.types import MessageType

DEVICE_MESSAGE_TYPES = {message_type.value for message_type in MessageType}


def now_timestamp() -> int:
    return int(time.time())


def new_turn_id() -> str:
    return f"turn-{uuid.uuid4().hex[:12]}"


def new_session_id() -> str:
    return f"session-{uuid.uuid4().hex[:12]}"


def build_message(message_type: MessageType | str, **payload: Any) -> dict[str, Any]:
    resolved_type = str(getattr(message_type, "value", message_type))
    message: dict[str, Any] = {
        "type": resolved_type,
        "timestamp": now_timestamp(),
    }
    message.update(payload)
    return message
