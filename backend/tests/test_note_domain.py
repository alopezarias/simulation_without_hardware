"""Tests for the Note domain model."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from backend.domain.note import CaptureMode, Note, NoteType


def test_note_auto_generates_id():
    note = Note(text="buy milk", device_id="raspi-1", capture_mode=CaptureMode.WAKE_WORD)
    assert note.id
    uuid.UUID(note.id)  # raises if not valid UUID


def test_note_id_is_unique():
    a = Note(text="a", device_id="d", capture_mode=CaptureMode.WAKE_WORD)
    b = Note(text="b", device_id="d", capture_mode=CaptureMode.WAKE_WORD)
    assert a.id != b.id


def test_note_defaults():
    note = Note(text="test", device_id="d", capture_mode=CaptureMode.MANUAL)
    assert note.type is None
    assert note.tags == []
    assert note.entities == {}
    assert note.summary == ""
    assert note.audio_path == ""
    assert note.duration_s == 0.0


def test_note_created_at_is_utc():
    note = Note(text="test", device_id="d", capture_mode=CaptureMode.WAKE_WORD)
    assert note.created_at.tzinfo == timezone.utc


def test_is_dictation_true():
    note = Note(text="x", device_id="d", capture_mode=CaptureMode.WAKE_WORD, type=NoteType.DICTATION)
    assert note.is_dictation()


def test_is_dictation_false_for_task():
    note = Note(text="x", device_id="d", capture_mode=CaptureMode.WAKE_WORD, type=NoteType.TASK)
    assert not note.is_dictation()


def test_is_dictation_false_when_unclassified():
    note = Note(text="x", device_id="d", capture_mode=CaptureMode.WAKE_WORD)
    assert not note.is_dictation()


def test_is_classified_false_initially():
    note = Note(text="x", device_id="d", capture_mode=CaptureMode.WAKE_WORD)
    assert not note.is_classified()


def test_is_classified_true_after_type_set():
    note = Note(text="x", device_id="d", capture_mode=CaptureMode.WAKE_WORD, type=NoteType.IDEA)
    assert note.is_classified()


@pytest.mark.parametrize("note_type", list(NoteType))
def test_note_type_roundtrip(note_type):
    assert NoteType(note_type.value) == note_type


@pytest.mark.parametrize("mode", list(CaptureMode))
def test_capture_mode_roundtrip(mode):
    assert CaptureMode(mode.value) == mode


def test_note_with_full_metadata():
    note = Note(
        text="call the supplier on Thursday",
        device_id="raspi-home",
        capture_mode=CaptureMode.WAKE_WORD,
        type=NoteType.TASK,
        tags=["supplier", "call"],
        entities={"date": "Thursday"},
        summary="Call supplier Thursday",
        duration_s=3.5,
    )
    assert note.type == NoteType.TASK
    assert "supplier" in note.tags
    assert note.entities["date"] == "Thursday"
    assert note.duration_s == 3.5
