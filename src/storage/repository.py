"""
Data Access Layer – PM Intelligence Agent.
All database interactions go through this module.
Keeps SQL logic out of business logic and makes unit testing easier.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.storage.models import (
    AgentRun,
    Article,
    ArticleTrendTag,
    KnowledgeExpansion,
    SeenItem,
    Source,
    Trend,
)

logger = logging.getLogger(__name__)


# ── Source Repository ──────────────────────────────────────────────────────────

class SourceRepository:
    """CRUD operations for Source records."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_all_active(self) -> list[Source]:
        """Return all active sources ordered by name."""
        return list(
            self._session.execute(
                select(Source).where(Source.is_active.is_(True)).order_by(Source.name)
            ).scalars()
        )

    def get_by_url(self, url: str) -> Optional[Source]:
        """Return a source by its URL, or None if not found."""
        return self._session.execute(
            select(Source).where(Source.url == url)
        ).scalar_one_or_none()

    def upsert(
        self,
        name: str,
        url: str,
        source_type: str,
        category: str,
        relevance_boost: int = 0,
    ) -> Source:
        """
        Insert a new source or return the existing one if the URL already exists.
        Safe against concurrent inserts – catches IntegrityError on the unique URL
        constraint and returns the winner record.
        """
        from sqlalchemy.exc import IntegrityError

        existing = self.get_by_url(url)
        if existing:
            return existing
        source = Source(
            name=name,
            url=url,
            source_type=source_type,
            category=category,
            relevance_boost=relevance_boost,
        )
        self._session.add(source)
        try:
            self._session.flush()
            logger.info("New PM source registered: %s (%s)", name, url)
            return source
        except IntegrityError:
            # Another process or session iteration already inserted this URL
            self._session.rollback()
            existing = self.get_by_url(url)
            if existing:
                return existing
            raise

    def mark_fetched(self, source: Source, had_error: bool = False) -> None:
        """Update fetch timestamp and counters."""
        source.last_fetched_at = datetime.now(timezone.utc)
        source.fetch_count += 1
        if had_error:
            source.error_count += 1


# ── Article Repository ─────────────────────────────────────────────────────────

class ArticleRepository:
    """CRUD operations for Article records."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def exists(self, url: str) -> bool:
        """Check if an article with this URL has already been collected (dedup guard)."""
        result = self._session.execute(
            select(func.count()).select_from(Article).where(Article.url == url)
        ).scalar()
        return bool(result and result > 0)

    def create(self, **kwargs) -> Article:
        """Create and flush a new Article record."""
        article = Article(**kwargs)
        self._session.add(article)
        self._session.flush()
        return article

    def get_unprocessed(self, limit: int = 200) -> list[Article]:
        """Return the most recently collected unprocessed articles."""
        return list(
            self._session.execute(
                select(Article)
                .where(Article.is_processed.is_(False))
                .order_by(Article.collected_at.desc())
                .limit(limit)
            ).scalars()
        )

    def get_for_report(self, since: datetime, min_score: float = 55.0) -> list[Article]:
        """Return processed, high-scoring articles for report generation."""
        return list(
            self._session.execute(
                select(Article)
                .where(
                    Article.is_processed.is_(True),
                    Article.relevance_score >= min_score,
                    Article.collected_at >= since,
                )
                .order_by(Article.relevance_score.desc())
            ).scalars()
        )

    def count_since(self, since: datetime) -> int:
        """Count total articles collected since a given timestamp."""
        result = self._session.execute(
            select(func.count()).select_from(Article).where(Article.collected_at >= since)
        ).scalar()
        return int(result or 0)


# ── Trend Repository ───────────────────────────────────────────────────────────

class TrendRepository:
    """CRUD operations for Trend records."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_or_create(self, name: str, category: str) -> tuple[Trend, bool]:
        """
        Return (trend, created) where `created` is True if the trend is new.
        Uses exact name matching to avoid duplicates.
        """
        existing = self._session.execute(
            select(Trend).where(Trend.name == name)
        ).scalar_one_or_none()
        if existing:
            return existing, False
        trend = Trend(name=name, category=category)
        self._session.add(trend)
        self._session.flush()
        return trend, True

    def link_article(self, trend: Trend, article: Article) -> None:
        """Associate an article with a trend (idempotent – safe to call multiple times)."""
        already_linked = self._session.execute(
            select(func.count()).select_from(ArticleTrendTag).where(
                ArticleTrendTag.article_id == article.id,
                ArticleTrendTag.trend_id == trend.id,
            )
        ).scalar()
        if already_linked:
            return
        tag = ArticleTrendTag(article_id=article.id, trend_id=trend.id)
        self._session.add(tag)
        self._session.flush()
        trend.article_count += 1
        trend.last_seen_at = datetime.now(timezone.utc)

    def get_top_trends(self, limit: int = 10, days: int = 7) -> list[Trend]:
        """Return top N trends by momentum score seen in the last N days."""
        since = datetime.now(timezone.utc) - timedelta(days=days)
        return list(
            self._session.execute(
                select(Trend)
                .where(Trend.last_seen_at >= since)
                .order_by(Trend.momentum_score.desc())
                .limit(limit)
            ).scalars()
        )

    def get_alert_trends(self) -> list[Trend]:
        """Return all trends flagged as requiring immediate attention."""
        return list(
            self._session.execute(
                select(Trend).where(Trend.is_alert.is_(True))
            ).scalars()
        )


# ── AgentRun Repository ────────────────────────────────────────────────────────

class AgentRunRepository:
    """Audit log for agent execution cycles."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def start_run(self) -> AgentRun:
        """Create a new AgentRun record with status='running'."""
        run = AgentRun(started_at=datetime.now(timezone.utc), status="running")
        self._session.add(run)
        self._session.flush()
        return run

    def finish_run(self, run: AgentRun, **stats) -> None:
        """Mark the run as successful and record final statistics."""
        run.finished_at = datetime.now(timezone.utc)
        run.status = "success"
        for key, value in stats.items():
            setattr(run, key, value)

    def fail_run(self, run: AgentRun, error: str) -> None:
        """Mark the run as failed and record the error message."""
        run.finished_at = datetime.now(timezone.utc)
        run.status = "failed"
        run.error_message = error

    def get_last(self, n: int = 10) -> list[AgentRun]:
        """Return the N most recent agent runs."""
        return list(
            self._session.execute(
                select(AgentRun).order_by(AgentRun.started_at.desc()).limit(n)
            ).scalars()
        )


# ── KnowledgeExpansion Repository ─────────────────────────────────────────────

class KnowledgeExpansionRepository:
    """Tracks self-discovered information sources (agent self-expansion)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        source_url: str,
        source_name: str,
        discovery_method: str,
        confidence_score: float = 1.0,
        reason: str = "",
    ) -> KnowledgeExpansion:
        """Record a newly discovered source."""
        expansion = KnowledgeExpansion(
            source_url=source_url,
            source_name=source_name,
            discovery_method=discovery_method,
            confidence_score=confidence_score,
            reason=reason,
        )
        self._session.add(expansion)
        self._session.flush()
        return expansion

    def already_known(self, source_url: str) -> bool:
        """Return True if this URL was already discovered and recorded."""
        result = self._session.execute(
            select(func.count())
            .select_from(KnowledgeExpansion)
            .where(KnowledgeExpansion.source_url == source_url)
        ).scalar()
        return bool(result and result > 0)


# ── Seen-Item Repository (REQ-07) ─────────────────────────────────────────────

class SeenItemRepository:
    """
    REQ-07: Persistent deduplication store.

    Tracks every article URL that has been shown in a report so the same item
    is never surfaced to the user twice.  Articles that are deduped are still
    counted in trend analysis – they are only excluded from the *displayed* list.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def is_seen(self, url: str) -> bool:
        """Return True if the URL has already been shown in a past report."""
        return bool(
            self._session.execute(
                select(func.count()).select_from(SeenItem).where(SeenItem.url == url)
            ).scalar()
        )

    def get_seen_urls(self) -> set[str]:
        """Return the full set of already-seen URLs (for bulk filtering)."""
        rows = self._session.execute(select(SeenItem.url)).scalars().all()
        return set(rows)

    def mark_seen(self, url: str, title: Optional[str] = None) -> None:
        """
        Mark a URL as seen.  Idempotent – increments report_count on repeat calls
        (should not happen in practice due to dedup filtering, but safe to call).
        """
        existing = self._session.execute(
            select(SeenItem).where(SeenItem.url == url)
        ).scalar_one_or_none()

        if existing:
            existing.report_count += 1
        else:
            self._session.add(SeenItem(url=url, title=title))
        self._session.flush()

    def mark_seen_bulk(self, articles: list) -> int:
        """
        Mark all articles in the list as seen.

        Args:
            articles: ORM Article objects with .url and .title attributes.

        Returns:
            Number of newly-marked items.
        """
        existing_urls = self.get_seen_urls()
        new_count = 0
        for article in articles:
            if article.url not in existing_urls:
                self._session.add(SeenItem(url=article.url, title=article.title))
                new_count += 1
            else:
                # Still increment the counter for audit purposes
                existing = self._session.execute(
                    select(SeenItem).where(SeenItem.url == article.url)
                ).scalar_one_or_none()
                if existing:
                    existing.report_count += 1
        self._session.flush()
        return new_count
