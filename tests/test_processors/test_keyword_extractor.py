"""
Tests for the PM Keyword Extractor.
One class per file per project convention.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.processors.keyword_extractor import KeywordExtractor
from src.storage.models import Article


def _make_article(title: str = "", content: str = "", insights: list | None = None) -> Article:
    """Factory helper for building test Article objects."""
    article = Article(
        source_id=1,
        title=title,
        url="https://example.com/pm-article",
        category="project_management",
        raw_content=content,
    )
    article.id = 1
    if insights is not None:
        article.key_insights = json.dumps(insights)
    return article


class TestStatisticalKeywordExtraction:
    """Verify the statistical fallback extraction works for PM content."""

    def test_extracts_meaningful_pm_keywords(self):
        """Statistical extractor should surface relevant PM terms."""
        extractor = KeywordExtractor(use_llm=False)
        article = _make_article(
            title="Agile Sprint Planning Best Practices",
            content="Sprint velocity backlog scrum kanban agile retrospective planning capacity",
        )
        keywords = extractor.extract(article)
        assert isinstance(keywords, list)
        assert len(keywords) <= 6

    def test_filters_stop_words(self):
        """Stop words like 'the', 'and', 'in' must not appear in keywords."""
        extractor = KeywordExtractor(use_llm=False)
        article = _make_article(
            title="The Future of Project Management in the Enterprise",
            content="the and in of to a is are was were",
        )
        keywords = extractor.extract(article)
        stop_words = {"the", "and", "in", "of", "to", "a", "is", "are", "was", "were"}
        for kw in keywords:
            assert kw.lower() not in stop_words

    def test_returns_at_most_six_keywords(self):
        """Should never return more than 6 keywords."""
        extractor = KeywordExtractor(use_llm=False)
        article = _make_article(
            title="PM Planning Risk Roadmap Sprint OKR SAFe",
            content="project program agile scrum kanban lean velocity burndown backlog retrospective",
        )
        keywords = extractor.extract(article)
        assert len(keywords) <= 6


class TestFromInsightsExtraction:
    """Verify keyword extraction from existing AI-generated PM insights."""

    def test_extracts_capitalised_terms_from_insights(self):
        """Capitalised terms like 'Scrum', 'SAFe', 'OKR' should be extracted."""
        extractor = KeywordExtractor(use_llm=False)
        article = _make_article(
            title="Program Management",
            insights=[
                "SAFe 6.0 introduces new PI planning ceremonies for Agile Release Trains",
                "OKR adoption increases team alignment across product and engineering",
                "Scrum retrospectives now automated with AI tools reduce prep time",
            ],
        )
        keywords = extractor.extract(article)
        assert isinstance(keywords, list)
        assert len(keywords) >= 1

    def test_malformed_json_insights_falls_back_to_statistical(self):
        """Malformed key_insights JSON should fall back to statistical extraction."""
        extractor = KeywordExtractor(use_llm=False)
        article = _make_article(
            title="Sprint Retrospective Guide",
            content="retrospective agile sprint team velocity improvement",
        )
        article.key_insights = "this is not valid json {"  # Malformed
        keywords = extractor.extract(article)
        assert isinstance(keywords, list)


class TestLLMKeywordExtraction:
    """Verify LLM-based keyword extraction is called and parsed correctly."""

    def test_llm_keywords_returned_as_list(self):
        """When LLM is available, it should return a clean list of PM keywords."""
        with patch("src.processors.keyword_extractor.OpenAI") as mock_client_class:
            mock_response = MagicMock()
            mock_response.choices[0].message.content = json.dumps({
                "keywords": ["OKR Alignment", "Sprint Planning", "Risk Management", "SAFe"]
            })
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_client_class.return_value = mock_client

            extractor = KeywordExtractor(use_llm=True)
            article = _make_article(
                title="Managing OKRs in SAFe Environments",
                content="OKR alignment with SAFe program increment planning requires...",
            )
            keywords = extractor.extract(article)

        assert isinstance(keywords, list)
        assert len(keywords) <= 6

    def test_llm_failure_falls_back_to_statistical(self):
        """An LLM API failure should fall back to statistical extraction without raising."""
        with patch("src.processors.keyword_extractor.OpenAI") as mock_client_class:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = Exception("API error")
            mock_client_class.return_value = mock_client

            extractor = KeywordExtractor(use_llm=True)
            article = _make_article(
                title="Agile Transformation Roadmap",
                content="agile scrum kanban sprint retrospective planning velocity",
            )
            keywords = extractor.extract(article)

        assert isinstance(keywords, list)
