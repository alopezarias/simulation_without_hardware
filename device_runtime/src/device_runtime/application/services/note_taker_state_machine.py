"""Pure note-taker state transitions — no I/O, no side effects."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from device_runtime.domain.note_taker_state import (
    CaptureMode,
    NoteTakerEvent,
    NoteTakerMode,
    NoteTakerState,
)


@dataclass(slots=True)
class NoteTakerTransition:
    state: NoteTakerState
    action: str                         # "none" | "start_recording" | "stop_and_upload"
    capture_mode: CaptureMode | None = None  # populated for start_recording / stop_and_upload


class NoteTakerStateMachine:
    """Applies note-taker mode transitions without depending on I/O."""

    def handle(self, state: NoteTakerState, event: NoteTakerEvent) -> NoteTakerTransition:
        s = deepcopy(state)

        if event == NoteTakerEvent.TAP:
            return self._handle_tap(s)

        if event == NoteTakerEvent.WAKE_WORD:
            return self._handle_wake_word(s)

        if event == NoteTakerEvent.LONG_PRESS:
            return self._handle_long_press(s)

        if event == NoteTakerEvent.RELEASE:
            return self._handle_release(s)

        if event == NoteTakerEvent.RECORDING_DONE:
            return self._handle_recording_done(s)

        return NoteTakerTransition(s, "none")

    # ── internal handlers ────────────────────────────────────────────────────

    @staticmethod
    def _handle_tap(s: NoteTakerState) -> NoteTakerTransition:
        if s.mode == NoteTakerMode.AMBIENT:
            s.mode = NoteTakerMode.SILENT
        elif s.mode == NoteTakerMode.SILENT:
            s.mode = NoteTakerMode.AMBIENT
        # TAP during RECORDING is ignored
        return NoteTakerTransition(s, "none")

    @staticmethod
    def _handle_wake_word(s: NoteTakerState) -> NoteTakerTransition:
        if s.mode != NoteTakerMode.AMBIENT:
            return NoteTakerTransition(s, "none")
        s.return_to = NoteTakerMode.AMBIENT
        s.mode = NoteTakerMode.RECORDING
        s.capture_mode = CaptureMode.WAKE_WORD
        return NoteTakerTransition(s, "start_recording", CaptureMode.WAKE_WORD)

    @staticmethod
    def _handle_long_press(s: NoteTakerState) -> NoteTakerTransition:
        if s.mode == NoteTakerMode.RECORDING:
            return NoteTakerTransition(s, "none")
        s.return_to = s.mode
        s.mode = NoteTakerMode.RECORDING
        s.capture_mode = CaptureMode.MANUAL
        return NoteTakerTransition(s, "start_recording", CaptureMode.MANUAL)

    @staticmethod
    def _handle_release(s: NoteTakerState) -> NoteTakerTransition:
        if s.mode != NoteTakerMode.RECORDING or s.capture_mode != CaptureMode.MANUAL:
            return NoteTakerTransition(s, "none")
        cap = s.capture_mode
        s.mode = s.return_to
        s.capture_mode = None
        return NoteTakerTransition(s, "stop_and_upload", cap)

    @staticmethod
    def _handle_recording_done(s: NoteTakerState) -> NoteTakerTransition:
        if s.mode != NoteTakerMode.RECORDING:
            return NoteTakerTransition(s, "none")
        cap = s.capture_mode
        s.mode = s.return_to
        s.capture_mode = None
        return NoteTakerTransition(s, "stop_and_upload", cap)
