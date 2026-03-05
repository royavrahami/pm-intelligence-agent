"""
Keyword Extractor – PM Intelligence Agent.
Pulls the most meaningful keywords from an article.

Strategy (in order of preference):
  1. If key_insights exist (AI-generated) → parse noun phrases from them
  2. Ask OpenAI for a compact keyword list (when API key is available)
  3. Fallback: fast statistical extraction from title + content
     (stop-word filtering + frequency ranking)
"""

from __future__ import annotations

import json
import logging
import re
import string
from collections import Counter

from openai import OpenAI

from src.config.settings import settings
from src.storage.models import Article

logger = logging.getLogger(__name__)

_STOP_WORDS = {
    "a","an","the","and","or","but","in","on","at","to","for","of","with",
    "by","from","is","are","was","were","be","been","being","have","has","had",
    "do","does","did","will","would","could","should","may","might","shall",
    "this","that","these","those","it","its","we","our","they","their","you",
    "your","he","she","his","her","i","my","me","us","them","who","which",
    "what","how","when","where","why","not","no","so","as","if","than","then",
    "can","just","also","more","new","use","used","using","about","into","over",
    "after","before","through","up","out","all","any","each","most","other",
    "some","such","only","same","both","been","here","there","while","though",
    "well","now","make","made","get","got","give","given","like","need","want",
    "help","work","show","come","go","see","say","said","know","think","take",
    "however","therefore","thus","hence","while","since","although","whether",
    "include","including","based","provides","using","allows","enables","offers",
    "support","supports","provide","ensures","involves","requires","focuses",
}

_MIN_KW_LEN = 3
_MAX_KEYWORDS = 6


class KeywordExtractor:
    """
    Extracts a compact, representative keyword list from a PM-focused article.

    Tries AI-powered extraction first, falls back to statistical extraction
    when no API key is configured or a rate limit is hit.
    """

    def __init__(self, use_llm: bool = True) -> None:
        self._use_llm = use_llm and bool(settings.openai_api_key)

    def extract(self, article: Article) -> list[str]:
        """
        Return up to _MAX_KEYWORDS representative keywords for the article.

        Args:
            article: The article to extract keywords from.

        Returns:
            List of keyword strings, most important first.
        """
        if article.key_insights:
            keywords = self._from_insights(article.key_insights)
            if keywords:
                return keywords[:_MAX_KEYWORDS]

        if self._use_llm:
            keywords = self._from_llm(article)
            if keywords:
                return keywords[:_MAX_KEYWORDS]

        return self._statistical(article.title or "", article.raw_content or "")

    @staticmethod
    def _from_insights(key_insights_json: str) -> list[str]:
        """
        Extract meaningful noun phrases from the AI-generated PM insights.
        Each insight sentence → pick the most meaningful 2-3 words.
        """
        try:
            insights: list[str] = json.loads(key_insights_json)
        except (json.JSONDecodeError, TypeError):
            return []

        keywords: list[str] = []
        for insight in insights:
            # Capitalised terms (frameworks, product names, methodologies)
            caps = re.findall(r'\b[A-Z][a-zA-Z]{2,}\b', insight)
            for w in caps:
                if w.lower() not in _STOP_WORDS and w not in keywords:
                    keywords.append(w)

            # PM-specific acronyms
            pm_terms = re.findall(
                r'\b(?:okr|okrs|safe|pmo|kpi|dora|jira|scrum|kanban|ci/cd|devops|sre|agile)\b',
                insight.lower(),
            )
            for t in pm_terms:
                if t.upper() not in keywords:
                    keywords.append(t.upper())

        return keywords[:_MAX_KEYWORDS]

    def _from_llm(self, article: Article) -> list[str]:
        """Ask GPT to return the top PM-focused keywords as a JSON array."""
        try:
            client = OpenAI(api_key=settings.openai_api_key)

            text = f"{article.title}\n{(article.raw_content or '')[:1500]}"
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a keyword extractor for project/program management content. "
                            "Return ONLY a JSON array of up to 6 short, meaningful keywords "
                            "or keyphrases (2-3 words max each). "
                            "Focus on PM frameworks, tools, methodologies, concepts. "
                            "No stop words. "
                            'Example: ["OKRs", "Agile Sprint", "Kanban", "SAFe", "Risk Management"]'
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                max_tokens=80,
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(k) for k in data]
            for key in ("keywords", "items", "tags"):
                if key in data and isinstance(data[key], list):
                    return [str(k) for k in data[key]]
        except Exception as exc:
            logger.debug("LLM keyword extraction failed: %s", exc)
        return []

    def _statistical(self, title: str, content: str) -> list[str]:
        """
        Fast frequency-based keyword extraction.
        Title words are weighted 3× to boost PM topic terms.
        """
        text = f"{title} {title} {title} {content}"
        tokens = self._tokenise(text)

        freq: Counter = Counter()
        for token in tokens:
            if (
                len(token) >= _MIN_KW_LEN
                and token not in _STOP_WORDS
                and not token.isdigit()
            ):
                freq[token] += 1

        return [w.title() for w, _ in freq.most_common(_MAX_KEYWORDS)]

    @staticmethod
    def _tokenise(text: str) -> list[str]:
        """Lowercase and split text into word tokens, removing punctuation."""
        text = text.lower()
        text = text.translate(str.maketrans(string.punctuation, " " * len(string.punctuation)))
        return text.split()
