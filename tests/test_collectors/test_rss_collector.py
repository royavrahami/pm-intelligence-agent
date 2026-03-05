"""
Tests for the RSS Collector – PM Intelligence Agent.
One class per file per project convention.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.rss_collector import RSSCollector, _parse_date, _extract_content
from src.storage.repository import ArticleRepository, SourceRepository


class TestRSSCollectorHappyPath:
    """Verify the collector stores new PM articles and deduplicates by URL."""

    def test_collect_new_articles_from_rss_feed(self, db_session):
        """Collector should store new articles from a valid RSS feed."""
        source_repo = SourceRepository(db_session)
        article_repo = ArticleRepository(db_session)

        source = source_repo.upsert(
            name="Agile Alliance",
            url="https://www.agilealliance.org/feed/",
            source_type="rss",
            category="agile",
            relevance_boost=14,
        )
        db_session.flush()

        mock_feed = MagicMock()
        mock_feed.bozo = False
        mock_entry = MagicMock()
        mock_entry.link = "https://www.agilealliance.org/post/new-scrum-guide"
        mock_entry.title = "New Scrum Guide Released"
        mock_entry.summary = "The Scrum Alliance releases an updated guide for 2026."
        # Explicitly disable content so _extract_content falls back to summary (plain string)
        del mock_entry.content
        mock_feed.entries = [mock_entry]

        with patch("feedparser.parse", return_value=mock_feed):
            collector = RSSCollector(source_repo=source_repo, article_repo=article_repo)
            count = collector.collect_all([source])

        assert count == 1

    def test_deduplicates_existing_articles(self, db_session):
        """Collector must not store an article whose URL already exists in the DB."""
        source_repo = SourceRepository(db_session)
        article_repo = ArticleRepository(db_session)

        source = source_repo.upsert(
            name="Test PM Feed",
            url="https://pm-blog.example.com/feed",
            source_type="rss",
            category="project_management",
            relevance_boost=10,
        )
        db_session.flush()

        existing_url = "https://pm-blog.example.com/already-collected"
        article_repo.create(
            source_id=source.id,
            title="Already stored article",
            url=existing_url,
            category="project_management",
        )
        db_session.flush()

        mock_feed = MagicMock()
        mock_feed.bozo = False
        mock_entry = MagicMock()
        mock_entry.link = existing_url
        mock_entry.title = "Already stored article"
        mock_entry.summary = "Should not be stored again."
        mock_feed.entries = [mock_entry]

        with patch("feedparser.parse", return_value=mock_feed):
            collector = RSSCollector(source_repo=source_repo, article_repo=article_repo)
            count = collector.collect_all([source])

        assert count == 0

    def test_only_processes_rss_source_type(self, db_session):
        """Collector should skip sources with source_type != 'rss'."""
        source_repo = SourceRepository(db_session)
        article_repo = ArticleRepository(db_session)

        source = source_repo.upsert(
            name="GitHub Trending",
            url="https://github.com/trending",
            source_type="github_trending",  # Not RSS – should be skipped
            category="tools",
            relevance_boost=6,
        )
        db_session.flush()

        collector = RSSCollector(source_repo=source_repo, article_repo=article_repo)
        count = collector.collect_all([source])

        assert count == 0


class TestRSSCollectorErrorHandling:
    """Verify the collector handles errors gracefully without crashing."""

    def test_marks_error_on_failed_feed(self, db_session):
        """A feed parse error should increment the source error counter."""
        source_repo = SourceRepository(db_session)
        article_repo = ArticleRepository(db_session)

        source = source_repo.upsert(
            name="Bad Feed",
            url="https://broken.example.com/feed",
            source_type="rss",
            category="general",
            relevance_boost=0,
        )
        db_session.flush()

        with patch("feedparser.parse", side_effect=Exception("Connection refused")):
            collector = RSSCollector(source_repo=source_repo, article_repo=article_repo)
            count = collector.collect_all([source])

        assert count == 0
        assert source.error_count == 1

    def test_parse_date_returns_none_for_missing_date(self):
        """_parse_date should return None if no date attributes are present."""
        entry = MagicMock(spec=[])  # Empty spec – no attributes
        result = _parse_date(entry)
        assert result is None

    def test_extract_content_prefers_full_content_over_summary(self):
        """_extract_content should prefer full 'content' over 'summary'."""
        entry = MagicMock()
        entry.content = [{"value": "Full article body text."}]
        entry.summary = "Short summary."
        result = _extract_content(entry)
        assert result == "Full article body text."
