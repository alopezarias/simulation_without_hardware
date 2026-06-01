"""Null note-capture gateway — records calls without performing any HTTP I/O."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CapturedUpload:
    audio_bytes: bytes
    capture_mode: str
    sample_rate: int
    channels: int


class NullNoteCaptureGateway:
    """Test/null adapter; captures upload calls for inspection."""

    def __init__(self, note_id: str = "null-note-id") -> None:
        self._note_id = note_id
        self.uploads: list[CapturedUpload] = []

    async def upload(
        self,
        audio_bytes: bytes,
        capture_mode: str,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
    ) -> str:
        self.uploads.append(
            CapturedUpload(
                audio_bytes=audio_bytes,
                capture_mode=capture_mode,
                sample_rate=sample_rate,
                channels=channels,
            )
        )
        return self._note_id

    async def upload_wav(self, wav_bytes: bytes, capture_mode: str) -> str:
        self.uploads.append(
            CapturedUpload(
                audio_bytes=wav_bytes,
                capture_mode=capture_mode,
                sample_rate=0,
                channels=0,
            )
        )
        return self._note_id
