"""Guarded ALSA playback adapter for Raspberry Pi runtime composition."""

from __future__ import annotations

import importlib
from typing import Any, Callable

try:
    _alsaaudio = importlib.import_module("alsaaudio")

    ALSA_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - depends on host environment
    _alsaaudio = None
    ALSA_IMPORT_ERROR = exc


class AlsaPlayback:
    def __init__(
        self,
        *,
        pcm_factory: Callable[[], Any] | None = None,
        device: str = "default",
        period_size: int = 0,
        chunk_ms: int = 200,
        start_buffer_ms: int = 1000,
    ) -> None:
        self._pcm_factory = pcm_factory
        self.device = device
        self.period_size = period_size
        self.chunk_ms = chunk_ms
        self.start_buffer_ms = start_buffer_ms
        self._pcm: Any | None = None
        self.started = False
        self.buffered: list[bytes] = []
        self._pending = bytearray()
        self._primed = False
        self.sample_rate = 16000
        self.channels = 1

    @property
    def available(self) -> bool:
        return self._pcm_factory is not None or _alsaaudio is not None

    def start(self, sample_rate: int, channels: int) -> None:
        self.stop(clear_buffer=True)
        self.sample_rate = sample_rate
        self.channels = channels
        if self._pcm_factory is not None:
            self._pcm = self._pcm_factory()
        elif _alsaaudio is None:
            detail = ""
            if ALSA_IMPORT_ERROR is not None:
                detail = f": {ALSA_IMPORT_ERROR}"
            raise RuntimeError("ALSA playback adapter requires pyalsaaudio on Raspberry Pi" + detail)
        else:
            pcm_kwargs: dict[str, Any] = {}
            if self.device:
                pcm_kwargs["device"] = self.device
            self._pcm = _alsaaudio.PCM(_alsaaudio.PCM_PLAYBACK, **pcm_kwargs)
        self._configure_pcm(self._pcm)
        self._pending.clear()
        self._primed = self.start_buffer_ms <= 0
        self.started = True

    def push(self, pcm_bytes: bytes) -> None:
        if not pcm_bytes:
            return
        self.buffered.append(pcm_bytes)
        self._pending.extend(pcm_bytes)
        self._drain_pending(force=False)

    def end_session(self) -> None:
        self._drain_pending(force=True)

    def stop(self, clear_buffer: bool = True) -> None:
        self.started = False
        pcm = self._pcm
        self._pcm = None
        self._primed = False
        close = getattr(pcm, "close", None)
        if close is not None:
            close()
        if clear_buffer:
            self.buffered.clear()
            self._pending.clear()

    def _configure_pcm(self, pcm: Any) -> None:
        period_size = self.period_size or max(1, int(self.sample_rate * self.chunk_ms / 1000))
        setters = [
            ("setchannels", self.channels),
            ("setrate", self.sample_rate),
            ("setperiodsize", period_size),
        ]
        format_value = None
        if _alsaaudio is not None:
            format_value = getattr(_alsaaudio, "PCM_FORMAT_S16_LE", None)
        if format_value is not None:
            setters.append(("setformat", format_value))
        for method_name, value in setters:
            method = getattr(pcm, method_name, None)
            if method is not None:
                method(value)

    def _drain_pending(self, *, force: bool) -> None:
        pcm = self._pcm
        write = getattr(pcm, "write", None)
        if pcm is None or not callable(write):
            return
        if not self._primed and len(self._pending) < self._start_buffer_bytes:
            return
        self._primed = True
        chunk_bytes = self._drain_chunk_bytes
        while self._pending:
            if not force and len(self._pending) < chunk_bytes:
                break
            take = len(self._pending) if force else chunk_bytes
            payload = bytes(self._pending[:take])
            del self._pending[:take]
            write(payload)

    @property
    def _period_bytes(self) -> int:
        period_size = self.period_size or max(1, int(self.sample_rate * self.chunk_ms / 1000))
        return max(2, period_size * max(1, self.channels) * 2)

    @property
    def _chunk_bytes(self) -> int:
        frames = max(1, int(self.sample_rate * self.chunk_ms / 1000))
        return max(2, frames * max(1, self.channels) * 2)

    @property
    def _drain_chunk_bytes(self) -> int:
        return max(self._period_bytes, self._chunk_bytes)

    @property
    def _start_buffer_bytes(self) -> int:
        if self.start_buffer_ms <= 0:
            return 0
        return max(
            self._drain_chunk_bytes,
            int(self.sample_rate * self.channels * 2 * self.start_buffer_ms / 1000),
        )
