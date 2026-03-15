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
    def __init__(self, *, pcm_factory: Callable[[], Any] | None = None) -> None:
        self._pcm_factory = pcm_factory
        self._pcm: Any | None = None
        self.started = False
        self.buffered: list[bytes] = []
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
            self._pcm = _alsaaudio.PCM(_alsaaudio.PCM_PLAYBACK)
        self._configure_pcm(self._pcm)
        self.started = True

    def push(self, pcm_bytes: bytes) -> None:
        if not pcm_bytes:
            return
        self.buffered.append(pcm_bytes)
        if self._pcm is not None and hasattr(self._pcm, "write"):
            self._pcm.write(pcm_bytes)

    def stop(self, clear_buffer: bool = True) -> None:
        self.started = False
        pcm = self._pcm
        self._pcm = None
        close = getattr(pcm, "close", None)
        if close is not None:
            close()
        if clear_buffer:
            self.buffered.clear()

    def _configure_pcm(self, pcm: Any) -> None:
        setters = [
            ("setchannels", self.channels),
            ("setrate", self.sample_rate),
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
