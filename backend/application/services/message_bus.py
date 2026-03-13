"""Outbound messaging primitives for device sessions."""

from __future__ import annotations

import logging
from typing import Any

from backend.shared.protocol import UiState, build_message

from backend.domain.session import DeviceSession
from backend.infrastructure.logging.sanitizer import sanitize_message_for_log

logger = logging.getLogger("simulation-backend")


async def send(session: DeviceSession, message: dict[str, Any]) -> None:
    logger.info("OUT -> %s", sanitize_message_for_log(message))
    await session.output.send_json(message)


async def send_ui_state(session: DeviceSession, state: UiState) -> None:
    session.ui_state = state
    await send(session, build_message("ui.state", state=state.value))


async def send_error(session: DeviceSession, detail: str, code: str = "protocol_error") -> None:
    await send(session, build_message("error", code=code, detail=detail))
    await send_ui_state(session, UiState.ERROR)
