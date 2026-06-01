from __future__ import annotations

import logging

from backend.application.ports.classifier_port import ClassificationResult, ClassifierPort
from backend.domain.note import NoteType
from backend.infrastructure.adapters._classifier_parsing import (
    _SYSTEM_PROMPT,
    parse_classification,
)

logger = logging.getLogger(__name__)


class OpenAIClassifier(ClassifierPort):
    """Classifier backed by OpenAI models (gpt-4o-mini by default)."""

    def __init__(self, api_key: str, model: str, *, _client=None) -> None:
        if _client is not None:
            self._client = _client
        else:
            import openai

            self._client = openai.AsyncOpenAI(api_key=api_key)
        self._model = model

    async def classify(self, text: str) -> ClassificationResult:
        if not text.strip():
            return ClassificationResult(type=NoteType.NOTE, summary="")

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                response_format={"type": "json_object"},
                max_tokens=300,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": f'Note: "{text}"'},
                ],
            )
            raw = response.choices[0].message.content or ""
        except Exception as exc:
            logger.warning("OpenAIClassifier API error: %s", exc)
            return ClassificationResult(type=NoteType.NOTE, summary=text[:100])

        return parse_classification(raw, fallback_text=text[:100])
