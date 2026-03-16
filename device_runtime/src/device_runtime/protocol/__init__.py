"""Runtime-owned WebSocket protocol helpers."""

from device_runtime.protocol.codec import coerce_ui_state, normalize_message_type
from device_runtime.protocol.messages import (
    DEVICE_MESSAGE_TYPES,
    build_message,
    new_session_id,
    new_turn_id,
    now_timestamp,
)
from device_runtime.protocol.types import MessageType, UiState
from device_runtime.protocol.validation import require_fields, validate_device_message

__all__ = [
    "DEVICE_MESSAGE_TYPES",
    "MessageType",
    "UiState",
    "build_message",
    "coerce_ui_state",
    "new_session_id",
    "new_turn_id",
    "normalize_message_type",
    "now_timestamp",
    "require_fields",
    "validate_device_message",
]
