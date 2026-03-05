"""
Arxiv Collector – PM Intelligence Agent.
Queries the Arxiv API for recent academic papers on:
  - AI-assisted project/program management
  - Agile software development research
  - Team performance and engineering productivity
  - Risk management and estimation methods
  - Remote/distributed team coordination

Uses the official Arxiv API (atom feed) – no authentication required.
"""

from __future__ import annotations

import logging
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

import feedparser
import requests

from src.storage.repository import ArticleRepository, SourceRepository

logger = logging.getLogger(__name__)

_ARXIV_API_BASE = "https://export.arxiv.org/api/query"
_REQUEST_TIMEOUT = 20
_RATE_LIMIT_DELAY = 3.0  # Respect Arxiv ToS: ~3 seconds between requests

# PM/PgM-focused Arxiv search queries
_QUERIES: list[dict] = [
    {
        "search": "ti:\"software project management\" AND (ti:\"machine learning\" OR ti:\"AI\")",
        "category": "ai_pm",
        "label": "AI in Software PM",
    },
    {
        "search": "ti:\"agile\" AND (ti:\"empirical\" OR ti:\"study\" OR ti:\"analysis\")",
        "category": "agile",
        "label": "Empirical Agile Research",
    },
    {
        "search": "ti:\"scrum\" OR (ti:\"kanban\" AND ti:\"software\")",
        "category": "agile",
        "label": "Scrum & Kanban Research",
    },
    {
        "search": "all:\"software effort estimation\" AND all:\"machine learning\"",
        "category": "project_management",
        "label": "AI-based Effort Estimation",
    },
    {
        "search": "ti:\"technical debt\" AND (ti:\"management\" OR ti:\"detection\")",
        "category": "program_management",
        "label": "Technical Debt Management",
    },
]

_MAX_RESULTS_PER_QUERY = 10


class ArxivCollector:
    """
    Fetches recent academic papers from Arxiv and stores their abstracts.

    Args:
        source_repo:  Repository for Source records.
        article_repo: Repository for Article records.
    """

    def __init__(
        self,
        source_repo: SourceRepository,
        article_repo: ArticleRepository,
    ) -> None:
        self._source_repo = source_repo
        self._article_repo = article_repo

    def collect_all(self) -> int:
        """Run all pre-defined PM-focused Arxiv search queries."""
        total = 0
        for query_def in _QUERIES:
            try:
                new = self._run_query(query_def)
                total += new
                time.sleep(_RATE_LIMIT_DELAY)
            except Exception as exc:
                logger.warning("Arxiv query '%s' failed: %s", query_def["label"], exc)
        logger.info("Arxiv Collector: %d new PM-related papers", total)
        return total

    def _run_query(self, query_def: dict) -> int:
        """Execute a single Arxiv API query and persist results."""
        source = self._source_repo.upsert(
            name=f"Arxiv – {query_def['label']}",
            url=f"{_ARXIV_API_BASE}?search_query={urllib.parse.quote(query_def['search'])}",
            source_type="arxiv",
            category=query_def["category"],
            relevance_boost=12,
        )

        params = {
            "search_query": query_def["search"],
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": _MAX_RESULTS_PER_QUERY,
        }
        response = requests.get(_ARXIV_API_BASE, params=params, timeout=_REQUEST_TIMEOUT)
        response.raise_for_status()

        feed = feedparser.parse(response.content)

        new_count = 0
        for entry in feed.entries:
            paper_url = self._get_abs_url(entry)
            if not paper_url or self._article_repo.exists(paper_url):
                continue

            authors = self._extract_authors(entry)
            abstract = getattr(entry, "summary", "").replace("\n", " ").strip()
            published_at = self._parse_date(entry)

            content = (
                f"Arxiv Paper – {query_def['label']}\n"
                f"Authors: {authors}\n"
                f"Abstract: {abstract}"
            )

            self._article_repo.create(
                source_id=source.id,
                title=getattr(entry, "title", "Untitled").replace("\n", " ").strip(),
                url=paper_url,
                published_at=published_at,
                category=query_def["category"],
                raw_content=content[:6000],
            )
            new_count += 1

        self._source_repo.mark_fetched(source)
        return new_count

    @staticmethod
    def _get_abs_url(entry: feedparser.FeedParserDict) -> Optional[str]:
        """Return the canonical abstract URL for an Arxiv entry."""
        for link in getattr(entry, "links", []):
            if link.get("type") == "text/html":
                return link.get("href")
        return getattr(entry, "link", None)

    @staticmethod
    def _extract_authors(entry: feedparser.FeedParserDict) -> str:
        """Return a comma-separated author string (capped at 5 names)."""
        authors = getattr(entry, "authors", [])
        names = [a.get("name", "") for a in authors if a.get("name")]
        return ", ".join(names[:5]) + ("..." if len(names) > 5 else "")

    @staticmethod
    def _parse_date(entry: feedparser.FeedParserDict) -> Optional[datetime]:
        """Parse the published date from the entry."""
        parsed = getattr(entry, "published_parsed", None)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except Exception:
                pass
        return None
