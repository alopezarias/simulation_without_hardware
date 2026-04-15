"""PiSugar power adapter with Raspberry-friendly fallback modes."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import socket
from typing import Any, Callable

from device_runtime.application.ports import PowerStatus


LOGGER = logging.getLogger(__name__)


class NullPowerStatus:
    def read_status(self) -> PowerStatus:
        return PowerStatus(None, None, "none", False, "power adapter disabled")


class PiSugarStatus:
    def __init__(
        self,
        *,
        mode: str = "auto",
        socket_path: str = "/tmp/pisugar-server.sock",
        host: str = "127.0.0.1",
        port: int = 8423,
        sysfs_root: str = "/sys/class/power_supply",
        timeout_s: float = 0.5,
        socket_factory: Callable[[tuple[str, int], float], socket.socket] | None = None,
        unix_socket_factory: Callable[[str, float], socket.socket] | None = None,
        file_reader: Callable[[str], str] | None = None,
    ) -> None:
        self._mode = mode.strip().lower() or "auto"
        self._socket_path = socket_path.strip() or "/tmp/pisugar-server.sock"
        self._host = host
        self._port = port
        self._sysfs_root = sysfs_root.strip() or "/sys/class/power_supply"
        self._timeout_s = timeout_s
        self._socket_factory = socket_factory or socket.create_connection
        self._unix_socket_factory = unix_socket_factory or self._create_unix_connection
        self._file_reader = file_reader or self._default_file_reader

    def read_status(self) -> PowerStatus:
        if self._mode in {"none", "null", "disabled"}:
            return PowerStatus(None, None, "pisugar", False, "PiSugar disabled")

        errors: list[str] = []
        for source in self._resolution_chain():
            try:
                if source == "uds":
                    return self._read_uds()
                if source == "tcp":
                    return self._read_tcp()
                if source == "sysfs":
                    return self._read_sysfs()
            except Exception as exc:
                errors.append(f"{source} {exc}")

        if errors:
            LOGGER.warning("PiSugar status unavailable: %s", "; ".join(errors))
        detail = "; ".join(errors) if errors else "PiSugar unavailable"
        return PowerStatus(None, None, "pisugar", False, self._public_detail(detail))

    def _resolution_chain(self) -> tuple[str, ...]:
        if self._mode in {"uds", "tcp", "sysfs"}:
            return (self._mode,)
        return ("uds", "tcp", "sysfs")

    def _read_uds(self) -> PowerStatus:
        battery_raw = self._send_unix_command("get battery")
        battery = self._parse_battery_percent(battery_raw)
        if battery is None:
            raise RuntimeError(f"battery missing: {battery_raw}")
        charging = self._read_optional_bool(self._send_unix_command, "get battery_charging", "get charging")
        return PowerStatus(
            battery_percent=battery,
            charging=charging,
            source="pisugar-uds",
            available=True,
            detail=self._socket_path,
        )

    def _read_tcp(self) -> PowerStatus:
        battery_raw = self._send_tcp_command("get battery")
        battery = self._parse_battery_percent(battery_raw)
        if battery is None:
            raise RuntimeError(f"battery missing: {battery_raw}")
        charging = self._read_optional_bool(self._send_tcp_command, "get battery_charging", "get charging")
        return PowerStatus(
            battery_percent=battery,
            charging=charging,
            source="pisugar-tcp",
            available=True,
            detail=f"{self._host}:{self._port}",
        )

    def _read_sysfs(self) -> PowerStatus:
        candidates = self._sysfs_candidates()
        errors: list[str] = []
        for base in candidates:
            try:
                battery = self._read_capacity(base)
                charging = self._read_sysfs_charging(base)
                return PowerStatus(
                    battery_percent=battery,
                    charging=charging,
                    source="pisugar-sysfs",
                    available=True,
                    detail=str(base),
                )
            except Exception as exc:
                errors.append(f"{base.name}: {exc}")
        raise RuntimeError("; ".join(errors) or f"no power_supply entries under {self._sysfs_root}")

    def _sysfs_candidates(self) -> list[Path]:
        root = Path(self._sysfs_root)
        names = ("pisugar-battery", "BAT0", "battery")
        candidates = [root / name for name in names]
        if root.exists():
            candidates.extend(path for path in root.iterdir() if path.is_dir())
        unique: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            unique.append(candidate)
        return unique

    def _read_capacity(self, base: Path) -> float:
        raw = self._read_file(base / "capacity")
        percent = self._parse_battery_percent(raw)
        if percent is None:
            raise RuntimeError("capacity missing")
        return percent

    def _read_sysfs_charging(self, base: Path) -> bool | None:
        status_path = base / "status"
        try:
            normalized = self._read_file(status_path).strip().lower()
            if normalized in {"charging", "full"}:
                return True
            if normalized in {"discharging", "not charging"}:
                return False
        except Exception:
            pass
        online_path = base / "online"
        try:
            return self._parse_bool(self._read_file(online_path))
        except Exception:
            pass
        return None

    def _read_optional_bool(self, reader: Callable[[str], str], *commands: str) -> bool | None:
        errors: list[str] = []
        for command in commands:
            try:
                return self._parse_bool(reader(command))
            except Exception as exc:
                errors.append(f"{command}: {exc}")
        if errors:
            LOGGER.info("PiSugar charging state unavailable: %s", "; ".join(errors))
        return None

    def _send_tcp_command(self, command: str) -> str:
        sock = self._socket_factory((self._host, self._port), self._timeout_s)
        return self._exchange(sock, command)

    def _send_unix_command(self, command: str) -> str:
        sock = self._unix_socket_factory(self._socket_path, self._timeout_s)
        return self._exchange(sock, command)

    def _exchange(self, sock: socket.socket, command: str) -> str:
        try:
            sock.settimeout(self._timeout_s)
            sock.sendall((command + "\n").encode("utf-8"))
            data = sock.recv(4096).decode("utf-8", errors="replace")
            return data.strip()
        finally:
            sock.close()

    def _create_unix_connection(self, path: str, timeout_s: float) -> socket.socket:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout_s)
            sock.connect(path)
            return sock
        except Exception:
            sock.close()
            raise

    def _read_file(self, path: Path) -> str:
        return self._file_reader(str(path)).strip()

    def _default_file_reader(self, path: str) -> str:
        return Path(path).read_text(encoding="utf-8")

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
        battery = self._pick_float(payload, ("battery", "battery_level", "percent", "percentage", "capacity"))
        if battery is not None:
            return max(0.0, min(100.0, battery))
        text = raw.strip().rstrip("%")
        try:
            return max(0.0, min(100.0, float(text)))
        except ValueError:
            match = re.search(r"(-?\d+(?:\.\d+)?)", text)
            if match is None:
                return None
            return max(0.0, min(100.0, float(match.group(1))))

    def _parse_bool(self, raw: str) -> bool | None:
        payload = self._parse_payload(raw)
        value = self._pick_bool(payload, ("battery_charging", "charging", "plugged", "online", "status"))
        if value is not None:
            return value
        normalized = raw.strip().lower()
        if normalized in {"true", "1", "yes", "charging", "on", "full"}:
            return True
        if normalized in {"false", "0", "no", "not charging", "off", "discharging"}:
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
            if normalized in {"true", "1", "yes", "on", "charging", "full"}:
                return True
            if normalized in {"false", "0", "no", "off", "not charging", "discharging"}:
                return False
        return None

    def _public_detail(self, detail: str) -> str:
        normalized = detail.strip()
        if not normalized:
            return "Battery unavailable"
        lowered = normalized.lower()
        if any(token in lowered for token in ("connection", "refused", "timeout", "missing", "no such file", "enoent")):
            return "Battery unavailable"
        return normalized
