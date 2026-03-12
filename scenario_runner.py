"""Run repeatable simulation scenarios against the websocket backend."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Any

import websockets
from dotenv import load_dotenv

from protocol import build_message

load_dotenv()


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    duration_ms: int
    details: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulation scenario runner")
    parser.add_argument(
        "--ws-url",
        default=os.getenv("SIM_WS_URL", "ws://127.0.0.1:8000/ws"),
        help="Backend websocket URL",
    )
    parser.add_argument(
        "--device-id",
        default=os.getenv("SIM_DEVICE_ID", "sim-runner-001"),
        help="Device id",
    )
    parser.add_argument(
        "--auth-token",
        default=os.getenv("SIM_DEVICE_AUTH_TOKEN", ""),
        help="Optional auth token",
    )
    parser.add_argument(
        "--scenario",
        choices=["baseline", "interrupt", "cancel", "all"],
        default="all",
        help="Scenario to run",
    )
    parser.add_argument(
        "--report",
        default="",
        help="Optional JSON report output path",
    )
    return parser.parse_args()


async def send_json(ws: websockets.ClientConnection, message: dict[str, Any]) -> None:
    await ws.send(json.dumps(message))


async def recv_until(
    ws: websockets.ClientConnection,
    target_type: str,
    timeout_s: float = 8.0,
) -> list[dict[str, Any]]:
    end = time.monotonic() + timeout_s
    seen: list[dict[str, Any]] = []

    while time.monotonic() < end:
        remain = max(0.1, end - time.monotonic())
        raw = await asyncio.wait_for(ws.recv(), timeout=remain)
        msg = json.loads(raw)
        seen.append(msg)
        if msg.get("type") == target_type:
            return seen

    raise RuntimeError(f"Timeout waiting for '{target_type}'. Seen: {[m.get('type') for m in seen]}")


async def open_session(
    ws: websockets.ClientConnection,
    device_id: str,
    auth_token: str,
) -> None:
    hello = build_message(
        "device.hello",
        device_id=device_id,
        firmware_version="0.3.0",
        simulated=True,
        capabilities=["screen", "leds", "button", "audio_in", "audio_out"],
        active_agent="assistant-general",
    )
    if auth_token:
        hello["auth_token"] = auth_token

    await send_json(ws, hello)
    await recv_until(ws, "session.ready", timeout_s=8)


async def run_baseline(
    ws: websockets.ClientConnection,
    prefix: str,
) -> ScenarioResult:
    started = time.monotonic()
    turn_id = f"{prefix}-baseline"

    try:
        await send_json(ws, build_message("recording.start", turn_id=turn_id, codec="pcm16", sample_rate=16000, channels=1))
        await asyncio.sleep(0.3)
        await send_json(ws, build_message("debug.user_text", turn_id=turn_id, text="Prueba de turno baseline"))
        await asyncio.sleep(0.25)
        await send_json(ws, build_message("recording.stop", turn_id=turn_id))

        stream = await recv_until(ws, "assistant.text.final", timeout_s=14)
        final = next(item for item in stream if item.get("type") == "assistant.text.final")
        text = str(final.get("text", "")).strip()
        if not text:
            raise RuntimeError("assistant.text.final returned empty text")

        latency = final.get("latency_ms")
        duration_ms = int((time.monotonic() - started) * 1000)
        return ScenarioResult(
            name="baseline",
            passed=True,
            duration_ms=duration_ms,
            details=f"final_text_len={len(text)} latency_ms={latency}",
        )

    except Exception as exc:
        return ScenarioResult(
            name="baseline",
            passed=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            details=str(exc),
        )


async def run_interrupt(
    ws: websockets.ClientConnection,
    prefix: str,
) -> ScenarioResult:
    started = time.monotonic()
    turn_id = f"{prefix}-interrupt"

    try:
        await send_json(ws, build_message("recording.start", turn_id=turn_id, codec="pcm16", sample_rate=16000, channels=1))
        await asyncio.sleep(0.25)
        await send_json(
            ws,
            build_message(
                "debug.user_text",
                turn_id=turn_id,
                text="Necesito una respuesta larga para comprobar interrupcion durante speaking",
            ),
        )
        await asyncio.sleep(0.2)
        await send_json(ws, build_message("recording.stop", turn_id=turn_id))

        await recv_until(ws, "assistant.text.partial", timeout_s=8)
        await send_json(ws, build_message("assistant.interrupt", turn_id=turn_id))

        stream = await recv_until(ws, "assistant.text.final", timeout_s=10)
        final = next(item for item in stream if item.get("type") == "assistant.text.final")
        interrupted = bool(final.get("interrupted"))
        if not interrupted:
            raise RuntimeError("Expected interrupted=true in assistant.text.final")

        return ScenarioResult(
            name="interrupt",
            passed=True,
            duration_ms=int((time.monotonic() - started) * 1000),
            details="assistant interrupt confirmed",
        )

    except Exception as exc:
        return ScenarioResult(
            name="interrupt",
            passed=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            details=str(exc),
        )


async def run_cancel(
    ws: websockets.ClientConnection,
    prefix: str,
) -> ScenarioResult:
    started = time.monotonic()
    turn_id = f"{prefix}-cancel"

    try:
        await send_json(ws, build_message("recording.start", turn_id=turn_id, codec="pcm16", sample_rate=16000, channels=1))
        await asyncio.sleep(0.2)
        await send_json(ws, build_message("debug.user_text", turn_id=turn_id, text="Turno que se cancela"))
        await asyncio.sleep(0.2)
        await send_json(ws, build_message("recording.cancel", turn_id=turn_id))

        end = time.monotonic() + 6
        idle_seen = False
        while time.monotonic() < end:
            remain = max(0.1, end - time.monotonic())
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=remain))
            if msg.get("type") == "ui.state" and msg.get("state") == "idle":
                idle_seen = True
                break
        if not idle_seen:
            raise RuntimeError("Did not observe ui.state=idle after cancel")

        return ScenarioResult(
            name="cancel",
            passed=True,
            duration_ms=int((time.monotonic() - started) * 1000),
            details="recording.cancel returned to idle",
        )

    except Exception as exc:
        return ScenarioResult(
            name="cancel",
            passed=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            details=str(exc),
        )


async def run_scenarios(args: argparse.Namespace) -> list[ScenarioResult]:
    scenarios: list[str]
    if args.scenario == "all":
        scenarios = ["baseline", "interrupt", "cancel"]
    else:
        scenarios = [args.scenario]

    results: list[ScenarioResult] = []

    for index, scenario_name in enumerate(scenarios, start=1):
        async with websockets.connect(args.ws_url) as ws:
            await open_session(ws, device_id=args.device_id, auth_token=args.auth_token)
            prefix = f"sim{index}-{int(time.time())}"

            if scenario_name == "baseline":
                result = await run_baseline(ws, prefix)
            elif scenario_name == "interrupt":
                result = await run_interrupt(ws, prefix)
            else:
                result = await run_cancel(ws, prefix)

            results.append(result)

    return results


def print_results(results: list[ScenarioResult]) -> None:
    print("SIMULATION RESULTS")
    for result in results:
        status = "OK" if result.passed else "FAIL"
        print(f"- {result.name:10s} {status:4s} {result.duration_ms:5d} ms | {result.details}")

    passed = sum(1 for item in results if item.passed)
    total = len(results)
    print(f"Summary: {passed}/{total} passed")


def save_report(path: str, args: argparse.Namespace, results: list[ScenarioResult]) -> None:
    payload = {
        "timestamp": int(time.time()),
        "ws_url": args.ws_url,
        "device_id": args.device_id,
        "scenario": args.scenario,
        "results": [asdict(item) for item in results],
    }

    with open(path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2)


def main() -> None:
    args = parse_args()
    results = asyncio.run(run_scenarios(args))
    print_results(results)

    if args.report:
        save_report(args.report, args, results)
        print(f"Report saved to: {args.report}")

    if any(not item.passed for item in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
