"""Shared controller that bridges device-state transitions and backend IO."""

from __future__ import annotations

from typing import Any

from simulator.application.ports import BackendGateway, Clock, StateObserver
from simulator.application.services.device_state_machine import DeviceStateMachine, TransitionResult
from simulator.application.services.protocol_service import ProtocolService, ProtocolUpdate
from simulator.domain.events import DeviceInputEvent, DomainEffect, EffectPayload
from simulator.domain.state import DeviceSnapshot


class SimulatorController:
    """Coordinates local device transitions, remote effects and incoming protocol."""

    def __init__(
        self,
        snapshot: DeviceSnapshot,
        *,
        gateway: BackendGateway,
        clock: Clock,
        observer: StateObserver | None = None,
        state_machine: DeviceStateMachine | None = None,
        protocol_service: ProtocolService | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._gateway = gateway
        self._clock = clock
        self._observer = observer
        self._state_machine = state_machine or DeviceStateMachine()
        self._protocol_service = protocol_service or ProtocolService()

    @property
    def snapshot(self) -> DeviceSnapshot:
        return self._snapshot

    async def handle_input(self, event: DeviceInputEvent) -> TransitionResult:
        result = self._state_machine.handle_event(
            self._snapshot,
            event,
            connected=self._snapshot.connected,
            now=self._clock.now(),
        )
        self._snapshot = result.snapshot
        self._publish()
        await self._apply_effects(result.effects)
        return result

    async def handle_backend_message(self, message: dict[str, Any]) -> ProtocolUpdate:
        update = self._protocol_service.apply_message(self._snapshot, message, now=self._clock.now())
        self._snapshot = update.snapshot
        self._publish()
        await self._apply_effects(update.effects)
        return update

    def replace_snapshot(self, snapshot: DeviceSnapshot) -> None:
        self._snapshot = snapshot
        self._publish()

    def _publish(self) -> None:
        if self._observer is not None:
            self._observer.publish(self._snapshot)

    async def _apply_effects(self, effects: list[EffectPayload]) -> None:
        for effect in effects:
            await self._apply_effect(effect)

    async def _apply_effect(self, effect: EffectPayload) -> None:
        if effect.kind == DomainEffect.START_LISTEN:
            await self._gateway.start_listen(str(effect.data["turn_id"]))
            return
        if effect.kind == DomainEffect.STOP_LISTEN_FINALIZE:
            turn_id = effect.data.get("turn_id")
            if isinstance(turn_id, str) and turn_id:
                await self._gateway.stop_listen(turn_id)
            return
        if effect.kind == DomainEffect.STOP_LISTEN_CANCEL:
            turn_id = effect.data.get("turn_id")
            await self._gateway.cancel_listen(turn_id if isinstance(turn_id, str) and turn_id else None)
            return
        if effect.kind == DomainEffect.REQUEST_AGENTS_VERSION:
            await self._gateway.request_agents_version()
            return
        if effect.kind == DomainEffect.REQUEST_AGENTS_LIST:
            await self._gateway.request_agents_list()
            return
        if effect.kind == DomainEffect.CONFIRM_AGENT:
            await self._gateway.confirm_agent(str(effect.data["agent_id"]))
