"""
SQLAlchemy ORM Models – PM Intelligence Agent

Tables:
  Source              – Registered information sources (RSS, web, arxiv, github)
  Article             – Collected content items with scoring and AI analysis
  Trend               – Detected trend clusters with momentum scoring
  ArticleTrendTag     – Many-to-many join: Article ↔ Trend
  AgentRun            – Execution audit log per agent cycle
  KnowledgeExpansion  – Self-learning audit log (new sources discovered)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


def _utcnow() -> datetime:
    """Return the current UTC time (timezone-aware)."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


class Source(Base):
    """
    Represents a known information source (RSS feed, web page, GitHub query, Arxiv search).

    The `url` column is the natural primary key / dedup key.
    """

    __tablename__ = "sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    url = Column(String(500), nullable=False, unique=True)
    source_type = Column(String(50), nullable=False, default="rss")
    category = Column(String(100), nullable=False, default="general")
    relevance_boost = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    last_fetched_at = Column(DateTime(timezone=True), nullable=True)
    fetch_count = Column(Integer, nullable=False, default=0)
    error_count = Column(Integer, nullable=False, default=0)

    articles = relationship("Article", back_populates="source", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Source id={self.id} name={self.name!r} type={self.source_type}>"


class Article(Base):
    """
    Represents a single collected content item (article, blog post, paper, repo).

    Lifecycle:
      1. Created with is_processed=False after collection.
      2. RelevanceScorer assigns relevance_score.
      3. Summarizer populates summary, key_insights, pm_relevance.
      4. is_processed=True once the pipeline is complete.
    """

    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint("url", name="uq_article_url"),
        Index("ix_articles_category_score", "category", "relevance_score"),
        Index("ix_articles_collected_at", "collected_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=False)
    title = Column(String(500), nullable=True)
    url = Column(String(1000), nullable=False, unique=True)
    raw_content = Column(Text, nullable=True)
    category = Column(String(100), nullable=True)

    # AI-generated fields (populated by Summarizer)
    summary = Column(Text, nullable=True)
    key_insights = Column(Text, nullable=True)     # JSON array stored as string
    pm_relevance = Column(Text, nullable=True)     # PM-specific relevance explanation

    # Scoring and metadata
    relevance_score = Column(Float, nullable=False, default=0.0)
    is_processed = Column(Boolean, nullable=False, default=False)
    published_at = Column(DateTime(timezone=True), nullable=True)
    collected_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    source = relationship("Source", back_populates="articles")
    trend_tags = relationship("ArticleTrendTag", back_populates="article", cascade="all, delete-orphan")

    @property
    def insights_list(self) -> list[str]:
        """Deserialise the JSON-stored key_insights field into a Python list."""
        if self.key_insights:
            try:
                return json.loads(self.key_insights)
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    def __repr__(self) -> str:
        return f"<Article id={self.id} score={self.relevance_score} title={self.title!r:.40}>"


class Trend(Base):
    """
    Represents a detected trend or pattern across multiple articles.

    momentum_score is recalculated each run as (article_count / days_active) × 10.
    is_alert=True triggers immediate notifications.
    """

    __tablename__ = "trends"
    __table_args__ = (
        UniqueConstraint("name", name="uq_trend_name"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    category = Column(String(100), nullable=True)
    momentum_score = Column(Float, nullable=False, default=0.0)
    article_count = Column(Integer, nullable=False, default=0)
    is_alert = Column(Boolean, nullable=False, default=False)
    first_seen_at = Column(DateTime(timezone=True), nullable=True, default=_utcnow)
    last_seen_at = Column(DateTime(timezone=True), nullable=True, default=_utcnow)

    article_tags = relationship("ArticleTrendTag", back_populates="trend", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Trend id={self.id} name={self.name!r} momentum={self.momentum_score}>"


class ArticleTrendTag(Base):
    """Many-to-many association between Article and Trend."""

    __tablename__ = "article_trend_tags"
    __table_args__ = (
        UniqueConstraint("article_id", "trend_id", name="uq_article_trend"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    article_id = Column(Integer, ForeignKey("articles.id"), nullable=False)
    trend_id = Column(Integer, ForeignKey("trends.id"), nullable=False)
    tagged_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    article = relationship("Article", back_populates="trend_tags")
    trend = relationship("Trend", back_populates="article_tags")


class AgentRun(Base):
    """
    Audit record for each agent execution cycle.

    status transitions: running → success | failed
    """

    __tablename__ = "agent_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), nullable=False, default="running")
    sources_checked = Column(Integer, nullable=False, default=0)
    articles_collected = Column(Integer, nullable=False, default=0)
    articles_processed = Column(Integer, nullable=False, default=0)
    trends_detected = Column(Integer, nullable=False, default=0)
    new_sources_discovered = Column(Integer, nullable=False, default=0)
    report_path = Column(String(500), nullable=True)
    error_message = Column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<AgentRun id={self.id} status={self.status} at={self.started_at}>"


class KnowledgeExpansion(Base):
    """
    Audit log for self-discovered information sources.

    Tracks how the agent expanded its source coverage over time.
    """

    __tablename__ = "knowledge_expansions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_url = Column(String(500), nullable=False)
    source_name = Column(String(200), nullable=True)
    discovered_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    discovery_method = Column(String(50), nullable=True)  # "rss_mining" | "llm_recommendation"
    confidence_score = Column(Float, nullable=True)
    reason = Column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<KnowledgeExpansion id={self.id} url={self.source_url!r:.60}>"
