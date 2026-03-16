"""Local device-state event and effect types shared by runtime entrypoints."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DeviceState(str, Enum):
    """Observable local device states owned by the runtime."""

    LOCKED = "LOCKED"
    READY = "READY"
    LISTEN = "LISTEN"
    MENU = "MENU"
    MODE = "MODE"
    AGENTS = "AGENTS"


class DeviceInputEvent(str, Enum):
    """Single-button interactions supported by the runtime."""

    PRESS = "press"
    DOUBLE_PRESS = "double_press"
    LONG_PRESS = "long_press"


class DomainEffect(str, Enum):
    """Remote integration effects emitted by the local state machine."""

    START_LISTEN = "start_listen"
    STOP_LISTEN_FINALIZE = "stop_listen_finalize"
    STOP_LISTEN_CANCEL = "stop_listen_cancel"
    REQUEST_AGENTS_VERSION = "request_agents_version"
    REQUEST_AGENTS_LIST = "request_agents_list"
    CONFIRM_AGENT = "confirm_agent"


class MenuOption(str, Enum):
    """Top-level menu entries navigated locally by the runtime."""

    MODE = "MODE"


@dataclass(slots=True)
class EffectPayload:
    """Effect envelope returned by domain transitions."""

    kind: DomainEffect
    data: dict[str, Any] = field(default_factory=dict)
