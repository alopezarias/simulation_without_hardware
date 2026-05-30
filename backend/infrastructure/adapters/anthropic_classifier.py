from __future__ import annotations

import logging

from backend.application.ports.classifier_port import ClassificationResult, ClassifierPort
from backend.domain.note import NoteType
from backend.infrastructure.adapters._classifier_parsing import (
    _SYSTEM_PROMPT,
    parse_classification,
)

logger = logging.getLogger(__name__)


class AnthropicClassifier(ClassifierPort):
    """Classifier backed by Claude.

    The system prompt is marked with cache_control so Anthropic caches it
    across requests — reduces latency and cost on repeated calls.
    """

    def __init__(self, api_key: str, model: str, *, _client=None) -> None:
        if _client is not None:
            self._client = _client
        else:
            import anthropic

            self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def classify(self, text: str) -> ClassificationResult:
        if not text.strip():
            return ClassificationResult(type=NoteType.NOTE, summary="")

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=300,
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": f'Note: "{text}"'}],
            )
            raw = response.content[0].text
        except Exception as exc:
            logger.warning("AnthropicClassifier API error: %s", exc)
            return ClassificationResult(type=NoteType.NOTE, summary=text[:100])

        return parse_classification(raw, fallback_text=text[:100])
