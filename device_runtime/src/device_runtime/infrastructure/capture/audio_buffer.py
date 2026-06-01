"""In-memory PCM accumulator for note-taker audio capture."""

from __future__ import annotations

import base64
import io
import wave
from typing import Any


class AudioBuffer:
    """Collects base64-encoded PCM16 chunks from an AudioCapturePort into memory."""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []

    def clear(self) -> None:
        self._chunks.clear()

    def collect(self, capture: Any, max_chunks: int = 100) -> int:
        """Drain up to *max_chunks* from *capture* and append decoded PCM. Returns count."""
        chunks = capture.read_chunks(max_chunks)
        collected = 0
        for chunk in chunks:
            payload = chunk.get("payload")
            if not isinstance(payload, str) or not payload:
                continue
            try:
                pcm = base64.b64decode(payload)
            except Exception:
                continue
            if pcm:
                self._chunks.append(pcm)
                collected += 1
        return collected

    def to_pcm_bytes(self) -> bytes:
        return b"".join(self._chunks)

    def to_wav_bytes(self, *, sample_rate: int = 16000, channels: int = 1) -> bytes:
        pcm = self.to_pcm_bytes()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)  # PCM16 = 2 bytes per sample
            wf.setframerate(sample_rate)
            wf.writeframes(pcm)
        return buf.getvalue()

    @property
    def duration_s(self) -> float:
        """Approximate duration of buffered audio in seconds."""
        total_pcm = sum(len(c) for c in self._chunks)
        return 0.0 if not total_pcm else total_pcm / (2 * 16000)

    def __len__(self) -> int:
        return len(self._chunks)

    def __bool__(self) -> bool:
        return bool(self._chunks)
