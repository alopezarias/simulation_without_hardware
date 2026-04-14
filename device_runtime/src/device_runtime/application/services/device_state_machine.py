"""Pure local device-state transitions for the shared runtime."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Callable

from device_runtime.domain.events import DeviceInputEvent, DeviceState, DomainEffect, EffectPayload
from device_runtime.domain.state import DeviceSnapshot
from device_runtime.protocol import UiState, new_turn_id

@dataclass(slots=True)
class TransitionResult:
    snapshot: DeviceSnapshot
    effects: list[EffectPayload] = field(default_factory=list)
    note: str = ""


class DeviceStateMachine:
    """Applies local transitions without depending on transport or UI code."""

    def __init__(
        self,
        turn_id_factory: Callable[[], str] = new_turn_id,
    ) -> None:
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
        if event == DeviceInputEvent.LONG_PRESS:
            return self._start_listening(current, connected=connected)
        if state == DeviceState.LISTENING and event == DeviceInputEvent.RELEASE:
            current.device_state = DeviceState.STANDBY
            current.remote_ui_state = UiState.STANDBY
            current.listening_active = False
            return TransitionResult(
                snapshot=current,
                effects=[EffectPayload(DomainEffect.STOP_LISTEN_FINALIZE, {"turn_id": current.turn_id})],
                note="listen finalized",
            )
        if event == DeviceInputEvent.PRESS and state == DeviceState.STANDBY:
            if not connected:
                return TransitionResult(snapshot=current, note="backend disconnected")
            if not current.session_id:
                return TransitionResult(snapshot=current, note="backend not ready")
            current.device_state = DeviceState.CALLING
            current.remote_ui_state = UiState.CALLING
            current.transcript = ""
            current.assistant_text = ""
            current.last_latency_ms = None
            return TransitionResult(
                snapshot=current,
                effects=[EffectPayload(DomainEffect.START_CALL)],
                note="call started",
            )
        if event == DeviceInputEvent.DOUBLE_PRESS and state == DeviceState.STANDBY:
            current.device_state = DeviceState.CONFIG
            current.remote_ui_state = UiState.CONFIG
            return TransitionResult(snapshot=current, note="config opened")
        return TransitionResult(snapshot=current)

    def _start_listening(self, snapshot: DeviceSnapshot, *, connected: bool) -> TransitionResult:
        if snapshot.device_state not in {DeviceState.STANDBY, DeviceState.INCOMING_CALL}:
            return TransitionResult(snapshot=snapshot)
        if not connected:
            return TransitionResult(snapshot=snapshot, note="backend disconnected")
        if not snapshot.session_id:
            return TransitionResult(snapshot=snapshot, note="backend not ready")
        snapshot.device_state = DeviceState.LISTENING
        snapshot.remote_ui_state = UiState.LISTENING
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
