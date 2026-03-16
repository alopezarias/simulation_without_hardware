"""Helpers to package PCM16 audio into protocol-friendly chunks."""

from __future__ import annotations

import base64
from dataclasses import dataclass


@dataclass(slots=True)
class PcmChunker:
    sample_rate: int = 16000
    channels: int = 1
    chunk_ms: int = 120
    codec: str = "pcm16"

    def build_chunk(self, pcm_bytes: bytes, *, seq: int, timestamp_ms: int) -> dict[str, object] | None:
        if not pcm_bytes:
            return None
        size_bytes = len(pcm_bytes)
        if size_bytes <= 0:
            return None
        duration_ms = int((size_bytes / (2 * self.channels)) * 1000 / self.sample_rate)
        if duration_ms <= 0:
            duration_ms = self.chunk_ms
        return {
            "seq": seq,
            "timestamp_ms": max(0, timestamp_ms),
            "duration_ms": duration_ms,
            "payload": base64.b64encode(pcm_bytes).decode("ascii"),
            "size_bytes": size_bytes,
            "codec": self.codec,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
        }
