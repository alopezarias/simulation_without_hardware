"""Tests for AI classifiers (Anthropic + OpenAI) and the parse_classification utility."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.application.ports.classifier_port import ClassificationResult
from backend.domain.note import NoteType
from backend.infrastructure.adapters._classifier_parsing import parse_classification
from backend.infrastructure.adapters.anthropic_classifier import AnthropicClassifier
from backend.infrastructure.adapters.null_classifier import NullClassifier
from backend.infrastructure.adapters.openai_classifier import OpenAIClassifier


# ── parse_classification ──────────────────────────────────────────────────────

class TestParseClassification:
    def test_task_type(self):
        raw = json.dumps({"type": "task", "tags": ["call"], "entities": {}, "summary": "Call Alice"})
        result = parse_classification(raw)
        assert result.type == NoteType.TASK
        assert result.summary == "Call Alice"

    def test_idea_type(self):
        raw = json.dumps({"type": "idea", "tags": ["design"], "entities": {}, "summary": "New UI concept"})
        result = parse_classification(raw)
        assert result.type == NoteType.IDEA

    def test_reminder_type(self):
        raw = json.dumps({"type": "reminder", "tags": [], "entities": {"date": "Friday"}, "summary": "Submit report"})
        result = parse_classification(raw)
        assert result.type == NoteType.REMINDER
        assert result.entities["date"] == "Friday"

    def test_dictation_type(self):
        raw = json.dumps({"type": "dictation", "tags": [], "entities": {}, "summary": "Dictated text"})
        result = parse_classification(raw)
        assert result.type == NoteType.DICTATION

    def test_tags_lowercased(self):
        raw = json.dumps({"type": "note", "tags": ["ProjectAlpha", "URGENT"], "entities": {}, "summary": "x"})
        result = parse_classification(raw)
        assert result.tags == ["projectalpha", "urgent"]

    def test_tags_spaces_become_underscores(self):
        raw = json.dumps({"type": "note", "tags": ["buy groceries"], "entities": {}, "summary": "x"})
        result = parse_classification(raw)
        assert result.tags == ["buy_groceries"]

    def test_tags_capped_at_5(self):
        raw = json.dumps({"type": "note", "tags": ["a", "b", "c", "d", "e", "f", "g"], "entities": {}, "summary": "x"})
        result = parse_classification(raw)
        assert len(result.tags) == 5

    def test_summary_truncated_to_100_chars(self):
        long = "x" * 200
        raw = json.dumps({"type": "note", "tags": [], "entities": {}, "summary": long})
        result = parse_classification(raw)
        assert len(result.summary) == 100

    def test_invalid_json_falls_back_to_note(self):
        result = parse_classification("not valid json", fallback_text="raw text")
        assert result.type == NoteType.NOTE
        assert result.summary == "raw text"

    def test_unknown_type_falls_back_to_note(self):
        raw = json.dumps({"type": "unknown_future_type", "tags": [], "entities": {}, "summary": "x"})
        result = parse_classification(raw)
        assert result.type == NoteType.NOTE

    def test_missing_fields_dont_crash(self):
        raw = json.dumps({"type": "task"})
        result = parse_classification(raw)
        assert result.type == NoteType.TASK
        assert result.tags == []
        assert result.entities == {}

    def test_entities_non_dict_ignored(self):
        raw = json.dumps({"type": "note", "tags": [], "entities": "invalid", "summary": "x"})
        result = parse_classification(raw)
        assert result.entities == {}

    def test_empty_json_object_defaults_to_note(self):
        result = parse_classification("{}")
        assert result.type == NoteType.NOTE


# ── AnthropicClassifier ───────────────────────────────────────────────────────

def _make_anthropic_response(text: str):
    content_block = MagicMock()
    content_block.text = text
    response = MagicMock()
    response.content = [content_block]
    return response


def _make_anthropic_client(response_text: str):
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=_make_anthropic_response(response_text)
    )
    return mock_client


class TestAnthropicClassifier:
    @pytest.mark.asyncio
    async def test_classify_task(self):
        payload = json.dumps({
            "type": "task", "tags": ["call", "supplier"],
            "entities": {"person": "supplier"}, "summary": "Call the supplier"
        })
        clf = AnthropicClassifier(api_key="fake", model="any", _client=_make_anthropic_client(payload))
        result = await clf.classify("call the supplier")
        assert result.type == NoteType.TASK
        assert "call" in result.tags
        assert result.summary == "Call the supplier"

    @pytest.mark.asyncio
    async def test_classify_idea(self):
        payload = json.dumps({"type": "idea", "tags": ["product"], "entities": {}, "summary": "New product feature"})
        clf = AnthropicClassifier(api_key="fake", model="any", _client=_make_anthropic_client(payload))
        result = await clf.classify("what if we added dark mode")
        assert result.type == NoteType.IDEA

    @pytest.mark.asyncio
    async def test_classify_with_entities(self):
        payload = json.dumps({
            "type": "reminder", "tags": ["meeting"],
            "entities": {"date": "Friday", "person": "Alice"},
            "summary": "Meeting with Alice on Friday"
        })
        clf = AnthropicClassifier(api_key="fake", model="any", _client=_make_anthropic_client(payload))
        result = await clf.classify("meeting with Alice on Friday")
        assert result.entities["date"] == "Friday"
        assert result.entities["person"] == "Alice"

    @pytest.mark.asyncio
    async def test_empty_text_returns_note_without_api_call(self):
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock()
        clf = AnthropicClassifier(api_key="fake", model="any", _client=mock_client)
        result = await clf.classify("   ")
        assert result.type == NoteType.NOTE
        mock_client.messages.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_api_error_falls_back_gracefully(self):
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(side_effect=RuntimeError("network error"))
        clf = AnthropicClassifier(api_key="fake", model="any", _client=mock_client)
        result = await clf.classify("some note text")
        assert result.type == NoteType.NOTE
        assert "some note text" in result.summary

    @pytest.mark.asyncio
    async def test_invalid_json_response_falls_back(self):
        clf = AnthropicClassifier(api_key="fake", model="any", _client=_make_anthropic_client("not json"))
        result = await clf.classify("test note")
        assert result.type == NoteType.NOTE

    @pytest.mark.asyncio
    async def test_uses_cache_control_in_system(self):
        mock_client = _make_anthropic_client(json.dumps({"type": "note", "tags": [], "entities": {}, "summary": "x"}))
        clf = AnthropicClassifier(api_key="fake", model="haiku", _client=mock_client)
        await clf.classify("test")
        call_kwargs = mock_client.messages.create.call_args.kwargs
        system = call_kwargs["system"]
        assert isinstance(system, list)
        assert any(
            block.get("cache_control", {}).get("type") == "ephemeral"
            for block in system
            if isinstance(block, dict)
        )

    @pytest.mark.asyncio
    async def test_model_passed_to_api(self):
        mock_client = _make_anthropic_client(json.dumps({"type": "note", "tags": [], "entities": {}, "summary": "x"}))
        clf = AnthropicClassifier(api_key="fake", model="claude-haiku-4-5-20251001", _client=mock_client)
        await clf.classify("test")
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-haiku-4-5-20251001"


# ── OpenAIClassifier ──────────────────────────────────────────────────────────

def _make_openai_client(response_text: str):
    message = MagicMock()
    message.content = response_text
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=response)
    return mock_client


class TestOpenAIClassifier:
    @pytest.mark.asyncio
    async def test_classify_task(self):
        payload = json.dumps({"type": "task", "tags": ["review"], "entities": {}, "summary": "Review PR"})
        clf = OpenAIClassifier(api_key="fake", model="any", _client=_make_openai_client(payload))
        result = await clf.classify("review the PR before merging")
        assert result.type == NoteType.TASK

    @pytest.mark.asyncio
    async def test_classify_dictation(self):
        payload = json.dumps({"type": "dictation", "tags": [], "entities": {}, "summary": "Dictated paragraph"})
        clf = OpenAIClassifier(api_key="fake", model="any", _client=_make_openai_client(payload))
        result = await clf.classify("dictado: la arquitectura hexagonal separa dominio")
        assert result.type == NoteType.DICTATION

    @pytest.mark.asyncio
    async def test_empty_text_skips_api(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock()
        clf = OpenAIClassifier(api_key="fake", model="any", _client=mock_client)
        result = await clf.classify("")
        assert result.type == NoteType.NOTE
        mock_client.chat.completions.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_api_error_falls_back_gracefully(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("timeout"))
        clf = OpenAIClassifier(api_key="fake", model="any", _client=mock_client)
        result = await clf.classify("test note")
        assert result.type == NoteType.NOTE

    @pytest.mark.asyncio
    async def test_none_response_content_falls_back(self):
        mock_client = _make_openai_client("")  # empty content
        clf = OpenAIClassifier(api_key="fake", model="any", _client=mock_client)
        result = await clf.classify("test")
        assert result.type == NoteType.NOTE

    @pytest.mark.asyncio
    async def test_model_passed_to_api(self):
        mock_client = _make_openai_client(json.dumps({"type": "note", "tags": [], "entities": {}, "summary": "x"}))
        clf = OpenAIClassifier(api_key="fake", model="gpt-4o-mini", _client=mock_client)
        await clf.classify("test")
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_json_object_response_format_requested(self):
        mock_client = _make_openai_client(json.dumps({"type": "note", "tags": [], "entities": {}, "summary": "x"}))
        clf = OpenAIClassifier(api_key="fake", model="gpt-4o-mini", _client=mock_client)
        await clf.classify("test")
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs.get("response_format", {}).get("type") == "json_object"


# ── Dependency: get_classifier ────────────────────────────────────────────────

class TestGetClassifierDependency:
    def test_null_provider_returns_null_classifier(self):
        from backend.api.dependencies import get_classifier
        from backend.config.settings import Settings

        s = Settings(notes_db_url="sqlite+aiosqlite:///:memory:", ai_provider="null")
        result = get_classifier(s)
        assert isinstance(result, NullClassifier)

    def test_classifier_disabled_returns_null(self):
        from backend.api.dependencies import get_classifier
        from backend.config.settings import Settings

        s = Settings(
            notes_db_url="sqlite+aiosqlite:///:memory:",
            ai_provider="anthropic",
            ai_classifier_enabled=False,
            anthropic_api_key="fake",
        )
        result = get_classifier(s)
        assert isinstance(result, NullClassifier)

    def test_anthropic_provider_returns_anthropic_classifier(self):
        from backend.api.dependencies import get_classifier
        from backend.config.settings import Settings

        s = Settings(
            notes_db_url="sqlite+aiosqlite:///:memory:",
            ai_provider="anthropic",
            anthropic_api_key="fake-key",
        )
        result = get_classifier(s)
        assert isinstance(result, AnthropicClassifier)

    def test_openai_provider_returns_openai_classifier(self):
        from backend.api.dependencies import get_classifier
        from backend.config.settings import Settings

        s = Settings(
            notes_db_url="sqlite+aiosqlite:///:memory:",
            ai_provider="openai",
            openai_api_key="fake-key",
        )
        result = get_classifier(s)
        assert isinstance(result, OpenAIClassifier)

    def test_anthropic_without_key_raises_503(self):
        from fastapi import HTTPException

        from backend.api.dependencies import get_classifier
        from backend.config.settings import Settings

        s = Settings(
            notes_db_url="sqlite+aiosqlite:///:memory:",
            ai_provider="anthropic",
            anthropic_api_key="",  # missing
        )
        with pytest.raises(HTTPException) as exc_info:
            get_classifier(s)
        assert exc_info.value.status_code == 503

    def test_openai_without_key_raises_503(self):
        from fastapi import HTTPException

        from backend.api.dependencies import get_classifier
        from backend.config.settings import Settings

        s = Settings(
            notes_db_url="sqlite+aiosqlite:///:memory:",
            ai_provider="openai",
            openai_api_key="",  # missing
        )
        with pytest.raises(HTTPException) as exc_info:
            get_classifier(s)
        assert exc_info.value.status_code == 503

    def test_same_settings_returns_cached_instance(self):
        from backend.api.dependencies import get_classifier
        from backend.config.settings import Settings

        s = Settings(
            notes_db_url="sqlite+aiosqlite:///:memory:",
            ai_provider="anthropic",
            anthropic_api_key="same-key",
        )
        clf1 = get_classifier(s)
        clf2 = get_classifier(s)
        assert clf1 is clf2


# ── NullClassifier ────────────────────────────────────────────────────────────

class TestNullClassifier:
    @pytest.mark.asyncio
    async def test_always_returns_note_type(self):
        clf = NullClassifier()
        result = await clf.classify("anything")
        assert result.type == NoteType.NOTE

    @pytest.mark.asyncio
    async def test_summary_is_truncated_text(self):
        clf = NullClassifier()
        long = "x" * 200
        result = await clf.classify(long)
        assert len(result.summary) <= 80

    @pytest.mark.asyncio
    async def test_empty_tags(self):
        clf = NullClassifier()
        result = await clf.classify("test")
        assert result.tags == []
