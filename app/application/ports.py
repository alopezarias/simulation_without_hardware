"""Hexagonal ports for application services."""

from __future__ import annotations

from typing import Any, AsyncIterator, Protocol

if False:  # pragma: no cover
    from app.domain.session import DeviceSession


class DeviceOutputPort(Protocol):
    async def send_json(self, message: dict[str, Any]) -> None: ...


class AssistantPort(Protocol):
    async def stream_response(
        self,
        agent_id: str,
        user_text: str,
        session_id: str,
    ) -> AsyncIterator[str]: ...


class SpeechPort(Protocol):
    @property
    def stt_available(self) -> bool: ...

    @property
    def tts_available(self) -> bool: ...

    def capabilities(self) -> dict[str, Any]: ...

    def transcribe_pcm_file(self, pcm_path: str, sample_rate: int, channels: int) -> str: ...

    def synthesize_text_to_pcm_file(
        self,
        text: str,
        sample_rate: int,
        channels: int,
    ) -> tuple[str, int]: ...


class AudioStorePort(Protocol):
    def start_new_recording(self, session: "DeviceSession") -> None: ...

    def append_chunk(self, session: "DeviceSession", chunk: bytes) -> None: ...

    def close(self, session: "DeviceSession") -> None: ...

    def cleanup(self, session: "DeviceSession") -> None: ...
