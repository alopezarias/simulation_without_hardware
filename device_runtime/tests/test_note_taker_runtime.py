"""Exhaustive tests for the note-taker runtime: domain, config, adapters and runner."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from device_runtime.domain.note_taker_state import (
    CaptureMode,
    NoteTakerEvent,
    NoteTakerMode,
    NoteTakerState,
)
from device_runtime.application.services.note_taker_state_machine import (
    NoteTakerStateMachine,
)
from device_runtime.application.services.note_taker_config import NoteTakerConfig
from device_runtime.infrastructure.config.note_taker_env_loader import load_note_taker_config
from device_runtime.infrastructure.capture.audio_buffer import AudioBuffer
from device_runtime.infrastructure.capture.null_note_gateway import NullNoteCaptureGateway
from device_runtime.infrastructure.capture.http_note_gateway import (
    HttpNoteCaptureGateway,
    _build_multipart,
    _pcm_to_wav,
)
from device_runtime.infrastructure.wake_word.null_wake_word import NullWakeWord
from device_runtime.entrypoints.note_taker_main import (
    NoteTakerBootstrap,
    NoteTakerRunner,
    build_note_taker,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_pcm(frames: int = 160) -> bytes:
    return b"\x00\x01" * frames  # silent PCM16 frames


def _make_chunk(pcm: bytes) -> dict[str, Any]:
    return {"payload": base64.b64encode(pcm).decode(), "size_bytes": len(pcm)}


def _make_config(**overrides: Any) -> NoteTakerConfig:
    base = {
        "device_id": "test-device",
        "note_api_url": "http://localhost:8000",
    }
    base.update(overrides)
    return NoteTakerConfig(**base)


def _make_bootstrap(config: NoteTakerConfig | None = None, **overrides: Any) -> NoteTakerBootstrap:
    from device_runtime.infrastructure.audio.null_audio import NullAudioCapture
    from device_runtime.infrastructure.capture.offline_queue import OfflineQueue
    from device_runtime.infrastructure.display.null_display import NullDisplay
    from device_runtime.infrastructure.input.null_button import NullButton
    from device_runtime.infrastructure.power.pisugar_status import NullPowerStatus
    from device_runtime.infrastructure.rgb.null_rgb import NullRgb

    cfg = config or _make_config()
    defaults: dict[str, Any] = dict(
        config=cfg,
        display=NullDisplay(),
        button=NullButton(),
        audio_capture=NullAudioCapture(),
        power=NullPowerStatus(),
        rgb=NullRgb(),
        wake_word=NullWakeWord(),
        note_gateway=NullNoteCaptureGateway(),
        offline_queue=OfflineQueue("/tmp/test_notes_queue"),
    )
    defaults.update(overrides)
    return NoteTakerBootstrap(**defaults)


# ── state machine tests ───────────────────────────────────────────────────────

class TestNoteTakerStateMachine:
    def setup_method(self) -> None:
        self.sm = NoteTakerStateMachine()
        self.initial = NoteTakerState()  # AMBIENT, no recording

    def _handle(self, event: NoteTakerEvent, state: NoteTakerState | None = None) -> Any:
        return self.sm.handle(state or self.initial, event)

    # TAP transitions
    def test_tap_from_ambient_goes_silent(self) -> None:
        t = self._handle(NoteTakerEvent.TAP)
        assert t.state.mode == NoteTakerMode.SILENT
        assert t.action == "none"

    def test_tap_from_silent_goes_ambient(self) -> None:
        silent = NoteTakerState(mode=NoteTakerMode.SILENT)
        t = self._handle(NoteTakerEvent.TAP, silent)
        assert t.state.mode == NoteTakerMode.AMBIENT

    def test_tap_during_recording_is_ignored(self) -> None:
        recording = NoteTakerState(mode=NoteTakerMode.RECORDING, capture_mode=CaptureMode.MANUAL)
        t = self._handle(NoteTakerEvent.TAP, recording)
        assert t.state.mode == NoteTakerMode.RECORDING
        assert t.action == "none"

    def test_two_taps_returns_to_original_mode(self) -> None:
        t1 = self.sm.handle(self.initial, NoteTakerEvent.TAP)
        t2 = self.sm.handle(t1.state, NoteTakerEvent.TAP)
        assert t2.state.mode == NoteTakerMode.AMBIENT

    # WAKE_WORD transitions
    def test_wake_word_from_ambient_starts_recording(self) -> None:
        t = self._handle(NoteTakerEvent.WAKE_WORD)
        assert t.state.mode == NoteTakerMode.RECORDING
        assert t.state.capture_mode == CaptureMode.WAKE_WORD
        assert t.action == "start_recording"
        assert t.capture_mode == CaptureMode.WAKE_WORD

    def test_wake_word_from_silent_is_ignored(self) -> None:
        silent = NoteTakerState(mode=NoteTakerMode.SILENT)
        t = self._handle(NoteTakerEvent.WAKE_WORD, silent)
        assert t.state.mode == NoteTakerMode.SILENT
        assert t.action == "none"

    def test_wake_word_during_recording_is_ignored(self) -> None:
        recording = NoteTakerState(mode=NoteTakerMode.RECORDING, capture_mode=CaptureMode.WAKE_WORD)
        t = self._handle(NoteTakerEvent.WAKE_WORD, recording)
        assert t.action == "none"

    def test_wake_word_sets_return_to_ambient(self) -> None:
        t = self._handle(NoteTakerEvent.WAKE_WORD)
        assert t.state.return_to == NoteTakerMode.AMBIENT

    # LONG_PRESS transitions
    def test_long_press_from_ambient_starts_manual_recording(self) -> None:
        t = self._handle(NoteTakerEvent.LONG_PRESS)
        assert t.state.mode == NoteTakerMode.RECORDING
        assert t.state.capture_mode == CaptureMode.MANUAL
        assert t.action == "start_recording"
        assert t.capture_mode == CaptureMode.MANUAL

    def test_long_press_from_silent_starts_manual_recording(self) -> None:
        silent = NoteTakerState(mode=NoteTakerMode.SILENT)
        t = self._handle(NoteTakerEvent.LONG_PRESS, silent)
        assert t.state.mode == NoteTakerMode.RECORDING
        assert t.state.return_to == NoteTakerMode.SILENT

    def test_long_press_during_recording_is_ignored(self) -> None:
        recording = NoteTakerState(mode=NoteTakerMode.RECORDING, capture_mode=CaptureMode.MANUAL)
        t = self._handle(NoteTakerEvent.LONG_PRESS, recording)
        assert t.action == "none"

    def test_long_press_records_previous_mode_as_return_to(self) -> None:
        silent = NoteTakerState(mode=NoteTakerMode.SILENT)
        t = self._handle(NoteTakerEvent.LONG_PRESS, silent)
        assert t.state.return_to == NoteTakerMode.SILENT

    # RELEASE transitions
    def test_release_after_manual_recording_stops_and_uploads(self) -> None:
        recording = NoteTakerState(
            mode=NoteTakerMode.RECORDING,
            capture_mode=CaptureMode.MANUAL,
            return_to=NoteTakerMode.AMBIENT,
        )
        t = self._handle(NoteTakerEvent.RELEASE, recording)
        assert t.state.mode == NoteTakerMode.AMBIENT
        assert t.state.capture_mode is None
        assert t.action == "stop_and_upload"
        assert t.capture_mode == CaptureMode.MANUAL

    def test_release_returns_to_remembered_mode(self) -> None:
        recording = NoteTakerState(
            mode=NoteTakerMode.RECORDING,
            capture_mode=CaptureMode.MANUAL,
            return_to=NoteTakerMode.SILENT,
        )
        t = self._handle(NoteTakerEvent.RELEASE, recording)
        assert t.state.mode == NoteTakerMode.SILENT

    def test_release_during_wake_recording_is_ignored(self) -> None:
        recording = NoteTakerState(
            mode=NoteTakerMode.RECORDING,
            capture_mode=CaptureMode.WAKE_WORD,
        )
        t = self._handle(NoteTakerEvent.RELEASE, recording)
        assert t.action == "none"
        assert t.state.mode == NoteTakerMode.RECORDING

    def test_release_when_not_recording_is_ignored(self) -> None:
        t = self._handle(NoteTakerEvent.RELEASE)
        assert t.action == "none"
        assert t.state.mode == NoteTakerMode.AMBIENT

    # RECORDING_DONE transitions
    def test_recording_done_stops_wake_recording(self) -> None:
        recording = NoteTakerState(
            mode=NoteTakerMode.RECORDING,
            capture_mode=CaptureMode.WAKE_WORD,
            return_to=NoteTakerMode.AMBIENT,
        )
        t = self._handle(NoteTakerEvent.RECORDING_DONE, recording)
        assert t.state.mode == NoteTakerMode.AMBIENT
        assert t.action == "stop_and_upload"
        assert t.capture_mode == CaptureMode.WAKE_WORD

    def test_recording_done_stops_manual_recording(self) -> None:
        recording = NoteTakerState(
            mode=NoteTakerMode.RECORDING,
            capture_mode=CaptureMode.MANUAL,
            return_to=NoteTakerMode.SILENT,
        )
        t = self._handle(NoteTakerEvent.RECORDING_DONE, recording)
        assert t.state.mode == NoteTakerMode.SILENT
        assert t.action == "stop_and_upload"

    def test_recording_done_when_not_recording_is_ignored(self) -> None:
        t = self._handle(NoteTakerEvent.RECORDING_DONE)
        assert t.action == "none"

    # Immutability: original state is not mutated
    def test_handle_does_not_mutate_original_state(self) -> None:
        original = NoteTakerState(mode=NoteTakerMode.AMBIENT)
        self.sm.handle(original, NoteTakerEvent.TAP)
        assert original.mode == NoteTakerMode.AMBIENT


# ── config tests ──────────────────────────────────────────────────────────────

class TestNoteTakerConfig:
    def test_load_minimal_config(self) -> None:
        cfg = load_note_taker_config({
            "DEVICE_ID": "dev-1",
            "DEVICE_NOTE_API_URL": "http://localhost:8000",
        })
        assert cfg.device_id == "dev-1"
        assert cfg.note_api_url == "http://localhost:8000"
        assert cfg.wake_word_engine == "null"
        assert cfg.button_long_press_ms == 1000
        assert cfg.silence_timeout_s == 3.0

    def test_load_https_url(self) -> None:
        cfg = load_note_taker_config({
            "DEVICE_ID": "dev-1",
            "DEVICE_NOTE_API_URL": "https://api.example.com",
        })
        assert cfg.note_api_url == "https://api.example.com"

    def test_load_api_token(self) -> None:
        cfg = load_note_taker_config({
            "DEVICE_ID": "dev-1",
            "DEVICE_NOTE_API_URL": "http://localhost:8000",
            "DEVICE_NOTE_API_TOKEN": "secret-token",
        })
        assert cfg.note_api_token == "secret-token"

    def test_load_recording_limits(self) -> None:
        cfg = load_note_taker_config({
            "DEVICE_ID": "dev-1",
            "DEVICE_NOTE_API_URL": "http://localhost:8000",
            "DEVICE_SILENCE_TIMEOUT_S": "5.0",
            "DEVICE_MAX_RECORDING_S": "120.0",
        })
        assert cfg.silence_timeout_s == 5.0
        assert cfg.max_recording_s == 120.0

    def test_load_long_press_override(self) -> None:
        cfg = load_note_taker_config({
            "DEVICE_ID": "dev-1",
            "DEVICE_NOTE_API_URL": "http://localhost:8000",
            "DEVICE_BUTTON_LONG_PRESS_MS": "2000",
        })
        assert cfg.button_long_press_ms == 2000

    def test_rejects_missing_device_id(self) -> None:
        with pytest.raises(ValueError, match="DEVICE_ID"):
            load_note_taker_config({"DEVICE_NOTE_API_URL": "http://localhost:8000"})

    def test_rejects_missing_api_url(self) -> None:
        with pytest.raises(ValueError, match="DEVICE_NOTE_API_URL"):
            load_note_taker_config({"DEVICE_ID": "dev-1"})

    def test_rejects_ws_url_instead_of_http(self) -> None:
        with pytest.raises(ValueError, match="http:// or https://"):
            load_note_taker_config({
                "DEVICE_ID": "dev-1",
                "DEVICE_NOTE_API_URL": "ws://localhost:8000",
            })

    def test_rejects_invalid_silence_timeout(self) -> None:
        with pytest.raises(ValueError):
            load_note_taker_config({
                "DEVICE_ID": "dev-1",
                "DEVICE_NOTE_API_URL": "http://localhost:8000",
                "DEVICE_SILENCE_TIMEOUT_S": "abc",
            })

    def test_whisplay_profile_resolution(self) -> None:
        cfg = load_note_taker_config({
            "DEVICE_ID": "dev-1",
            "DEVICE_NOTE_API_URL": "http://localhost:8000",
            "DEVICE_DISPLAY_ADAPTER": "whisplay",
        })
        assert cfg.resolved_hardware_profile == "whisplay"
        assert cfg.button_adapter == "whisplay"
        assert cfg.rgb_adapter == "hardware"
        assert cfg.whisplay_bundle_active is True

    def test_whisplay_gpio_button_replaced_with_warning(self) -> None:
        cfg = load_note_taker_config({
            "DEVICE_ID": "dev-1",
            "DEVICE_NOTE_API_URL": "http://localhost:8000",
            "DEVICE_HARDWARE_PROFILE": "whisplay",
            "DEVICE_BUTTON_ADAPTER": "gpio",
        })
        assert cfg.button_adapter == "whisplay"
        assert any("GPIO17" in w for w in cfg.config_warnings)

    def test_whisplay_defaults_wm8960_device_for_alsa(self) -> None:
        cfg = load_note_taker_config({
            "DEVICE_ID": "dev-1",
            "DEVICE_NOTE_API_URL": "http://localhost:8000",
            "DEVICE_HARDWARE_PROFILE": "whisplay",
            "DEVICE_AUDIO_IN_ADAPTER": "alsa",
        })
        assert cfg.audio_in_alsa_device == "plughw:wm8960soundcard,0"


# ── AudioBuffer tests ─────────────────────────────────────────────────────────

class TestAudioBuffer:
    def test_empty_buffer_is_falsy(self) -> None:
        buf = AudioBuffer()
        assert not buf
        assert len(buf) == 0
        assert buf.to_pcm_bytes() == b""

    def test_collect_decodes_base64_chunks(self) -> None:
        pcm = _make_pcm(160)
        chunk = _make_chunk(pcm)

        class FakeCapture:
            def read_chunks(self, _: int) -> list[dict]:
                return [chunk]

        buf = AudioBuffer()
        collected = buf.collect(FakeCapture())
        assert collected == 1
        assert buf.to_pcm_bytes() == pcm

    def test_collect_skips_missing_payload(self) -> None:
        class FakeCapture:
            def read_chunks(self, _: int) -> list[dict]:
                return [{"size_bytes": 10}]  # no payload key

        buf = AudioBuffer()
        assert buf.collect(FakeCapture()) == 0

    def test_collect_skips_invalid_base64(self) -> None:
        class FakeCapture:
            def read_chunks(self, _: int) -> list[dict]:
                return [{"payload": "not-valid-!!base64"}]

        buf = AudioBuffer()
        assert buf.collect(FakeCapture()) == 0

    def test_collect_multiple_chunks_concatenates_pcm(self) -> None:
        pcm1 = _make_pcm(80)
        pcm2 = _make_pcm(80)

        class FakeCapture:
            def read_chunks(self, _: int) -> list[dict]:
                return [_make_chunk(pcm1), _make_chunk(pcm2)]

        buf = AudioBuffer()
        buf.collect(FakeCapture())
        assert buf.to_pcm_bytes() == pcm1 + pcm2

    def test_clear_resets_buffer(self) -> None:
        pcm = _make_pcm()
        chunk = _make_chunk(pcm)

        class FakeCapture:
            def read_chunks(self, _: int) -> list[dict]:
                return [chunk]

        buf = AudioBuffer()
        buf.collect(FakeCapture())
        assert buf
        buf.clear()
        assert not buf

    def test_to_wav_bytes_produces_valid_wav(self) -> None:
        pcm = _make_pcm(320)
        chunk = _make_chunk(pcm)

        class FakeCapture:
            def read_chunks(self, _: int) -> list[dict]:
                return [chunk]

        buf = AudioBuffer()
        buf.collect(FakeCapture())
        wav = buf.to_wav_bytes(sample_rate=16000, channels=1)
        with wave.open(io.BytesIO(wav)) as wf:
            assert wf.getframerate() == 16000
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2

    def test_duration_s_approximation(self) -> None:
        pcm = b"\x00" * 32000  # 1 s of PCM16 mono @16 kHz
        buf = AudioBuffer()
        buf._chunks.append(pcm)
        assert abs(buf.duration_s - 1.0) < 0.01

    def test_duration_s_zero_for_empty_buffer(self) -> None:
        assert AudioBuffer().duration_s == 0.0


# ── NullWakeWord tests ────────────────────────────────────────────────────────

class TestNullWakeWord:
    def test_available(self) -> None:
        assert NullWakeWord().available is True

    def test_start_does_not_crash(self) -> None:
        fired = []
        NullWakeWord().start(lambda: fired.append(True))
        assert fired == []  # never fires

    def test_stop_does_not_crash(self) -> None:
        ww = NullWakeWord()
        ww.start(lambda: None)
        ww.stop()  # must not raise


# ── NullNoteCaptureGateway tests ──────────────────────────────────────────────

class TestNullNoteCaptureGateway:
    @pytest.mark.asyncio
    async def test_upload_records_call(self) -> None:
        gw = NullNoteCaptureGateway(note_id="n-1")
        note_id = await gw.upload(b"pcm", "manual")
        assert note_id == "n-1"
        assert len(gw.uploads) == 1
        assert gw.uploads[0].capture_mode == "manual"

    @pytest.mark.asyncio
    async def test_upload_stores_audio_bytes(self) -> None:
        gw = NullNoteCaptureGateway()
        await gw.upload(b"raw", "wake_word", sample_rate=16000, channels=1)
        assert gw.uploads[0].audio_bytes == b"raw"
        assert gw.uploads[0].sample_rate == 16000

    @pytest.mark.asyncio
    async def test_multiple_uploads_appended(self) -> None:
        gw = NullNoteCaptureGateway()
        await gw.upload(b"a", "manual")
        await gw.upload(b"b", "wake_word")
        assert len(gw.uploads) == 2


# ── HttpNoteCaptureGateway helpers ────────────────────────────────────────────

class TestHttpHelpers:
    def test_pcm_to_wav_produces_valid_wav(self) -> None:
        pcm = _make_pcm(320)
        wav = _pcm_to_wav(pcm, sample_rate=16000, channels=1)
        with wave.open(io.BytesIO(wav)) as wf:
            assert wf.getframerate() == 16000
            assert wf.getsampwidth() == 2

    def test_build_multipart_contains_boundary(self) -> None:
        body = _build_multipart(
            "testboundary",
            fields={"capture_mode": "manual"},
            file_field="audio",
            filename="audio.wav",
            file_data=b"wavdata",
            content_type="audio/wav",
        )
        assert b"--testboundary" in body
        assert b"capture_mode" in body
        assert b"manual" in body
        assert b"wavdata" in body

    def test_build_multipart_ends_with_boundary(self) -> None:
        body = _build_multipart(
            "end123",
            fields={},
            file_field="audio",
            filename="audio.wav",
            file_data=b"x",
            content_type="audio/wav",
        )
        assert body.endswith(b"--end123--\r\n")


class TestHttpNoteCaptureGatewayIntegration:
    """Uses a local HTTP server to verify the full HTTP round-trip."""

    def _start_server(self, response_body: bytes, status_code: int = 202) -> tuple[HTTPServer, int]:
        class Handler(BaseHTTPRequestHandler):
            received: list[bytes] = []

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                Handler.received.append(body)
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(response_body)

            def log_message(self, *args: Any) -> None:
                pass  # suppress test output

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        return server, port

    @pytest.mark.asyncio
    async def test_upload_posts_to_correct_endpoint(self) -> None:
        server, port = self._start_server(b'{"note_id":"abc-123"}')
        try:
            gw = HttpNoteCaptureGateway(f"http://127.0.0.1:{port}")
            note_id = await gw.upload(_make_pcm(160), "manual")
            assert note_id == "abc-123"
        finally:
            server.shutdown()

    @pytest.mark.asyncio
    async def test_upload_sends_bearer_token(self) -> None:
        received_headers: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                received_headers.append(dict(self.headers))
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                self.send_response(202)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"note_id":"x"}')

            def log_message(self, *args: Any) -> None:
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            gw = HttpNoteCaptureGateway(f"http://127.0.0.1:{port}", api_token="tok-abc")
            await gw.upload(_make_pcm(160), "wake_word")
            auth = received_headers[0].get("Authorization", "")
            assert auth == "Bearer tok-abc"
        finally:
            server.shutdown()

    @pytest.mark.asyncio
    async def test_upload_raises_on_http_error(self) -> None:
        server, port = self._start_server(b"Server Error", status_code=500)
        try:
            gw = HttpNoteCaptureGateway(f"http://127.0.0.1:{port}", retry_delays_s=())
            with pytest.raises(RuntimeError, match="HTTP 500"):
                await gw.upload(_make_pcm(160), "manual")
        finally:
            server.shutdown()

    @pytest.mark.asyncio
    async def test_upload_raises_on_connection_refused(self) -> None:
        gw = HttpNoteCaptureGateway("http://127.0.0.1:1", retry_delays_s=())
        with pytest.raises(RuntimeError):
            await gw.upload(_make_pcm(160), "manual")


# ── OfflineQueue tests ────────────────────────────────────────────────────────

class TestOfflineQueue:
    """Unit tests for the offline audio queue (disk persistence + drain)."""

    @pytest.fixture
    def queue(self, tmp_path: Any) -> Any:
        from device_runtime.infrastructure.capture.offline_queue import OfflineQueue
        return OfflineQueue(str(tmp_path / "queue"))

    _WAV = b"RIFF\x00\x00\x00\x00WAVEfmt "  # minimal fake WAV header bytes

    def test_save_creates_file(self, queue: Any) -> None:
        path = queue.save(self._WAV, "manual")
        assert path.exists()
        assert path.read_bytes() == self._WAV

    def test_save_encodes_capture_mode_in_filename(self, queue: Any) -> None:
        path = queue.save(self._WAV, "wake_word")
        assert "wake_word" in path.name

    def test_pending_returns_empty_when_queue_dir_missing(self, tmp_path: Any) -> None:
        from device_runtime.infrastructure.capture.offline_queue import OfflineQueue
        q = OfflineQueue(str(tmp_path / "nonexistent"))
        assert q.pending() == []
        assert q.pending_count == 0

    def test_pending_count_reflects_saved_files(self, queue: Any) -> None:
        queue.save(self._WAV, "manual")
        queue.save(self._WAV, "wake_word")
        assert queue.pending_count == 2

    def test_pending_returns_files_in_chronological_order(self, queue: Any) -> None:
        p1 = queue.save(self._WAV, "manual")
        p2 = queue.save(self._WAV, "wake_word")
        pending = queue.pending()
        assert pending[0].name <= pending[1].name  # alphabetical = chronological by ts prefix

    @pytest.mark.asyncio
    async def test_drain_uploads_queued_file(self, queue: Any) -> None:
        queue.save(self._WAV, "manual")
        uploads: list[tuple[bytes, str]] = []

        async def upload_fn(wav: bytes, mode: str) -> str:
            uploads.append((wav, mode))
            return "note-id"

        uploaded, failed = await queue.drain(upload_fn)
        assert uploaded == 1
        assert failed == 0
        assert len(uploads) == 1
        assert uploads[0][1] == "manual"

    @pytest.mark.asyncio
    async def test_drain_deletes_file_on_success(self, queue: Any) -> None:
        path = queue.save(self._WAV, "manual")

        async def upload_fn(wav: bytes, mode: str) -> str:
            return "ok"

        await queue.drain(upload_fn)
        assert not path.exists()
        assert queue.pending_count == 0

    @pytest.mark.asyncio
    async def test_drain_keeps_file_on_failure(self, queue: Any) -> None:
        queue.save(self._WAV, "manual")

        async def upload_fn(wav: bytes, mode: str) -> str:
            raise RuntimeError("backend unreachable")

        uploaded, failed = await queue.drain(upload_fn)
        assert uploaded == 0
        assert failed == 1
        assert queue.pending_count == 1  # file still there

    @pytest.mark.asyncio
    async def test_drain_processes_multiple_files(self, queue: Any) -> None:
        for mode in ("manual", "wake_word", "manual"):
            queue.save(self._WAV, mode)

        uploaded_modes: list[str] = []

        async def upload_fn(wav: bytes, mode: str) -> str:
            uploaded_modes.append(mode)
            return "ok"

        uploaded, failed = await queue.drain(upload_fn)
        assert uploaded == 3
        assert failed == 0
        assert sorted(uploaded_modes) == ["manual", "manual", "wake_word"]

    @pytest.mark.asyncio
    async def test_drain_continues_after_partial_failure(self, queue: Any) -> None:
        queue.save(self._WAV, "manual")
        queue.save(self._WAV, "wake_word")
        calls = 0

        async def upload_fn(wav: bytes, mode: str) -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("first fails")
            return "ok"

        uploaded, failed = await queue.drain(upload_fn)
        assert uploaded == 1
        assert failed == 1

    @pytest.mark.asyncio
    async def test_drain_empty_queue_returns_zeros(self, queue: Any) -> None:
        uploaded, failed = await queue.drain(lambda w, m: (_ for _ in ()).throw(RuntimeError()))
        assert uploaded == 0
        assert failed == 0


# ── Retry tests ──────────────────────────────────────────────────────────────

class TestHttpNoteCaptureGatewayRetry:
    """Verify exponential-backoff retry behaviour using a local HTTP server."""

    def _start_flaky_server(
        self, fail_count: int, fail_status: int = 503, success_body: bytes = b'{"note_id":"ok"}'
    ) -> tuple[HTTPServer, int, type]:
        """Server that returns fail_status for the first `fail_count` requests, then 202."""

        class Handler(BaseHTTPRequestHandler):
            calls: int = 0

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                Handler.calls += 1
                if Handler.calls <= fail_count:
                    self.send_response(fail_status)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b"error")
                else:
                    self.send_response(202)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(success_body)

            def log_message(self, *args: Any) -> None:
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, server.server_address[1], Handler

    @pytest.mark.asyncio
    async def test_retry_succeeds_after_one_transient_failure(self) -> None:
        server, port, Handler = self._start_flaky_server(fail_count=1)
        try:
            gw = HttpNoteCaptureGateway(
                f"http://127.0.0.1:{port}", retry_delays_s=(0.0, 0.0, 0.0)
            )
            note_id = await gw.upload(_make_pcm(160), "manual")
            assert note_id == "ok"
            assert Handler.calls == 2  # 1 failure + 1 success
        finally:
            server.shutdown()

    @pytest.mark.asyncio
    async def test_retry_succeeds_after_two_transient_failures(self) -> None:
        server, port, Handler = self._start_flaky_server(fail_count=2)
        try:
            gw = HttpNoteCaptureGateway(
                f"http://127.0.0.1:{port}", retry_delays_s=(0.0, 0.0, 0.0)
            )
            note_id = await gw.upload(_make_pcm(160), "manual")
            assert note_id == "ok"
            assert Handler.calls == 3
        finally:
            server.shutdown()

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises_after_all_attempts(self) -> None:
        server, port, Handler = self._start_flaky_server(fail_count=99)
        try:
            gw = HttpNoteCaptureGateway(
                f"http://127.0.0.1:{port}", retry_delays_s=(0.0, 0.0, 0.0)
            )
            with pytest.raises(RuntimeError, match="HTTP 503"):
                await gw.upload(_make_pcm(160), "manual")
            assert Handler.calls == 4  # 1 initial + 3 retries
        finally:
            server.shutdown()

    @pytest.mark.asyncio
    async def test_no_retry_on_4xx_client_error(self) -> None:
        server, port, Handler = self._start_flaky_server(fail_count=99, fail_status=422)
        try:
            gw = HttpNoteCaptureGateway(
                f"http://127.0.0.1:{port}", retry_delays_s=(0.0, 0.0, 0.0)
            )
            with pytest.raises(RuntimeError, match="HTTP 422"):
                await gw.upload(_make_pcm(160), "manual")
            assert Handler.calls == 1  # no retries for client errors
        finally:
            server.shutdown()

    @pytest.mark.asyncio
    async def test_no_retry_on_401_unauthorized(self) -> None:
        server, port, Handler = self._start_flaky_server(fail_count=99, fail_status=401)
        try:
            gw = HttpNoteCaptureGateway(
                f"http://127.0.0.1:{port}", retry_delays_s=(0.0, 0.0, 0.0)
            )
            with pytest.raises(RuntimeError, match="HTTP 401"):
                await gw.upload(_make_pcm(160), "manual")
            assert Handler.calls == 1
        finally:
            server.shutdown()

    @pytest.mark.asyncio
    async def test_retry_uses_correct_delays(self) -> None:
        """asyncio.sleep is called with each delay value in order."""
        server, port, _ = self._start_flaky_server(fail_count=99)
        slept: list[float] = []

        async def fake_sleep(t: float) -> None:
            slept.append(t)

        import unittest.mock as mock

        try:
            gw = HttpNoteCaptureGateway(
                f"http://127.0.0.1:{port}", retry_delays_s=(0.1, 0.2, 0.3)
            )
            with mock.patch("asyncio.sleep", side_effect=fake_sleep):
                with pytest.raises(RuntimeError):
                    await gw.upload(_make_pcm(160), "manual")
            assert slept == [0.1, 0.2, 0.3]
        finally:
            server.shutdown()

    @pytest.mark.asyncio
    async def test_zero_retries_raises_immediately(self) -> None:
        server, port, Handler = self._start_flaky_server(fail_count=99)
        try:
            gw = HttpNoteCaptureGateway(
                f"http://127.0.0.1:{port}", retry_delays_s=()
            )
            with pytest.raises(RuntimeError):
                await gw.upload(_make_pcm(160), "manual")
            assert Handler.calls == 1
        finally:
            server.shutdown()

    @pytest.mark.asyncio
    async def test_default_retry_delays_are_2_4_8(self) -> None:
        gw = HttpNoteCaptureGateway("http://127.0.0.1:1")
        assert gw._retry_delays_s == (2.0, 4.0, 8.0)


# ── NoteTakerRunner tests ─────────────────────────────────────────────────────

class FakeCapture:
    """Controllable fake audio capture."""

    def __init__(self, chunks: list[dict[str, Any]] | None = None) -> None:
        self.available = True
        self.started = False
        self.stopped = False
        self._chunks = list(chunks or [])

    def start(self) -> None:
        self.started = True
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True
        self.started = False

    def read_chunks(self, max_chunks: int) -> list[dict[str, Any]]:
        result = self._chunks[:max_chunks]
        self._chunks = self._chunks[max_chunks:]
        return result


class FakeButton:
    """Injects button events programmatically."""

    def __init__(self) -> None:
        self._handler: Any = None

    def start(self, on_event: Any) -> None:
        self._handler = on_event

    def stop(self) -> None:
        self._handler = None

    def emit(self, event_name: str) -> None:
        if self._handler:
            self._handler(event_name)


class FakeWakeWord:
    """Injects wake word events programmatically."""

    def __init__(self) -> None:
        self.available = True
        self._handler: Any = None

    def start(self, on_wake: Any) -> None:
        self._handler = on_wake

    def stop(self) -> None:
        self._handler = None

    def fire(self) -> None:
        if self._handler:
            self._handler()


@pytest.mark.asyncio
async def test_runner_initial_mode_is_ambient() -> None:
    bootstrap = _make_bootstrap()
    runner = NoteTakerRunner(bootstrap)
    assert runner.state.mode == NoteTakerMode.AMBIENT


@pytest.mark.asyncio
async def test_runner_tap_toggles_to_silent() -> None:
    button = FakeButton()
    bootstrap = _make_bootstrap(button=button)
    runner = NoteTakerRunner(bootstrap)
    stop = asyncio.Event()
    task = asyncio.create_task(runner.run(stop_event=stop))
    await asyncio.sleep(0.05)

    button.emit("press")
    await asyncio.sleep(0.05)
    assert runner.state.mode == NoteTakerMode.SILENT

    stop.set()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_runner_two_taps_return_to_ambient() -> None:
    button = FakeButton()
    bootstrap = _make_bootstrap(button=button)
    runner = NoteTakerRunner(bootstrap)
    stop = asyncio.Event()
    task = asyncio.create_task(runner.run(stop_event=stop))
    await asyncio.sleep(0.05)

    button.emit("press")
    await asyncio.sleep(0.02)
    button.emit("press")
    await asyncio.sleep(0.05)
    assert runner.state.mode == NoteTakerMode.AMBIENT

    stop.set()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_runner_wake_word_triggers_recording_and_upload() -> None:
    wake_word = FakeWakeWord()
    pcm = _make_pcm(160)
    audio = FakeCapture(chunks=[_make_chunk(pcm)])
    gateway = NullNoteCaptureGateway()
    bootstrap = _make_bootstrap(
        config=_make_config(silence_timeout_s=0.1),
        wake_word=wake_word,
        audio_capture=audio,
        note_gateway=gateway,
    )
    runner = NoteTakerRunner(bootstrap)
    stop = asyncio.Event()
    task = asyncio.create_task(runner.run(stop_event=stop))
    await asyncio.sleep(0.05)

    wake_word.fire()
    await asyncio.sleep(0.5)  # let silence_timeout_s=0.1 expire

    stop.set()
    await asyncio.wait_for(task, timeout=2.0)

    assert len(gateway.uploads) == 1
    assert gateway.uploads[0].capture_mode == "wake_word"


@pytest.mark.asyncio
async def test_runner_long_press_release_uploads_with_manual_mode() -> None:
    button = FakeButton()
    pcm = _make_pcm(160)
    audio = FakeCapture(chunks=[_make_chunk(pcm)])
    gateway = NullNoteCaptureGateway()
    bootstrap = _make_bootstrap(
        button=button,
        audio_capture=audio,
        note_gateway=gateway,
    )
    runner = NoteTakerRunner(bootstrap)
    stop = asyncio.Event()
    task = asyncio.create_task(runner.run(stop_event=stop))
    await asyncio.sleep(0.05)

    button.emit("long_press")
    await asyncio.sleep(0.05)
    assert runner.state.mode == NoteTakerMode.RECORDING

    button.emit("release")
    await asyncio.sleep(0.1)

    stop.set()
    await asyncio.wait_for(task, timeout=1.0)

    assert len(gateway.uploads) == 1
    assert gateway.uploads[0].capture_mode == "manual"


@pytest.mark.asyncio
async def test_runner_starts_audio_capture_on_long_press() -> None:
    button = FakeButton()
    audio = FakeCapture()
    bootstrap = _make_bootstrap(button=button, audio_capture=audio)
    runner = NoteTakerRunner(bootstrap)
    stop = asyncio.Event()
    task = asyncio.create_task(runner.run(stop_event=stop))
    await asyncio.sleep(0.05)

    button.emit("long_press")
    await asyncio.sleep(0.05)
    assert audio.started is True

    stop.set()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_runner_stops_audio_capture_on_release() -> None:
    button = FakeButton()
    audio = FakeCapture()
    bootstrap = _make_bootstrap(button=button, audio_capture=audio)
    runner = NoteTakerRunner(bootstrap)
    stop = asyncio.Event()
    task = asyncio.create_task(runner.run(stop_event=stop))
    await asyncio.sleep(0.05)

    button.emit("long_press")
    await asyncio.sleep(0.05)
    button.emit("release")
    await asyncio.sleep(0.1)
    assert audio.stopped is True

    stop.set()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_runner_skips_upload_when_audio_is_empty() -> None:
    button = FakeButton()
    audio = FakeCapture(chunks=[])  # no audio
    gateway = NullNoteCaptureGateway()
    bootstrap = _make_bootstrap(button=button, audio_capture=audio, note_gateway=gateway)
    runner = NoteTakerRunner(bootstrap)
    stop = asyncio.Event()
    task = asyncio.create_task(runner.run(stop_event=stop))
    await asyncio.sleep(0.05)

    button.emit("long_press")
    await asyncio.sleep(0.05)
    button.emit("release")
    await asyncio.sleep(0.1)

    stop.set()
    await asyncio.wait_for(task, timeout=1.0)

    assert len(gateway.uploads) == 0  # nothing to upload


@pytest.mark.asyncio
async def test_runner_wake_word_ignored_in_silent_mode() -> None:
    wake_word = FakeWakeWord()
    button = FakeButton()
    gateway = NullNoteCaptureGateway()
    bootstrap = _make_bootstrap(wake_word=wake_word, button=button, note_gateway=gateway)
    runner = NoteTakerRunner(bootstrap)
    stop = asyncio.Event()
    task = asyncio.create_task(runner.run(stop_event=stop))
    await asyncio.sleep(0.05)

    button.emit("press")  # → SILENT
    await asyncio.sleep(0.05)
    wake_word.fire()      # should be ignored in SILENT
    await asyncio.sleep(0.1)

    stop.set()
    await asyncio.wait_for(task, timeout=1.0)

    assert len(gateway.uploads) == 0
    assert runner.state.mode == NoteTakerMode.SILENT


@pytest.mark.asyncio
async def test_runner_long_press_from_silent_returns_to_silent() -> None:
    button = FakeButton()
    pcm = _make_pcm(160)
    audio = FakeCapture(chunks=[_make_chunk(pcm)])
    gateway = NullNoteCaptureGateway()
    bootstrap = _make_bootstrap(button=button, audio_capture=audio, note_gateway=gateway)
    runner = NoteTakerRunner(bootstrap)
    stop = asyncio.Event()
    task = asyncio.create_task(runner.run(stop_event=stop))
    await asyncio.sleep(0.05)

    button.emit("press")    # → SILENT
    await asyncio.sleep(0.02)
    button.emit("long_press")  # → RECORDING (return_to=SILENT)
    await asyncio.sleep(0.05)
    button.emit("release")  # → SILENT + upload
    await asyncio.sleep(0.1)

    stop.set()
    await asyncio.wait_for(task, timeout=1.0)

    assert runner.state.mode == NoteTakerMode.SILENT
    assert len(gateway.uploads) == 1


@pytest.mark.asyncio
async def test_build_note_taker_runs_with_minimal_env() -> None:
    bootstrap = build_note_taker({
        "DEVICE_ID": "test-build",
        "DEVICE_NOTE_API_URL": "http://localhost:8000",
    })
    assert bootstrap.config.device_id == "test-build"
    assert isinstance(bootstrap.wake_word, NullWakeWord)
    assert isinstance(bootstrap.note_gateway, HttpNoteCaptureGateway)
