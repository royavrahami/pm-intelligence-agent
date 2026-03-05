"""
Trend Analyzer – PM Intelligence Agent.
Identifies emerging patterns and trends across collected articles
using OpenAI and persists them as Trend records.

Algorithm:
  1. Gather recent processed articles.
  2. Submit a batch to the LLM asking it to identify PM/PgM themes.
  3. For each identified trend, upsert a Trend record and link articles.
  4. Calculate momentum_score based on article growth rate.
  5. Flag high-momentum trends as alerts.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import openai
from openai import OpenAI

from src.config.settings import settings
from src.storage.models import Article, Trend
from src.storage.repository import ArticleRepository, TrendRepository

logger = logging.getLogger(__name__)

_TREND_DETECTION_PROMPT = """You are a Senior Program Management Analyst tracking the Project Management,
Program Management, and Engineering Leadership landscape in high-tech companies.

Analyse the following list of article titles and summaries collected in the last 7 days.
Identify the TOP 5 most significant trends, shifts, or emerging topics relevant to:
- Project & program management practices
- Agile methodologies and frameworks
- Engineering leadership and team management
- OKRs, roadmapping, and strategic planning
- AI/LLM tools for PM productivity
- DevOps and delivery excellence

For each trend return a JSON object:
{
  "name": "<Short trend name, max 60 chars>",
  "description": "<2-3 sentences describing the trend and why it matters>",
  "category": "<one of: project_management | program_management | agile | leadership | strategy | ai_pm | tools>",
  "is_alert": <true if this trend requires IMMEDIATE attention from a Project/Program Manager>,
  "article_indices": [<list of 0-based indices from the input that support this trend>]
}

Return a JSON object with a single key `trends` whose value is an array of trend objects.
Example: {"trends": [{"name": "...", "description": "...", ...}, ...]}
No markdown, no preamble.
Focus on: AI-assisted PM tools, agile framework shifts, new leadership patterns, OKR methodologies, remote/hybrid team challenges."""

_MOMENTUM_ALERT_THRESHOLD = 5


class TrendAnalyzer:
    """
    Detects PM/PgM trends from recently collected and processed articles.

    Args:
        article_repo: Repository for Article records.
        trend_repo:   Repository for Trend records.
        api_key:      OpenAI API key (optional override).
        model:        OpenAI model name (optional override).
    """

    def __init__(
        self,
        article_repo: ArticleRepository,
        trend_repo: TrendRepository,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        self._article_repo = article_repo
        self._trend_repo = trend_repo
        self._client = OpenAI(api_key=api_key or settings.openai_api_key)
        self._model = model or settings.openai_model

    def analyse(self, lookback_days: int = 7) -> list[Trend]:
        """
        Run PM-focused trend detection on articles from the last N days.

        Args:
            lookback_days: How many days of articles to analyse.

        Returns:
            List of newly created or updated Trend objects.
        """
        since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        articles = self._article_repo.get_for_report(
            since=since,
            min_score=settings.min_relevance_score,
        )

        if len(articles) < 5:
            logger.info("TrendAnalyzer: not enough articles (%d) for PM trend detection", len(articles))
            return []

        logger.info("TrendAnalyzer: analysing %d articles for PM trends", len(articles))
        time.sleep(2)  # Rate-limit buffer before the LLM call
        trend_data = self._detect_trends_with_llm(articles)

        if not trend_data:
            return []

        created_trends: list[Trend] = []
        for td in trend_data:
            try:
                trend = self._upsert_trend(td, articles)
                if trend:
                    created_trends.append(trend)
            except Exception as exc:
                logger.warning("Failed to upsert PM trend '%s': %s", td.get("name"), exc)

        logger.info("TrendAnalyzer: detected/updated %d PM trends", len(created_trends))
        return created_trends

    def _detect_trends_with_llm(self, articles: list[Article]) -> list[dict]:
        """Send article metadata to OpenAI and parse the PM trend response."""
        article_list_str = "\n".join(
            f"[{i}] {a.title} | {a.category} | {a.summary[:120] if a.summary else (a.raw_content or '')[:120]}"
            for i, a in enumerate(articles[:50])
        )

        user_content = f"Articles collected (last 7 days):\n{article_list_str}"

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _TREND_DETECTION_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=1500,
                temperature=0.4,
                response_format={"type": "json_object"},
                timeout=60,
            )
            raw = response.choices[0].message.content
            data = json.loads(raw)

            if isinstance(data, list):
                # Ensure list contains dicts, not primitives (e.g. article indices)
                return [item for item in data if isinstance(item, dict)]

            if isinstance(data, dict):
                # 1. Try well-known wrapper keys – only accept lists of dicts
                for key in ("trends", "items", "results", "data", "output", "response"):
                    if key in data and isinstance(data[key], list):
                        candidates = [item for item in data[key] if isinstance(item, dict)]
                        if candidates:
                            return candidates

                # 2. Find the first list-of-dicts anywhere in the values
                for value in data.values():
                    if isinstance(value, list) and value:
                        candidates = [item for item in value if isinstance(item, dict)]
                        if candidates:
                            logger.debug("TrendAnalyzer: extracted list-of-dicts from dict values (keys=%s)", list(data.keys()))
                            return candidates

                # 3. The dict values themselves might be trend objects (keyed by index)
                expected_keys = {"name", "description", "category", "is_alert"}
                dict_values = [v for v in data.values() if isinstance(v, dict)]
                if dict_values and any(expected_keys & set(v.keys()) for v in dict_values):
                    logger.debug("TrendAnalyzer: wrapping dict-of-trends into list (keys=%s)", list(data.keys()))
                    return dict_values

                # 4. Last resort: the dict itself IS a single trend object
                if expected_keys & set(data.keys()):
                    logger.debug("TrendAnalyzer: wrapping single flat trend dict into list")
                    return [data]

            logger.warning(
                "Unexpected PM trend response format: %s | keys: %s",
                type(data),
                list(data.keys()) if isinstance(data, dict) else "N/A",
            )
            return []

        except openai.RateLimitError:
            logger.warning("OpenAI rate limit hit during PM trend analysis")
        except openai.APITimeoutError:
            logger.warning("OpenAI API timeout during PM trend analysis (timeout=60s)")
        except Exception as exc:
            logger.error("PM trend LLM call failed: %s", exc)
        return []

    def _upsert_trend(self, trend_data: dict, articles: list[Article]) -> Optional[Trend]:
        """Create or update a Trend record and link supporting articles."""
        name = trend_data.get("name", "").strip()
        if not name:
            return None

        trend, created = self._trend_repo.get_or_create(
            name=name,
            category=trend_data.get("category", "general"),
        )

        trend.description = trend_data.get("description", "")
        trend.last_seen_at = datetime.now(timezone.utc)

        article_indices = trend_data.get("article_indices", [])
        for idx in article_indices:
            if 0 <= idx < len(articles):
                self._trend_repo.link_article(trend, articles[idx])

        trend.momentum_score = self._calculate_momentum(trend)

        if trend.article_count >= _MOMENTUM_ALERT_THRESHOLD or trend_data.get("is_alert"):
            trend.is_alert = True

        if created:
            logger.info("New PM trend detected: '%s' (category=%s)", name, trend.category)

        return trend

    @staticmethod
    def _calculate_momentum(trend: Trend) -> float:
        """
        Simple momentum = articles/day since first seen, capped at 100.
        """
        if not trend.first_seen_at:
            return float(trend.article_count)

        first = trend.first_seen_at
        if first.tzinfo is None:
            first = first.replace(tzinfo=timezone.utc)
        days_active = max(
            (datetime.now(timezone.utc) - first).total_seconds() / 86400,
            0.1,
        )
        return min(round((trend.article_count / days_active) * 10, 2), 100.0)
