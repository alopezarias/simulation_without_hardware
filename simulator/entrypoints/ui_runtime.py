"""Runtime helpers extracted from the Tk simulator composition root."""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import queue
import threading
from typing import Any

import websockets

from simulator.domain.events import DeviceInputEvent, DeviceState
from device_runtime.infrastructure.audio.sounddevice_capture import SoundDeviceCapture
from device_runtime.infrastructure.audio.sounddevice_playback import SoundDevicePlayback
from device_runtime.infrastructure.transport.websocket_client import SessionNotReadyError, WebSocketTransport
from simulator.application.ports import BackendGateway
from simulator.shared.protocol import build_message


class WsWorker(threading.Thread):
    """Background websocket client that exchanges JSON messages with backend."""

    def __init__(
        self,
        ws_url: str,
        device_id: str,
        auth_token: str,
        initial_agent: str,
        inbox: queue.Queue[dict[str, Any]],
    ) -> None:
        super().__init__(daemon=True)
        self.ws_url = ws_url
        self.device_id = device_id
        self.auth_token = auth_token
        self.initial_agent = initial_agent
        self.inbox = inbox
        self.outbox: queue.Queue[dict[str, Any]] = queue.Queue()
        self.stop_event = threading.Event()
        self._transport: WebSocketTransport | None = None

    def send(self, message: dict[str, Any]) -> None:
        self.outbox.put(message)

    def stop(self) -> None:
        self.stop_event.set()
        if self._transport is not None:
            self._transport.close()

    async def _send_loop(self, ws: websockets.ClientConnection) -> None:
        while not self.stop_event.is_set():
            try:
                message = self.outbox.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.05)
                continue
            await ws.send(json.dumps(message))

    async def _recv_loop(self, ws: websockets.ClientConnection) -> None:
        async for raw in ws:
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            self.inbox.put(message)

    async def _run(self) -> None:
        hello = build_message(
            "device.hello",
            device_id=self.device_id,
            firmware_version="0.3.0",
            simulated=True,
            capabilities=["screen", "leds", "button", "audio_in", "audio_out"],
            active_agent=self.initial_agent,
        )
        if self.auth_token:
            hello["auth_token"] = self.auth_token
        self._transport = WebSocketTransport(
            self.ws_url,
            hello_payload=hello,
            reconnect_initial_ms=1000,
            reconnect_max_ms=6000,
        )
        transport = self._transport
        assert transport is not None
        transport.set_message_handler(self.inbox.put)
        transport.set_connection_handler(
            lambda status, detail: self.inbox.put({"type": "_connection", "status": status, "detail": detail})
        )
        forwarder = asyncio.create_task(self._forward_outbox())
        try:
            await transport.connect()
        finally:
            forwarder.cancel()
            try:
                await forwarder
            except asyncio.CancelledError:
                pass

    async def _forward_outbox(self) -> None:
        while not self.stop_event.is_set():
            try:
                message = self.outbox.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.05)
                continue
            if self._transport is None:
                continue
            try:
                await self._transport.send(message)
            except SessionNotReadyError as exc:
                self.inbox.put({"type": "_tx_blocked", "detail": str(exc), "message": message})

    def run(self) -> None:
        asyncio.run(self._run())


class MicAudioStreamer(SoundDeviceCapture):
    """Compatibility alias for the shared sounddevice capture adapter."""


class AudioOutputPlayer(SoundDevicePlayback):
    """Compatibility alias for the shared sounddevice playback adapter."""


class UiGateway(BackendGateway):
    """Backend gateway that writes transport messages through the worker queue."""

    def __init__(self, sender: Any, *, sample_rate: int, channels: int) -> None:
        self._sender = sender
        self._sample_rate = sample_rate
        self._channels = channels

    async def start_listen(self, turn_id: str) -> None:
        self._sender(
            build_message(
                "recording.start",
                turn_id=turn_id,
                codec="pcm16",
                sample_rate=self._sample_rate,
                channels=self._channels,
            )
        )

    async def stop_listen(self, turn_id: str) -> None:
        self._sender(build_message("recording.stop", turn_id=turn_id))

    async def cancel_listen(self, turn_id: str | None) -> None:
        payload: dict[str, Any] = {}
        if turn_id:
            payload["turn_id"] = turn_id
        self._sender(build_message("recording.cancel", **payload))

    async def send_audio_chunk(self, turn_id: str, chunk: dict[str, Any]) -> None:
        payload = dict(chunk)
        payload["turn_id"] = turn_id
        self._sender(build_message("audio.chunk", **payload))

    async def request_agents_version(self) -> None:
        self._sender(build_message("agents.version.request"))

    async def request_agents_list(self) -> None:
        self._sender(build_message("agents.list.request"))

    async def confirm_agent(self, agent_id: str) -> None:
        self._sender(build_message("agent.select", agent_id=agent_id))


class UiRuntimeSession:
    """Owns backend polling and local mic/playback lifecycle for the Tk UI."""

    def __init__(
        self,
        owner: Any,
        *,
        sounddevice_available: bool,
        sample_rate: int,
        channels: int,
        chunk_ms: int,
        max_chunks_per_flush: int,
    ) -> None:
        self._owner = owner
        self._sounddevice_available = sounddevice_available
        self._sample_rate = sample_rate
        self._channels = channels
        self._chunk_ms = chunk_ms
        self._max_chunks_per_flush = max_chunks_per_flush

    def send_worker_message(self, message: dict[str, Any]) -> None:
        if not self.state.connected:
            self._owner.note_var.set("Sin conexion al backend")
            self._owner._append_log(f"TX blocked {message.get('type', '-')}")
            self._owner._append_wire("TX-BLOCKED", message)
            return
        self._owner.worker.send(message)
        self._owner._append_log(f"TX {message.get('type', '-')}")
        self._owner._append_wire("TX", message)

    def dispatch(self, event: DeviceInputEvent) -> None:
        previous_state = self.state.device_state
        result = asyncio.run(self._owner.controller.handle_input(event))
        self._reconcile_runtime_after_transition(previous_state=previous_state)
        labels = getattr(self._owner, "_button_labels", {})
        label = labels.get(event, event.value) if isinstance(labels, dict) else event.value
        self._owner.note_var.set(result.note or label)
        self._owner._append_log(f"BTN {label} -> {result.note or self.state.device_state.value}")
        self._owner._render()

    def open_mic(self) -> None:
        if not self._sounddevice_available:
            self._owner.note_var.set("Mic no disponible: instala sounddevice y portaudio")
            self._owner.mic_error_var.set("sounddevice no instalado")
            return
        if self.state.device_state != DeviceState.LISTEN:
            self._owner.note_var.set("Entra en LISTEN con Press antes de abrir el micro")
            return
        if self._owner._mic_streamer.active:
            self._owner.note_var.set("Microfono ya abierto")
            return
        self._start_mic_capture(auto=False)
        if self._owner._mic_streamer.active:
            self._owner.note_var.set("Microfono abierto")
        self._owner._render()

    def close_mic(self) -> None:
        if not self._owner._mic_streamer.active:
            self._owner.note_var.set("Microfono ya esta cerrado")
            return
        self._flush_mic_chunks()
        self._stop_mic_capture()
        self._owner.note_var.set("Microfono cerrado")
        self._owner._render()

    def poll_inbox(self, *, limit: int = 80) -> None:
        for _ in range(limit):
            try:
                message = self._owner.inbox.get_nowait()
            except queue.Empty:
                break
            if message.get("type") == "_connection":
                self._handle_connection_event(message)
            elif message.get("type") == "_tx_blocked":
                self._owner.note_var.set(str(message.get("detail", "message blocked")))
                self._owner._append_log(f"TX blocked {message.get('message', {}).get('type', '-')}")
                self._owner._append_wire("TX-BLOCKED", message.get("message", {}))
            else:
                self._handle_backend_message(message)
        self._flush_mic_chunks()
        self._maybe_finish_audio_playback()
        self._owner._render()

    def shutdown(self) -> None:
        self._flush_mic_chunks()
        self._stop_mic_capture()
        self._stop_audio_playback(clear_buffer=True)
        self._owner.worker.stop()

    @property
    def state(self) -> Any:
        return self._owner.state

    def _reconcile_runtime_after_transition(self, *, previous_state: DeviceState) -> None:
        if previous_state == DeviceState.LISTEN and self.state.device_state != DeviceState.LISTEN:
            self._flush_mic_chunks()
            self._stop_mic_capture()
        if previous_state != DeviceState.LISTEN and self.state.device_state == DeviceState.LISTEN:
            self._owner._turn_audio_chunks_sent = 0
            self._owner._turn_audio_bytes_sent = 0
            self._owner._turn_audio_chunks_rx = 0
            self._owner._turn_audio_bytes_rx = 0
            self._stop_audio_playback(clear_buffer=True)
            if self._sounddevice_available:
                self._start_mic_capture(auto=True)
        if self.state.device_state != DeviceState.LISTEN and self._owner._mic_streamer.active:
            self._flush_mic_chunks()
            self._stop_mic_capture()

    def _stop_mic_capture(self) -> None:
        if not self._owner._mic_streamer.active:
            return
        self._owner._mic_streamer.stop()
        self._owner.mic_error_var.set("-")
        self._owner._append_log("AUDIO mic capture stopped")
        self._owner._append_wire(
            "SYS",
            {
                "type": "audio.capture.stopped",
                "bytes_sent": self._owner._mic_streamer.bytes_sent,
                "dropped_chunks": self._owner._mic_streamer.dropped_chunks,
            },
        )

    def _start_mic_capture(self, *, auto: bool) -> None:
        if self._owner._mic_streamer.active:
            return
        if self._sounddevice_available and not self._owner._mic_input_devices:
            self._owner.refresh_mic_devices()
        try:
            self._owner._mic_streamer.start()
            self._owner.mic_error_var.set("-")
            self._owner._append_log("AUDIO mic capture started")
            self._owner._append_wire(
                "SYS",
                {
                    "type": "audio.capture.started",
                    "sample_rate": self._sample_rate,
                    "channels": self._channels,
                    "chunk_ms": self._chunk_ms,
                    "device_index": self._owner._mic_streamer.device_index,
                    "auto": auto,
                },
            )
            if auto:
                self._owner.note_var.set("LISTEN activo: microfono abierto")
        except Exception as exc:
            self._owner.mic_error_var.set(str(exc))
            self._owner.note_var.set(f"No se pudo iniciar microfono: {exc}")
            self._owner._append_log(f"AUDIO error: {exc}")
            self._owner._append_wire("SYS", {"type": "audio.capture.error", "detail": str(exc), "auto": auto})

    def _flush_mic_chunks(self) -> None:
        if not self._owner._mic_streamer.active or self.state.device_state != DeviceState.LISTEN:
            return
        if not self.state.turn_id:
            return
        sent = asyncio.run(
            self._owner.controller.flush_audio_capture(
                self._owner._mic_streamer,
                max_chunks=self._max_chunks_per_flush,
            )
        )
        for chunk in self._owner._mic_streamer.last_read_chunks[:sent]:
            self._owner._turn_audio_chunks_sent += 1
            self._owner._turn_audio_bytes_sent += int(chunk["size_bytes"])

    def _stop_audio_playback(self, clear_buffer: bool = True) -> None:
        self._owner._audio_player.stop(clear_buffer=clear_buffer)
        self._owner._audio_end_pending = False

    def _maybe_finish_audio_playback(self) -> None:
        if not self._owner._audio_end_pending:
            return
        if not self._owner._audio_player.active:
            self._owner._audio_end_pending = False
            return
        if self._owner._audio_player.buffered_bytes <= 0:
            self._owner._audio_player.stop(clear_buffer=True)
            self._owner._audio_end_pending = False

    def _handle_connection_event(self, message: dict[str, Any]) -> None:
        snapshot = copy.deepcopy(self.state)
        status = str(message.get("status", ""))
        if status == "connected":
            snapshot.connected = True
            self._owner.note_var.set("Conectado a backend")
        elif status in {"disconnected", "stopped"}:
            snapshot.connected = False
            self._flush_mic_chunks()
            self._stop_mic_capture()
            self._stop_audio_playback(clear_buffer=True)
            detail = str(message.get("detail", "")).strip()
            if status == "disconnected":
                suffix = f" ({detail})" if detail else ""
                self._owner.note_var.set(f"Desconectado, reintentando{suffix}")
            else:
                self._owner.note_var.set("Conexion detenida")
        self._owner.controller.replace_snapshot(snapshot)
        self._owner._append_log(f"SYS {status}")
        self._owner._append_wire("SYS", message)
        self._owner._render()

    def _handle_audio_message(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type", ""))
        if message_type == "assistant.audio.start":
            if self._sounddevice_available:
                sample_rate = int(message.get("sample_rate", self._sample_rate) or self._sample_rate)
                channels = int(message.get("channels", self._channels) or self._channels)
                try:
                    self._owner._audio_player.start(sample_rate=sample_rate, channels=channels)
                    self._owner._audio_end_pending = False
                except Exception as exc:
                    self._stop_audio_playback(clear_buffer=True)
                    self._owner.note_var.set(f"No se pudo abrir salida de audio: {exc}")
            else:
                self._owner.note_var.set("Audio de salida no disponible")
            return

        if message_type == "assistant.audio.chunk":
            payload = message.get("payload")
            if not isinstance(payload, str) or not payload:
                return
            if self._sounddevice_available and not self._owner._audio_player.active:
                try:
                    self._owner._audio_player.start(sample_rate=self._sample_rate, channels=self._channels)
                except Exception:
                    pass
            try:
                pcm_bytes = base64.b64decode(payload, validate=True)
            except Exception:
                pcm_bytes = b""
            if pcm_bytes:
                self._owner._audio_player.push(pcm_bytes)
                self._owner._turn_audio_chunks_rx += 1
                self._owner._turn_audio_bytes_rx += len(pcm_bytes)
            return

        if message_type == "assistant.audio.end":
            self._owner._audio_end_pending = True

    def _handle_backend_message(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type", ""))
        previous_state = self.state.device_state
        update = asyncio.run(self._owner.controller.handle_backend_message(message))
        if self.state.device_state != DeviceState.LISTEN:
            self._flush_mic_chunks()
            self._stop_mic_capture()
        self._handle_audio_message(message)
        if update.note:
            self._owner.note_var.set(update.note)
        self._owner._append_log(f"RX {message_type}")
        self._owner._append_wire("RX", message)
        if previous_state != self.state.device_state:
            self._owner._append_log(f"STATE {previous_state.value} -> {self.state.device_state.value}")
        self._owner._render()
