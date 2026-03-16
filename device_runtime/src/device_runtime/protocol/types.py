"""Runtime-owned protocol enums and constants."""

from __future__ import annotations

from enum import Enum


class UiState(str, Enum):
    """Main UI states consumed by the runtime."""

    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    ERROR = "error"


class MessageType(str, Enum):
    DEVICE_HELLO = "device.hello"
    SESSION_START = "session.start"
    AGENT_SELECT = "agent.select"
    AGENTS_VERSION_REQUEST = "agents.version.request"
    AGENTS_LIST_REQUEST = "agents.list.request"
    RECORDING_START = "recording.start"
    AUDIO_CHUNK = "audio.chunk"
    RECORDING_STOP = "recording.stop"
    RECORDING_CANCEL = "recording.cancel"
    ASSISTANT_INTERRUPT = "assistant.interrupt"
    PING = "ping"
    DEBUG_USER_TEXT = "debug.user_text"
