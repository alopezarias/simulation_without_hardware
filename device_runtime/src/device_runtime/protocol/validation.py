"""Validation helpers for the runtime-owned wire contract."""

from __future__ import annotations

from typing import Any

from device_runtime.protocol.messages import DEVICE_MESSAGE_TYPES
from device_runtime.protocol.codec import normalize_message_type


def validate_device_message(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Message must be a JSON object.")

    message_type = normalize_message_type(raw.get("type"))
    if not message_type:
        raise ValueError("Message requires a non-empty 'type' field.")

    if message_type not in DEVICE_MESSAGE_TYPES:
        raise ValueError(f"Unsupported message type: {message_type}")

    return raw


def require_fields(message: dict[str, Any], *fields: str) -> None:
    missing = [field for field in fields if field not in message]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")
