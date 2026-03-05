"""
Tests for the PM Summarizer.
One class per file per project convention.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import openai

from src.processors.summarizer import Summarizer

# Patch target: the OpenAI class as imported inside the summarizer module
_PATCH_TARGET = "src.processors.summarizer.OpenAI"


class TestSummarizerHappyPath:
    """Verify the summarizer correctly parses and returns PM-focused summaries."""

    def test_summarise_returns_required_fields(self):
        """summarise() must return a dict with summary, key_insights, pm_relevance."""
        mock_response = MagicMock()
        mock_response.choices[0].message.content = json.dumps({
            "summary": "A PM summary.",
            "key_insights": ["Insight 1", "Insight 2", "Insight 3"],
            "pm_relevance": "Relevant for program managers.",
        })
        with patch(_PATCH_TARGET) as mock_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_cls.return_value = mock_client

            summarizer = Summarizer(api_key="test-key", model="gpt-4o-mini")
            result = summarizer.summarise(
                title="OKR Planning Best Practices for Engineering Teams",
                content="OKRs help align engineering teams with business strategy...",
                source_name="Agile Alliance",
                category="strategy",
                url="https://example.com/okr-planning",
            )
        assert result is not None
        assert "summary" in result
        assert "key_insights" in result
        assert "pm_relevance" in result

    def test_key_insights_is_a_list(self):
        """key_insights must be returned as a Python list."""
        mock_response = MagicMock()
        mock_response.choices[0].message.content = json.dumps({
            "summary": "Sprint planning summary.",
            "key_insights": ["Anti-pattern 1", "Anti-pattern 2"],
            "pm_relevance": "Relevant for Scrum masters.",
        })
        with patch(_PATCH_TARGET) as mock_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_cls.return_value = mock_client

            summarizer = Summarizer(api_key="test-key", model="gpt-4o-mini")
            result = summarizer.summarise(
                title="Sprint Planning Anti-Patterns",
                content="Common sprint planning mistakes...",
                source_name="Scrum.org",
                category="agile",
                url="https://example.com/sprint-anti-patterns",
            )
        assert isinstance(result["key_insights"], list)
        assert len(result["key_insights"]) >= 1

    def test_content_is_truncated_to_max_chars(self):
        """Very long content should be truncated before sending to the API."""
        mock_response = MagicMock()
        mock_response.choices[0].message.content = json.dumps({
            "summary": "Summary.",
            "key_insights": ["Insight 1"],
            "pm_relevance": "Relevant.",
        })
        with patch(_PATCH_TARGET) as mock_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_cls.return_value = mock_client

            summarizer = Summarizer(api_key="test-key", model="gpt-4o-mini")
            long_content = "program management " * 1000  # >> 4000 chars

            summarizer.summarise(
                title="Program Management at Scale",
                content=long_content,
                source_name="Test Source",
                category="program_management",
                url="https://example.com/pm-scale",
            )

            call_kwargs = mock_client.chat.completions.create.call_args
            user_msg = call_kwargs[1]["messages"][1]["content"]
            # Content truncated to 4000 chars + surrounding prompt text
            assert len(user_msg) < len(long_content) + 500


class TestSummarizerErrorHandling:
    """Verify the summarizer returns None gracefully on API failures."""

    def test_returns_none_on_rate_limit_error(self):
        """summarise() should return None (not raise) on rate limit."""
        with patch(_PATCH_TARGET) as mock_client_class:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = openai.RateLimitError(
                message="rate limit", response=MagicMock(), body={}
            )
            mock_client_class.return_value = mock_client

            summarizer = Summarizer(api_key="test-key", model="gpt-4o-mini")
            result = summarizer.summarise(
                title="Test PM Article",
                content="Content",
                source_name="Test",
                category="agile",
                url="https://example.com",
            )

        assert result is None

    def test_returns_none_on_malformed_json(self):
        """summarise() should return None if the API returns invalid JSON."""
        with patch(_PATCH_TARGET) as mock_client_class:
            mock_response = MagicMock()
            mock_response.choices[0].message.content = "This is not valid JSON at all."
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_client_class.return_value = mock_client

            summarizer = Summarizer(api_key="test-key", model="gpt-4o-mini")
            result = summarizer.summarise(
                title="Test Article",
                content="Content",
                source_name="Test",
                category="agile",
                url="https://example.com",
            )

        assert result is None

    def test_returns_none_on_missing_pm_relevance_field(self):
        """summarise() should return None if pm_relevance key is missing."""
        with patch(_PATCH_TARGET) as mock_client_class:
            mock_response = MagicMock()
            # Missing 'pm_relevance' key – should be rejected
            mock_response.choices[0].message.content = json.dumps({
                "summary": "A summary.",
                "key_insights": ["Insight 1"],
                # pm_relevance intentionally omitted
            })
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_client_class.return_value = mock_client

            summarizer = Summarizer(api_key="test-key", model="gpt-4o-mini")
            result = summarizer.summarise(
                title="Test",
                content="Content",
                source_name="Test",
                category="agile",
                url="https://example.com",
            )

        assert result is None
