"""Tkinter UI simulator for the conversational device."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import queue
import threading
import time
import textwrap
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

import websockets
from dotenv import load_dotenv

from simulator.domain.state import UiStateModel
from simulator.shared.protocol import UiState, build_message, new_turn_id

try:
    import sounddevice as sd

    SOUNDDEVICE_AVAILABLE = True
except Exception:
    sd = None
    SOUNDDEVICE_AVAILABLE = False

load_dotenv()

LED_COLORS = {
    UiState.IDLE: "#1f77b4",      # blue
    UiState.LISTENING: "#2ca02c", # green
    UiState.PROCESSING: "#f1c40f",# yellow
    UiState.SPEAKING: "#ecf0f1",  # white
    UiState.ERROR: "#e74c3c",     # red
}

LCD_WIDTH = 240
LCD_HEIGHT = 280
LCD_CORNER_HEIGHT = 20
MIC_SAMPLE_RATE = 16000
MIC_CHANNELS = 1
MIC_CHUNK_MS = 120
MAX_CHUNKS_PER_FLUSH = 6
MAX_LOG_LINES = 800
MAX_WIRE_LINES = 1800

STATE_LABELS = {
    UiState.IDLE: "LISTO",
    UiState.LISTENING: "ESCUCHANDO",
    UiState.PROCESSING: "PROCESANDO",
    UiState.SPEAKING: "HABLANDO",
    UiState.ERROR: "ERROR",
}

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
            raise RuntimeError(
                "sounddevice is not available. Install dependencies from requirements.txt."
            )
        if self.active:
            return

        self._queue = queue.Queue(maxsize=self.max_queue_chunks)
        self._seq = 0
        self._bytes_sent = 0
        self._dropped_chunks = 0
        self._started_monotonic = time.monotonic()

        def _callback(indata: Any, frames: int, _time: Any, status: Any) -> None:
            if status:
                # Keep capture alive even if warnings appear.
                pass
            if frames <= 0:
                return
            pcm_bytes = bytes(indata.tobytes())
            if not pcm_bytes:
                return

            try:
                self._queue.put_nowait(pcm_bytes)
            except queue.Full:
                # Keep bounded memory: drop oldest chunk when UI loop cannot keep up.
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

            duration_ms = int(
                (len(pcm_bytes) / (2 * self.channels)) * 1000 / self.sample_rate
            )
            if duration_ms <= 0:
                duration_ms = self.chunk_ms

            chunk = {
                "seq": self._seq,
                "timestamp_ms": int((time.monotonic() - self._started_monotonic) * 1000),
                "duration_ms": duration_ms,
                "payload": base64.b64encode(pcm_bytes).decode("ascii"),
                "size_bytes": len(pcm_bytes),
            }
            chunks.append(chunk)

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

        self.stop(clear_buffer=True)
        self.sample_rate = sample_rate
        self.channels = channels
        self._max_buffer_bytes = max(2048, self.sample_rate * self.channels * 2 * 8)

        def _callback(outdata: Any, frames: int, _time: Any, status: Any) -> None:
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


class SimulatorUi:
    def __init__(self, root: tk.Tk, ws_url: str, device_id: str, auth_token: str) -> None:
        self.root = root
        self.root.title("Simulador de Dispositivo Conversacional")
        self.root.geometry("1280x860")

        self.state = UiStateModel(device_id=device_id)
        self.inbox: queue.Queue[dict[str, Any]] = queue.Queue()
        self.worker = WsWorker(
            ws_url=ws_url,
            device_id=device_id,
            auth_token=auth_token,
            initial_agent=self.state.active_agent,
            inbox=self.inbox,
        )

        self.connection_var = tk.StringVar(value="disconnected")
        self.session_var = tk.StringVar(value="-")
        self.agent_var = tk.StringVar(value=self.state.active_agent)
        self.state_var = tk.StringVar(value=self.state.ui_state.value)
        self.turn_var = tk.StringVar(value="-")
        self.latency_var = tk.StringVar(value="-")
        self.mic_status_var = tk.StringVar(value="OFF")
        self.audio_chunks_var = tk.StringVar(value="0")
        self.audio_kb_var = tk.StringVar(value="0.0 KB")
        self.audio_rx_chunks_var = tk.StringVar(value="0")
        self.audio_rx_kb_var = tk.StringVar(value="0.0 KB")
        self.audio_playback_var = tk.StringVar(value="OFF")
        self.note_var = tk.StringVar(value="Ready")
        self.mic_error_var = tk.StringVar(value="-")
        self.battery_var = tk.DoubleVar(value=self.state.battery_level)
        self.battery_label_var = tk.StringVar(value=f"{int(self.state.battery_level)}%")
        self.preview_mode_var = tk.StringVar(
            value=os.getenv("SIM_PREVIEW_MODE", "cased").strip().lower() or "cased"
        )

        self._last_space_time = 0.0
        self._last_battery_tick = time.monotonic()
        self._simulation_running = False
        self._simulation_after_ids: list[str] = []
        self._mic_streamer = MicAudioStreamer()
        self._audio_player = AudioOutputPlayer()
        self._turn_audio_chunks_sent = 0
        self._turn_audio_bytes_sent = 0
        self._turn_audio_chunks_rx = 0
        self._turn_audio_bytes_rx = 0
        self._audio_end_pending = False
        self._mic_input_devices: list[tuple[int, str]] = []
        self.mic_device_var = tk.StringVar(value="")

        self._build_layout()
        self._bind_keys()
        self.refresh_mic_devices()

        self.worker.start()
        self._poll_inbox()

    def _build_layout(self) -> None:
        root_frame = ttk.Frame(self.root, padding=12)
        root_frame.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(root_frame)
        top.pack(fill=tk.BOTH, expand=False)

        summary = ttk.LabelFrame(top, text="Estado del dispositivo", padding=10)
        summary.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        ttk.Label(summary, text="Conexion:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Label(summary, textvariable=self.connection_var).grid(row=0, column=1, sticky="w")

        ttk.Label(summary, text="Sesion:").grid(row=1, column=0, sticky="w", padx=(0, 8))
        ttk.Label(summary, textvariable=self.session_var).grid(row=1, column=1, sticky="w")

        ttk.Label(summary, text="Agente:").grid(row=2, column=0, sticky="w", padx=(0, 8))
        ttk.Label(summary, textvariable=self.agent_var).grid(row=2, column=1, sticky="w")

        ttk.Label(summary, text="Estado:").grid(row=3, column=0, sticky="w", padx=(0, 8))
        ttk.Label(summary, textvariable=self.state_var).grid(row=3, column=1, sticky="w")

        ttk.Label(summary, text="Turno:").grid(row=4, column=0, sticky="w", padx=(0, 8))
        ttk.Label(summary, textvariable=self.turn_var).grid(row=4, column=1, sticky="w")

        ttk.Label(summary, text="Latencia:").grid(row=5, column=0, sticky="w", padx=(0, 8))
        ttk.Label(summary, textvariable=self.latency_var).grid(row=5, column=1, sticky="w")

        ttk.Label(summary, text="Mic:").grid(row=6, column=0, sticky="w", padx=(0, 8))
        mic_row = ttk.Frame(summary)
        mic_row.grid(row=6, column=1, sticky="w")
        ttk.Label(mic_row, textvariable=self.mic_status_var).pack(side=tk.LEFT)
        self.mic_canvas = tk.Canvas(mic_row, width=14, height=14, highlightthickness=0)
        self.mic_canvas.pack(side=tk.LEFT, padx=(8, 0))
        self.mic_dot = self.mic_canvas.create_oval(2, 2, 12, 12, fill="#4b5563", outline="")

        ttk.Label(summary, text="Chunks TX:").grid(row=7, column=0, sticky="w", padx=(0, 8))
        ttk.Label(summary, textvariable=self.audio_chunks_var).grid(row=7, column=1, sticky="w")

        ttk.Label(summary, text="Audio TX:").grid(row=8, column=0, sticky="w", padx=(0, 8))
        ttk.Label(summary, textvariable=self.audio_kb_var).grid(row=8, column=1, sticky="w")

        ttk.Label(summary, text="Chunks RX:").grid(row=9, column=0, sticky="w", padx=(0, 8))
        ttk.Label(summary, textvariable=self.audio_rx_chunks_var).grid(row=9, column=1, sticky="w")

        ttk.Label(summary, text="Audio RX:").grid(row=10, column=0, sticky="w", padx=(0, 8))
        ttk.Label(summary, textvariable=self.audio_rx_kb_var).grid(row=10, column=1, sticky="w")

        ttk.Label(summary, text="Audio OUT:").grid(row=11, column=0, sticky="w", padx=(0, 8))
        ttk.Label(summary, textvariable=self.audio_playback_var).grid(row=11, column=1, sticky="w")

        ttk.Label(summary, text="Mic Error:").grid(row=12, column=0, sticky="w", padx=(0, 8))
        ttk.Label(summary, textvariable=self.mic_error_var, foreground="#ef4444").grid(
            row=12, column=1, sticky="w"
        )

        hat_preview = ttk.LabelFrame(top, text="Mini pantalla estilo HAT", padding=10)
        hat_preview.pack(side=tk.LEFT, padx=(12, 0))

        self.hat_canvas_width = 460
        self.hat_canvas_height = 560
        self.hat_canvas = tk.Canvas(
            hat_preview,
            width=self.hat_canvas_width,
            height=self.hat_canvas_height,
            highlightthickness=0,
            bg="#111827",
        )
        self.hat_canvas.pack()

        wire_frame = ttk.LabelFrame(top, text="Terminal trafico WS", padding=10)
        wire_frame.pack(side=tk.LEFT, padx=(12, 0), fill=tk.BOTH, expand=True)

        wire_inner = ttk.Frame(wire_frame)
        wire_inner.pack(fill=tk.BOTH, expand=True)

        self.wire_text = tk.Text(
            wire_inner,
            height=28,
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

        controls = ttk.LabelFrame(root_frame, text="Controles", padding=10)
        controls.pack(fill=tk.X, pady=(12, 8))

        ttk.Button(controls, text="Tap", command=self.on_tap).grid(row=0, column=0, padx=4, pady=4)
        ttk.Button(controls, text="Double Tap", command=self.on_double_tap).grid(row=0, column=1, padx=4, pady=4)
        ttk.Button(controls, text="Long Press", command=self.on_long_press).grid(row=0, column=2, padx=4, pady=4)

        self.text_entry = ttk.Entry(controls)
        self.text_entry.grid(row=0, column=3, padx=8, pady=4, sticky="ew")
        controls.columnconfigure(3, weight=1)

        ttk.Button(controls, text="Enviar Texto", command=self.on_send_text).grid(row=0, column=4, padx=4, pady=4)
        ttk.Button(controls, text="Abrir Mic", command=self.on_open_mic).grid(
            row=0, column=5, padx=4, pady=4
        )
        ttk.Button(controls, text="Cerrar Mic", command=self.on_close_mic).grid(
            row=0, column=6, padx=4, pady=4
        )
        ttk.Button(controls, text="Refrescar Mic", command=self.refresh_mic_devices).grid(
            row=0, column=7, padx=4, pady=4
        )

        ttk.Label(controls, text="Dispositivo Mic").grid(row=1, column=5, padx=(8, 4), pady=(8, 0), sticky="e")
        self.mic_device_combo = ttk.Combobox(
            controls,
            state="readonly",
            textvariable=self.mic_device_var,
            values=[],
            width=28,
        )
        self.mic_device_combo.grid(row=1, column=6, columnspan=2, padx=(0, 4), pady=(8, 0), sticky="w")
        self.mic_device_combo.bind("<<ComboboxSelected>>", self.on_mic_device_change)

        ttk.Label(controls, text="Vista").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=(8, 0))
        preview_combo = ttk.Combobox(
            controls,
            state="readonly",
            textvariable=self.preview_mode_var,
            values=("cased", "bare"),
            width=10,
        )
        preview_combo.grid(row=1, column=1, sticky="w", pady=(8, 0))
        preview_combo.bind("<<ComboboxSelected>>", self.on_preview_mode_change)

        ttk.Label(controls, text="Bateria").grid(row=1, column=2, sticky="w", padx=(16, 6), pady=(8, 0))
        battery_scale = ttk.Scale(
            controls,
            from_=0,
            to=100,
            variable=self.battery_var,
            command=self.on_battery_change,
        )
        battery_scale.grid(row=1, column=3, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(controls, textvariable=self.battery_label_var).grid(row=1, column=4, sticky="e", pady=(8, 0))

        ttk.Label(controls, text="Teclado: Space=tap, doble Space=double, Esc=long").grid(
            row=2, column=0, columnspan=8, sticky="w", pady=(8, 0)
        )

        ttk.Label(controls, text="Simulaciones").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Button(controls, text="Turno", command=self.run_simulation_turn).grid(
            row=3, column=1, sticky="w", pady=(8, 0)
        )
        ttk.Button(controls, text="Interrupcion", command=self.run_simulation_interrupt).grid(
            row=3, column=2, sticky="w", pady=(8, 0)
        )
        ttk.Button(controls, text="Cancelacion", command=self.run_simulation_cancel).grid(
            row=3, column=3, sticky="w", pady=(8, 0)
        )

        logs = ttk.LabelFrame(root_frame, text="Log de eventos", padding=10)
        logs.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(logs, height=16, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.configure(state=tk.DISABLED)

        footer = ttk.Label(root_frame, textvariable=self.note_var)
        footer.pack(fill=tk.X, pady=(8, 0))

        self._render()

    def _bind_keys(self) -> None:
        self.root.bind("<space>", self._on_space)
        self.root.bind("<Escape>", self._on_escape)
        self.text_entry.bind("<Return>", lambda _event: self.on_send_text())
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _focused_on_text_input(self, widget: tk.Widget) -> bool:
        return widget.winfo_class() in {"Entry", "Text", "TEntry"}

    def _on_space(self, event: tk.Event[Any]) -> str | None:
        if self._focused_on_text_input(event.widget):
            return None

        now = time.monotonic()
        if now - self._last_space_time < 0.32:
            self._last_space_time = 0.0
            self.on_double_tap()
            return "break"

        self._last_space_time = now
        self.root.after(340, lambda: self._fire_single_tap(now))
        return "break"

    def _fire_single_tap(self, timestamp: float) -> None:
        if abs(self._last_space_time - timestamp) < 1e-3:
            self._last_space_time = 0.0
            self.on_tap()

    def _on_escape(self, event: tk.Event[Any]) -> str | None:
        if self._focused_on_text_input(event.widget):
            return None

        self.on_long_press()
        return "break"

    def _append_log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}\n"
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line)
        line_count = int(float(self.log_text.index("end-1c").split(".")[0]))
        if line_count > MAX_LOG_LINES:
            overflow = line_count - MAX_LOG_LINES
            self.log_text.delete("1.0", f"{overflow + 1}.0")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _wire_safe_payload(self, payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload

        safe = dict(payload)
        msg_type = str(safe.get("type", ""))
        if msg_type in {"audio.chunk", "assistant.audio.chunk"} and isinstance(
            safe.get("payload"), str
        ):
            raw = safe["payload"]
            safe["payload"] = f"<base64:{len(raw)} chars>"

        text = safe.get("text")
        if isinstance(text, str) and len(text) > 220:
            safe["text"] = text[:220] + "...<trimmed>"

        return safe

    def _append_wire(self, direction: str, payload: Any) -> None:
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

    def _send(self, message: dict[str, Any], note: str) -> None:
        if not self.state.connected:
            self._append_log(f"TX blocked (offline): {message.get('type', '-')}")
            self._append_wire("TX-BLOCKED", message)
            self.note_var.set("Sin conexion al backend")
            return

        self.worker.send(message)
        self._append_log(f"TX {message.get('type', '-')}")
        self._append_wire("TX", message)
        self.note_var.set(note)

    def _send_quiet(self, message: dict[str, Any]) -> None:
        if not self.state.connected:
            self._append_wire("TX-BLOCKED", message)
            return
        self.worker.send(message)
        self._append_wire("TX", message)

    def _battery_color(self, level: float) -> str:
        if level >= 70:
            return "#22c55e"
        if level >= 40:
            return "#facc15"
        if level >= 10:
            return "#fb923c"
        return "#ef4444"

    def _battery_dot_color(self, level: float) -> str:
        if level >= 10:
            return self._battery_color(level)

        # 0-10% red blinking.
        blink_on = int(time.monotonic() * 2) % 2 == 0
        return "#ef4444" if blink_on else "#3b0a0a"

    def _state_header(self) -> str:
        if self.state.ui_state == UiState.LISTENING:
            return "listening"
        if self.state.ui_state == UiState.PROCESSING:
            return "thinking"
        if self.state.ui_state == UiState.SPEAKING:
            return "answering"
        if self.state.ui_state == UiState.ERROR:
            return "error"
        return "ready"

    def _wrap_for_display(self, text: str, max_chars: int, max_lines: int) -> str:
        compact = " ".join(text.split()).strip()
        if not compact:
            return "-"

        lines = textwrap.wrap(
            compact,
            width=max_chars,
            break_long_words=True,
            break_on_hyphens=False,
        )
        if not lines:
            return "-"

        if len(lines) > max_lines:
            lines = lines[:max_lines]
            if len(lines[-1]) > 3:
                lines[-1] = lines[-1][:-3] + "..."
            else:
                lines[-1] = lines[-1] + "..."

        return "\n".join(lines)

    def _scrolling_message_lines(self, max_chars: int, max_lines: int) -> list[tuple[str, str]]:
        user_lines = textwrap.wrap(
            " ".join(self.state.transcript.split()).strip() or "Tap to start recording",
            width=max_chars,
            break_long_words=True,
            break_on_hyphens=False,
        )
        assistant_lines = textwrap.wrap(
            " ".join(self.state.assistant_text.split()).strip() or "Waiting for assistant response",
            width=max_chars,
            break_long_words=True,
            break_on_hyphens=False,
        )

        line_items: list[tuple[str, str]] = [("YOU", line) for line in user_lines] + [
            ("AGENT", line) for line in assistant_lines
        ]

        if len(line_items) > max_lines:
            line_items = line_items[-max_lines:]

        return line_items

    def _draw_rounded_rect(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        radius: int,
        **kwargs: Any,
    ) -> int:
        points = [
            x1 + radius,
            y1,
            x2 - radius,
            y1,
            x2 - radius,
            y1,
            x2,
            y1,
            x2,
            y1 + radius,
            x2,
            y2 - radius,
            x2,
            y2 - radius,
            x2,
            y2,
            x2 - radius,
            y2,
            x1 + radius,
            y2,
            x1 + radius,
            y2,
            x1,
            y2,
            x1,
            y2 - radius,
            x1,
            y1 + radius,
            x1,
            y1 + radius,
            x1,
            y1,
            x1 + radius,
            y1,
        ]
        return self.hat_canvas.create_polygon(points, smooth=True, splinesteps=36, **kwargs)

    def _draw_network_icon(self, x: int, y: int, connected: bool) -> None:
        color = "#d1d5db" if connected else "#6b7280"
        for index, height in enumerate((4, 8, 12, 16)):
            left = x + index * 6
            self.hat_canvas.create_rectangle(
                left,
                y + (16 - height),
                left + 3,
                y + 16,
                fill=color,
                outline=color,
            )

    def _draw_bare_preview(self) -> None:
        c = self.hat_canvas
        w = self.hat_canvas_width
        h = self.hat_canvas_height
        mic_active = self._mic_streamer.active and self.state.ui_state == UiState.LISTENING

        c.delete("all")

        # background table/desk
        c.create_rectangle(0, 0, w, h, fill="#0f172a", outline="")

        module_x1 = 128
        module_y1 = 48
        module_x2 = 334
        module_y2 = 536

        # side depth (to mimic the bare hardware stack)
        c.create_polygon(
            module_x2,
            module_y1 + 22,
            module_x2 + 28,
            module_y1 + 32,
            module_x2 + 28,
            module_y2 - 12,
            module_x2,
            module_y2,
            fill="#321f2a",
            outline="#49303d",
            width=2,
        )

        # front board
        c.create_rectangle(
            module_x1,
            module_y1,
            module_x2,
            module_y2,
            fill="#0b1220",
            outline="#45556f",
            width=2,
        )

        # top extension board + mounting holes
        top_y1 = module_y1 - 20
        top_y2 = module_y1 + 16
        c.create_rectangle(
            module_x1 - 24,
            top_y1,
            module_x2 + 24,
            top_y2,
            fill="#111827",
            outline="#374151",
            width=2,
        )
        c.create_oval(module_x1 - 18, top_y1 + 8, module_x1 - 8, top_y1 + 18, outline="#cbd5e1")
        c.create_oval(module_x2 + 8, top_y1 + 8, module_x2 + 18, top_y1 + 18, outline="#cbd5e1")

        # top hardware LED (state-driven)
        led_color = LED_COLORS.get(self.state.ui_state, LED_COLORS[UiState.ERROR])
        c.create_oval(module_x1 + 68, top_y1 + 7, module_x1 + 88, top_y1 + 27, fill=led_color, outline="#60a5fa")
        c.create_oval(module_x1 + 62, top_y1 + 1, module_x1 + 94, top_y1 + 33, outline="#1d4ed8", width=1)

        # device screen area
        screen_x1 = module_x1 + 14
        screen_y1 = module_y1 + 28
        screen_x2 = module_x2 - 14
        screen_w = screen_x2 - screen_x1
        screen_h = int(screen_w * LCD_HEIGHT / LCD_WIDTH)
        screen_y2 = screen_y1 + screen_h
        corner_radius = max(8, int(screen_h * LCD_CORNER_HEIGHT / LCD_HEIGHT))
        self._draw_rounded_rect(
            screen_x1,
            screen_y1,
            screen_x2,
            screen_y2,
            radius=corner_radius,
            fill="#020617",
            outline="#0f172a",
            width=2,
        )

        # screen top status
        self._draw_network_icon(screen_x1 + 10, screen_y1 + 10, connected=self.state.connected)
        battery_level = max(0, min(100, int(round(self.state.battery_level))))
        c.create_text(
            screen_x2 - 32,
            screen_y1 + 18,
            text=f"{battery_level}%",
            fill="#cbd5e1",
            font=("Helvetica", 11, "bold"),
            anchor="e",
        )
        c.create_oval(
            screen_x2 - 25,
            screen_y1 + 12,
            screen_x2 - 13,
            screen_y1 + 24,
            fill=self._battery_dot_color(float(battery_level)),
            outline="",
        )
        if mic_active:
            c.create_oval(
                screen_x1 + 36,
                screen_y1 + 12,
                screen_x1 + 46,
                screen_y1 + 22,
                fill="#ef4444",
                outline="",
            )
            c.create_text(
                screen_x1 + 50,
                screen_y1 + 17,
                text="REC",
                anchor="w",
                fill="#fca5a5",
                font=("Helvetica", 8, "bold"),
            )

        # headline + mood icon
        c.create_text(
            screen_x1 + 18,
            screen_y1 + 44,
            anchor="nw",
            text=self._state_header(),
            fill="#f8fafc",
            font=("Helvetica", 18, "bold"),
        )
        c.create_oval(screen_x1 + 72, screen_y1 + 70, screen_x1 + 106, screen_y1 + 104, fill="#facc15", outline="#f59e0b")
        c.create_text(screen_x1 + 89, screen_y1 + 87, text=":)", fill="#111827", font=("Helvetica", 10, "bold"))

        # message area with vertical scrolling to avoid overlap
        line_height = 17
        line_start_y = screen_y1 + 108
        line_end_y = screen_y2 - 12
        max_lines = max(1, (line_end_y - line_start_y) // line_height)
        line_items = self._scrolling_message_lines(max_chars=19, max_lines=max_lines)

        for index, (origin, text_line) in enumerate(line_items):
            y = line_start_y + index * line_height
            color = "#86efac" if origin == "AGENT" else "#e2e8f0"
            c.create_text(
                screen_x1 + 14,
                y,
                anchor="nw",
                text=text_line,
                fill=color,
                font=("Helvetica", 10, "bold"),
            )

        # bottom board details
        board_y1 = screen_y2 + 12
        board_y2 = module_y2 - 18
        c.create_rectangle(module_x1 + 16, board_y1, module_x2 - 16, board_y2, fill="#111827", outline="#334155")
        c.create_text(
            module_x1 + 24,
            board_y1 + 14,
            anchor="nw",
            text="PiSugar WHISPLAY",
            fill="#d1d5db",
            font=("Helvetica", 8, "bold"),
        )
        c.create_text(
            module_x1 + 24,
            board_y1 + 30,
            anchor="nw",
            text=f"AGENT: {self.state.active_agent}",
            fill="#94a3b8",
            font=("Helvetica", 7),
        )
        c.create_text(
            module_x1 + 24,
            board_y1 + 44,
            anchor="nw",
            text=f"STATE: {STATE_LABELS.get(self.state.ui_state, self.state.ui_state.value.upper())}",
            fill="#94a3b8",
            font=("Helvetica", 7),
        )

    def _draw_cased_preview(self) -> None:
        c = self.hat_canvas
        w = self.hat_canvas_width
        h = self.hat_canvas_height
        mic_active = self._mic_streamer.active and self.state.ui_state == UiState.LISTENING

        c.delete("all")

        # background
        c.create_rectangle(0, 0, w, h, fill="#101827", outline="")

        case_x1 = 120
        case_y1 = 26
        case_x2 = 340
        case_y2 = 542

        # soft shadow
        self._draw_rounded_rect(
            case_x1 + 10,
            case_y1 + 10,
            case_x2 + 12,
            case_y2 + 12,
            radius=28,
            fill="#0b1220",
            outline="",
        )

        # orange strap/cap on top (as in photo)
        self._draw_rounded_rect(
            case_x1 + 60,
            case_y1 - 34,
            case_x2 - 60,
            case_y1 + 6,
            radius=10,
            fill="#d97706",
            outline="#b45309",
            width=2,
        )

        # white case body
        self._draw_rounded_rect(
            case_x1,
            case_y1,
            case_x2,
            case_y2,
            radius=28,
            fill="#f8fafc",
            outline="#d1d5db",
            width=2,
        )

        # top LED window
        led_slot_x1 = (case_x1 + case_x2) // 2 - 18
        led_slot_x2 = (case_x1 + case_x2) // 2 + 18
        led_slot_y1 = case_y1 + 20
        led_slot_y2 = case_y1 + 44
        self._draw_rounded_rect(
            led_slot_x1,
            led_slot_y1,
            led_slot_x2,
            led_slot_y2,
            radius=7,
            fill="#111827",
            outline="#1f2937",
            width=1,
        )
        led_color = LED_COLORS.get(self.state.ui_state, LED_COLORS[UiState.ERROR])
        c.create_oval(
            led_slot_x1 + 10,
            led_slot_y1 + 6,
            led_slot_x1 + 22,
            led_slot_y1 + 18,
            fill=led_color,
            outline="#60a5fa",
            width=1,
        )

        # screen window
        screen_x1 = case_x1 + 24
        screen_y1 = case_y1 + 66
        screen_x2 = case_x2 - 24
        screen_w = screen_x2 - screen_x1
        screen_h = int(screen_w * LCD_HEIGHT / LCD_WIDTH)
        screen_y2 = screen_y1 + screen_h
        corner_radius = max(8, int(screen_h * LCD_CORNER_HEIGHT / LCD_HEIGHT))
        self._draw_rounded_rect(
            screen_x1,
            screen_y1,
            screen_x2,
            screen_y2,
            radius=corner_radius,
            fill="#030712",
            outline="#111827",
            width=2,
        )

        # top status line in screen
        c.create_text(
            screen_x1 + 16,
            screen_y1 + 18,
            anchor="nw",
            text=self._state_header(),
            fill="#bfdbfe",
            font=("Helvetica", 15, "bold"),
        )
        battery_level = max(0, min(100, int(round(self.state.battery_level))))
        c.create_text(
            screen_x2 - 30,
            screen_y1 + 20,
            text=f"{battery_level}%",
            fill="#e2e8f0",
            font=("Helvetica", 11, "bold"),
            anchor="e",
        )
        c.create_oval(
            screen_x2 - 23,
            screen_y1 + 14,
            screen_x2 - 11,
            screen_y1 + 26,
            fill=self._battery_dot_color(float(battery_level)),
            outline="",
        )
        if mic_active:
            c.create_oval(
                screen_x1 + 42,
                screen_y1 + 15,
                screen_x1 + 52,
                screen_y1 + 25,
                fill="#ef4444",
                outline="",
            )
            c.create_text(
                screen_x1 + 56,
                screen_y1 + 20,
                text="REC",
                anchor="w",
                fill="#fca5a5",
                font=("Helvetica", 8, "bold"),
            )

        # avatar icon
        c.create_text(
            (screen_x1 + screen_x2) // 2,
            screen_y1 + 62,
            text="*",
            fill="#8b5cf6",
            font=("Helvetica", 26, "bold"),
        )

        # message area with vertical scrolling to avoid overlap
        line_height = 16
        line_start_y = screen_y1 + 98
        line_end_y = screen_y2 - 12
        max_lines = max(1, (line_end_y - line_start_y) // line_height)
        line_items = self._scrolling_message_lines(max_chars=18, max_lines=max_lines)

        for index, (origin, text_line) in enumerate(line_items):
            y = line_start_y + index * line_height
            color = "#93c5fd" if origin == "AGENT" else "#e2e8f0"
            c.create_text(
                screen_x1 + 14,
                y,
                anchor="nw",
                text=text_line,
                fill=color,
                font=("Helvetica", 10, "bold"),
            )

        # bottom mic holes
        holes_y = case_y2 - 44
        center_x = (case_x1 + case_x2) // 2
        for dx in (-24, 0, 24):
            c.create_oval(
                center_x + dx - 5,
                holes_y - 5,
                center_x + dx + 5,
                holes_y + 5,
                fill="#111827",
                outline="#1f2937",
            )

    def _draw_hat_preview(self) -> None:
        mode = self.preview_mode_var.get().strip().lower()
        if mode == "bare":
            self._draw_bare_preview()
            return
        self._draw_cased_preview()

    def _drain_battery(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_battery_tick
        self._last_battery_tick = now

        if elapsed <= 0:
            return

        if self.state.ui_state == UiState.LISTENING:
            drain_per_sec = 0.06
        elif self.state.ui_state == UiState.SPEAKING:
            drain_per_sec = 0.05
        elif self.state.ui_state == UiState.PROCESSING:
            drain_per_sec = 0.03
        else:
            drain_per_sec = 0.01

        next_level = max(0.0, self.state.battery_level - drain_per_sec * elapsed)
        self.state.battery_level = next_level
        self.battery_var.set(next_level)

    def on_battery_change(self, _value: str) -> None:
        self.state.battery_level = float(self.battery_var.get())
        self.battery_label_var.set(f"{int(round(self.state.battery_level))}%")
        self._draw_hat_preview()

    def on_preview_mode_change(self, _event: tk.Event[Any]) -> None:
        mode = self.preview_mode_var.get().strip().lower()
        if mode not in {"cased", "bare"}:
            self.preview_mode_var.set("cased")
        self._draw_hat_preview()

    def on_open_mic(self) -> None:
        if not SOUNDDEVICE_AVAILABLE:
            self.mic_error_var.set("sounddevice no instalado")
            self.note_var.set("Mic no disponible: instala sounddevice y portaudio")
            return

        if self.state.ui_state in (UiState.IDLE, UiState.ERROR):
            self.on_tap()

        if self.state.ui_state != UiState.LISTENING:
            self.note_var.set("Abre un turno (LISTENING) antes de activar mic")
            return

        self._start_mic_capture()
        if self._mic_streamer.active:
            self.mic_error_var.set("-")
            self.note_var.set("Microfono abierto: enviando audio por chunks")

    def on_close_mic(self) -> None:
        if not self._mic_streamer.active:
            self.note_var.set("Microfono ya esta cerrado")
            return

        self._flush_mic_chunks()
        self._stop_mic_capture()
        dropped = self._mic_streamer.dropped_chunks
        dropped_suffix = f", dropped={dropped}" if dropped > 0 else ""
        self.note_var.set(
            f"Microfono cerrado ({self._turn_audio_chunks_sent} chunks, "
            f"{self._turn_audio_bytes_sent / 1024:.1f} KB{dropped_suffix})"
        )

    def refresh_mic_devices(self) -> None:
        if not SOUNDDEVICE_AVAILABLE:
            self.mic_error_var.set("sounddevice no instalado")
            self.mic_device_combo.configure(values=[])
            self.mic_device_var.set("")
            return

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
            name = str(dev.get("name", f"device-{index}")).strip()
            label = f"{index}: {name}"
            entries.append((index, label))
            labels.append(label)

        self._mic_input_devices = entries
        self.mic_device_combo.configure(values=labels)

        if not entries:
            self.mic_device_var.set("")
            self.mic_error_var.set("No hay entradas de micro disponibles")
            return

        current_idx = self._mic_streamer.device_index
        selected_label = next((label for idx, label in entries if idx == current_idx), entries[0][1])
        self.mic_device_var.set(selected_label)
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

    def _start_mic_capture(self) -> None:
        if self._mic_streamer.active:
            return
        if SOUNDDEVICE_AVAILABLE and not self._mic_input_devices:
            self.refresh_mic_devices()
        try:
            self._mic_streamer.start()
            self._append_log("AUDIO mic capture started")
            self._append_wire(
                "SYS",
                {
                    "type": "audio.capture.started",
                    "sample_rate": MIC_SAMPLE_RATE,
                    "channels": MIC_CHANNELS,
                    "chunk_ms": MIC_CHUNK_MS,
                    "device_index": self._mic_streamer.device_index,
                },
            )
            self.mic_error_var.set("-")
        except Exception as exc:
            self.mic_error_var.set(str(exc))
            self.note_var.set(f"No se pudo iniciar microfono: {exc}")
            self._append_log(f"AUDIO error: {exc}")

    def _stop_mic_capture(self) -> None:
        if not self._mic_streamer.active:
            return
        self._mic_streamer.stop()
        self._append_log("AUDIO mic capture stopped")
        self._append_wire(
            "SYS",
            {
                "type": "audio.capture.stopped",
                "bytes_sent": self._mic_streamer.bytes_sent,
                "dropped_chunks": self._mic_streamer.dropped_chunks,
            },
        )

    def _flush_mic_chunks(self) -> None:
        if not self._mic_streamer.active or self.state.ui_state != UiState.LISTENING:
            return
        if not self.state.turn_id:
            return

        for chunk in self._mic_streamer.pop_chunks(max_chunks=MAX_CHUNKS_PER_FLUSH):
            self._turn_audio_chunks_sent += 1
            self._turn_audio_bytes_sent += int(chunk["size_bytes"])
            self._send_quiet(
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

    def _render(self) -> None:
        self.connection_var.set("connected" if self.state.connected else "disconnected")
        self.session_var.set(self.state.session_id or "-")
        self.agent_var.set(self.state.active_agent)
        self.state_var.set(self.state.ui_state.value)
        self.turn_var.set(self.state.turn_id or "-")
        self.latency_var.set(
            f"{self.state.last_latency_ms} ms" if self.state.last_latency_ms is not None else "-"
        )
        mic_active = self._mic_streamer.active and self.state.ui_state == UiState.LISTENING
        self.mic_status_var.set("REC" if mic_active else "OFF")
        if mic_active:
            blink_on = int(time.monotonic() * 4) % 2 == 0
            self.mic_canvas.itemconfigure(self.mic_dot, fill="#ef4444" if blink_on else "#7f1d1d")
        else:
            self.mic_canvas.itemconfigure(self.mic_dot, fill="#4b5563")
        self.audio_chunks_var.set(str(self._turn_audio_chunks_sent))
        self.audio_kb_var.set(f"{self._turn_audio_bytes_sent / 1024:.1f} KB")
        self.audio_rx_chunks_var.set(str(self._turn_audio_chunks_rx))
        self.audio_rx_kb_var.set(f"{self._turn_audio_bytes_rx / 1024:.1f} KB")
        self.audio_playback_var.set("ON" if self._audio_player.active else "OFF")
        self.battery_label_var.set(f"{int(round(self.state.battery_level))}%")
        self._draw_hat_preview()

    def on_tap(self) -> None:
        if self.state.ui_state in (UiState.IDLE, UiState.ERROR):
            if not self.state.connected:
                self.note_var.set("Sin conexion al backend")
                return
            self.state.turn_id = new_turn_id()
            self.state.transcript = ""
            self.state.assistant_text = ""
            self.state.last_latency_ms = None
            self._turn_audio_chunks_sent = 0
            self._turn_audio_bytes_sent = 0
            self._turn_audio_chunks_rx = 0
            self._turn_audio_bytes_rx = 0
            self._stop_audio_playback(clear_buffer=True)
            self.state.ui_state = UiState.LISTENING
            self._send(
                build_message(
                    "recording.start",
                    turn_id=self.state.turn_id,
                    codec="pcm16",
                    sample_rate=16000,
                    channels=1,
                ),
                note="Grabacion iniciada",
            )
            if SOUNDDEVICE_AVAILABLE:
                self._start_mic_capture()
            else:
                self.note_var.set("Grabacion iniciada (mic no disponible)")
            self._render()
            return

        if self.state.ui_state == UiState.LISTENING:
            self._flush_mic_chunks()
            self._stop_mic_capture()
            dropped = self._mic_streamer.dropped_chunks
            dropped_suffix = f", dropped={dropped}" if dropped > 0 else ""
            self.state.ui_state = UiState.PROCESSING
            self._send(
                build_message("recording.stop", turn_id=self.state.turn_id),
                note=(
                    f"Turno enviado ({self._turn_audio_chunks_sent} chunks, "
                    f"{self._turn_audio_bytes_sent / 1024:.1f} KB{dropped_suffix})"
                ),
            )
            self._render()
            return

        if self.state.ui_state == UiState.SPEAKING:
            self._send(
                build_message("assistant.interrupt", turn_id=self.state.turn_id),
                note="Interrupcion solicitada",
            )
            return

        self.note_var.set("Tap ignorado en estado actual")

    def on_double_tap(self) -> None:
        if self.state.ui_state != UiState.IDLE:
            self.note_var.set("Double tap solo permitido en IDLE")
            return

        self.state.agent_index = (self.state.agent_index + 1) % len(self.state.agents)
        self._send(
            build_message("agent.select", agent_id=self.state.active_agent),
            note=f"Agente solicitado: {self.state.active_agent}",
        )
        self._render()

    def on_long_press(self) -> None:
        if self.state.ui_state == UiState.LISTENING:
            self._flush_mic_chunks()
            self._stop_mic_capture()
            self.state.ui_state = UiState.IDLE
            self.state.turn_id = None
            self._send(build_message("recording.cancel"), note="Grabacion cancelada")
            self._render()
            return

        if self.state.ui_state == UiState.SPEAKING:
            self._send(build_message("assistant.interrupt"), note="Interrupcion solicitada")
            return

        self.note_var.set("Long press reservado para menu/apagado")

    def on_send_text(self) -> None:
        text = self.text_entry.get().strip()
        if not text:
            return

        if self.state.ui_state in (UiState.IDLE, UiState.ERROR):
            self.on_tap()

        self._send(
            build_message("debug.user_text", turn_id=self.state.turn_id, text=text),
            note="Texto de prueba enviado",
        )
        self.text_entry.delete(0, tk.END)

    def _send_text_direct(self, text: str) -> None:
        cleaned = text.strip()
        if not cleaned:
            return

        if self.state.ui_state in (UiState.IDLE, UiState.ERROR):
            self.on_tap()

        self._send(
            build_message("debug.user_text", turn_id=self.state.turn_id, text=cleaned),
            note="Texto de simulacion enviado",
        )

    def _cancel_simulation_timers(self) -> None:
        for after_id in self._simulation_after_ids:
            try:
                self.root.after_cancel(after_id)
            except Exception:
                pass
        self._simulation_after_ids.clear()

    def _start_simulation(
        self,
        name: str,
        actions: list[tuple[int, Callable[[], None]]],
    ) -> None:
        if self._simulation_running:
            self.note_var.set("Ya hay una simulacion en curso")
            return

        if self.state.ui_state not in (UiState.IDLE, UiState.ERROR):
            self.note_var.set("Pon el dispositivo en IDLE antes de simular")
            return

        self._simulation_running = True
        self.note_var.set(f"Simulacion '{name}' en curso")
        self._append_log(f"SIM START {name}")

        for delay_ms, action in actions:
            after_id = self.root.after(delay_ms, action)
            self._simulation_after_ids.append(after_id)

        final_delay = (actions[-1][0] if actions else 0) + 500
        end_id = self.root.after(final_delay, lambda: self._finish_simulation(name))
        self._simulation_after_ids.append(end_id)

    def _finish_simulation(self, name: str) -> None:
        self._simulation_running = False
        self._cancel_simulation_timers()
        self.note_var.set(f"Simulacion '{name}' completada")
        self._append_log(f"SIM END {name}")

    def run_simulation_turn(self) -> None:
        self._start_simulation(
            "turno",
            [
                (0, self.on_tap),
                (450, lambda: self._send_text_direct("Quiero comprobar un turno de simulacion completo.")),
                (1200, self.on_tap),
            ],
        )

    def run_simulation_interrupt(self) -> None:
        self._start_simulation(
            "interrupcion",
            [
                (0, self.on_tap),
                (450, lambda: self._send_text_direct("Genera una respuesta algo larga para probar interrupcion.")),
                (1150, self.on_tap),
                (2400, self.on_tap),
            ],
        )

    def run_simulation_cancel(self) -> None:
        self._start_simulation(
            "cancelacion",
            [
                (0, self.on_tap),
                (500, lambda: self._send_text_direct("Este turno se va a cancelar.")),
                (1000, self.on_long_press),
            ],
        )

    def _handle_connection_event(self, message: dict[str, Any]) -> None:
        status = str(message.get("status", ""))
        self._append_wire("SYS", message)
        if status == "connected":
            self.state.connected = True
            self.note_var.set("Conectado a backend")
            self._append_log("RX _connection connected")
        elif status == "disconnected":
            self._stop_mic_capture()
            self._stop_audio_playback(clear_buffer=True)
            self.state.connected = False
            detail = str(message.get("detail", "")).strip()
            suffix = f" ({detail})" if detail else ""
            self.note_var.set(f"Desconectado, reintentando{suffix}")
            self._append_log("RX _connection disconnected")
        elif status == "stopped":
            self._stop_mic_capture()
            self._stop_audio_playback(clear_buffer=True)
            self.state.connected = False
            self.note_var.set("Conexion detenida")
            self._append_log("RX _connection stopped")

    def _handle_backend_message(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type", ""))
        self._append_log(f"RX {message_type}")
        self._append_wire("RX", message)

        if message_type == "session.ready":
            self.state.session_id = str(message.get("session_id", ""))
            self.state.connected = True

            agents = message.get("available_agents")
            if isinstance(agents, list):
                normalized = [str(item).strip() for item in agents if str(item).strip()]
                if normalized:
                    current = self.state.active_agent
                    self.state.agents = normalized
                    self.state.set_agent(current)

            remote_agent = str(message.get("active_agent", "")).strip()
            if remote_agent:
                self.state.set_agent(remote_agent)

        elif message_type == "agent.selected":
            selected = str(message.get("agent_id", "")).strip()
            if selected:
                self.state.set_agent(selected)

        elif message_type == "ui.state":
            state_value = str(message.get("state", UiState.IDLE.value))
            try:
                self.state.ui_state = UiState(state_value)
            except ValueError:
                self.state.ui_state = UiState.ERROR
            if self.state.ui_state != UiState.LISTENING:
                self._stop_mic_capture()

        elif message_type == "transcript.partial":
            piece = str(message.get("text", "")).strip()
            if piece:
                self.state.transcript = (self.state.transcript + " " + piece).strip()

        elif message_type == "transcript.final":
            self.state.transcript = str(message.get("text", self.state.transcript))

        elif message_type == "assistant.text.partial":
            self.state.assistant_text += str(message.get("text", ""))

        elif message_type == "assistant.text.final":
            self.state.assistant_text = str(message.get("text", self.state.assistant_text))
            interrupted = bool(message.get("interrupted"))
            if interrupted:
                self.state.assistant_text += " [interrupted]"

            latency = message.get("latency_ms")
            if isinstance(latency, int):
                self.state.last_latency_ms = latency

        elif message_type == "assistant.audio.start":
            if SOUNDDEVICE_AVAILABLE:
                sample_rate = int(message.get("sample_rate", MIC_SAMPLE_RATE) or MIC_SAMPLE_RATE)
                channels = int(message.get("channels", MIC_CHANNELS) or MIC_CHANNELS)
                try:
                    self._audio_player.start(sample_rate=sample_rate, channels=channels)
                    self._audio_end_pending = False
                    self.note_var.set("Reproduciendo audio recibido")
                except Exception as exc:
                    self._stop_audio_playback(clear_buffer=True)
                    self.note_var.set(f"No se pudo abrir salida de audio: {exc}")
                    self._append_log(f"AUDIO output error: {exc}")
            else:
                self.note_var.set("Audio de salida no disponible (sounddevice)")

        elif message_type == "assistant.audio.chunk":
            payload = message.get("payload")
            if isinstance(payload, str) and payload:
                if SOUNDDEVICE_AVAILABLE and not self._audio_player.active:
                    try:
                        self._audio_player.start(sample_rate=MIC_SAMPLE_RATE, channels=MIC_CHANNELS)
                        self._audio_end_pending = False
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

        elif message_type == "assistant.audio.end":
            self._audio_end_pending = True

        elif message_type == "error":
            self.state.ui_state = UiState.ERROR
            detail = str(message.get("detail", "")).strip()
            if detail:
                self.note_var.set(detail)

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
        self._drain_battery()
        self._render()
        self.root.after(120, self._poll_inbox)

    def on_close(self) -> None:
        self._cancel_simulation_timers()
        self._simulation_running = False
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
