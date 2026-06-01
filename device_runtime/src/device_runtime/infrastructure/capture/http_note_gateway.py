"""HTTP gateway that POSTs audio to the note-taker backend /audio/capture endpoint."""

from __future__ import annotations

import asyncio
import io
import json
import uuid
import wave
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class HttpNoteCaptureGateway:
    """Converts raw PCM to WAV and POSTs it to the backend using stdlib urllib.

    On transient server-side (5xx) or network errors the upload is retried up to
    len(retry_delays_s) times.  Client errors (4xx) are never retried.
    Default retry schedule: 2 s → 4 s → 8 s (3 retries, 4 total attempts).
    """

    def __init__(
        self,
        api_url: str,
        *,
        api_token: str = "",
        timeout_s: float = 30.0,
        retry_delays_s: tuple[float, ...] = (2.0, 4.0, 8.0),
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._api_token = api_token
        self._timeout_s = timeout_s
        self._retry_delays_s = retry_delays_s

    async def upload(
        self,
        audio_bytes: bytes,
        capture_mode: str,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
    ) -> str:
        wav_bytes = _pcm_to_wav(audio_bytes, sample_rate=sample_rate, channels=channels)
        delays = self._retry_delays_s
        for attempt in range(len(delays) + 1):
            try:
                return await asyncio.to_thread(self._post_sync, wav_bytes, capture_mode)
            except RuntimeError as exc:
                if _is_client_error(exc) or attempt == len(delays):
                    raise
                await asyncio.sleep(delays[attempt])

    def _post_sync(self, wav_bytes: bytes, capture_mode: str) -> str:
        boundary = uuid.uuid4().hex
        body = _build_multipart(
            boundary,
            fields={"capture_mode": capture_mode},
            file_field="audio",
            filename="audio.wav",
            file_data=wav_bytes,
            content_type="audio/wav",
        )
        url = f"{self._api_url}/audio/capture"
        req = Request(url, data=body, method="POST")  # noqa: S310
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        req.add_header("Content-Length", str(len(body)))
        if self._api_token:
            req.add_header("Authorization", f"Bearer {self._api_token}")
        try:
            with urlopen(req, timeout=self._timeout_s) as resp:  # noqa: S310
                raw = resp.read()
                try:
                    body_json = json.loads(raw)
                    return str(body_json.get("note_id", ""))
                except (json.JSONDecodeError, AttributeError):
                    return ""
        except HTTPError as exc:
            raise RuntimeError(f"Backend returned HTTP {exc.code}: {exc.reason}") from exc
        except URLError as exc:
            raise RuntimeError(f"Upload failed: {exc.reason}") from exc


# ── helpers ──────────────────────────────────────────────────────────────────

def _is_client_error(exc: RuntimeError) -> bool:
    """Return True for 4xx HTTP errors (non-retryable)."""
    return "HTTP 4" in str(exc)


def _pcm_to_wav(pcm_bytes: bytes, *, sample_rate: int, channels: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def _build_multipart(
    boundary: str,
    *,
    fields: dict[str, str],
    file_field: str,
    filename: str,
    file_data: bytes,
    content_type: str,
) -> bytes:
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n'
            f"\r\n"
            f"{value}\r\n".encode()
        )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n"
            f"\r\n"
        ).encode()
        + file_data
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts)
