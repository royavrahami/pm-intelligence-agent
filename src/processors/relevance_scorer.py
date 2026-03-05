"""
Relevance Scorer – PM Intelligence Agent.
Assigns a numeric relevance score (0–100) to each article BEFORE
it is sent to the LLM, filtering noise and reducing API costs.

Scoring strategy (additive):
  1. Keyword match score  (0–50): high/medium/low keywords from sources.yaml
  2. Source boost         (0–20): pre-configured per-source bonus
  3. Category bonus       (0–20): PM/Agile categories get the highest bonus
  4. Freshness bonus      (0–10): articles < 48 h old get extra points
  5. Title heuristics     (0–10): title length, structural signals

Total cap: 100
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from src.storage.models import Article, Source

logger = logging.getLogger(__name__)

_HIGH_KW_SCORE = 8
_MEDIUM_KW_SCORE = 3
_LOW_KW_SCORE = 1

# Category bonuses tuned for PM/PgM relevance
_CATEGORY_BONUSES: dict[str, int] = {
    "project_management": 20,
    "program_management": 20,
    "agile":              18,
    "leadership":         16,
    "strategy":           14,
    "ai_pm":              18,
    "tools":               6,
    "general":             2,
}

# Built-in high-priority PM/PgM keywords (supplement sources.yaml)
_BUILTIN_HIGH_KEYWORDS = [
    "project management", "program management", "okr", "okrs",
    "agile", "scrum", "kanban", "safe", "scaled agile",
    "sprint planning", "roadmap", "stakeholder",
    "risk management", "portfolio management",
    "engineering manager", "tech lead", "team lead",
    "pmo", "program manager", "project manager",
    "ai project management", "llm pm",
    "definition of done", "definition of ready",
]

# Built-in medium-priority PM/PgM keywords
_BUILTIN_MEDIUM_KEYWORDS = [
    "velocity", "burndown", "backlog", "product owner", "scrum master",
    "agile transformation", "lean", "continuous improvement",
    "capacity planning", "milestone", "jira", "confluence",
    "asana", "linear", "estimation", "sprint", "retrospective",
    "planning", "dependencies", "kpi", "metrics", "dora",
    "technical debt", "refactoring", "devops", "ci/cd",
    "team performance", "remote team", "distributed team",
    "delivery", "release", "prioritization",
]

_FRESHNESS_HOURS = 48


class RelevanceScorer:
    """
    Fast, deterministic relevance scorer – no LLM call required.

    Args:
        high_keywords:   List of high-importance keywords (from config).
        medium_keywords: List of medium-importance keywords (from config).
        low_keywords:    List of low-importance keywords (from config).
    """

    def __init__(
        self,
        high_keywords: list[str],
        medium_keywords: list[str],
        low_keywords: list[str],
    ) -> None:
        self._high_kw = list({kw.lower() for kw in high_keywords + _BUILTIN_HIGH_KEYWORDS})
        self._medium_kw = list({kw.lower() for kw in medium_keywords + _BUILTIN_MEDIUM_KEYWORDS})
        self._low_kw = [kw.lower() for kw in low_keywords]

    def score(self, article: Article, source: Source) -> float:
        """
        Compute a PM relevance score for an article.

        Args:
            article: The article to score.
            source:  The source the article came from.

        Returns:
            Float between 0.0 and 100.0.
        """
        text = self._normalise(article.title or "", article.raw_content or "")
        total = 0.0

        # 1. Keyword scoring (cap at 50)
        kw_score = 0
        for kw in self._high_kw:
            if kw in text:
                kw_score += _HIGH_KW_SCORE
        for kw in self._medium_kw:
            if kw in text:
                kw_score += _MEDIUM_KW_SCORE
        for kw in self._low_kw:
            if kw in text:
                kw_score += _LOW_KW_SCORE
        total += min(kw_score, 50)

        # 2. Source boost (from config, max 20)
        total += min(source.relevance_boost or 0, 20)

        # 3. Category bonus (max 20)
        total += _CATEGORY_BONUSES.get(article.category or "general", 0)

        # 4. Freshness bonus (max 10)
        total += self._freshness_bonus(article.published_at or article.collected_at)

        # 5. Title heuristics (max 10)
        total += self._title_bonus(article.title or "")

        final = min(round(total, 1), 100.0)
        logger.debug(
            "Scored '%s': %.1f (kw=%d, src=%d, cat=%d)",
            (article.title or "")[:40],
            final,
            min(kw_score, 50),
            source.relevance_boost or 0,
            _CATEGORY_BONUSES.get(article.category or "general", 0),
        )
        return final

    @staticmethod
    def _normalise(*texts: str) -> str:
        """Merge and lowercase all text for keyword matching."""
        return " ".join(texts).lower()

    @staticmethod
    def _freshness_bonus(published_at: Optional[datetime]) -> float:
        """Award up to 10 points for very recent articles."""
        if not published_at:
            return 0.0
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - published_at).total_seconds() / 3600
        if age_hours < 6:
            return 10.0
        if age_hours < 24:
            return 7.0
        if age_hours < _FRESHNESS_HOURS:
            return 4.0
        return 0.0

    @staticmethod
    def _title_bonus(title: str) -> float:
        """Heuristic title quality signals (max 10)."""
        score = 0.0
        title_lower = title.lower()

        action_words = ["launch", "release", "introduce", "announce", "new", "2024", "2025", "2026"]
        for word in action_words:
            if word in title_lower:
                score += 1.0
                break

        # PM tool and framework signals the PM manager cares about
        pm_tool_signals = ["jira", "asana", "linear", "confluence", "safe", "okr", "scrum", "agile", "kanban"]
        for sig in pm_tool_signals:
            if sig in title_lower:
                score += 2.0
                break

        # Good title length
        if 20 <= len(title) <= 120:
            score += 2.0

        return min(score, 10.0)
