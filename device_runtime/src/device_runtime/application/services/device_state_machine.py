"""Pure local device-state transitions for the shared runtime."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Callable

from device_runtime.domain.events import DeviceInputEvent, DeviceState, DomainEffect, EffectPayload
from device_runtime.domain.state import DeviceSnapshot
from device_runtime.protocol import new_turn_id

AGENT_CACHE_TTL_SECONDS = 300.0


@dataclass(slots=True)
class TransitionResult:
    snapshot: DeviceSnapshot
    effects: list[EffectPayload] = field(default_factory=list)
    note: str = ""


class DeviceStateMachine:
    """Applies local transitions without depending on transport or UI code."""

    def __init__(
        self,
        ttl_seconds: float = AGENT_CACHE_TTL_SECONDS,
        turn_id_factory: Callable[[], str] = new_turn_id,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._turn_id_factory = turn_id_factory

    def handle_event(
        self,
        snapshot: DeviceSnapshot,
        event: DeviceInputEvent,
        *,
        connected: bool,
        now: float,
    ) -> TransitionResult:
        current = deepcopy(snapshot)
        state = current.device_state

        if state == DeviceState.LOCKED:
            return self._from_locked(current, event)
        if state == DeviceState.READY:
            return self._from_ready(current, event, connected=connected)
        if state == DeviceState.LISTEN:
            return self._from_listen(current, event, now=now)
        if state == DeviceState.MENU:
            return self._from_menu(current, event)
        if state == DeviceState.MODE:
            return self._from_mode(current, event)
        if state == DeviceState.AGENTS:
            return self._from_agents(current, event)
        return TransitionResult(snapshot=current, note="unsupported state")

    def _from_locked(self, snapshot: DeviceSnapshot, event: DeviceInputEvent) -> TransitionResult:
        if event == DeviceInputEvent.LONG_PRESS:
            snapshot.device_state = DeviceState.READY
            return TransitionResult(snapshot=snapshot, note="device unlocked")
        return TransitionResult(snapshot=snapshot, note="ignored while locked")

    def _from_ready(
        self,
        snapshot: DeviceSnapshot,
        event: DeviceInputEvent,
        *,
        connected: bool,
    ) -> TransitionResult:
        if event == DeviceInputEvent.PRESS:
            if not connected:
                return TransitionResult(snapshot=snapshot, note="backend disconnected")
            if not snapshot.session_id:
                return TransitionResult(snapshot=snapshot, note="backend not ready")
            snapshot.device_state = DeviceState.LISTEN
            snapshot.listening_active = True
            snapshot.turn_id = self._turn_id_factory()
            snapshot.transcript = ""
            snapshot.assistant_text = ""
            snapshot.last_latency_ms = None
            return TransitionResult(
                snapshot=snapshot,
                effects=[EffectPayload(DomainEffect.START_LISTEN, {"turn_id": snapshot.turn_id})],
                note="listen started",
            )

        if event == DeviceInputEvent.DOUBLE_PRESS:
            snapshot.device_state = DeviceState.MENU
            return TransitionResult(snapshot=snapshot, note="menu opened")

        if event == DeviceInputEvent.LONG_PRESS:
            snapshot.device_state = DeviceState.LOCKED
            return TransitionResult(snapshot=snapshot, note="device locked")

        return TransitionResult(snapshot=snapshot)

    def _from_listen(
        self,
        snapshot: DeviceSnapshot,
        event: DeviceInputEvent,
        *,
        now: float,
    ) -> TransitionResult:
        if event == DeviceInputEvent.PRESS:
            snapshot.device_state = DeviceState.READY
            snapshot.listening_active = False
            return TransitionResult(
                snapshot=snapshot,
                effects=[EffectPayload(DomainEffect.STOP_LISTEN_FINALIZE, {"turn_id": snapshot.turn_id})],
                note="listen finalized",
            )

        if event == DeviceInputEvent.DOUBLE_PRESS:
            cancel_turn_id = snapshot.turn_id
            snapshot.device_state = DeviceState.READY
            snapshot.listening_active = False
            snapshot.turn_id = None
            return TransitionResult(
                snapshot=snapshot,
                effects=[EffectPayload(DomainEffect.STOP_LISTEN_CANCEL, {"turn_id": cancel_turn_id})],
                note="listen canceled",
            )

        if event == DeviceInputEvent.LONG_PRESS:
            cancel_turn_id = snapshot.turn_id
            snapshot.device_state = DeviceState.AGENTS
            snapshot.listening_active = False
            snapshot.turn_id = None
            self._focus_current_agent(snapshot)
            effects = [EffectPayload(DomainEffect.STOP_LISTEN_CANCEL, {"turn_id": cancel_turn_id})]
            effects.extend(self._agents_sync_effects(snapshot, now=now))
            return TransitionResult(snapshot=snapshot, effects=effects, note="agent selector opened")

        return TransitionResult(snapshot=snapshot)

    def _from_menu(self, snapshot: DeviceSnapshot, event: DeviceInputEvent) -> TransitionResult:
        if event == DeviceInputEvent.PRESS:
            options = snapshot.navigation.menu_options or ["MODE"]
            snapshot.navigation.menu_index = (snapshot.navigation.menu_index + 1) % len(options)
            return TransitionResult(snapshot=snapshot, note="menu focus advanced")

        if event == DeviceInputEvent.DOUBLE_PRESS:
            snapshot.device_state = DeviceState.READY
            return TransitionResult(snapshot=snapshot, note="menu canceled")

        if event == DeviceInputEvent.LONG_PRESS:
            focused_option = snapshot.navigation.menu_options[snapshot.navigation.menu_index]
            if focused_option == "MODE":
                snapshot.device_state = DeviceState.MODE
                try:
                    snapshot.navigation.mode_index = snapshot.navigation.available_modes.index(
                        snapshot.navigation.active_mode
                    )
                except ValueError:
                    snapshot.navigation.mode_index = 0
                return TransitionResult(snapshot=snapshot, note="mode selector opened")
            return TransitionResult(snapshot=snapshot, note="menu option not implemented")

        return TransitionResult(snapshot=snapshot)

    def _from_mode(self, snapshot: DeviceSnapshot, event: DeviceInputEvent) -> TransitionResult:
        modes = snapshot.navigation.available_modes or [snapshot.navigation.active_mode]

        if event == DeviceInputEvent.PRESS:
            snapshot.navigation.mode_index = (snapshot.navigation.mode_index + 1) % len(modes)
            return TransitionResult(snapshot=snapshot, note="mode focus advanced")

        if event == DeviceInputEvent.DOUBLE_PRESS:
            try:
                snapshot.navigation.mode_index = modes.index(snapshot.navigation.active_mode)
            except ValueError:
                snapshot.navigation.mode_index = 0
            snapshot.device_state = DeviceState.READY
            return TransitionResult(snapshot=snapshot, note="mode selection canceled")

        if event == DeviceInputEvent.LONG_PRESS:
            snapshot.navigation.active_mode = modes[snapshot.navigation.mode_index]
            snapshot.device_state = DeviceState.READY
            return TransitionResult(snapshot=snapshot, note="mode confirmed")

        return TransitionResult(snapshot=snapshot)

    def _from_agents(self, snapshot: DeviceSnapshot, event: DeviceInputEvent) -> TransitionResult:
        agents = snapshot.agents

        if event == DeviceInputEvent.PRESS:
            if not agents:
                return TransitionResult(snapshot=snapshot, note="no agents available")
            snapshot.navigation.focused_agent_index = (snapshot.navigation.focused_agent_index + 1) % len(agents)
            return TransitionResult(snapshot=snapshot, note="agent focus advanced")

        if event == DeviceInputEvent.DOUBLE_PRESS:
            snapshot.device_state = DeviceState.READY
            self._focus_current_agent(snapshot)
            return TransitionResult(snapshot=snapshot, note="agent selection canceled")

        if event == DeviceInputEvent.LONG_PRESS:
            if not agents:
                return TransitionResult(snapshot=snapshot, note="no agents available")

            focused_agent = agents[snapshot.navigation.focused_agent_index]
            snapshot.device_state = DeviceState.READY

            if snapshot.pending_agent_ack:
                return TransitionResult(snapshot=snapshot, note="waiting for agent ACK")

            if focused_agent == snapshot.active_agent:
                return TransitionResult(snapshot=snapshot, note="agent unchanged")

            snapshot.pending_agent_ack = focused_agent
            return TransitionResult(
                snapshot=snapshot,
                effects=[EffectPayload(DomainEffect.CONFIRM_AGENT, {"agent_id": focused_agent})],
                note="agent change pending ACK",
            )

        return TransitionResult(snapshot=snapshot)

    def _agents_sync_effects(self, snapshot: DeviceSnapshot, *, now: float) -> list[EffectPayload]:
        has_loaded_cache = snapshot.agent_cache.loaded_at is not None
        cache_valid = snapshot.agent_cache.expires_at is not None and now < snapshot.agent_cache.expires_at

        if has_loaded_cache and cache_valid:
            return []

        if snapshot.agents_version and has_loaded_cache:
            return [EffectPayload(DomainEffect.REQUEST_AGENTS_VERSION)]

        return [EffectPayload(DomainEffect.REQUEST_AGENTS_LIST)]

    def _focus_current_agent(self, snapshot: DeviceSnapshot) -> None:
        target = snapshot.pending_agent_ack or snapshot.active_agent
        try:
            snapshot.navigation.focused_agent_index = snapshot.agents.index(target)
        except ValueError:
            snapshot.navigation.focused_agent_index = 0
