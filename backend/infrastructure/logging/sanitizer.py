"""Helpers to sanitize structured logs."""

from __future__ import annotations

from typing import Any


def sanitize_message_for_log(message: dict[str, Any]) -> dict[str, Any]:
    safe = dict(message)

    payload = safe.get("payload")
    if isinstance(payload, str) and payload:
        safe["payload"] = f"<base64:{len(payload)} chars>"

    text = safe.get("text")
    if isinstance(text, str) and len(text) > 240:
        safe["text"] = text[:240] + "...<trimmed>"

    return safe
