"""PiSugar power adapter with safe fallback modes."""

from __future__ import annotations

import json
import shlex
import socket
import subprocess
from typing import Any, Callable

from device_runtime.application.ports import PowerStatus


class NullPowerStatus:
    def read_status(self) -> PowerStatus:
        return PowerStatus(None, None, "none", False, "power adapter disabled")


class PiSugarStatus:
    def __init__(
        self,
        *,
        mode: str = "auto",
        host: str = "127.0.0.1",
        port: int = 8423,
        command: str = "",
        timeout_s: float = 0.5,
        socket_factory: Callable[[tuple[str, int], float], socket.socket] | None = None,
        command_runner: Callable[..., Any] | None = None,
    ) -> None:
        self._mode = mode.strip().lower() or "auto"
        self._host = host
        self._port = port
        self._command = command.strip()
        self._timeout_s = timeout_s
        self._socket_factory = socket_factory or socket.create_connection
        self._command_runner = command_runner or subprocess.run

    def read_status(self) -> PowerStatus:
        if self._mode in {"none", "null", "disabled"}:
            return PowerStatus(None, None, "pisugar", False, "PiSugar disabled")

        errors: list[str] = []
        if self._mode in {"auto", "tcp"}:
            try:
                return self._read_tcp()
            except Exception as exc:
                errors.append(f"tcp {exc}")

        if self._mode in {"auto", "command"} and self._command:
            try:
                return self._read_command()
            except Exception as exc:
                errors.append(f"command {exc}")

        detail = "; ".join(errors) if errors else "PiSugar unavailable"
        return PowerStatus(None, None, "pisugar", False, detail)

    def _read_tcp(self) -> PowerStatus:
        battery_raw = self._send_tcp_command("get battery")
        charging_raw = self._send_tcp_command("get battery_charging")
        battery = self._parse_battery_percent(battery_raw)
        charging = self._parse_bool(charging_raw)
        if battery is None:
            raise RuntimeError(f"battery missing: {battery_raw}")
        return PowerStatus(
            battery_percent=battery,
            charging=charging,
            source="pisugar-tcp",
            available=True,
            detail=f"{self._host}:{self._port}",
        )

    def _read_command(self) -> PowerStatus:
        completed = self._command_runner(
            shlex.split(self._command),
            capture_output=True,
            text=True,
            timeout=self._timeout_s,
            check=True,
        )
        payload = self._parse_payload(completed.stdout)
        battery = self._pick_float(payload, ("battery", "battery_level", "percent", "percentage"))
        charging = self._pick_bool(payload, ("charging", "is_charging", "plugged"))
        if battery is None:
            raise RuntimeError("battery missing")
        return PowerStatus(
            battery_percent=battery,
            charging=charging,
            source="pisugar-command",
            available=True,
            detail=self._command,
        )

    def _send_tcp_command(self, command: str) -> str:
        sock = self._socket_factory((self._host, self._port), self._timeout_s)
        try:
            sock.settimeout(self._timeout_s)
            sock.sendall((command + "\n").encode("utf-8"))
            data = sock.recv(4096).decode("utf-8", errors="replace")
            return data.strip()
        finally:
            sock.close()

    def _parse_payload(self, raw: str) -> dict[str, Any]:
        text = raw.strip()
        if not text:
            return {}
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass

        payload: dict[str, Any] = {}
        for line in text.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            payload[key.strip().lower()] = value.strip()
        return payload

    def _parse_battery_percent(self, raw: str) -> float | None:
        payload = self._parse_payload(raw)
        battery = self._pick_float(payload, ("battery", "battery_level", "percent", "percentage"))
        if battery is not None:
            return max(0.0, min(100.0, battery))
        text = raw.strip().rstrip("%")
        try:
            return max(0.0, min(100.0, float(text)))
        except ValueError:
            return None

    def _parse_bool(self, raw: str) -> bool | None:
        payload = self._parse_payload(raw)
        value = self._pick_bool(payload, ("battery_charging", "charging", "plugged"))
        if value is not None:
            return value
        normalized = raw.strip().lower()
        if normalized in {"true", "1", "yes", "charging", "on"}:
            return True
        if normalized in {"false", "0", "no", "not charging", "off"}:
            return False
        return None

    def _pick_float(self, payload: dict[str, Any], keys: tuple[str, ...]) -> float | None:
        for key in keys:
            if key not in payload:
                continue
            try:
                return float(str(payload[key]).strip().rstrip("%"))
            except ValueError:
                continue
        return None

    def _pick_bool(self, payload: dict[str, Any], keys: tuple[str, ...]) -> bool | None:
        for key in keys:
            if key not in payload:
                continue
            normalized = str(payload[key]).strip().lower()
            if normalized in {"true", "1", "yes", "on", "charging"}:
                return True
            if normalized in {"false", "0", "no", "off", "not charging"}:
                return False
        return None
