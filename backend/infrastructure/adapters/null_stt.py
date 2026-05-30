from __future__ import annotations

from backend.application.ports.stt_port import SttPort, TranscriptionResult


class NullStt(SttPort):
    """No-op STT for tests and local runs without Whisper."""

    def __init__(self, fixed_text: str = "test transcription", language: str = "es") -> None:
        self.fixed_text = fixed_text
        self.language = language

    async def transcribe(self, audio_bytes: bytes) -> TranscriptionResult:
        return TranscriptionResult(
            text=self.fixed_text,
            language=self.language,
            duration_s=max(1.0, len(audio_bytes) / 32000),
        )
