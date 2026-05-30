from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

from backend.application.ports.audio_store import AudioStore


class LocalAudioStore(AudioStore):
    def __init__(self, base_dir: str) -> None:
        self.base_dir = Path(base_dir)

    def _save_sync(self, note_id: str, audio_bytes: bytes) -> str:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        target_dir = self.base_dir / month
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{note_id}.wav"
        path.write_bytes(audio_bytes)
        return str(path)

    async def save(self, note_id: str, audio_bytes: bytes) -> str:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._save_sync, note_id, audio_bytes)

    async def delete(self, path: str) -> None:
        p = Path(path)
        if p.exists():
            await asyncio.get_event_loop().run_in_executor(None, p.unlink)
