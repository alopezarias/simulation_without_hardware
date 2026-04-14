"""Local device-state event and effect types shared by runtime entrypoints."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DeviceState(str, Enum):
    """Observable local device states owned by the runtime."""

    STANDBY = "standby"
    LISTENING = "listening"
    CALLING = "calling"
    INCOMING_CALL = "incoming_call"
    CONFIG = "config"


class DeviceInputEvent(str, Enum):
    """Single-button interactions supported by the runtime."""

    PRESS = "press"
    DOUBLE_PRESS = "double_press"
    LONG_PRESS = "long_press"
    RELEASE = "release"


class DomainEffect(str, Enum):
    """Remote integration effects emitted by the local state machine."""

    START_LISTEN = "start_listen"
    STOP_LISTEN_FINALIZE = "stop_listen_finalize"
    START_CALL = "start_call"


@dataclass(slots=True)
class EffectPayload:
    """Effect envelope returned by domain transitions."""

    kind: DomainEffect
    data: dict[str, Any] = field(default_factory=dict)
