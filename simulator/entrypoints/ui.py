"""Tkinter UI simulator aligned with the local device state machine."""

from __future__ import annotations

import argparse
import json
import os
import queue
import time
import tkinter as tk
from tkinter import ttk
from typing import Any

from dotenv import load_dotenv

from simulator.application.ports import Clock
from simulator.application.services import SimulatorController
from simulator.domain.events import DeviceInputEvent, DeviceState
from simulator.domain.state import UiStateModel
from device_runtime.application.services import DisplayModelService
from device_runtime.infrastructure.audio.sounddevice_capture import (
    query_input_devices,
    sounddevice_is_available,
)
from device_runtime.infrastructure.display.tk_preview_display import TkPreviewDisplay
from device_runtime.infrastructure.input.keyboard_button import KeyboardButton
from simulator.entrypoints.ui_runtime import (
    AudioOutputPlayer,
    MicAudioStreamer,
    UiGateway,
    UiRuntimeSession,
    WsWorker,
)
from simulator.shared.protocol import UiState, build_message

load_dotenv()

SOUNDDEVICE_AVAILABLE = sounddevice_is_available()

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


class SimulatorUi:
    def __init__(self, root: tk.Tk, ws_url: str, device_id: str, auth_token: str) -> None:
        self.root = root
        self.root.title("Simulador de Dispositivo Conversacional")
        self.root.geometry("1460x820")
        self.root.minsize(1280, 720)
        self._button_labels = DEVICE_BUTTONS

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
            gateway=UiGateway(self._send_worker_message, sample_rate=MIC_SAMPLE_RATE, channels=MIC_CHANNELS),
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
        self._keyboard_button = KeyboardButton(self.root)
        self._display_model_service = DisplayModelService()
        self._preview_display: TkPreviewDisplay | None = None
        self._mic_input_devices: list[tuple[int, str]] = []
        self._turn_audio_chunks_sent = 0
        self._turn_audio_bytes_sent = 0
        self._turn_audio_chunks_rx = 0
        self._turn_audio_bytes_rx = 0
        self._runtime_session = UiRuntimeSession(
            self,
            sounddevice_available=SOUNDDEVICE_AVAILABLE,
            sample_rate=MIC_SAMPLE_RATE,
            channels=MIC_CHANNELS,
            chunk_ms=MIC_CHUNK_MS,
            max_chunks_per_flush=MAX_CHUNKS_PER_FLUSH,
        )

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
        self._keyboard_button.start(lambda event_name: self._dispatch(DeviceInputEvent(event_name)))
        self._keyboard_button.bind_default_keys(self.root)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def refresh_mic_devices(self) -> None:
        if not SOUNDDEVICE_AVAILABLE:
            self.mic_error_var.set("sounddevice no instalado")
            self.mic_device_combo.configure(values=[])
            self.mic_device_var.set("")
            return
        try:
            entries = query_input_devices()
        except Exception as exc:
            self.mic_error_var.set(f"query_devices error: {exc}")
            self.mic_device_combo.configure(values=[])
            self.mic_device_var.set("")
            return

        labels = [label for _, label in entries]

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

    def _draw_hardware_preview(self) -> None:
        if not hasattr(self, "hat_canvas"):
            return
        if self._preview_display is None:
            self._preview_display = TkPreviewDisplay(self.hat_canvas, mode_provider=self.preview_mode_var.get)
        model = self._display_model_service.build(self.state)
        model.mic_live = self._mic_streamer.active and self.state.device_state == DeviceState.LISTEN
        preview_display = self._preview_display
        assert preview_display is not None
        preview_display.show_diagnostic(self.note_var.get())
        preview_display.render(model)

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
        self._runtime_session.send_worker_message(message)

    def _dispatch(self, event: DeviceInputEvent) -> None:
        self._runtime_session.dispatch(event)

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
        self._runtime_session.open_mic()

    def on_close_mic(self) -> None:
        self._runtime_session.close_mic()

    def _stop_mic_capture(self) -> None:
        self._runtime_session._stop_mic_capture()

    def _start_mic_capture(self, *, auto: bool) -> None:
        self._runtime_session._start_mic_capture(auto=auto)

    def _flush_mic_chunks(self) -> None:
        self._runtime_session._flush_mic_chunks()

    def _stop_audio_playback(self, clear_buffer: bool = True) -> None:
        self._runtime_session._stop_audio_playback(clear_buffer=clear_buffer)

    def _maybe_finish_audio_playback(self) -> None:
        self._runtime_session._maybe_finish_audio_playback()

    def _handle_connection_event(self, message: dict[str, Any]) -> None:
        self._runtime_session._handle_connection_event(message)

    def _handle_audio_message(self, message: dict[str, Any]) -> None:
        self._runtime_session._handle_audio_message(message)

    def _handle_backend_message(self, message: dict[str, Any]) -> None:
        self._runtime_session._handle_backend_message(message)

    def _poll_inbox(self) -> None:
        self._runtime_session.poll_inbox()
        self.root.after(120, self._poll_inbox)

    def on_close(self) -> None:
        self._runtime_session.shutdown()
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
