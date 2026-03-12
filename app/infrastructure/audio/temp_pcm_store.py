"""Temporary PCM file store for streamed audio chunks."""

from __future__ import annotations

import logging
import os
import tempfile

from app.domain.session import DeviceSession

logger = logging.getLogger("simulation-backend")


class TempPcmAudioStore:
    def start_new_recording(self, session: DeviceSession) -> None:
        self.cleanup(session)
        fd, audio_path = tempfile.mkstemp(prefix="sim_audio_", suffix=".pcm")
        os.close(fd)
        session.audio_file_path = audio_path
        session.audio_file_handle = open(audio_path, "wb")

    def append_chunk(self, session: DeviceSession, chunk: bytes) -> None:
        if not chunk:
            return
        if session.audio_file_handle is not None:
            session.audio_file_handle.write(chunk)

    def close(self, session: DeviceSession) -> None:
        if session.audio_file_handle is None:
            return
        try:
            session.audio_file_handle.close()
        finally:
            session.audio_file_handle = None

    def cleanup(self, session: DeviceSession) -> None:
        self.close(session)
        path = session.audio_file_path
        session.audio_file_path = None
        if not path:
            return
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except Exception:
            logger.exception("Failed to delete temp audio file: %s", path)
