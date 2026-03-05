"""
Tests for the PM Intelligence Agent data repositories.
One class per file per project convention.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.storage.models import Article, Source, Trend
from src.storage.repository import (
    AgentRunRepository,
    ArticleRepository,
    KnowledgeExpansionRepository,
    SourceRepository,
    TrendRepository,
)


class TestSourceRepository:
    """Verify Source CRUD operations and upsert idempotency."""

    def test_upsert_creates_new_source(self, db_session):
        """upsert() should create a new source when URL doesn't exist."""
        repo = SourceRepository(db_session)
        source = repo.upsert(
            name="PMI Blog",
            url="https://www.pmi.org/feed",
            source_type="rss",
            category="project_management",
            relevance_boost=15,
        )
        db_session.flush()
        assert source.id is not None
        assert source.name == "PMI Blog"
        assert source.category == "project_management"

    def test_upsert_is_idempotent(self, db_session):
        """upsert() should return existing source on second call with same URL."""
        repo = SourceRepository(db_session)
        url = "https://www.agilealliance.org/feed/"
        first = repo.upsert(name="Agile Alliance", url=url, source_type="rss", category="agile")
        second = repo.upsert(name="Agile Alliance Dupe", url=url, source_type="rss", category="agile")
        db_session.flush()
        assert first.id == second.id

    def test_get_all_active_returns_only_active_sources(self, db_session):
        """get_all_active() must exclude deactivated sources."""
        repo = SourceRepository(db_session)
        active = repo.upsert(
            name="Active Source",
            url="https://active.example.com/feed",
            source_type="rss",
            category="agile",
        )
        inactive = repo.upsert(
            name="Inactive Source",
            url="https://inactive.example.com/feed",
            source_type="rss",
            category="agile",
        )
        inactive.is_active = False
        db_session.flush()

        all_active = repo.get_all_active()
        urls = [s.url for s in all_active]
        assert "https://active.example.com/feed" in urls
        assert "https://inactive.example.com/feed" not in urls


class TestArticleRepository:
    """Verify Article dedup, creation, and query operations."""

    def test_exists_returns_false_for_new_url(self, db_session):
        """exists() should return False for a URL not yet in the database."""
        repo = ArticleRepository(db_session)
        assert repo.exists("https://never-seen-before.example.com/post") is False

    def test_exists_returns_true_after_article_created(self, db_session, sample_article):
        """exists() should return True for an article already in the database."""
        repo = ArticleRepository(db_session)
        assert repo.exists(sample_article.url) is True

    def test_get_for_report_filters_by_score_and_date(self, db_session, sample_source):
        """get_for_report() should only return processed articles above min_score."""
        repo = ArticleRepository(db_session)

        # High-score PM article – should be included
        high = Article(
            source_id=sample_source.id,
            title="OKR Best Practices for Tech Companies",
            url="https://example.com/okr-best",
            category="strategy",
            relevance_score=80.0,
            is_processed=True,
            collected_at=datetime.now(timezone.utc),
        )

        # Low-score article – should be excluded
        low = Article(
            source_id=sample_source.id,
            title="Random Article Below Threshold",
            url="https://example.com/random-low",
            category="general",
            relevance_score=20.0,
            is_processed=True,
            collected_at=datetime.now(timezone.utc),
        )

        db_session.add_all([high, low])
        db_session.flush()

        since = datetime.now(timezone.utc) - timedelta(hours=1)
        results = repo.get_for_report(since=since, min_score=60.0)
        urls = [a.url for a in results]

        assert "https://example.com/okr-best" in urls
        assert "https://example.com/random-low" not in urls


class TestTrendRepository:
    """Verify Trend creation, article linking, and query operations."""

    def test_get_or_create_new_trend(self, db_session):
        """get_or_create() should create a new trend and return created=True."""
        repo = TrendRepository(db_session)
        trend, created = repo.get_or_create(name="AI-Powered Sprint Planning", category="agile")
        db_session.flush()
        assert created is True
        assert trend.name == "AI-Powered Sprint Planning"

    def test_get_or_create_existing_trend(self, db_session):
        """get_or_create() should return existing trend and created=False."""
        repo = TrendRepository(db_session)
        first, _ = repo.get_or_create(name="Remote Team Retrospectives", category="agile")
        db_session.flush()
        second, created = repo.get_or_create(name="Remote Team Retrospectives", category="agile")
        assert created is False
        assert first.id == second.id

    def test_link_article_increments_count(self, db_session, sample_source):
        """link_article() should increment the trend's article_count."""
        trend_repo = TrendRepository(db_session)
        article_repo = ArticleRepository(db_session)

        article = article_repo.create(
            source_id=sample_source.id,
            title="SAFe PI Planning Guide 2026",
            url="https://example.com/safe-pi",
            category="program_management",
        )
        trend, _ = trend_repo.get_or_create(name="SAFe Adoption", category="program_management")
        db_session.flush()

        trend_repo.link_article(trend, article)
        db_session.flush()

        assert trend.article_count == 1


class TestAgentRunRepository:
    """Verify agent run lifecycle: start → finish/fail."""

    def test_start_and_finish_run(self, db_session):
        """A completed run should have status='success'."""
        repo = AgentRunRepository(db_session)
        run = repo.start_run()
        db_session.flush()
        assert run.status == "running"
        assert run.id is not None

        repo.finish_run(run, articles_collected=50, articles_processed=30)
        db_session.flush()
        assert run.status == "success"
        assert run.articles_collected == 50

    def test_fail_run_sets_error_message(self, db_session):
        """fail_run() should set status='failed' and record the error."""
        repo = AgentRunRepository(db_session)
        run = repo.start_run()
        db_session.flush()

        repo.fail_run(run, error="Connection timeout")
        db_session.flush()

        assert run.status == "failed"
        assert "Connection timeout" in run.error_message

    def test_get_last_returns_most_recent_runs(self, db_session):
        """get_last(n=3) should return at most 3 runs in reverse chronological order."""
        repo = AgentRunRepository(db_session)
        for _ in range(5):
            run = repo.start_run()
            repo.finish_run(run)
        db_session.flush()

        recent = repo.get_last(n=3)
        assert len(recent) == 3


class TestKnowledgeExpansionRepository:
    """Verify self-discovery tracking for new PM sources."""

    def test_record_and_already_known(self, db_session):
        """already_known() should return True after a source is recorded."""
        repo = KnowledgeExpansionRepository(db_session)
        url = "https://new-pm-blog.example.com/feed"

        assert repo.already_known(url) is False

        repo.record(
            source_url=url,
            source_name="New PM Blog",
            discovery_method="llm_recommendation",
            confidence_score=0.9,
            reason="LLM recommended this PM-focused blog",
        )
        db_session.flush()

        assert repo.already_known(url) is True
