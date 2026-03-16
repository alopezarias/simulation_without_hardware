"""Compatibility tests between the runtime-owned and backend-shared protocols."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.shared import protocol as backend_protocol
from device_runtime.protocol import DEVICE_MESSAGE_TYPES, MessageType, UiState, build_message, require_fields, validate_device_message


def test_runtime_ui_state_values_match_backend_contract() -> None:
    assert {state.value for state in UiState} == {state.value for state in backend_protocol.UiState}


def test_runtime_message_types_match_backend_contract() -> None:
    assert {message_type.value for message_type in MessageType} == backend_protocol.DEVICE_MESSAGE_TYPES
    assert DEVICE_MESSAGE_TYPES == backend_protocol.DEVICE_MESSAGE_TYPES


def test_runtime_build_message_shape_matches_backend() -> None:
    runtime_message = build_message(MessageType.DEVICE_HELLO, device_id="raspi-1")
    backend_message = backend_protocol.build_message("device.hello", device_id="raspi-1")

    assert runtime_message["type"] == backend_message["type"]
    assert runtime_message["device_id"] == backend_message["device_id"]
    assert isinstance(runtime_message["timestamp"], int)


def test_runtime_validation_matches_backend_message_types() -> None:
    valid = {"type": "ping"}

    assert validate_device_message(valid) == backend_protocol.validate_device_message(valid)


def test_runtime_require_fields_matches_backend_behavior() -> None:
    message = {"type": "recording.start", "turn_id": "turn-1"}

    require_fields(message, "turn_id")
    backend_protocol.require_fields(message, "turn_id")
