"""Guarded ALSA capture adapter for Raspberry Pi runtime composition."""

from __future__ import annotations

import importlib
import time
from typing import Any, Callable

from device_runtime.infrastructure.audio.pcm_chunker import PcmChunker

try:
    _alsaaudio = importlib.import_module("alsaaudio")

    ALSA_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - depends on host environment
    _alsaaudio = None
    ALSA_IMPORT_ERROR = exc


class AlsaCapture:
    def __init__(
        self,
        *,
        pcm_factory: Callable[[], Any] | None = None,
        sample_rate: int = 16000,
        channels: int = 1,
    ) -> None:
        self._pcm_factory = pcm_factory
        self.sample_rate = sample_rate
        self.channels = channels
        self._pcm: Any | None = None
        self.started = False
        self.chunk_ms = 120
        self.period_size = max(1, int(sample_rate * self.chunk_ms / 1000))
        self._chunker = PcmChunker(sample_rate=sample_rate, channels=channels, chunk_ms=self.chunk_ms)
        self._seq = 0
        self._started_monotonic = 0.0
        self.last_read_chunks: list[dict[str, object]] = []

    @property
    def available(self) -> bool:
        return self._pcm_factory is not None or _alsaaudio is not None

    def start(self) -> None:
        if self.started:
            return
        if self._pcm_factory is not None:
            self._pcm = self._pcm_factory()
        elif _alsaaudio is None:
            detail = ""
            if ALSA_IMPORT_ERROR is not None:
                detail = f": {ALSA_IMPORT_ERROR}"
            raise RuntimeError("ALSA capture adapter requires pyalsaaudio on Raspberry Pi" + detail)
        else:
            pcm_kwargs: dict[str, Any] = {}
            mode = getattr(_alsaaudio, "PCM_NONBLOCK", None)
            if mode is not None:
                pcm_kwargs["mode"] = mode
            self._pcm = _alsaaudio.PCM(_alsaaudio.PCM_CAPTURE, **pcm_kwargs)
        self._configure_pcm(self._pcm)
        self._seq = 0
        self._started_monotonic = time.monotonic()
        self.started = True

    def stop(self) -> None:
        self.started = False
        pcm = self._pcm
        self._pcm = None
        close = getattr(pcm, "close", None)
        if close is not None:
            close()

    def read_chunks(self, max_chunks: int) -> list[dict[str, object]]:
        if not self.started or self._pcm is None or max_chunks <= 0:
            return []
        chunks: list[dict[str, object]] = []
        while len(chunks) < max_chunks:
            pcm_bytes = self._read_once()
            if not pcm_bytes:
                break
            chunk = self._chunker.build_chunk(
                pcm_bytes,
                seq=self._seq,
                timestamp_ms=int((time.monotonic() - self._started_monotonic) * 1000),
            )
            if chunk is None:
                continue
            chunks.append(chunk)
            self._seq += 1
        self.last_read_chunks = list(chunks)
        return list(chunks)

    def _configure_pcm(self, pcm: Any) -> None:
        setters = [
            ("setchannels", self.channels),
            ("setrate", self.sample_rate),
            ("setperiodsize", self.period_size),
        ]
        format_value = None
        if _alsaaudio is not None:
            format_value = getattr(_alsaaudio, "PCM_FORMAT_S16_LE", None)
        if format_value is not None:
            setters.insert(2, ("setformat", format_value))
        for method_name, value in setters:
            method = getattr(pcm, method_name, None)
            if method is not None:
                method(value)

    def _read_once(self) -> bytes:
        assert self._pcm is not None
        raw = self._pcm.read()
        if isinstance(raw, tuple):
            if len(raw) >= 2:
                length, payload = raw[0], raw[1]
                if isinstance(length, int) and length <= 0:
                    return b""
                if isinstance(payload, (bytes, bytearray)):
                    return bytes(payload)
                return b""
            return b""
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
        return b""
