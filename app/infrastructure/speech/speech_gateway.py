"""Speech gateway adapter over SpeechPipeline."""

from __future__ import annotations

from typing import Any

from speech_pipeline import SpeechPipeline


class SpeechGateway:
    def __init__(self, pipeline: SpeechPipeline | None = None) -> None:
        self._pipeline = pipeline or SpeechPipeline()

    @property
    def stt_available(self) -> bool:
        return self._pipeline.stt_available

    @property
    def tts_available(self) -> bool:
        return self._pipeline.tts_available

    def capabilities(self) -> dict[str, Any]:
        return self._pipeline.capabilities()

    def transcribe_pcm_file(self, pcm_path: str, sample_rate: int, channels: int) -> str:
        return self._pipeline.transcribe_pcm_file(pcm_path, sample_rate, channels)

    def synthesize_text_to_pcm_file(
        self,
        text: str,
        sample_rate: int,
        channels: int,
    ) -> tuple[str, int]:
        return self._pipeline.synthesize_text_to_pcm_file(text, sample_rate, channels)
