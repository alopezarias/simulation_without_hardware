"""Shared sounddevice-based playback adapter for development runtimes."""

from __future__ import annotations

import threading
from typing import Any

from device_runtime.infrastructure.audio.sounddevice_capture import require_sounddevice


class SoundDevicePlayback:
    def __init__(self, *, sd_module: Any | None = None) -> None:
        self.sample_rate = 16000
        self.channels = 1
        self._stream: Any | None = None
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._max_buffer_bytes = self.sample_rate * self.channels * 2 * 8
        self._sd_module = sd_module

    @property
    def active(self) -> bool:
        return self._stream is not None

    @property
    def buffered_bytes(self) -> int:
        with self._lock:
            return len(self._buffer)

    @property
    def available(self) -> bool:
        return self.active

    def start(self, sample_rate: int, channels: int) -> None:
        sd = self._sd_module or require_sounddevice()
        self.stop(clear_buffer=True)
        self.sample_rate = sample_rate
        self.channels = channels
        self._max_buffer_bytes = max(2048, self.sample_rate * self.channels * 2 * 8)

        def _callback(outdata: Any, _frames: int, _time: Any, status: Any) -> None:
            if status:
                return
            need = len(outdata)
            if need <= 0:
                return
            with self._lock:
                outdata[:] = b"\x00" * need
                take = min(need, len(self._buffer))
                if take > 0:
                    outdata[:take] = self._buffer[:take]
                    del self._buffer[:take]

        self._stream = sd.RawOutputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
            callback=_callback,
        )
        self._stream.start()

    def push(self, pcm_bytes: bytes) -> None:
        if not pcm_bytes or not self.active:
            return
        with self._lock:
            self._buffer.extend(pcm_bytes)
            overflow = len(self._buffer) - self._max_buffer_bytes
            if overflow > 0:
                del self._buffer[:overflow]

    def stop(self, clear_buffer: bool = True) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
            finally:
                self._stream.close()
                self._stream = None
        if clear_buffer:
            with self._lock:
                self._buffer.clear()
