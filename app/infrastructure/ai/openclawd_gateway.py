"""Assistant gateway adapter based on OpenClawdAdapter."""

from __future__ import annotations

from collections.abc import AsyncIterator

from openclawd_adapter import OpenClawdAdapter


class OpenClawdGateway:
    def __init__(self, adapter: OpenClawdAdapter | None = None) -> None:
        self._adapter = adapter or OpenClawdAdapter()

    async def stream_response(
        self,
        agent_id: str,
        user_text: str,
        session_id: str,
    ) -> AsyncIterator[str]:
        async for chunk in self._adapter.stream_response(
            agent_id=agent_id,
            user_text=user_text,
            session_id=session_id,
        ):
            yield chunk
