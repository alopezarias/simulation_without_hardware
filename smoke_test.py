"""Quick end-to-end smoke test for backend websocket protocol."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

import websockets
from dotenv import load_dotenv

from protocol import build_message

load_dotenv()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test for simulation backend")
    parser.add_argument("--ws-url", default=os.getenv("SIM_WS_URL", "ws://127.0.0.1:8000/ws"))
    parser.add_argument("--device-id", default="sim-smoke-001")
    parser.add_argument("--auth-token", default=os.getenv("SIM_DEVICE_AUTH_TOKEN", ""))
    return parser.parse_args()


async def send_json(ws: websockets.ClientConnection, message: dict[str, Any]) -> None:
    await ws.send(json.dumps(message))


async def recv_until(
    ws: websockets.ClientConnection,
    target_type: str,
    timeout_s: float = 8,
) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    deadline = asyncio.get_running_loop().time() + timeout_s

    while asyncio.get_running_loop().time() < deadline:
        remaining = deadline - asyncio.get_running_loop().time()
        raw = await asyncio.wait_for(ws.recv(), timeout=max(remaining, 0.1))
        msg = json.loads(raw)
        seen.append(msg)
        if msg.get("type") == target_type:
            return seen

    raise RuntimeError(f"Timeout waiting for {target_type}; seen={[m.get('type') for m in seen]}")


async def run(args: argparse.Namespace) -> None:
    async with websockets.connect(args.ws_url) as ws:
        hello = build_message(
            "device.hello",
            device_id=args.device_id,
            firmware_version="0.2.0",
            simulated=True,
            capabilities=["screen", "leds", "button", "audio_in", "audio_out"],
        )
        if args.auth_token:
            hello["auth_token"] = args.auth_token

        await send_json(ws, hello)
        ready_msgs = await recv_until(ws, "session.ready")
        print("step1 session.ready OK", [m.get("type") for m in ready_msgs])

        await send_json(ws, build_message("agent.select", agent_id="assistant-tech"))
        selected_msgs = await recv_until(ws, "agent.selected")
        print("step2 agent.select OK", [m.get("type") for m in selected_msgs])

        await send_json(
            ws,
            build_message(
                "recording.start",
                turn_id="turn-smoke-1",
                codec="pcm16",
                sample_rate=16000,
                channels=1,
            ),
        )
        await recv_until(ws, "ui.state")

        await send_json(
            ws,
            build_message("debug.user_text", turn_id="turn-smoke-1", text="prueba de flujo base"),
        )
        await recv_until(ws, "transcript.partial")

        await send_json(ws, build_message("recording.stop", turn_id="turn-smoke-1"))
        final_turn = await recv_until(ws, "assistant.text.final")
        final1 = next(item for item in final_turn if item.get("type") == "assistant.text.final")
        print("step3 turn complete OK", final1.get("text", "")[:80])

        await send_json(
            ws,
            build_message(
                "recording.start",
                turn_id="turn-smoke-2",
                codec="pcm16",
                sample_rate=16000,
                channels=1,
            ),
        )
        await send_json(
            ws,
            build_message(
                "debug.user_text",
                turn_id="turn-smoke-2",
                text="quiero probar interrupcion de respuesta en streaming",
            ),
        )
        await send_json(ws, build_message("recording.stop", turn_id="turn-smoke-2"))

        await recv_until(ws, "assistant.text.partial")
        await send_json(ws, build_message("assistant.interrupt", turn_id="turn-smoke-2"))
        interrupted_stream = await recv_until(ws, "assistant.text.final")
        final2 = next(item for item in interrupted_stream if item.get("type") == "assistant.text.final")
        if not final2.get("interrupted"):
            raise RuntimeError("Expected interrupted=true in second turn")
        print("step4 interrupt OK", final2.get("interrupted"))

        print("SMOKE TEST PASSED")


def main() -> None:
    args = parse_args()

    try:
        asyncio.run(run(args))
    except Exception as exc:
        print(f"SMOKE TEST FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
