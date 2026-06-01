from __future__ import annotations

from backend.application.ports.classifier_port import ClassificationResult, ClassifierPort
from backend.domain.note import NoteType


class NullClassifier(ClassifierPort):
    """No-op classifier — labels every note as NOTE with no tags.

    Used in Feature 2. Feature 3 swaps this for a real AI classifier.
    """

    async def classify(self, text: str) -> ClassificationResult:
        return ClassificationResult(
            type=NoteType.NOTE,
            tags=[],
            entities={},
            summary=text[:80].strip(),
        )
