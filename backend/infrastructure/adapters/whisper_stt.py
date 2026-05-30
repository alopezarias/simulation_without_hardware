from __future__ import annotations

import asyncio
import os
import tempfile
from functools import lru_cache

from backend.application.ports.stt_port import SttPort, TranscriptionResult


class WhisperStt(SttPort):
    """faster-whisper backed STT. The model is loaded once on first use."""

    def __init__(self, model_size: str = "base", language: str = "auto") -> None:
        self.model_size = model_size
        self.language = None if language == "auto" else language
        self._model = None

    def _load_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(self.model_size, device="auto", compute_type="int8")
        return self._model

    def _transcribe_sync(self, audio_bytes: bytes) -> TranscriptionResult:
        model = self._load_model()
        fd, tmp_path = tempfile.mkstemp(suffix=".wav")
        try:
            os.write(fd, audio_bytes)
            os.close(fd)
            segments, info = model.transcribe(
                tmp_path,
                language=self.language,
                beam_size=1,
                vad_filter=True,
            )
            text = " ".join(seg.text.strip() for seg in segments).strip()
            return TranscriptionResult(
                text=text,
                language=info.language or "unknown",
                duration_s=info.duration,
            )
        finally:
            os.unlink(tmp_path)

    async def transcribe(self, audio_bytes: bytes) -> TranscriptionResult:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._transcribe_sync, audio_bytes)


@lru_cache(maxsize=1)
def get_whisper_stt(model_size: str, language: str) -> WhisperStt:
    return WhisperStt(model_size=model_size, language=language)
