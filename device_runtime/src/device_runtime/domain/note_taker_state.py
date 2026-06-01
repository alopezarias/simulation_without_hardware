"""Note-taker domain state and event types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class NoteTakerMode(str, Enum):
    """Observable mode of the note-taker device."""

    AMBIENT = "ambient"    # passive wake-word listening
    SILENT = "silent"      # wake word disabled; button-only
    RECORDING = "recording"  # actively capturing audio


class NoteTakerEvent(str, Enum):
    """Inputs that drive note-taker mode transitions."""

    TAP = "tap"                         # single button press / release
    LONG_PRESS = "long_press"           # button held ≥ threshold
    RELEASE = "release"                 # button released after long press
    WAKE_WORD = "wake_word"             # wake word detected by engine
    RECORDING_DONE = "recording_done"   # silence timeout / max-duration reached


class CaptureMode(str, Enum):
    """How the current recording was triggered."""

    WAKE_WORD = "wake_word"
    MANUAL = "manual"


@dataclass(slots=True)
class NoteTakerState:
    """Full note-taker device state (immutable-by-convention; deepcopy before mutating)."""

    mode: NoteTakerMode = NoteTakerMode.AMBIENT
    return_to: NoteTakerMode = NoteTakerMode.AMBIENT  # mode to restore after recording
    capture_mode: CaptureMode | None = None           # set only while RECORDING
