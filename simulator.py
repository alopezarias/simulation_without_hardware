"""CLI simulator that mimics a button-based conversational device."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import dataclass, field

import websockets
from dotenv import load_dotenv
from websockets.exceptions import ConnectionClosed

from protocol import UiState, build_message, new_turn_id

load_dotenv()

LED_BY_STATE = {
    UiState.IDLE: "BLUE",
    UiState.LISTENING: "GREEN",
    UiState.PROCESSING: "YELLOW",
    UiState.SPEAKING: "WHITE",
    UiState.ERROR: "RED",
}


@dataclass
class SimulatorState:
    device_id: str
    agents: list[str] = field(
        default_factory=lambda: ["assistant-general", "assistant-tech", "assistant-ops"]
    )
    agent_index: int = 0
    ui_state: UiState = UiState.IDLE
    turn_id: str | None = None
    transcript: str = ""
    assistant_text: str = ""
    session_id: str = ""
    connected: bool = False

    @property
    def active_agent(self) -> str:
        if not self.agents:
            return "assistant-general"
        return self.agents[self.agent_index]

    def set_agent(self, agent_id: str) -> None:
        if not self.agents:
            self.agents = [agent_id]
            self.agent_index = 0
            return

        if agent_id in self.agents:
            self.agent_index = self.agents.index(agent_id)
            return

        self.agents.append(agent_id)
        self.agent_index = len(self.agents) - 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Conversational device simulator")
    parser.add_argument(
        "--ws-url",
        default=os.getenv("SIM_WS_URL", "ws://127.0.0.1:8000/ws"),
        help="Backend WS URL",
    )
    parser.add_argument(
        "--device-id",
        default=os.getenv("SIM_DEVICE_ID", "sim-device-001"),
        help="Simulated device id",
    )
    parser.add_argument(
        "--auth-token",
        default=os.getenv("SIM_DEVICE_AUTH_TOKEN", ""),
        help="Optional auth token for device.hello",
    )
    return parser.parse_args()


def print_help() -> None:
    print(
        "\nCommands:\n"
        "  tap                 Short press.\n"
        "  double              Double tap. Changes active agent.\n"
        "  long                Long press. Cancels recording or interrupts speaking.\n"
        "  text <message>      Send debug text to backend.\n"
        "  send                Alias for tap while listening.\n"
        "  state               Print current simulated screen.\n"
        "  help                Show this help.\n"
        "  quit                Exit simulator.\n"
    )


def render_screen(state: SimulatorState, note: str = "") -> None:
    led = LED_BY_STATE.get(state.ui_state, "RED")
    header = f"[{time.strftime('%H:%M:%S')}]"
    if note:
        header = f"{header} {note}"

    print("\n" + "=" * 72)
    print(header)
    print("DISPOSITIVO SIMULADO")
    print(f"Connected  : {'yes' if state.connected else 'no'}")
    print(f"Session ID : {state.session_id or '-'}")
    print(f"Agent      : {state.active_agent}")
    print(f"State      : {state.ui_state.value}")
    print(f"LED        : {led}")
    print(f"Turn ID    : {state.turn_id or '-'}")
    print("--- Transcript ---")
    print(state.transcript or "-")
    print("--- Assistant ---")
    print(state.assistant_text or "-")
    print("=" * 72)


async def send_json(ws: websockets.ClientConnection, message: dict) -> None:
    await ws.send(json.dumps(message))


async def tap(ws: websockets.ClientConnection, state: SimulatorState) -> None:
    if state.ui_state in (UiState.IDLE, UiState.ERROR):
        state.turn_id = new_turn_id()
        state.transcript = ""
        state.assistant_text = ""
        await send_json(
            ws,
            build_message(
                "recording.start",
                turn_id=state.turn_id,
                codec="pcm16",
                sample_rate=16000,
                channels=1,
            ),
        )
        state.ui_state = UiState.LISTENING
        render_screen(state, "TX recording.start")
        return

    if state.ui_state == UiState.LISTENING:
        await send_json(ws, build_message("recording.stop", turn_id=state.turn_id))
        state.ui_state = UiState.PROCESSING
        render_screen(state, "TX recording.stop")
        return

    if state.ui_state == UiState.SPEAKING:
        await send_json(ws, build_message("assistant.interrupt", turn_id=state.turn_id))
        render_screen(state, "TX assistant.interrupt")
        return

    render_screen(state, "tap ignored in current state")


async def long_press(ws: websockets.ClientConnection, state: SimulatorState) -> None:
    if state.ui_state == UiState.LISTENING:
        await send_json(ws, build_message("recording.cancel", turn_id=state.turn_id))
        state.ui_state = UiState.IDLE
        state.turn_id = None
        render_screen(state, "TX recording.cancel")
        return

    if state.ui_state == UiState.SPEAKING:
        await send_json(ws, build_message("assistant.interrupt", turn_id=state.turn_id))
        render_screen(state, "TX assistant.interrupt")
        return

    render_screen(state, "long press mapped to menu/power in future")


async def double_tap(ws: websockets.ClientConnection, state: SimulatorState) -> None:
    if not state.agents:
        state.agents = ["assistant-general"]

    state.agent_index = (state.agent_index + 1) % len(state.agents)
    await send_json(ws, build_message("agent.select", agent_id=state.active_agent))
    render_screen(state, f"TX agent.select -> {state.active_agent}")


async def send_debug_text(ws: websockets.ClientConnection, state: SimulatorState, text: str) -> None:
    cleaned = text.strip()
    if not cleaned:
        render_screen(state, "ignored empty text")
        return

    if state.ui_state in (UiState.IDLE, UiState.ERROR):
        await tap(ws, state)

    await send_json(
        ws,
        build_message(
            "debug.user_text",
            turn_id=state.turn_id,
            text=cleaned,
        ),
    )
    render_screen(state, "TX debug.user_text")


async def receiver_loop(ws: websockets.ClientConnection, state: SimulatorState) -> None:
    async for raw in ws:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue

        message_type = message.get("type", "")

        if message_type == "session.ready":
            state.connected = True
            state.session_id = str(message.get("session_id", ""))
            agents = message.get("available_agents")
            if isinstance(agents, list):
                normalized = [str(agent).strip() for agent in agents if str(agent).strip()]
                if normalized:
                    previous = state.active_agent
                    state.agents = normalized
                    state.set_agent(previous)

            remote_active = str(message.get("active_agent", "")).strip()
            if remote_active:
                state.set_agent(remote_active)

        elif message_type == "agent.selected":
            selected = str(message.get("agent_id", "")).strip()
            if selected:
                state.set_agent(selected)

        elif message_type == "ui.state":
            value = str(message.get("state", UiState.IDLE.value))
            try:
                state.ui_state = UiState(value)
            except ValueError:
                state.ui_state = UiState.ERROR

        elif message_type == "transcript.partial":
            piece = str(message.get("text", "")).strip()
            if piece:
                state.transcript = (state.transcript + " " + piece).strip()

        elif message_type == "transcript.final":
            state.transcript = str(message.get("text", state.transcript))

        elif message_type == "assistant.text.partial":
            state.assistant_text += str(message.get("text", ""))

        elif message_type == "assistant.text.final":
            state.assistant_text = str(message.get("text", state.assistant_text))
            if bool(message.get("interrupted")):
                state.assistant_text += " [interrupted]"

        elif message_type == "error":
            state.ui_state = UiState.ERROR

        render_screen(state, f"RX {message_type}")


async def ping_loop(ws: websockets.ClientConnection) -> None:
    while True:
        await asyncio.sleep(15)
        await send_json(ws, build_message("ping"))


async def command_loop(ws: websockets.ClientConnection, state: SimulatorState) -> None:
    print_help()
    render_screen(state, "connected")

    while True:
        command_line = await asyncio.to_thread(input, "sim> ")
        command_line = command_line.strip()

        if not command_line:
            continue

        if command_line in {"quit", "exit"}:
            break
        if command_line in {"help", "?"}:
            print_help()
            continue
        if command_line in {"state", "status"}:
            render_screen(state, "local state")
            continue
        if command_line in {"tap", "send"}:
            await tap(ws, state)
            continue
        if command_line == "double":
            await double_tap(ws, state)
            continue
        if command_line == "long":
            await long_press(ws, state)
            continue
        if command_line.startswith("text "):
            await send_debug_text(ws, state, command_line[5:])
            continue

        render_screen(state, "unknown command")


async def run_simulator(args: argparse.Namespace) -> None:
    state = SimulatorState(device_id=args.device_id)

    async with websockets.connect(args.ws_url) as ws:
        state.connected = True

        hello_payload = build_message(
            "device.hello",
            device_id=state.device_id,
            firmware_version="0.2.0",
            simulated=True,
            capabilities=["screen", "leds", "button", "audio_in", "audio_out"],
            active_agent=state.active_agent,
        )
        if args.auth_token:
            hello_payload["auth_token"] = args.auth_token

        await send_json(ws, hello_payload)

        receiver_task = asyncio.create_task(receiver_loop(ws, state))
        ping_task = asyncio.create_task(ping_loop(ws))

        try:
            await command_loop(ws, state)
        finally:
            receiver_task.cancel()
            ping_task.cancel()
            try:
                await receiver_task
            except asyncio.CancelledError:
                pass
            try:
                await ping_task
            except asyncio.CancelledError:
                pass


def main() -> None:
    args = parse_args()

    try:
        asyncio.run(run_simulator(args))
    except KeyboardInterrupt:
        pass
    except ConnectionClosed as exc:
        print(f"Connection closed: {exc}")


if __name__ == "__main__":
    main()
