"""Shared websocket transport with hello, gating, keepalive and reconnect."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import websockets

from backend.shared.protocol import build_message


class SessionNotReadyError(RuntimeError):
    """Raised when a functional message is sent before `session.ready`."""


class WebSocketTransport:
    def __init__(
        self,
        ws_url: str,
        *,
        hello_payload: dict[str, Any],
        reconnect_initial_ms: int = 1000,
        reconnect_max_ms: int = 6000,
        keepalive_interval_s: float = 15.0,
        connect_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self._ws_url = ws_url
        self._hello_payload = dict(hello_payload)
        self._reconnect_initial_s = reconnect_initial_ms / 1000
        self._reconnect_max_s = reconnect_max_ms / 1000
        self._keepalive_interval_s = keepalive_interval_s
        self._connect_factory = connect_factory
        self._message_handler: Callable[[dict[str, Any]], None] | None = None
        self._connection_handler: Callable[[str, str | None], None] | None = None
        self._outbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._active_ws: Any | None = None
        self._closed = False
        self._session_ready = False

    def set_message_handler(self, handler: Callable[[dict[str, Any]], None]) -> None:
        self._message_handler = handler

    def set_connection_handler(self, handler: Callable[[str, str | None], None]) -> None:
        self._connection_handler = handler

    @property
    def session_ready(self) -> bool:
        return self._session_ready

    async def connect(self) -> None:
        reconnect_delay = self._reconnect_initial_s
        self._closed = False
        while not self._closed:
            self._session_ready = False
            self._drop_pending_messages()
            try:
                connector = self._connect_factory or websockets.connect
                async with connector(self._ws_url) as ws:
                    self._active_ws = ws
                    self._notify_connection("connected", None)
                    await ws.send(json.dumps(self._hello_payload))
                    sender = asyncio.create_task(self._send_loop(ws))
                    receiver = asyncio.create_task(self._recv_loop(ws))
                    pinger = asyncio.create_task(self._keepalive_loop(ws))
                    done, pending = await asyncio.wait(
                        {sender, receiver, pinger},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    for task in done:
                        task.result()
                    self._active_ws = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._closed:
                    break
                self._notify_connection("disconnected", str(exc))
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.8, self._reconnect_max_s)
            else:
                if self._closed:
                    break
                self._notify_connection("disconnected", None)
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.8, self._reconnect_max_s)
        self._notify_connection("stopped", None)

    async def send(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type", ""))
        if message_type not in {"device.hello", "ping"} and not self._session_ready:
            raise SessionNotReadyError(f"{message_type or 'message'} blocked before session.ready")
        await self._outbox.put(dict(message))

    def close(self) -> None:
        self._closed = True
        active_ws = self._active_ws
        if active_ws is None:
            return
        close = getattr(active_ws, "close", None)
        if close is None:
            return
        result = close()
        if asyncio.iscoroutine(result):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            loop.create_task(result)

    async def _send_loop(self, ws: Any) -> None:
        while not self._closed:
            try:
                message = await asyncio.wait_for(self._outbox.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            await ws.send(json.dumps(message))

    async def _recv_loop(self, ws: Any) -> None:
        async for raw in ws:
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if str(message.get("type", "")) == "session.ready":
                self._session_ready = True
            if self._message_handler is not None:
                self._message_handler(message)

    async def _keepalive_loop(self, ws: Any) -> None:
        while not self._closed:
            await asyncio.sleep(self._keepalive_interval_s)
            await ws.send(json.dumps(build_message("ping")))

    def _notify_connection(self, status: str, detail: str | None) -> None:
        if self._connection_handler is not None:
            self._connection_handler(status, detail)

    def _drop_pending_messages(self) -> None:
        while True:
            try:
                self._outbox.get_nowait()
            except asyncio.QueueEmpty:
                return
