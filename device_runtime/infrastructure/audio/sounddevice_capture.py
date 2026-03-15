"""Shared sounddevice-based capture adapter for development runtimes."""

from __future__ import annotations

import queue
import time
from typing import Any

from device_runtime.infrastructure.audio.pcm_chunker import PcmChunker

try:
    import sounddevice as _sounddevice

    SOUNDDEVICE_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - exercised via tests with dependency missing
    _sounddevice = None
    SOUNDDEVICE_IMPORT_ERROR = exc


def sounddevice_is_available() -> bool:
    return _sounddevice is not None


def require_sounddevice() -> Any:
    if _sounddevice is None:
        detail = ""
        if SOUNDDEVICE_IMPORT_ERROR is not None:
            detail = f": {SOUNDDEVICE_IMPORT_ERROR}"
        raise RuntimeError("sounddevice is not available. Install simulator audio dependencies" + detail)
    return _sounddevice


def query_input_devices(sd_module: Any | None = None) -> list[tuple[int, str]]:
    sd = sd_module or require_sounddevice()
    devices = sd.query_devices()
    entries: list[tuple[int, str]] = []
    for index, dev in enumerate(devices):
        if int(dev.get("max_input_channels", 0)) <= 0:
            continue
        label = f"{index}: {str(dev.get('name', f'device-{index}')).strip()}"
        entries.append((index, label))
    return entries


class SoundDeviceCapture:
    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_ms: int = 120,
        max_queue_chunks: int = 80,
        *,
        sd_module: Any | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_ms = chunk_ms
        self.frames_per_chunk = max(1, int(sample_rate * chunk_ms / 1000))
        self.max_queue_chunks = max(4, max_queue_chunks)
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=self.max_queue_chunks)
        self._stream: Any | None = None
        self._chunker = PcmChunker(sample_rate=sample_rate, channels=channels, chunk_ms=chunk_ms)
        self.last_read_chunks: list[dict[str, Any]] = []
        self._started_monotonic = 0.0
        self._seq = 0
        self._bytes_sent = 0
        self.device_index: int | None = None
        self._dropped_chunks = 0
        self._sd_module = sd_module

    @property
    def active(self) -> bool:
        return self._stream is not None

    @property
    def bytes_sent(self) -> int:
        return self._bytes_sent

    @property
    def dropped_chunks(self) -> int:
        return self._dropped_chunks

    @property
    def available(self) -> bool:
        return self.active

    def start(self) -> None:
        sd = self._sd_module or require_sounddevice()
        if self.active:
            return

        self._queue = queue.Queue(maxsize=self.max_queue_chunks)
        self._seq = 0
        self._bytes_sent = 0
        self._dropped_chunks = 0
        self._started_monotonic = time.monotonic()

        def _callback(indata: Any, frames: int, _time: Any, status: Any) -> None:
            if status or frames <= 0:
                return
            pcm_bytes = bytes(indata.tobytes())
            if not pcm_bytes:
                return
            try:
                self._queue.put_nowait(pcm_bytes)
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._queue.put_nowait(pcm_bytes)
                except queue.Full:
                    pass
                self._dropped_chunks += 1

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
            blocksize=self.frames_per_chunk,
            device=self.device_index,
            callback=_callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if not self.active:
            return
        assert self._stream is not None
        try:
            self._stream.stop()
        finally:
            self._stream.close()
            self._stream = None

    def read_chunks(self, max_chunks: int) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        while len(chunks) < max_chunks:
            try:
                pcm_bytes = self._queue.get_nowait()
            except queue.Empty:
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
            self._bytes_sent += len(pcm_bytes)
        self.last_read_chunks = list(chunks)
        return list(chunks)

    def pop_chunks(self, max_chunks: int | None = None) -> list[dict[str, Any]]:
        return self.read_chunks(max_chunks or self.max_queue_chunks)
