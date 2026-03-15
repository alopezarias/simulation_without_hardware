"""Null audio adapters used for degraded or test runtimes."""

from __future__ import annotations


class NullAudioCapture:
    def __init__(self) -> None:
        self.started = False

    @property
    def available(self) -> bool:
        return False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def read_chunks(self, max_chunks: int) -> list[dict[str, object]]:
        return []


class NullAudioPlayback:
    def __init__(self) -> None:
        self.started = False
        self.buffered: list[bytes] = []

    @property
    def available(self) -> bool:
        return False

    def start(self, sample_rate: int, channels: int) -> None:
        self.started = True

    def push(self, pcm_bytes: bytes) -> None:
        self.buffered.append(pcm_bytes)

    def stop(self, clear_buffer: bool = True) -> None:
        self.started = False
        if clear_buffer:
            self.buffered.clear()
