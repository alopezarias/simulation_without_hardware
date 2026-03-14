"""CLI simulator that mimics the conversational device state machine."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from typing import Any

import websockets
from dotenv import load_dotenv
from websockets.exceptions import ConnectionClosed

from simulator.application.ports import BackendGateway, Clock
from simulator.application.services import SimulatorController
from simulator.domain.events import DeviceInputEvent, DeviceState
from simulator.domain.state import SimulatorState
from simulator.shared.protocol import UiState, build_message

load_dotenv()

LED_BY_REMOTE_STATE = {
    UiState.IDLE: "BLUE",
    UiState.LISTENING: "GREEN",
    UiState.PROCESSING: "YELLOW",
    UiState.SPEAKING: "WHITE",
    UiState.ERROR: "RED",
}


class SystemClock(Clock):
    def now(self) -> float:
        return time.monotonic()


class CliGateway(BackendGateway):
    def __init__(self, ws: websockets.ClientConnection) -> None:
        self._ws = ws

    async def start_listen(self, turn_id: str) -> None:
        await send_json(
            self._ws,
            build_message(
                "recording.start",
                turn_id=turn_id,
                codec="pcm16",
                sample_rate=16000,
                channels=1,
            ),
        )

    async def stop_listen(self, turn_id: str) -> None:
        await send_json(self._ws, build_message("recording.stop", turn_id=turn_id))

    async def cancel_listen(self, turn_id: str | None) -> None:
        payload: dict[str, Any] = {}
        if turn_id:
            payload["turn_id"] = turn_id
        await send_json(self._ws, build_message("recording.cancel", **payload))

    async def request_agents_version(self) -> None:
        await send_json(self._ws, build_message("agents.version.request"))

    async def request_agents_list(self) -> None:
        await send_json(self._ws, build_message("agents.list.request"))

    async def confirm_agent(self, agent_id: str) -> None:
        await send_json(self._ws, build_message("agent.select", agent_id=agent_id))


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
        "  press               Press. Enter LISTEN, advance MENU/MODE/AGENTS, or finish LISTEN.\n"
        "  double              Double Press. Open/cancel MENU, cancel LISTEN, or exit AGENTS.\n"
        "  long                Long Press. Lock/unlock, confirm MODE/AGENTS, or enter AGENTS from LISTEN.\n"
        "  text <message>      Send debug text while preserving the local device machine.\n"
        "  tap / send          Legacy aliases for press.\n"
        "  state               Print current simulated screen.\n"
        "  help                Show this help.\n"
        "  quit                Exit simulator.\n"
    )


def _focus_label(state: SimulatorState) -> str:
    if state.device_state == DeviceState.MENU:
        return state.navigation.menu_options[state.navigation.menu_index]
    if state.device_state == DeviceState.MODE:
        return state.navigation.available_modes[state.navigation.mode_index]
    if state.device_state == DeviceState.AGENTS:
        if not state.agents:
            return "-"
        return state.focused_agent
    return "-"


def render_screen(state: SimulatorState, note: str = "") -> None:
    led = LED_BY_REMOTE_STATE.get(state.remote_ui_state, "RED")
    header = f"[{time.strftime('%H:%M:%S')}]"
    if note:
        header = f"{header} {note}"

    agent_line = state.active_agent
    if state.pending_agent_ack:
        agent_line = f"{agent_line} (pending ack: {state.pending_agent_ack})"

    cache_status = "cold"
    if state.agent_cache.loaded_at is not None:
        cache_status = "warm"
        if state.agent_cache.expires_at is not None and state.agent_cache.expires_at < time.monotonic():
            cache_status = "stale"

    print("\n" + "=" * 72)
    print(header)
    print("DISPOSITIVO SIMULADO")
    print(f"Connected     : {'yes' if state.connected else 'no'}")
    print(f"Session ID    : {state.session_id or '-'}")
    print(f"Device State  : {state.device_state.value}")
    print(f"Remote State  : {state.remote_ui_state.value}")
    print(f"Focus         : {_focus_label(state)}")
    print(f"Agent         : {agent_line}")
    print(f"Agent Cache   : {cache_status} / version={state.agents_version or '-'}")
    print(f"LED           : {led}")
    print(f"Turn ID       : {state.turn_id or '-'}")
    print("--- Transcript ---")
    print(state.transcript or "-")
    print("--- Assistant ---")
    print(state.assistant_text or "-")
    print("=" * 72)


async def send_json(ws: websockets.ClientConnection, message: dict[str, Any]) -> None:
    await ws.send(json.dumps(message))


async def tap(controller: SimulatorController) -> None:
    result = await controller.handle_input(DeviceInputEvent.PRESS)
    render_screen(controller.snapshot, result.note or "tap")


async def double_tap(controller: SimulatorController) -> None:
    result = await controller.handle_input(DeviceInputEvent.DOUBLE_PRESS)
    render_screen(controller.snapshot, result.note or "double press")


async def long_press(controller: SimulatorController) -> None:
    result = await controller.handle_input(DeviceInputEvent.LONG_PRESS)
    render_screen(controller.snapshot, result.note or "long press")


async def send_debug_text(
    ws: websockets.ClientConnection,
    controller: SimulatorController,
    text: str,
) -> None:
    cleaned = text.strip()
    if not cleaned:
        render_screen(controller.snapshot, "ignored empty text")
        return

    if controller.snapshot.device_state == DeviceState.LOCKED:
        render_screen(controller.snapshot, "unlock device before sending text")
        return

    if controller.snapshot.device_state != DeviceState.LISTEN:
        result = await controller.handle_input(DeviceInputEvent.PRESS)
        if controller.snapshot.device_state != DeviceState.LISTEN:
            render_screen(controller.snapshot, result.note or "could not enter listen")
            return

    await send_json(
        ws,
        build_message(
            "debug.user_text",
            turn_id=controller.snapshot.turn_id,
            text=cleaned,
        ),
    )
    render_screen(controller.snapshot, "TX debug.user_text")


async def receiver_loop(
    ws: websockets.ClientConnection,
    controller: SimulatorController,
) -> None:
    async for raw in ws:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue

        update = await controller.handle_backend_message(message)
        message_type = str(message.get("type", ""))
        render_screen(controller.snapshot, update.note or f"RX {message_type}")


async def ping_loop(ws: websockets.ClientConnection) -> None:
    while True:
        await asyncio.sleep(15)
        await send_json(ws, build_message("ping"))


async def command_loop(ws: websockets.ClientConnection, controller: SimulatorController) -> None:
    print_help()
    render_screen(controller.snapshot, "connected")

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
            render_screen(controller.snapshot, "local state")
            continue
        if command_line in {"press", "tap", "send"}:
            await tap(controller)
            continue
        if command_line == "double":
            await double_tap(controller)
            continue
        if command_line == "long":
            await long_press(controller)
            continue
        if command_line.startswith("text "):
            await send_debug_text(ws, controller, command_line[5:])
            continue

        render_screen(controller.snapshot, "unknown command")


async def run_simulator(args: argparse.Namespace) -> None:
    state = SimulatorState(device_id=args.device_id)

    async with websockets.connect(args.ws_url) as ws:
        state.connected = True
        controller = SimulatorController(
            state,
            gateway=CliGateway(ws),
            clock=SystemClock(),
        )

        hello_payload = build_message(
            "device.hello",
            device_id=state.device_id,
            firmware_version="0.3.0",
            simulated=True,
            capabilities=["screen", "leds", "button", "audio_in", "audio_out"],
            active_agent=state.active_agent,
        )
        if args.auth_token:
            hello_payload["auth_token"] = args.auth_token

        await send_json(ws, hello_payload)

        receiver_task = asyncio.create_task(receiver_loop(ws, controller))
        ping_task = asyncio.create_task(ping_loop(ws))

        try:
            await command_loop(ws, controller)
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
