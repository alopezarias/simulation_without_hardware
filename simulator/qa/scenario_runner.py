"""Run repeatable controller-driven scenarios against the websocket backend."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

import websockets
from dotenv import load_dotenv

from simulator.application.ports import BackendGateway, Clock
from simulator.application.services import SimulatorController
from simulator.domain.events import DeviceInputEvent, DeviceState
from simulator.domain.state import SimulatorState
from simulator.shared.protocol import build_message

load_dotenv()


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    duration_ms: int
    details: str


class ScenarioClock(Clock):
    def now(self) -> float:
        return time.monotonic()


class ScenarioGateway(BackendGateway):
    def __init__(self, ws: websockets.ClientConnection, sent_messages: list[dict[str, Any]]) -> None:
        self._ws = ws
        self._sent_messages = sent_messages

    async def _send(self, message: dict[str, Any]) -> None:
        self._sent_messages.append(message)
        await self._ws.send(json.dumps(message))

    async def start_listen(self, turn_id: str) -> None:
        await self._send(
            build_message(
                "recording.start",
                turn_id=turn_id,
                codec="pcm16",
                sample_rate=16000,
                channels=1,
            )
        )

    async def stop_listen(self, turn_id: str) -> None:
        await self._send(build_message("recording.stop", turn_id=turn_id))

    async def cancel_listen(self, turn_id: str | None) -> None:
        payload: dict[str, Any] = {}
        if turn_id:
            payload["turn_id"] = turn_id
        await self._send(build_message("recording.cancel", **payload))

    async def request_agents_version(self) -> None:
        await self._send(build_message("agents.version.request"))

    async def request_agents_list(self) -> None:
        await self._send(build_message("agents.list.request"))

    async def confirm_agent(self, agent_id: str) -> None:
        await self._send(build_message("agent.select", agent_id=agent_id))


class ScenarioHarness:
    def __init__(
        self,
        ws: websockets.ClientConnection,
        device_id: str,
        sent_messages: list[dict[str, Any]],
    ) -> None:
        self.ws = ws
        self.controller = SimulatorController(
            SimulatorState(device_id=device_id),
            gateway=ScenarioGateway(ws, sent_messages),
            clock=ScenarioClock(),
        )
        self.sent_messages = sent_messages
        self.received_messages: list[dict[str, Any]] = []

    async def open_session(self, auth_token: str) -> None:
        hello = build_message(
            "device.hello",
            device_id=self.controller.snapshot.device_id,
            firmware_version="0.3.0",
            simulated=True,
            capabilities=["screen", "leds", "button", "audio_in", "audio_out"],
            active_agent=self.controller.snapshot.active_agent,
        )
        if auth_token:
            hello["auth_token"] = auth_token
        await self.ws.send(json.dumps(hello))
        await self.recv_until(lambda msg: msg.get("type") == "session.ready", timeout_s=8.0)

    async def recv_until(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        timeout_s: float,
    ) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout_s
        seen: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            remain = max(0.1, deadline - time.monotonic())
            raw = await asyncio.wait_for(self.ws.recv(), timeout=remain)
            message = json.loads(raw)
            seen.append(message)
            self.received_messages.append(message)
            await self.controller.handle_backend_message(message)
            if predicate(message):
                return seen
        raise RuntimeError(f"Timeout waiting for expected backend message; seen={[m.get('type') for m in seen]}")

    async def press(self, event: DeviceInputEvent) -> None:
        await self.controller.handle_input(event)

    async def send_debug_text(self, text: str) -> None:
        await self.ws.send(
            json.dumps(build_message("debug.user_text", turn_id=self.controller.snapshot.turn_id, text=text))
        )
        self.sent_messages.append({"type": "debug.user_text", "turn_id": self.controller.snapshot.turn_id, "text": text})


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
        choices=["locked-ready", "listen-agents", "cache-refresh", "agent-ack", "all"],
        default="all",
        help="Scenario to run",
    )
    parser.add_argument("--report", default="", help="Optional JSON report output path")
    return parser.parse_args()


async def run_locked_ready(harness: ScenarioHarness) -> ScenarioResult:
    started = time.monotonic()
    try:
        await harness.press(DeviceInputEvent.LONG_PRESS)
        if harness.controller.snapshot.device_state != DeviceState.READY:
            raise RuntimeError("Device did not unlock to READY")

        await harness.press(DeviceInputEvent.PRESS)
        if harness.controller.snapshot.device_state != DeviceState.LISTEN:
            raise RuntimeError("READY -> LISTEN failed")

        await harness.send_debug_text("Prueba del flujo principal")
        await harness.press(DeviceInputEvent.PRESS)
        await harness.recv_until(lambda msg: msg.get("type") == "assistant.text.final", timeout_s=20.0)

        if harness.controller.snapshot.device_state != DeviceState.READY:
            raise RuntimeError("LISTEN did not return to READY after finalize")

        return ScenarioResult(
            name="locked-ready",
            passed=True,
            duration_ms=int((time.monotonic() - started) * 1000),
            details="unlock, listen, finalize and response flow succeeded",
        )
    except Exception as exc:
        return ScenarioResult(
            name="locked-ready",
            passed=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            details=str(exc),
        )


async def run_listen_agents(harness: ScenarioHarness) -> ScenarioResult:
    started = time.monotonic()
    try:
        if harness.controller.snapshot.device_state == DeviceState.LOCKED:
            await harness.press(DeviceInputEvent.LONG_PRESS)
        harness.sent_messages.clear()

        await harness.press(DeviceInputEvent.PRESS)
        await harness.press(DeviceInputEvent.LONG_PRESS)

        if harness.controller.snapshot.device_state != DeviceState.AGENTS:
            raise RuntimeError("LISTEN -> AGENTS failed")

        sent_types = [message["type"] for message in harness.sent_messages]
        if sent_types.count("recording.cancel") != 1:
            raise RuntimeError(f"Expected one recording.cancel before AGENTS, got {sent_types}")
        if "agents.version.request" in sent_types or "agents.list.request" in sent_types:
            raise RuntimeError("Warm cache path should not refresh agents on first AGENTS entry")

        harness.sent_messages.clear()
        await harness.press(DeviceInputEvent.PRESS)
        if harness.sent_messages:
            raise RuntimeError("Navigating AGENTS with Press emitted unexpected remote traffic")

        await harness.press(DeviceInputEvent.DOUBLE_PRESS)
        if harness.controller.snapshot.device_state != DeviceState.READY:
            raise RuntimeError("AGENTS cancel did not return to READY")

        return ScenarioResult(
            name="listen-agents",
            passed=True,
            duration_ms=int((time.monotonic() - started) * 1000),
            details="AGENTS entry cancels listen first and navigation stays local",
        )
    except Exception as exc:
        return ScenarioResult(
            name="listen-agents",
            passed=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            details=str(exc),
        )


async def run_cache_refresh(harness: ScenarioHarness) -> ScenarioResult:
    started = time.monotonic()
    try:
        if harness.controller.snapshot.device_state == DeviceState.LOCKED:
            await harness.press(DeviceInputEvent.LONG_PRESS)
        harness.controller.snapshot.agent_cache.expires_at = time.monotonic() - 1
        harness.sent_messages.clear()

        await harness.press(DeviceInputEvent.PRESS)
        await harness.press(DeviceInputEvent.LONG_PRESS)
        await harness.recv_until(
            lambda msg: msg.get("type") in {"agents.version.response", "agents.list.response"},
            timeout_s=8.0,
        )

        sent_types = [message["type"] for message in harness.sent_messages]
        if "agents.version.request" not in sent_types:
            raise RuntimeError(f"Expected agents.version.request, got {sent_types}")
        if harness.controller.snapshot.device_state != DeviceState.AGENTS:
            raise RuntimeError("Refresh path should stay in AGENTS")

        return ScenarioResult(
            name="cache-refresh",
            passed=True,
            duration_ms=int((time.monotonic() - started) * 1000),
            details=f"refresh path emitted {sent_types}",
        )
    except Exception as exc:
        return ScenarioResult(
            name="cache-refresh",
            passed=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            details=str(exc),
        )


async def run_agent_ack(harness: ScenarioHarness) -> ScenarioResult:
    started = time.monotonic()
    try:
        if harness.controller.snapshot.device_state == DeviceState.LOCKED:
            await harness.press(DeviceInputEvent.LONG_PRESS)
        harness.controller.snapshot.agent_cache.expires_at = time.monotonic() + 60
        harness.sent_messages.clear()

        await harness.press(DeviceInputEvent.PRESS)
        await harness.press(DeviceInputEvent.LONG_PRESS)
        original_agent = harness.controller.snapshot.active_agent
        await harness.press(DeviceInputEvent.PRESS)
        focused_agent = harness.controller.snapshot.focused_agent
        if focused_agent == original_agent:
            raise RuntimeError("Scenario did not move focus to a different agent")

        await harness.press(DeviceInputEvent.LONG_PRESS)
        if harness.controller.snapshot.active_agent != original_agent:
            raise RuntimeError("Agent changed before backend ACK")
        if harness.controller.snapshot.pending_agent_ack != focused_agent:
            raise RuntimeError("Pending ACK marker missing after agent confirm")

        await harness.recv_until(lambda msg: msg.get("type") == "agent.selected", timeout_s=8.0)
        if harness.controller.snapshot.active_agent != focused_agent:
            raise RuntimeError("Active agent did not update after ACK")
        if harness.controller.snapshot.pending_agent_ack is not None:
            raise RuntimeError("Pending ACK was not cleared")

        return ScenarioResult(
            name="agent-ack",
            passed=True,
            duration_ms=int((time.monotonic() - started) * 1000),
            details=f"agent.select confirmed only after ACK for {focused_agent}",
        )
    except Exception as exc:
        return ScenarioResult(
            name="agent-ack",
            passed=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            details=str(exc),
        )


async def run_named_scenario(name: str, args: argparse.Namespace) -> ScenarioResult:
    sent_messages: list[dict[str, Any]] = []
    async with websockets.connect(args.ws_url) as ws:
        harness = ScenarioHarness(ws, device_id=args.device_id, sent_messages=sent_messages)
        await harness.open_session(auth_token=args.auth_token)
        if name == "locked-ready":
            return await run_locked_ready(harness)
        if name == "listen-agents":
            return await run_listen_agents(harness)
        if name == "cache-refresh":
            return await run_cache_refresh(harness)
        return await run_agent_ack(harness)


async def run_scenarios(args: argparse.Namespace) -> list[ScenarioResult]:
    scenario_names = ["locked-ready", "listen-agents", "cache-refresh", "agent-ack"]
    if args.scenario != "all":
        scenario_names = [args.scenario]
    return [await run_named_scenario(name, args) for name in scenario_names]


def print_results(results: list[ScenarioResult]) -> None:
    print("SIMULATION RESULTS")
    for result in results:
        status = "OK" if result.passed else "FAIL"
        print(f"- {result.name:14s} {status:4s} {result.duration_ms:5d} ms | {result.details}")
    passed = sum(1 for item in results if item.passed)
    print(f"Summary: {passed}/{len(results)} passed")


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
