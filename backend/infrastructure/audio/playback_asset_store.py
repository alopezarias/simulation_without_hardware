"""Temporary store for outbound playback assets served over HTTP."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import uuid


@dataclass(slots=True)
class PlaybackAsset:
    audio_id: str
    path: str
    codec: str
    sample_rate: int
    channels: int
    size_bytes: int
    source: str
    loopback: bool
    expires_at: int


class PlaybackAssetStore:
    def __init__(self, *, ttl_seconds: int = 600) -> None:
        self._ttl_seconds = max(1, ttl_seconds)
        self._root_dir = Path(tempfile.mkdtemp(prefix="sim_playback_assets_"))
        self._assets: dict[str, PlaybackAsset] = {}
        self._lock = threading.Lock()

    def publish_file(
        self,
        source_path: str,
        *,
        codec: str,
        sample_rate: int,
        channels: int,
        source: str,
        loopback: bool,
    ) -> PlaybackAsset:
        self.cleanup_expired()
        audio_id = f"audio-{uuid.uuid4().hex[:16]}"
        suffix = Path(source_path).suffix or ".pcm"
        target_path = self._root_dir / f"{audio_id}{suffix}"
        shutil.copyfile(source_path, target_path)
        size_bytes = target_path.stat().st_size
        expires_at = int(time.time()) + self._ttl_seconds
        asset = PlaybackAsset(
            audio_id=audio_id,
            path=str(target_path),
            codec=codec,
            sample_rate=sample_rate,
            channels=channels,
            size_bytes=size_bytes,
            source=source,
            loopback=loopback,
            expires_at=expires_at,
        )
        with self._lock:
            self._assets[audio_id] = asset
        return asset

    def get(self, audio_id: str) -> PlaybackAsset | None:
        self.cleanup_expired()
        with self._lock:
            asset = self._assets.get(audio_id)
        if asset is None:
            return None
        if asset.expires_at <= int(time.time()) or not os.path.exists(asset.path):
            self.delete(audio_id)
            return None
        return asset

    def cleanup_expired(self) -> None:
        now = int(time.time())
        with self._lock:
            expired_ids = [
                audio_id
                for audio_id, asset in self._assets.items()
                if asset.expires_at <= now or not os.path.exists(asset.path)
            ]
        for audio_id in expired_ids:
            self.delete(audio_id)

    def delete(self, audio_id: str) -> None:
        with self._lock:
            asset = self._assets.pop(audio_id, None)
        if asset is None:
            return
        try:
            os.remove(asset.path)
        except FileNotFoundError:
            pass
