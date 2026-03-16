"""Standalone smoke checks for Raspberry deployments."""

from __future__ import annotations

import argparse
import json
import socket
from pathlib import Path
from urllib.parse import urlparse

import device_runtime

from device_runtime.entrypoints.raspi_main import build_hello_payload, build_runtime


def run_smoke(*, skip_network: bool, timeout: float) -> dict[str, object]:
    runtime = build_runtime()
    hello = build_hello_payload(runtime)
    config = runtime.config
    package_root = Path(next(iter(device_runtime.__path__))).resolve()
    network_ok: bool | None = None
    network_detail = "skipped"

    if not skip_network:
        parsed = urlparse(config.ws_url)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        try:
            with socket.create_connection((host, port), timeout=timeout):
                network_ok = True
                network_detail = f"tcp ok {host}:{port}"
        except OSError as exc:
            network_ok = False
            network_detail = f"tcp failed {host}:{port}: {exc}"

    return {
        "device_id": config.device_id,
        "ws_url": config.ws_url,
        "hello_type": hello["type"],
        "package_root": str(package_root),
        "warnings": list(runtime.snapshot.warnings),
        "network_ok": network_ok,
        "network_detail": network_detail,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke checks for installed Raspberry runtime")
    parser.add_argument("--skip-network", action="store_true", help="Skip TCP reachability check to DEVICE_WS_URL")
    parser.add_argument("--timeout", type=float, default=3.0, help="Socket timeout in seconds")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = run_smoke(skip_network=args.skip_network, timeout=args.timeout)
    if args.json:
        print(json.dumps(report))
    else:
        print("DEVICE RUNTIME SMOKE")
        print(f"- device_id: {report['device_id']}")
        print(f"- ws_url: {report['ws_url']}")
        print(f"- hello_type: {report['hello_type']}")
        print(f"- package_root: {report['package_root']}")
        print(f"- network: {report['network_detail']}")
        print(f"- warnings: {report['warnings'] or ['none']}")
    if report["network_ok"] is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
