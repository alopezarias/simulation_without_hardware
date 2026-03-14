"""Session bootstrap, authentication and readiness flows."""

from __future__ import annotations

from backend.shared.protocol import UiState, build_message, require_fields

from backend.application.context import AppContext
from backend.application.services.message_bus import send, send_error, send_ui_state
from backend.domain.session import DeviceSession


async def ensure_authenticated(session: DeviceSession) -> bool:
    if session.authenticated:
        return True

    await send_error(
        session,
        "device.hello must be sent and authenticated before other messages.",
        code="unauthorized",
    )
    return False


async def ensure_not_busy(session: DeviceSession) -> bool:
    if session.response_task and not session.response_task.done():
        await send_error(
            session,
            "Cannot start a new turn while assistant is speaking. Send assistant.interrupt first.",
            code="busy",
        )
        return False

    return True


def validate_device_hello(ctx: AppContext, message: dict[str, object]) -> tuple[str, str | None]:
    require_fields(message, "device_id")
    device_id = str(message["device_id"]).strip()
    if not device_id:
        raise ValueError("device_id cannot be empty.")

    if ctx.settings.allowed_device_ids and device_id not in ctx.settings.allowed_device_ids:
        raise ValueError(f"device_id '{device_id}' is not allowed.")

    active_agent = None
    if "active_agent" in message:
        active_agent = str(message["active_agent"]).strip()
        if active_agent and active_agent not in ctx.settings.available_agents:
            raise ValueError(
                f"active_agent '{active_agent}' is not valid. "
                f"Available agents: {', '.join(ctx.settings.available_agents)}"
            )

    if ctx.settings.device_auth_token:
        token = str(message.get("auth_token", "")).strip()
        if token != ctx.settings.device_auth_token:
            raise ValueError("Invalid auth token for device.")

    return device_id, active_agent


async def send_session_ready(ctx: AppContext, session: DeviceSession) -> None:
    await send(
        session,
        build_message(
            "session.ready",
            session_id=session.session_id,
            device_id=session.device_id,
            active_agent=session.active_agent,
            available_agents=ctx.settings.available_agents,
            agents_version=ctx.settings.agent_catalog_version,
            agents_cache_seed=True,
            protocol_version="0.2",
            speech=ctx.speech.capabilities(),
            audio_reply_mode=ctx.settings.audio_reply_mode,
        ),
    )


async def complete_hello(
    ctx: AppContext,
    session: DeviceSession,
    message: dict[str, object],
) -> bool:
    try:
        device_id, requested_agent = validate_device_hello(ctx, message)
    except ValueError as exc:
        await send_error(session, str(exc), code="auth_error")
        return False

    session.device_id = device_id
    session.authenticated = True
    if requested_agent:
        session.active_agent = requested_agent

    await send_session_ready(ctx, session)
    await send_ui_state(session, UiState.IDLE)
    return True
