"""Offline audio queue — persists WAV files when the backend is unreachable.

Files are stored as  {timestamp_ms}_{capture_mode}.wav  so they can be
re-uploaded in chronological order when the connection is restored.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class OfflineQueue:
    """Save failed uploads to disk and retry them via a gateway drain."""

    def __init__(self, queue_dir: str = "/tmp/notes_queue") -> None:
        self._dir = Path(queue_dir)

    # ── write ─────────────────────────────────────────────────────────────────

    def save(self, wav_bytes: bytes, capture_mode: str) -> Path:
        """Persist wav_bytes to the queue directory; return the file path."""
        self._dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time() * 1000)
        uid = uuid.uuid4().hex[:8]
        # capture_mode values are "wake_word" / "manual" — safe for filenames
        path = self._dir / f"{ts}_{uid}_{capture_mode}.wav"
        path.write_bytes(wav_bytes)
        logger.info("offline_queue: saved %s (%d bytes)", path.name, len(wav_bytes))
        return path

    # ── read ──────────────────────────────────────────────────────────────────

    def pending(self) -> list[Path]:
        """Return queued WAV files sorted by filename (chronological order)."""
        if not self._dir.exists():
            return []
        return sorted(self._dir.glob("*.wav"))

    @property
    def pending_count(self) -> int:
        return len(self.pending())

    # ── drain ─────────────────────────────────────────────────────────────────

    async def drain(
        self,
        upload_fn: Callable[[bytes, str], Awaitable[str]],
    ) -> tuple[int, int]:
        """Try to upload every queued file via upload_fn(wav_bytes, capture_mode).

        Returns (uploaded, failed) counts.  Successfully uploaded files are
        deleted; failures are left in the queue for the next drain cycle.
        """
        files = self.pending()
        if not files:
            return 0, 0

        uploaded = failed = 0
        for path in files:
            capture_mode = _capture_mode_from_name(path.name)
            try:
                wav_bytes = path.read_bytes()
            except OSError:
                logger.warning("offline_queue: could not read %s; skipping", path.name)
                failed += 1
                continue
            try:
                await upload_fn(wav_bytes, capture_mode)
                path.unlink(missing_ok=True)
                uploaded += 1
                logger.info("offline_queue: drained %s", path.name)
            except Exception as exc:
                logger.warning("offline_queue: drain failed for %s: %s", path.name, exc)
                failed += 1

        return uploaded, failed


# ── helpers ──────────────────────────────────────────────────────────────────

def _capture_mode_from_name(filename: str) -> str:
    """Extract capture_mode from '{ts}_{uid}_{capture_mode}.wav'."""
    stem = filename.removesuffix(".wav")
    parts = stem.split("_", 2)   # [ts, uid, capture_mode]
    return parts[2] if len(parts) == 3 else "manual"
