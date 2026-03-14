"""Shared simulator ports for backend IO and state observation."""

from __future__ import annotations

from typing import Protocol

from simulator.domain.state import DeviceSnapshot


class BackendGateway(Protocol):
    async def start_listen(self, turn_id: str) -> None: ...

    async def stop_listen(self, turn_id: str) -> None: ...

    async def cancel_listen(self, turn_id: str | None) -> None: ...

    async def request_agents_version(self) -> None: ...

    async def request_agents_list(self) -> None: ...

    async def confirm_agent(self, agent_id: str) -> None: ...


class Clock(Protocol):
    def now(self) -> float: ...


class StateObserver(Protocol):
    def publish(self, snapshot: DeviceSnapshot) -> None: ...
