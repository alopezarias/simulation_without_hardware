"""Compatibility re-exports for the canonical backend protocol helpers."""

from backend.shared.protocol import (  # noqa: F401
    DEVICE_MESSAGE_TYPES,
    UiState,
    build_message,
    new_session_id,
    new_turn_id,
    now_timestamp,
    require_fields,
    validate_device_message,
)

__all__ = [
    "DEVICE_MESSAGE_TYPES",
    "UiState",
    "build_message",
    "new_session_id",
    "new_turn_id",
    "now_timestamp",
    "require_fields",
    "validate_device_message",
]
