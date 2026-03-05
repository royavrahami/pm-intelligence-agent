"""
Tests for the PM Report Generator.
One class per file per project convention.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.reports.report_generator import ReportGenerator
from src.storage.models import Article, Source, Trend


def _make_source() -> Source:
    source = Source(
        id=1,
        name="PMI Blog",
        url="https://www.pmi.org/feed",
        source_type="rss",
        category="project_management",
        relevance_boost=15,
    )
    return source


def _make_article(score: float = 75.0, category: str = "agile", has_summary: bool = True) -> Article:
    article = Article(
        id=1,
        source_id=1,
        title="Agile Transformation: What PMs Need to Know in 2026",
        url="https://example.com/pm/agile-2026",
        category=category,
        raw_content="Detailed discussion of agile transformation practices.",
        relevance_score=score,
        is_processed=True,
        published_at=datetime.now(timezone.utc),
        collected_at=datetime.now(timezone.utc),
    )
    if has_summary:
        article.summary = "Agile transformation continues to evolve for enterprise PM teams."
        article.key_insights = json.dumps([
            "SAFe adoption increases 30% YoY in enterprise environments",
            "Remote PI planning requires new facilitation toolkits",
            "AI-assisted retrospectives improve team morale metrics",
        ])
        article.pm_relevance = "Essential reading for program managers leading agile transformations."
    return article


def _make_trend(is_alert: bool = False) -> Trend:
    trend = Trend(
        id=1,
        name="AI-Powered Roadmapping Tools",
        description="A growing wave of LLM-powered tools that automate roadmap generation.",
        category="ai_pm",
        momentum_score=72.5,
        article_count=8,
        is_alert=is_alert,
        first_seen_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
    )
    return trend


class TestReportGeneratorOutput:
    """Verify the PM report generator produces valid HTML and Markdown output."""

    def test_generate_creates_html_file(self, tmp_path: Path):
        """generate() must create an HTML file in the reports directory."""
        generator = ReportGenerator(reports_dir=tmp_path)
        articles = [_make_article()]
        trends = [_make_trend()]

        path = generator.generate(articles=articles, trends=trends, run_id=42)

        assert path is not None
        assert path.exists()
        assert path.suffix == ".html"

    def test_generate_creates_markdown_companion(self, tmp_path: Path):
        """generate() must also produce a .md companion file."""
        generator = ReportGenerator(reports_dir=tmp_path)
        articles = [_make_article()]
        trends = [_make_trend()]

        path = generator.generate(articles=articles, trends=trends, run_id=1)

        md_path = path.with_suffix(".md")
        assert md_path.exists()

    def test_html_contains_pm_specific_title(self, tmp_path: Path):
        """The generated HTML must reference PM content (not QA)."""
        generator = ReportGenerator(reports_dir=tmp_path)
        articles = [_make_article()]
        trends = []

        path = generator.generate(articles=articles, trends=trends, run_id=1)
        html = path.read_text(encoding="utf-8")

        assert "PM Intelligence" in html or "Project Management" in html or "Agile" in html

    def test_html_contains_article_title(self, tmp_path: Path):
        """The generated report must include the article title."""
        generator = ReportGenerator(reports_dir=tmp_path)
        article = _make_article()
        path = generator.generate(articles=[article], trends=[], run_id=1)
        html = path.read_text(encoding="utf-8")

        assert "Agile Transformation" in html

    def test_html_contains_alert_section_when_alert_trend(self, tmp_path: Path):
        """Report must show an ALERT section when a trend has is_alert=True."""
        generator = ReportGenerator(reports_dir=tmp_path)
        articles = [_make_article()]
        alert_trend = _make_trend(is_alert=True)

        path = generator.generate(articles=articles, trends=[alert_trend], run_id=1)
        html = path.read_text(encoding="utf-8")

        assert "Alert" in html or "🚨" in html


class TestReportGeneratorEdgeCases:
    """Verify the generator handles edge cases gracefully."""

    def test_returns_none_when_no_articles_or_trends(self, tmp_path: Path):
        """generate() should return None if there is nothing to report."""
        generator = ReportGenerator(reports_dir=tmp_path)
        path = generator.generate(articles=[], trends=[], run_id=0)
        assert path is None

    def test_handles_article_without_summary(self, tmp_path: Path):
        """Articles with no AI summary should still appear in the report."""
        generator = ReportGenerator(reports_dir=tmp_path)
        article = _make_article(has_summary=False)
        path = generator.generate(articles=[article], trends=[], run_id=1)

        assert path is not None
        html = path.read_text(encoding="utf-8")
        assert "Agile Transformation" in html

    def test_markdown_contains_top_articles_section(self, tmp_path: Path):
        """The Markdown companion should include a Top Articles section."""
        generator = ReportGenerator(reports_dir=tmp_path)
        articles = [_make_article()]
        path = generator.generate(articles=articles, trends=[], run_id=1)

        md_path = path.with_suffix(".md")
        content = md_path.read_text(encoding="utf-8")
        assert "Top Articles" in content
