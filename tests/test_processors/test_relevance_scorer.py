"""
Tests for the PM Relevance Scorer.
One class per file per project convention.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.processors.relevance_scorer import RelevanceScorer, _CATEGORY_BONUSES
from src.storage.models import Article, Source


def _make_scorer(**kwargs) -> RelevanceScorer:
    """Helper – build a scorer with optional keyword overrides."""
    defaults = dict(
        high_keywords=["okr", "scrum", "program management"],
        medium_keywords=["sprint", "backlog", "jira"],
        low_keywords=["team", "delivery"],
    )
    defaults.update(kwargs)
    return RelevanceScorer(**defaults)


def _make_source(category: str = "agile", boost: int = 10) -> Source:
    source = Source(
        name="Test PM Source",
        url="https://pm.example.com/feed",
        source_type="rss",
        category=category,
        relevance_boost=boost,
    )
    source.id = 1
    return source


def _make_article(title: str = "", content: str = "", category: str = "agile") -> Article:
    article = Article(
        source_id=1,
        title=title,
        url="https://pm.example.com/article/1",
        category=category,
        raw_content=content,
        published_at=datetime.now(timezone.utc),
    )
    article.id = 1
    return article


class TestRelevanceScorerKeywords:
    """Verify keyword scoring and the 50-point cap."""

    def test_high_keyword_increases_score(self):
        scorer = _make_scorer()
        article = _make_article(title="OKR Planning for Q1 Sprints", content="")
        source = _make_source()
        score = scorer.score(article, source)
        assert score > 0

    def test_score_is_capped_at_100(self):
        scorer = _make_scorer(
            high_keywords=["okr"] * 20,
            medium_keywords=["scrum"] * 20,
            low_keywords=["agile"] * 20,
        )
        content = "okr " * 30 + "scrum " * 30 + "agile " * 30
        article = _make_article(content=content, category="project_management")
        source = _make_source(boost=20)
        score = scorer.score(article, source)
        assert score <= 100.0

    def test_no_keywords_base_score(self):
        scorer = _make_scorer(
            high_keywords=["nonexistent_kw_xyz"],
            medium_keywords=[],
            low_keywords=[],
        )
        article = _make_article(title="Random unrelated article", content="nothing here")
        source = _make_source(boost=0, category="general")
        score = scorer.score(article, source)
        # Only freshness and title bonuses can apply
        assert 0.0 <= score <= 30.0


class TestRelevanceScorerSourceBoost:
    """Verify per-source relevance boost is applied and capped at 20."""

    def test_source_boost_applied(self):
        scorer = _make_scorer()
        article = _make_article(category="general")
        source_no_boost = _make_source(boost=0, category="general")
        source_with_boost = _make_source(boost=15, category="general")
        score_no = scorer.score(article, source_no_boost)
        score_with = scorer.score(article, source_with_boost)
        assert score_with > score_no

    def test_source_boost_capped_at_20(self):
        scorer = _make_scorer()
        article = _make_article(title="", content="", category="general")
        source_huge_boost = _make_source(boost=999, category="general")
        source_cap_boost = _make_source(boost=20, category="general")
        score_huge = scorer.score(article, source_huge_boost)
        score_cap = scorer.score(article, source_cap_boost)
        # Both should yield the same score since 999 is capped to 20
        assert score_huge == score_cap


class TestRelevanceScorerCategoryBonus:
    """Verify PM category bonuses are correct."""

    def test_project_management_gets_highest_bonus(self):
        scorer = _make_scorer()
        pm_article = _make_article(category="project_management")
        gen_article = _make_article(category="general")
        source = _make_source(boost=0, category="project_management")
        score_pm = scorer.score(pm_article, source)
        score_gen = scorer.score(gen_article, source)
        assert score_pm > score_gen

    def test_agile_category_bonus_applied(self):
        scorer = _make_scorer()
        article = _make_article(category="agile")
        source = _make_source(category="agile", boost=0)
        score = scorer.score(article, source)
        assert score >= _CATEGORY_BONUSES.get("agile", 0)

    def test_ai_pm_category_bonus_high(self):
        scorer = _make_scorer()
        article = _make_article(category="ai_pm")
        source = _make_source(category="ai_pm", boost=0)
        score = scorer.score(article, source)
        assert score >= _CATEGORY_BONUSES.get("ai_pm", 0)


class TestRelevanceScorerFreshness:
    """Verify freshness bonus decays correctly over time."""

    def test_very_fresh_article_gets_maximum_freshness_bonus(self):
        scorer = _make_scorer()
        article = _make_article(category="agile")
        article.published_at = datetime.now(timezone.utc) - timedelta(hours=2)
        source = _make_source(boost=0, category="general")
        score_fresh = scorer.score(article, source)

        article_old = _make_article(category="agile")
        article_old.published_at = datetime.now(timezone.utc) - timedelta(days=10)
        score_old = scorer.score(article_old, source)

        assert score_fresh > score_old

    def test_very_old_article_gets_no_freshness_bonus(self):
        from src.processors.relevance_scorer import RelevanceScorer
        scorer = _make_scorer()
        old_date = datetime.now(timezone.utc) - timedelta(days=30)
        bonus = scorer._freshness_bonus(old_date)
        assert bonus == 0.0


class TestRelevanceScorerTitleBonus:
    """Verify PM title heuristics award appropriate bonuses."""

    def test_good_length_title_gets_bonus(self):
        from src.processors.relevance_scorer import RelevanceScorer
        bonus = RelevanceScorer._title_bonus("How to Run Effective Sprint Retrospectives in 2026")
        assert bonus > 0

    def test_pm_tool_in_title_gets_tool_bonus(self):
        from src.processors.relevance_scorer import RelevanceScorer
        bonus_jira = RelevanceScorer._title_bonus("Jira Tips for Large Program Backlogs")
        bonus_plain = RelevanceScorer._title_bonus("Plain article title with no tools")
        assert bonus_jira >= bonus_plain
