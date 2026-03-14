"""Quick smoke test for the simulator device-state-machine flow."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv

from simulator.qa import scenario_runner

load_dotenv()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test for simulation backend")
    parser.add_argument("--ws-url", default=os.getenv("SIM_WS_URL", "ws://127.0.0.1:8000/ws"))
    parser.add_argument("--device-id", default="sim-smoke-001")
    parser.add_argument("--auth-token", default=os.getenv("SIM_DEVICE_AUTH_TOKEN", ""))
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    scenario_args = argparse.Namespace(
        ws_url=args.ws_url,
        device_id=args.device_id,
        auth_token=args.auth_token,
        scenario="all",
        report="",
    )
    results = await scenario_runner.run_scenarios(scenario_args)
    scenario_runner.print_results(results)

    failed = [result for result in results if not result.passed]
    if failed:
        joined = "; ".join(f"{result.name}: {result.details}" for result in failed)
        raise RuntimeError(joined)

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
