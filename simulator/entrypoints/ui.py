"""Tkinter UI simulator aligned with the local device state machine."""

from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import json
import os
import queue
import threading
import time
import textwrap
import tkinter as tk
from tkinter import ttk
from typing import Any

import websockets
from dotenv import load_dotenv

from simulator.application.ports import BackendGateway, Clock
from simulator.application.services import SimulatorController
from simulator.domain.events import DeviceInputEvent, DeviceState
from simulator.domain.state import UiStateModel
from simulator.shared.protocol import UiState, build_message

try:
    import sounddevice as sd

    SOUNDDEVICE_AVAILABLE = True
except Exception:
    sd = None
    SOUNDDEVICE_AVAILABLE = False

load_dotenv()

MIC_SAMPLE_RATE = 16000
MIC_CHANNELS = 1
MIC_CHUNK_MS = 120
MAX_CHUNKS_PER_FLUSH = 6
MAX_LOG_LINES = 800
MAX_WIRE_LINES = 1800
STATUS_PANEL_MIN_WIDTH = 250
SCREEN_PANEL_MIN_WIDTH = 384
TERMINAL_PANEL_MIN_WIDTH = 360
BUTTON_PANEL_MIN_WIDTH = 240

DEVICE_BUTTONS = {
    DeviceInputEvent.PRESS: "Press",
    DeviceInputEvent.DOUBLE_PRESS: "Double Press",
    DeviceInputEvent.LONG_PRESS: "Long Press",
}

DEVICE_STATE_LABELS = {
    DeviceState.LOCKED: "LOCKED",
    DeviceState.READY: "READY",
    DeviceState.LISTEN: "LISTEN",
    DeviceState.MENU: "MENU",
    DeviceState.MODE: "MODE",
    DeviceState.AGENTS: "AGENTS",
}

REMOTE_STATE_LABELS = {
    UiState.IDLE: "idle",
    UiState.LISTENING: "listening",
    UiState.PROCESSING: "processing",
    UiState.SPEAKING: "speaking",
    UiState.ERROR: "error",
}

DEVICE_STATE_COLORS = {
    DeviceState.LOCKED: "#475569",
    DeviceState.READY: "#38bdf8",
    DeviceState.LISTEN: "#ef4444",
    DeviceState.MENU: "#f59e0b",
    DeviceState.MODE: "#a855f7",
    DeviceState.AGENTS: "#22c55e",
}


class SystemClock(Clock):
    def now(self) -> float:
        return time.monotonic()


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

    def send(self, message: dict[str, Any]) -> None:
        self.outbox.put(message)

    def stop(self) -> None:
        self.stop_event.set()

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
        reconnect_delay = 1.0
        while not self.stop_event.is_set():
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self.inbox.put({"type": "_connection", "status": "connected"})
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
                    await ws.send(json.dumps(hello))

                    sender = asyncio.create_task(self._send_loop(ws))
                    receiver = asyncio.create_task(self._recv_loop(ws))
                    done, pending = await asyncio.wait(
                        {sender, receiver},
                        return_when=asyncio.FIRST_EXCEPTION,
                    )
                    for task in pending:
                        task.cancel()
                    for task in done:
                        task.result()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self.inbox.put(
                    {
                        "type": "_connection",
                        "status": "disconnected",
                        "detail": str(exc),
                    }
                )
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.8, 6.0)
            else:
                reconnect_delay = 1.0

        self.inbox.put({"type": "_connection", "status": "stopped"})

    def run(self) -> None:
        asyncio.run(self._run())


class MicAudioStreamer:
    """Capture microphone audio and expose fixed-size PCM chunks."""

    def __init__(
        self,
        sample_rate: int = MIC_SAMPLE_RATE,
        channels: int = MIC_CHANNELS,
        chunk_ms: int = MIC_CHUNK_MS,
        max_queue_chunks: int = 80,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_ms = chunk_ms
        self.frames_per_chunk = max(1, int(sample_rate * chunk_ms / 1000))
        self.max_queue_chunks = max(4, max_queue_chunks)
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=self.max_queue_chunks)
        self._stream: Any | None = None
        self._started_monotonic: float = 0.0
        self._seq: int = 0
        self._bytes_sent: int = 0
        self.device_index: int | None = None
        self._dropped_chunks: int = 0

    @property
    def active(self) -> bool:
        return self._stream is not None

    @property
    def bytes_sent(self) -> int:
        return self._bytes_sent

    @property
    def dropped_chunks(self) -> int:
        return self._dropped_chunks

    def start(self) -> None:
        if not SOUNDDEVICE_AVAILABLE:
            raise RuntimeError("sounddevice is not available. Install dependencies from requirements.txt.")
        assert sd is not None
        if self.active:
            return

        self._queue = queue.Queue(maxsize=self.max_queue_chunks)
        self._seq = 0
        self._bytes_sent = 0
        self._dropped_chunks = 0
        self._started_monotonic = time.monotonic()

        def _callback(indata: Any, frames: int, _time: Any, status: Any) -> None:
            if status or frames <= 0:
                pass
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

    def pop_chunks(self, max_chunks: int | None = None) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        while max_chunks is None or len(chunks) < max_chunks:
            try:
                pcm_bytes = self._queue.get_nowait()
            except queue.Empty:
                break
            duration_ms = int((len(pcm_bytes) / (2 * self.channels)) * 1000 / self.sample_rate)
            if duration_ms <= 0:
                duration_ms = self.chunk_ms
            chunks.append(
                {
                    "seq": self._seq,
                    "timestamp_ms": int((time.monotonic() - self._started_monotonic) * 1000),
                    "duration_ms": duration_ms,
                    "payload": base64.b64encode(pcm_bytes).decode("ascii"),
                    "size_bytes": len(pcm_bytes),
                }
            )
            self._seq += 1
            self._bytes_sent += len(pcm_bytes)
        return chunks


class AudioOutputPlayer:
    """Play PCM16 audio chunks arriving from backend."""

    def __init__(self) -> None:
        self.sample_rate = MIC_SAMPLE_RATE
        self.channels = MIC_CHANNELS
        self._stream: Any | None = None
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._max_buffer_bytes = self.sample_rate * self.channels * 2 * 8

    @property
    def active(self) -> bool:
        return self._stream is not None

    @property
    def buffered_bytes(self) -> int:
        with self._lock:
            return len(self._buffer)

    def start(self, sample_rate: int, channels: int) -> None:
        if not SOUNDDEVICE_AVAILABLE:
            return
        assert sd is not None
        self.stop(clear_buffer=True)
        self.sample_rate = sample_rate
        self.channels = channels
        self._max_buffer_bytes = max(2048, self.sample_rate * self.channels * 2 * 8)

        def _callback(outdata: Any, _frames: int, _time: Any, status: Any) -> None:
            if status:
                pass
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


class UiGateway(BackendGateway):
    """Backend gateway that writes transport messages through the worker queue."""

    def __init__(self, sender: Any) -> None:
        self._sender = sender

    async def start_listen(self, turn_id: str) -> None:
        self._sender(
            build_message(
                "recording.start",
                turn_id=turn_id,
                codec="pcm16",
                sample_rate=MIC_SAMPLE_RATE,
                channels=MIC_CHANNELS,
            )
        )

    async def stop_listen(self, turn_id: str) -> None:
        self._sender(build_message("recording.stop", turn_id=turn_id))

    async def cancel_listen(self, turn_id: str | None) -> None:
        payload: dict[str, Any] = {}
        if turn_id:
            payload["turn_id"] = turn_id
        self._sender(build_message("recording.cancel", **payload))

    async def request_agents_version(self) -> None:
        self._sender(build_message("agents.version.request"))

    async def request_agents_list(self) -> None:
        self._sender(build_message("agents.list.request"))

    async def confirm_agent(self, agent_id: str) -> None:
        self._sender(build_message("agent.select", agent_id=agent_id))


class SimulatorUi:
    def __init__(self, root: tk.Tk, ws_url: str, device_id: str, auth_token: str) -> None:
        self.root = root
        self.root.title("Simulador de Dispositivo Conversacional")
        self.root.geometry("1460x820")
        self.root.minsize(1280, 720)

        initial_state = UiStateModel(device_id=device_id)
        self.inbox: queue.Queue[dict[str, Any]] = queue.Queue()
        self.worker = WsWorker(
            ws_url=ws_url,
            device_id=device_id,
            auth_token=auth_token,
            initial_agent=initial_state.active_agent,
            inbox=self.inbox,
        )
        self.controller = SimulatorController(
            initial_state,
            gateway=UiGateway(self._send_worker_message),
            clock=SystemClock(),
        )

        self.connection_var = tk.StringVar(value="disconnected")
        self.session_var = tk.StringVar(value="-")
        self.device_state_var = tk.StringVar(value=self.state.device_state.value)
        self.remote_state_var = tk.StringVar(value=self.state.remote_ui_state.value)
        self.focus_var = tk.StringVar(value="-")
        self.agent_var = tk.StringVar(value=self.state.active_agent)
        self.mode_var = tk.StringVar(value=self.state.navigation.active_mode)
        self.pending_agent_var = tk.StringVar(value="-")
        self.cache_var = tk.StringVar(value="cold")
        self.turn_var = tk.StringVar(value="-")
        self.latency_var = tk.StringVar(value="-")
        self.note_var = tk.StringVar(value="Ready")
        self.mic_status_var = tk.StringVar(value="OFF")
        self.mic_error_var = tk.StringVar(value="-")
        self.audio_rx_var = tk.StringVar(value="0 chunks")
        self.audio_tx_var = tk.StringVar(value="0 chunks")
        self.audio_playback_var = tk.StringVar(value="OFF")
        self.preview_mode_var = tk.StringVar(value=os.getenv("SIM_PREVIEW_MODE", "cased").strip().lower() or "cased")
        self.mic_device_var = tk.StringVar(value="")
        self.text_entry_var = tk.StringVar(value="")

        self._audio_player = AudioOutputPlayer()
        self._audio_end_pending = False
        self._mic_streamer = MicAudioStreamer()
        self._mic_input_devices: list[tuple[int, str]] = []
        self._turn_audio_chunks_sent = 0
        self._turn_audio_bytes_sent = 0
        self._turn_audio_chunks_rx = 0
        self._turn_audio_bytes_rx = 0

        self._build_layout()
        self._bind_keys()
        self.refresh_mic_devices()

        self.worker.start()
        self._poll_inbox()

    @property
    def state(self) -> UiStateModel:
        return self.controller.snapshot  # type: ignore[return-value]

    def _build_layout(self) -> None:
        root_frame = ttk.Frame(self.root, padding=12)
        root_frame.pack(fill=tk.BOTH, expand=True)
        root_frame.columnconfigure(0, weight=1)
        root_frame.rowconfigure(2, weight=1)

        top = ttk.Frame(root_frame)
        top.grid(row=0, column=0, sticky="nsew")
        top.rowconfigure(0, weight=1)
        top.columnconfigure(0, weight=0, minsize=STATUS_PANEL_MIN_WIDTH)
        top.columnconfigure(1, weight=0, minsize=SCREEN_PANEL_MIN_WIDTH)
        top.columnconfigure(2, weight=1, minsize=TERMINAL_PANEL_MIN_WIDTH)
        top.columnconfigure(3, weight=0, minsize=BUTTON_PANEL_MIN_WIDTH)

        summary = ttk.LabelFrame(top, text="Estado", padding=12)
        summary.grid(row=0, column=0, sticky="nsw")

        rows = [
            ("Conexion", self.connection_var),
            ("Sesion", self.session_var),
            ("DeviceState", self.device_state_var),
            ("Remote UiState", self.remote_state_var),
            ("Foco", self.focus_var),
            ("Agente activo", self.agent_var),
            ("Modo activo", self.mode_var),
            ("ACK agente", self.pending_agent_var),
            ("Cache agentes", self.cache_var),
            ("Turn ID", self.turn_var),
            ("Latencia", self.latency_var),
            ("Mic", self.mic_status_var),
            ("Mic error", self.mic_error_var),
            ("TX audio", self.audio_tx_var),
            ("RX audio", self.audio_rx_var),
            ("Audio OUT", self.audio_playback_var),
        ]
        for index, (label, variable) in enumerate(rows):
            ttk.Label(summary, text=f"{label}:").grid(row=index, column=0, sticky="w", padx=(0, 8), pady=2)
            ttk.Label(summary, textvariable=variable).grid(row=index, column=1, sticky="w", pady=2)

        hat_preview = ttk.LabelFrame(top, text="Pantalla / hardware", padding=12)
        hat_preview.grid(row=0, column=1, sticky="nsw", padx=(12, 0))

        self.hat_canvas = tk.Canvas(
            hat_preview,
            width=360,
            height=520,
            highlightthickness=0,
            bg="#0f172a",
        )
        self.hat_canvas.pack(fill=tk.BOTH, expand=True)

        wire_frame = ttk.LabelFrame(top, text="Terminal trafico WS", padding=12)
        wire_frame.grid(row=0, column=2, sticky="nsew", padx=(12, 0))

        wire_inner = ttk.Frame(wire_frame)
        wire_inner.pack(fill=tk.BOTH, expand=True)

        self.wire_text = tk.Text(
            wire_inner,
            height=20,
            wrap=tk.NONE,
            bg="#0b1220",
            fg="#d1fae5",
            insertbackground="#d1fae5",
            font=("Courier", 10),
        )
        self.wire_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.wire_text.configure(state=tk.DISABLED)
        self.wire_text.tag_configure("TX", foreground="#86efac")
        self.wire_text.tag_configure("RX", foreground="#93c5fd")
        self.wire_text.tag_configure("SYS", foreground="#fcd34d")
        self.wire_text.tag_configure("TX-BLOCKED", foreground="#fca5a5")

        wire_scroll = ttk.Scrollbar(wire_inner, orient=tk.VERTICAL, command=self.wire_text.yview)
        wire_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.wire_text.configure(yscrollcommand=wire_scroll.set)

        primary = ttk.LabelFrame(top, text="Controles del dispositivo", padding=12)
        primary.grid(row=0, column=3, sticky="nse", padx=(12, 0))

        ttk.Label(
            primary,
            text="Solo estos botones alteran la maquina principal.",
            wraplength=240,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(0, 10))

        self.primary_buttons: dict[DeviceInputEvent, ttk.Button] = {}
        for event in (DeviceInputEvent.PRESS, DeviceInputEvent.DOUBLE_PRESS, DeviceInputEvent.LONG_PRESS):
            button = ttk.Button(
                primary,
                text=DEVICE_BUTTONS[event],
                command=lambda current=event: self._dispatch(current),
            )
            button.pack(fill=tk.X, pady=4)
            self.primary_buttons[event] = button

        debug_panel = ttk.LabelFrame(root_frame, text="Diagnostico secundario", padding=12)
        debug_panel.grid(row=1, column=0, sticky="ew", pady=(12, 0))

        ttk.Label(
            debug_panel,
            text="Utilidades opcionales: no cambian la maquina principal salvo usando el controlador.",
        ).grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 8))

        text_entry = ttk.Entry(debug_panel, textvariable=self.text_entry_var)
        text_entry.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(0, 8))
        debug_panel.columnconfigure(0, weight=1)
        debug_panel.columnconfigure(1, weight=1)
        text_entry.bind("<Return>", lambda _event: self.on_send_text())

        ttk.Button(debug_panel, text="Enviar Texto", command=self.on_send_text).grid(row=1, column=2, padx=4)
        ttk.Button(debug_panel, text="Abrir Mic", command=self.on_open_mic).grid(row=1, column=3, padx=4)
        ttk.Button(debug_panel, text="Cerrar Mic", command=self.on_close_mic).grid(row=1, column=4, padx=4)
        ttk.Button(debug_panel, text="Refrescar Mic", command=self.refresh_mic_devices).grid(row=1, column=5, padx=4)

        ttk.Label(debug_panel, text="Dispositivo Mic").grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.mic_device_combo = ttk.Combobox(
            debug_panel,
            state="readonly",
            textvariable=self.mic_device_var,
            values=[],
            width=44,
        )
        self.mic_device_combo.grid(row=2, column=1, columnspan=3, sticky="ew", padx=(8, 8), pady=(10, 0))
        self.mic_device_combo.bind("<<ComboboxSelected>>", self.on_mic_device_change)

        ttk.Label(debug_panel, text="Vista").grid(row=2, column=4, sticky="e", pady=(10, 0))
        preview_combo = ttk.Combobox(
            debug_panel,
            state="readonly",
            textvariable=self.preview_mode_var,
            values=("cased", "bare"),
            width=10,
        )
        preview_combo.grid(row=2, column=5, sticky="w", pady=(10, 0))
        preview_combo.bind("<<ComboboxSelected>>", self.on_preview_mode_change)

        logs = ttk.LabelFrame(root_frame, text="Log", padding=12)
        logs.grid(row=2, column=0, sticky="nsew", pady=(12, 0))

        self.log_text = tk.Text(logs, height=22, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.configure(state=tk.DISABLED)

        ttk.Label(root_frame, textvariable=self.note_var).grid(row=3, column=0, sticky="ew", pady=(8, 0))

        self._render()

    def _bind_keys(self) -> None:
        self.root.bind("<space>", lambda _event: self._dispatch(DeviceInputEvent.PRESS) or "break")
        self.root.bind("<Escape>", lambda _event: self._dispatch(DeviceInputEvent.LONG_PRESS) or "break")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def refresh_mic_devices(self) -> None:
        if not SOUNDDEVICE_AVAILABLE:
            self.mic_error_var.set("sounddevice no instalado")
            self.mic_device_combo.configure(values=[])
            self.mic_device_var.set("")
            return
        assert sd is not None

        try:
            devices = sd.query_devices()
        except Exception as exc:
            self.mic_error_var.set(f"query_devices error: {exc}")
            self.mic_device_combo.configure(values=[])
            self.mic_device_var.set("")
            return

        entries: list[tuple[int, str]] = []
        labels: list[str] = []
        for index, dev in enumerate(devices):
            if int(dev.get("max_input_channels", 0)) <= 0:
                continue
            label = f"{index}: {str(dev.get('name', f'device-{index}')).strip()}"
            entries.append((index, label))
            labels.append(label)

        self._mic_input_devices = entries
        self.mic_device_combo.configure(values=labels)
        if not entries:
            self.mic_device_var.set("")
            self.mic_error_var.set("No hay entradas de micro disponibles")
            return

        current = self._mic_streamer.device_index
        selected = next((label for index, label in entries if index == current), entries[0][1])
        self.mic_device_var.set(selected)
        self.on_mic_device_change(None)
        self.mic_error_var.set("-")

    def on_mic_device_change(self, _event: tk.Event[Any] | None) -> None:
        label = self.mic_device_var.get().strip()
        if not label:
            self._mic_streamer.device_index = None
            return
        for index, entry_label in self._mic_input_devices:
            if entry_label == label:
                self._mic_streamer.device_index = index
                self._append_wire(
                    "SYS",
                    {"type": "audio.device.selected", "device_index": index, "label": label},
                )
                return

    def on_preview_mode_change(self, _event: tk.Event[Any] | None) -> None:
        mode = self.preview_mode_var.get().strip().lower()
        if mode not in {"cased", "bare"}:
            self.preview_mode_var.set("cased")
        self._draw_hardware_preview()

    def _wire_safe_payload(self, payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload
        safe = dict(payload)
        if safe.get("type") in {"audio.chunk", "assistant.audio.chunk"} and isinstance(safe.get("payload"), str):
            safe["payload"] = f"<base64:{len(safe['payload'])} chars>"
        text = safe.get("text")
        if isinstance(text, str) and len(text) > 220:
            safe["text"] = text[:220] + "...<trimmed>"
        return safe

    def _append_wire(self, direction: str, payload: Any) -> None:
        if not hasattr(self, "wire_text"):
            return
        timestamp = time.strftime("%H:%M:%S")
        payload = self._wire_safe_payload(payload)
        if isinstance(payload, (dict, list)):
            text = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        else:
            text = str(payload)
        line = f"[{timestamp}] {direction} {text}\n"
        self.wire_text.configure(state=tk.NORMAL)
        tag = direction if direction in {"TX", "RX", "SYS", "TX-BLOCKED"} else ""
        self.wire_text.insert(tk.END, line, tag)
        line_count = int(float(self.wire_text.index("end-1c").split(".")[0]))
        if line_count > MAX_WIRE_LINES:
            overflow = line_count - MAX_WIRE_LINES
            self.wire_text.delete("1.0", f"{overflow + 1}.0")
        self.wire_text.see(tk.END)
        self.wire_text.configure(state=tk.DISABLED)

    def _wrap_preview_text(self, text: str, width: int, lines: int) -> str:
        compact = " ".join(text.split()).strip()
        if not compact:
            return "-"
        wrapped = textwrap.wrap(compact, width=width, break_long_words=True, break_on_hyphens=False)
        if len(wrapped) > lines:
            wrapped = wrapped[:lines]
            wrapped[-1] = wrapped[-1][: max(0, width - 3)] + "..."
        return "\n".join(wrapped)

    def _draw_hardware_preview(self) -> None:
        if not hasattr(self, "hat_canvas"):
            return
        canvas = self.hat_canvas
        canvas.delete("all")
        mode = self.preview_mode_var.get().strip().lower()
        shell_fill = "#f8fafc" if mode == "cased" else "#111827"
        shell_outline = "#cbd5e1" if mode == "cased" else "#334155"
        screen_fill = "#020617"
        canvas.create_rectangle(0, 0, 360, 520, fill="#0f172a", outline="")
        canvas.create_rectangle(58, 24, 302, 496, fill=shell_fill, outline=shell_outline, width=2)
        if mode == "cased":
            canvas.create_rectangle(122, 2, 238, 34, fill="#d97706", outline="#b45309", width=2)
        led_color = DEVICE_STATE_COLORS.get(self.state.device_state, "#ef4444")
        canvas.create_oval(168, 42, 192, 66, fill=led_color, outline="#1d4ed8")
        canvas.create_rectangle(78, 88, 282, 392, fill=screen_fill, outline="#1e293b", width=2)
        canvas.create_text(92, 104, anchor="nw", text=self.state.device_state.value, fill="#e2e8f0", font=("Helvetica", 15, "bold"))
        canvas.create_text(268, 104, anchor="ne", text=self.state.remote_ui_state.value, fill="#93c5fd", font=("Helvetica", 10, "bold"))
        canvas.create_text(92, 132, anchor="nw", text=f"agent: {self.state.active_agent}", fill="#94a3b8", font=("Helvetica", 9))
        canvas.create_text(92, 148, anchor="nw", text=f"focus: {self._focus_label()}", fill="#94a3b8", font=("Helvetica", 9))
        mic_live = self._mic_streamer.active and self.state.device_state == DeviceState.LISTEN
        canvas.create_text(92, 174, anchor="nw", text="mic", fill="#f8fafc", font=("Helvetica", 10, "bold"))
        canvas.create_oval(128, 175, 140, 187, fill="#ef4444" if mic_live else "#475569", outline="")
        canvas.create_text(146, 174, anchor="nw", text="REC" if mic_live else "OFF", fill="#fca5a5" if mic_live else "#94a3b8", font=("Helvetica", 9, "bold"))
        canvas.create_text(92, 212, anchor="nw", text="YOU", fill="#e2e8f0", font=("Helvetica", 9, "bold"))
        canvas.create_text(92, 230, anchor="nw", text=self._wrap_preview_text(self.state.transcript or "Tap/Press to speak", 22, 5), fill="#e2e8f0", font=("Helvetica", 10), width=166)
        canvas.create_text(92, 306, anchor="nw", text="AGENT", fill="#86efac", font=("Helvetica", 9, "bold"))
        canvas.create_text(92, 324, anchor="nw", text=self._wrap_preview_text(self.state.assistant_text or "Waiting for backend response", 22, 5), fill="#86efac", font=("Helvetica", 10), width=166)
        canvas.create_text(180, 430, anchor="center", text=f"TX {self._turn_audio_chunks_sent} / RX {self._turn_audio_chunks_rx}", fill="#cbd5e1", font=("Helvetica", 10, "bold"))
        canvas.create_text(180, 452, anchor="center", text=self.note_var.get(), fill="#f8fafc", font=("Helvetica", 9), width=220)

    def _append_log(self, message: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {message}\n"
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line)
        line_count = int(float(self.log_text.index("end-1c").split(".")[0]))
        if line_count > MAX_LOG_LINES:
            overflow = line_count - MAX_LOG_LINES
            self.log_text.delete("1.0", f"{overflow + 1}.0")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _send_worker_message(self, message: dict[str, Any]) -> None:
        if not self.state.connected:
            self.note_var.set("Sin conexion al backend")
            self._append_log(f"TX blocked {message.get('type', '-')}")
            self._append_wire("TX-BLOCKED", message)
            return
        self.worker.send(message)
        self._append_log(f"TX {message.get('type', '-')}")
        self._append_wire("TX", message)

    def _dispatch(self, event: DeviceInputEvent) -> None:
        previous_state = self.state.device_state
        result = asyncio.run(self.controller.handle_input(event))
        self._reconcile_runtime_after_transition(previous_state=previous_state)
        label = DEVICE_BUTTONS[event]
        self.note_var.set(result.note or label)
        self._append_log(f"BTN {label} -> {result.note or self.state.device_state.value}")
        self._render()

    def _reconcile_runtime_after_transition(self, *, previous_state: DeviceState) -> None:
        if previous_state == DeviceState.LISTEN and self.state.device_state != DeviceState.LISTEN:
            self._flush_mic_chunks()
            self._stop_mic_capture()
        if previous_state != DeviceState.LISTEN and self.state.device_state == DeviceState.LISTEN:
            self._turn_audio_chunks_sent = 0
            self._turn_audio_bytes_sent = 0
            self._turn_audio_chunks_rx = 0
            self._turn_audio_bytes_rx = 0
            self._stop_audio_playback(clear_buffer=True)
            if SOUNDDEVICE_AVAILABLE:
                self._start_mic_capture(auto=True)
        if self.state.device_state != DeviceState.LISTEN and self._mic_streamer.active:
            self._flush_mic_chunks()
            self._stop_mic_capture()

    def _focus_label(self) -> str:
        if self.state.device_state == DeviceState.MENU:
            return self.state.navigation.menu_options[self.state.navigation.menu_index]
        if self.state.device_state == DeviceState.MODE:
            return self.state.navigation.available_modes[self.state.navigation.mode_index]
        if self.state.device_state == DeviceState.AGENTS:
            return self.state.focused_agent or "-"
        return "-"

    def _cache_status(self) -> str:
        cache = self.state.agent_cache
        if cache.loaded_at is None:
            return "cold"
        if cache.expires_at is not None and cache.expires_at < time.monotonic():
            return f"stale / version={self.state.agents_version or '-'}"
        return f"warm / version={self.state.agents_version or '-'}"

    def _render(self) -> None:
        remote_label = REMOTE_STATE_LABELS.get(self.state.remote_ui_state)
        if remote_label is None:
            remote_label = self.state.remote_ui_state.value
        self.connection_var.set("connected" if self.state.connected else "disconnected")
        self.session_var.set(self.state.session_id or "-")
        self.device_state_var.set(DEVICE_STATE_LABELS[self.state.device_state])
        self.remote_state_var.set(remote_label)
        self.focus_var.set(self._focus_label())
        self.agent_var.set(self.state.active_agent)
        self.mode_var.set(self.state.navigation.active_mode)
        self.pending_agent_var.set(self.state.pending_agent_ack or "-")
        self.cache_var.set(self._cache_status())
        self.turn_var.set(self.state.turn_id or "-")
        self.latency_var.set(
            f"{self.state.last_latency_ms} ms" if self.state.last_latency_ms is not None else "-"
        )
        self.mic_status_var.set("REC" if self._mic_streamer.active else "OFF")
        self.audio_tx_var.set(f"{self._turn_audio_chunks_sent} chunks / {self._turn_audio_bytes_sent // 1024} KB")
        self.audio_rx_var.set(f"{self._turn_audio_chunks_rx} chunks / {self._turn_audio_bytes_rx // 1024} KB")
        self.audio_playback_var.set("ON" if self._audio_player.active else "OFF")
        self._draw_hardware_preview()

    def on_send_text(self) -> None:
        text = self.text_entry_var.get().strip()
        if not text:
            return
        if self.state.device_state == DeviceState.LOCKED:
            self.note_var.set("Desbloquea el dispositivo antes de enviar texto")
            return
        if self.state.device_state != DeviceState.LISTEN:
            self._dispatch(DeviceInputEvent.PRESS)
            if self.state.device_state != DeviceState.LISTEN:
                self.note_var.set("No se pudo entrar en LISTEN")
                return
        self._send_worker_message(
            build_message("debug.user_text", turn_id=self.state.turn_id, text=text)
        )
        self.text_entry_var.set("")
        self.note_var.set("Texto de diagnostico enviado")
        self._render()

    def on_open_mic(self) -> None:
        if not SOUNDDEVICE_AVAILABLE:
            self.note_var.set("Mic no disponible: instala sounddevice y portaudio")
            self.mic_error_var.set("sounddevice no instalado")
            return
        if self.state.device_state != DeviceState.LISTEN:
            self.note_var.set("Entra en LISTEN con Press antes de abrir el micro")
            return
        if self._mic_streamer.active:
            self.note_var.set("Microfono ya abierto")
            return
        self._start_mic_capture(auto=False)
        if self._mic_streamer.active:
            self.note_var.set("Microfono abierto")
        self._render()

    def on_close_mic(self) -> None:
        if not self._mic_streamer.active:
            self.note_var.set("Microfono ya esta cerrado")
            return
        self._flush_mic_chunks()
        self._stop_mic_capture()
        self.note_var.set("Microfono cerrado")
        self._render()

    def _stop_mic_capture(self) -> None:
        if not self._mic_streamer.active:
            return
        self._mic_streamer.stop()
        self.mic_error_var.set("-")
        self._append_log("AUDIO mic capture stopped")
        self._append_wire(
            "SYS",
            {
                "type": "audio.capture.stopped",
                "bytes_sent": self._mic_streamer.bytes_sent,
                "dropped_chunks": self._mic_streamer.dropped_chunks,
            },
        )

    def _start_mic_capture(self, *, auto: bool) -> None:
        if self._mic_streamer.active:
            return
        if SOUNDDEVICE_AVAILABLE and not self._mic_input_devices:
            self.refresh_mic_devices()
        try:
            self._mic_streamer.start()
            self.mic_error_var.set("-")
            self._append_log("AUDIO mic capture started")
            self._append_wire(
                "SYS",
                {
                    "type": "audio.capture.started",
                    "sample_rate": MIC_SAMPLE_RATE,
                    "channels": MIC_CHANNELS,
                    "chunk_ms": MIC_CHUNK_MS,
                    "device_index": self._mic_streamer.device_index,
                    "auto": auto,
                },
            )
            if auto:
                self.note_var.set("LISTEN activo: microfono abierto")
        except Exception as exc:
            self.mic_error_var.set(str(exc))
            self.note_var.set(f"No se pudo iniciar microfono: {exc}")
            self._append_log(f"AUDIO error: {exc}")
            self._append_wire("SYS", {"type": "audio.capture.error", "detail": str(exc), "auto": auto})

    def _flush_mic_chunks(self) -> None:
        if not self._mic_streamer.active or self.state.device_state != DeviceState.LISTEN:
            return
        if not self.state.turn_id:
            return
        for chunk in self._mic_streamer.pop_chunks(max_chunks=MAX_CHUNKS_PER_FLUSH):
            self._turn_audio_chunks_sent += 1
            self._turn_audio_bytes_sent += int(chunk["size_bytes"])
            self._send_worker_message(
                build_message(
                    "audio.chunk",
                    turn_id=self.state.turn_id,
                    seq=chunk["seq"],
                    timestamp_ms=chunk["timestamp_ms"],
                    duration_ms=chunk["duration_ms"],
                    payload=chunk["payload"],
                    codec="pcm16",
                    sample_rate=MIC_SAMPLE_RATE,
                    channels=MIC_CHANNELS,
                    size_bytes=chunk["size_bytes"],
                )
            )

    def _stop_audio_playback(self, clear_buffer: bool = True) -> None:
        self._audio_player.stop(clear_buffer=clear_buffer)
        self._audio_end_pending = False

    def _maybe_finish_audio_playback(self) -> None:
        if not self._audio_end_pending:
            return
        if not self._audio_player.active:
            self._audio_end_pending = False
            return
        if self._audio_player.buffered_bytes <= 0:
            self._audio_player.stop(clear_buffer=True)
            self._audio_end_pending = False

    def _handle_connection_event(self, message: dict[str, Any]) -> None:
        snapshot = copy.deepcopy(self.state)
        status = str(message.get("status", ""))
        if status == "connected":
            snapshot.connected = True
            self.note_var.set("Conectado a backend")
        elif status in {"disconnected", "stopped"}:
            snapshot.connected = False
            self._flush_mic_chunks()
            self._stop_mic_capture()
            self._stop_audio_playback(clear_buffer=True)
            detail = str(message.get("detail", "")).strip()
            if status == "disconnected":
                suffix = f" ({detail})" if detail else ""
                self.note_var.set(f"Desconectado, reintentando{suffix}")
            else:
                self.note_var.set("Conexion detenida")
        self.controller.replace_snapshot(snapshot)
        self._append_log(f"SYS {status}")
        self._append_wire("SYS", message)
        self._render()

    def _handle_audio_message(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type", ""))
        if message_type == "assistant.audio.start":
            if SOUNDDEVICE_AVAILABLE:
                sample_rate = int(message.get("sample_rate", MIC_SAMPLE_RATE) or MIC_SAMPLE_RATE)
                channels = int(message.get("channels", MIC_CHANNELS) or MIC_CHANNELS)
                try:
                    self._audio_player.start(sample_rate=sample_rate, channels=channels)
                    self._audio_end_pending = False
                except Exception as exc:
                    self._stop_audio_playback(clear_buffer=True)
                    self.note_var.set(f"No se pudo abrir salida de audio: {exc}")
            else:
                self.note_var.set("Audio de salida no disponible")
            return

        if message_type == "assistant.audio.chunk":
            payload = message.get("payload")
            if not isinstance(payload, str) or not payload:
                return
            if SOUNDDEVICE_AVAILABLE and not self._audio_player.active:
                try:
                    self._audio_player.start(sample_rate=MIC_SAMPLE_RATE, channels=MIC_CHANNELS)
                except Exception:
                    pass
            try:
                pcm_bytes = base64.b64decode(payload, validate=True)
            except Exception:
                pcm_bytes = b""
            if pcm_bytes:
                self._audio_player.push(pcm_bytes)
                self._turn_audio_chunks_rx += 1
                self._turn_audio_bytes_rx += len(pcm_bytes)
            return

        if message_type == "assistant.audio.end":
            self._audio_end_pending = True

    def _handle_backend_message(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type", ""))
        previous_state = self.state.device_state
        update = asyncio.run(self.controller.handle_backend_message(message))
        if self.state.device_state != DeviceState.LISTEN:
            self._flush_mic_chunks()
            self._stop_mic_capture()
        self._handle_audio_message(message)
        if update.note:
            self.note_var.set(update.note)
        self._append_log(f"RX {message_type}")
        self._append_wire("RX", message)
        if previous_state != self.state.device_state:
            self._append_log(f"STATE {previous_state.value} -> {self.state.device_state.value}")
        self._render()

    def _poll_inbox(self) -> None:
        for _ in range(80):
            try:
                message = self.inbox.get_nowait()
            except queue.Empty:
                break
            if message.get("type") == "_connection":
                self._handle_connection_event(message)
            else:
                self._handle_backend_message(message)
        self._flush_mic_chunks()
        self._maybe_finish_audio_playback()
        self._render()
        self.root.after(120, self._poll_inbox)

    def on_close(self) -> None:
        self._flush_mic_chunks()
        self._stop_mic_capture()
        self._stop_audio_playback(clear_buffer=True)
        self.worker.stop()
        self.root.after(150, self.root.destroy)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="UI simulator for conversational device")
    parser.add_argument(
        "--ws-url",
        default=os.getenv("SIM_WS_URL", "ws://127.0.0.1:8000/ws"),
        help="Backend websocket URL",
    )
    parser.add_argument(
        "--device-id",
        default=os.getenv("SIM_DEVICE_ID", "sim-device-ui-001"),
        help="Device id",
    )
    parser.add_argument(
        "--auth-token",
        default=os.getenv("SIM_DEVICE_AUTH_TOKEN", ""),
        help="Optional auth token",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = tk.Tk()
    SimulatorUi(root, ws_url=args.ws_url, device_id=args.device_id, auth_token=args.auth_token)
    root.mainloop()


if __name__ == "__main__":
    main()
