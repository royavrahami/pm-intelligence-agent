"""
Shared pytest fixtures for the PM Intelligence Agent test suite.

Provides:
  - in-memory SQLite session for isolated DB tests
  - sample PM Source and Article factories
  - mock OpenAI client patch
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.storage.models import Article, Base, Source


# ── Database fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def db_session() -> Generator[Session, None, None]:
    """
    Provide an isolated in-memory SQLite session for each test.
    All tables are created fresh and dropped after the test.
    """
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine)
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        Base.metadata.drop_all(engine)


# ── PM-specific object factories ───────────────────────────────────────────────

@pytest.fixture
def sample_source(db_session: Session) -> Source:
    """Create and persist a standard PM RSS source for use in tests."""
    source = Source(
        name="Agile Alliance Blog",
        url="https://www.agilealliance.org/feed/",
        source_type="rss",
        category="agile",
        relevance_boost=14,
    )
    db_session.add(source)
    db_session.flush()
    return source


@pytest.fixture
def sample_article(db_session: Session, sample_source: Source) -> Article:
    """Create and persist a raw (unprocessed) PM article for use in tests."""
    article = Article(
        source_id=sample_source.id,
        title="How AI Is Transforming Project Management in High-Tech",
        url="https://example.com/article/pm-ai",
        category="project_management",
        raw_content=(
            "AI tools are revolutionising how project managers plan sprints, manage "
            "backlogs, and communicate with stakeholders. LLM-powered roadmapping tools "
            "help program managers forecast risks and optimise resource allocation. "
            "OKR alignment becomes easier with automated check-ins and retrospective analysis."
        ),
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(article)
    db_session.flush()
    return article


@pytest.fixture
def processed_article(db_session: Session, sample_source: Source) -> Article:
    """Create a fully processed PM article with AI-generated fields."""
    article = Article(
        source_id=sample_source.id,
        title="SAFe 6.0 Releases New Program Increment Planning Guide",
        url="https://example.com/article/safe-60",
        category="program_management",
        raw_content="Scaled Agile releases the SAFe 6.0 framework with enhanced PI planning guides.",
        published_at=datetime.now(timezone.utc),
        summary=(
            "SAFe 6.0 introduces significant improvements to PI planning, including "
            "lean portfolio management and built-in DevSecOps practices."
        ),
        key_insights=json.dumps([
            "SAFe 6.0 strengthens integration between business strategy and technical execution",
            "New lean portfolio management tools reduce overhead in large programs",
            "PI planning ceremonies now include remote team collaboration templates",
        ]),
        pm_relevance="Directly applicable for program managers running large Agile Release Trains.",
        relevance_score=88.0,
        is_processed=True,
    )
    db_session.add(article)
    db_session.flush()
    return article


# ── Mock OpenAI client ─────────────────────────────────────────────────────────

@pytest.fixture
def mock_openai():
    """
    Patch the OpenAI client to avoid real API calls in tests.
    Returns a configurable mock that mimics the chat.completions.create interface.
    """
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps({
        "summary": "A concise PM-focused summary of the article content.",
        "key_insights": [
            "OKRs drive team alignment across distributed squads",
            "Scrum retrospectives using AI tooling cut prep time by 40%",
            "Risk management automation reduces escalations significantly",
        ],
        "pm_relevance": "Highly relevant for program managers adopting AI-assisted planning workflows.",
    })

    with patch("openai.OpenAI") as mock_client_class:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_client_class.return_value = mock_client
        yield mock_client
