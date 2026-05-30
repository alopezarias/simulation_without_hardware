from __future__ import annotations

import json
import logging

from backend.application.ports.classifier_port import ClassificationResult
from backend.domain.note import NoteType

logger = logging.getLogger(__name__)

_VALID_TYPES = set(NoteType._value2member_map_)

_SYSTEM_PROMPT = """\
You are a note classification system. Given a voice note transcript, return a JSON object.

Fields:
- "type": classify as exactly one of: "task", "idea", "reminder", "note", "dictation"
- "tags": array of 1-5 lowercase tags (use_underscores, no duplicates)
- "entities": object with detected named entities — include only keys that apply:
    "date" (relative or absolute), "person" (name or role), "place", "org"
- "summary": single sentence, max 10 words

Type rules:
- "task"      → actionable item ("call X", "buy Y", "fix Z", "send Y to Z")
- "idea"      → speculative or creative thought ("what if...", "concept for...")
- "reminder"  → time-sensitive ("tomorrow", "at 3pm", "before Friday", "don't forget")
- "dictation" → starts with a dictation trigger: "dictado:", "escribir:", "write:", "transcribe:"
- "note"      → everything else (facts, data, observations)

Return ONLY valid JSON. No markdown, no explanation.\
"""


def parse_classification(raw: str, fallback_text: str = "") -> ClassificationResult:
    try:
        data = json.loads(raw)
        raw_type = data.get("type", "note")
        note_type = NoteType(raw_type) if raw_type in _VALID_TYPES else NoteType.NOTE
        tags = [
            str(t).lower().replace(" ", "_")
            for t in (data.get("tags") or [])
            if t
        ][:5]
        entities = data.get("entities") or {}
        if not isinstance(entities, dict):
            entities = {}
        summary = str(data.get("summary") or fallback_text)[:100]
        return ClassificationResult(
            type=note_type,
            tags=tags,
            entities=entities,
            summary=summary,
        )
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.warning("Classifier response parsing failed: %s | raw=%r", exc, raw[:200])
        return ClassificationResult(
            type=NoteType.NOTE,
            summary=fallback_text[:100],
        )
