"""
Report Generator – PM Intelligence Agent.
Produces a rich HTML report and a companion Markdown file
from processed articles and detected trends.

Report structure:
  1. Executive Summary (key numbers)
  2. Alert-level Trends (immediate attention required)
  3. Top Articles by Category (with AI summary, insights, PM relevance, link)
  4. Trend Landscape (all trends with momentum)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import openai
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.config.settings import settings
from src.storage.models import Article, Trend

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"

# PM-specific category display names and emoji indicators
_CATEGORY_META = {
    "project_management": {"label": "Project Management",  "icon": "📋"},
    "program_management": {"label": "Program Management",  "icon": "🗂️"},
    "agile":              {"label": "Agile & Scrum",        "icon": "🔄"},
    "leadership":         {"label": "Engineering Leadership","icon": "🎯"},
    "strategy":           {"label": "Strategy & OKRs",      "icon": "🧭"},
    "ai_pm":              {"label": "AI for PM",            "icon": "🤖"},
    "tools":              {"label": "PM Tools",             "icon": "🛠️"},
    "general":            {"label": "General Tech",         "icon": "📰"},
}


class ReportGenerator:
    """
    Generates HTML and Markdown PM intelligence reports.

    Args:
        reports_dir: Directory where report files are saved.
    """

    def __init__(self, reports_dir: Path) -> None:
        self._reports_dir = reports_dir
        self._reports_dir.mkdir(parents=True, exist_ok=True)
        self._jinja_env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(["html"]),
        )
        self._jinja_env.filters["category_label"] = lambda c: _CATEGORY_META.get(c, {}).get("label", c)
        self._jinja_env.filters["category_icon"] = lambda c: _CATEGORY_META.get(c, {}).get("icon", "📄")
        self._jinja_env.filters["format_dt"] = lambda dt: dt.strftime("%d %b %Y %H:%M UTC") if dt else "N/A"
        self._jinja_env.filters["score_stars"] = self._score_to_stars
        self._jinja_env.filters["score_color"] = self._score_to_color

    @staticmethod
    def _score_to_stars(score: float) -> str:
        """Convert a 0–100 relevance score to a filled/empty star string (★★★★☆)."""
        stars = max(1, min(5, round(score / 20)))
        return "★" * stars + "☆" * (5 - stars)

    @staticmethod
    def _score_to_color(score: float) -> str:
        """Return a hex color representing the score band."""
        if score >= 80:
            return "#059669"   # green
        if score >= 60:
            return "#d97706"   # amber
        if score >= 40:
            return "#6366f1"   # indigo
        return "#6b7280"       # grey

    def generate(
        self,
        articles: list[Article],
        trends: list[Trend],
        run_id: int,
        quota_warning: bool = False,
    ) -> Optional[Path]:
        """
        Build the full HTML report and save it to disk.

        Args:
            articles:      Processed, scored articles to include.
            trends:        Detected trends to highlight.
            run_id:        Agent run ID for traceability.
            quota_warning: Show API-quota warning banner (REQ-08).

        Returns:
            Path to the generated HTML report file.
        """
        if not articles and not trends:
            logger.info("PM ReportGenerator: nothing to report – skipping")
            return None

        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        report_path = self._reports_dir / f"pm_intelligence_report_{timestamp}.html"

        categorised = self._categorise_articles(articles)
        # REQ-06: build thematic sections with central idea summaries
        thematic_sections = self._build_thematic_sections(articles)

        context = {
            "generated_at": now,
            "run_id": run_id,
            "total_articles": len(articles),
            "total_trends": len(trends),
            "alert_trends": [t for t in trends if t.is_alert],
            "all_trends": sorted(trends, key=lambda t: t.momentum_score, reverse=True),
            "categorised_articles": categorised,
            "top_articles": sorted(articles, key=lambda a: a.relevance_score, reverse=True)[:10],
            "category_meta": _CATEGORY_META,
            "quota_warning": quota_warning,
            "thematic_sections": thematic_sections,
        }

        html_content = self._render_html(context)
        report_path.write_text(html_content, encoding="utf-8")

        md_path = report_path.with_suffix(".md")
        md_content = self._render_markdown(context)
        md_path.write_text(md_content, encoding="utf-8")

        logger.info(
            "PM report generated: %s (%d articles, %d trends)",
            report_path.name, len(articles), len(trends),
        )
        return report_path

    def _render_html(self, context: dict) -> str:
        """Render the HTML report from the Jinja2 template (with inline fallback)."""
        try:
            template = self._jinja_env.get_template("report.html")
            return template.render(**context)
        except Exception as exc:
            logger.error("HTML template render failed: %s – falling back to inline", exc)
            return self._build_inline_html(context)

    def _render_markdown(self, context: dict) -> str:
        """Render a Markdown companion report."""
        lines: list[str] = []
        now = context["generated_at"]
        lines.append(f"# PM Intelligence Report – {now.strftime('%d %b %Y %H:%M UTC')}")
        lines.append(
            f"\n**Run ID:** {context['run_id']} | "
            f"**Articles:** {context['total_articles']} | "
            f"**Trends:** {context['total_trends']}"
        )

        if context["alert_trends"]:
            lines.append("\n## 🚨 Alerts – Immediate Attention Required\n")
            for trend in context["alert_trends"]:
                lines.append(f"### {trend.name}")
                lines.append(f"{trend.description or ''}")
                cat_label = _CATEGORY_META.get(trend.category, {}).get("label", trend.category)
                lines.append(f"- **Category:** {cat_label}")
                lines.append(f"- **Momentum Score:** {trend.momentum_score:.1f}")
                lines.append("")

        lines.append("\n## Top Articles by Relevance\n")
        for article in context["top_articles"]:
            lines.append(f"### [{article.title}]({article.url})")
            cat_label = _CATEGORY_META.get(article.category, {}).get("label", article.category)
            lines.append(f"**Score:** {article.relevance_score:.0f} | **Category:** {cat_label}")
            if article.summary:
                lines.append(f"\n**Summary:** {article.summary}")
            if article.key_insights:
                try:
                    insights = json.loads(article.key_insights)
                    if insights:
                        lines.append("\n**Key Insights:**")
                        for insight in insights:
                            lines.append(f"- {insight}")
                except Exception:
                    pass
            if article.pm_relevance:
                lines.append(f"\n**For PM/PgM:** {article.pm_relevance}")
            lines.append("")

        lines.append("\n## Trend Landscape\n")
        for trend in context["all_trends"]:
            icon = _CATEGORY_META.get(trend.category, {}).get("icon", "📄")
            alert_badge = " 🚨" if trend.is_alert else ""
            lines.append(
                f"- **{icon} {trend.name}**{alert_badge} — "
                f"momentum: {trend.momentum_score:.1f}, articles: {trend.article_count}"
            )

        return "\n".join(lines)

    @staticmethod
    def _categorise_articles(articles: list[Article]) -> dict[str, list[Article]]:
        """Group articles by category, sorted by relevance score desc."""
        result: dict[str, list[Article]] = {}
        for article in articles:
            cat = article.category or "general"
            result.setdefault(cat, []).append(article)
        for cat in result:
            result[cat].sort(key=lambda a: a.relevance_score, reverse=True)
        return result

    @staticmethod
    def _build_inline_html(context: dict) -> str:
        """
        Fallback: build a minimal HTML report without Jinja2 templates.
        Used when the template file is missing or fails to render.
        """
        now = context["generated_at"].strftime("%d %b %Y %H:%M UTC")
        sections = [
            f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8">
<title>PM Intelligence Report – {now}</title>
<style>
  body {{font-family: system-ui, sans-serif; max-width: 960px; margin: 40px auto; padding: 0 20px; color: #1a1a2e;}}
  h1 {{color: #16213e;}} h2 {{color: #0f3460; border-bottom: 2px solid #e94560; padding-bottom: 8px;}}
  .card {{border: 1px solid #ddd; border-radius: 8px; padding: 16px; margin: 12px 0; background: #fafafa;}}
  .score {{display: inline-block; background: #e94560; color: white; border-radius: 12px; padding: 2px 10px; font-size: 0.85em;}}
  .alert {{background: #fff3cd; border-left: 4px solid #e94560;}}
  .insight {{background: #e8f4fd; border-left: 3px solid #0f3460; padding: 8px 12px; margin: 4px 0;}}
  a {{color: #0f3460;}} .meta {{color: #666; font-size: 0.85em;}}
  .trend-badge {{display: inline-block; background: #0f3460; color: white; border-radius: 12px; padding: 2px 8px; font-size: 0.8em; margin-right: 4px;}}
</style></head><body>
<h1>📋 PM Intelligence Report</h1>
<p class="meta">Generated: {now} | Run ID: {context['run_id']} |
Articles: {context['total_articles']} | Trends: {context['total_trends']}</p>"""
        ]

        if context["alert_trends"]:
            sections.append('<h2>🚨 Alerts – Immediate Attention Required</h2>')
            for trend in context["alert_trends"]:
                cat_label = _CATEGORY_META.get(trend.category, {}).get("label", trend.category)
                sections.append(
                    f'<div class="card alert"><strong>{trend.name}</strong> '
                    f'<span class="trend-badge">{cat_label}</span>'
                    f'<br>{trend.description or ""}'
                    f'<br><span class="meta">Momentum: {trend.momentum_score:.1f} | Articles: {trend.article_count}</span></div>'
                )

        sections.append('<h2>📰 Top Articles</h2>')
        for article in context["top_articles"]:
            cat_label = _CATEGORY_META.get(article.category, {}).get("label", article.category)
            cat_icon = _CATEGORY_META.get(article.category, {}).get("icon", "📄")
            insights_html = ""
            if article.key_insights:
                try:
                    insights = json.loads(article.key_insights)
                    insights_html = "".join(
                        f'<div class="insight">• {i}</div>' for i in insights
                    )
                except Exception:
                    pass

            pm_rel_html = (
                f'<p><strong>For PM/PgM:</strong> {article.pm_relevance}</p>'
                if article.pm_relevance else ""
            )

            sections.append(
                f'<div class="card">'
                f'<a href="{article.url}" target="_blank"><strong>{article.title}</strong></a>'
                f' <span class="score">{article.relevance_score:.0f}</span>'
                f'<br><span class="meta">{cat_icon} {cat_label}'
                + (f' | {article.published_at.strftime("%d %b %Y") if article.published_at else ""}')
                + f'</span>'
                f'<p>{article.summary or ""}</p>'
                + insights_html
                + pm_rel_html
                + '</div>'
            )

        sections.append('<h2>📈 Trend Landscape</h2>')
        for trend in context["all_trends"]:
            icon = _CATEGORY_META.get(trend.category, {}).get("icon", "📄")
            alert_icon = " 🚨" if trend.is_alert else ""
            sections.append(
                f'<div class="card">'
                f'<strong>{icon} {trend.name}</strong>{alert_icon}'
                f' <span class="trend-badge">momentum: {trend.momentum_score:.1f}</span>'
                f'<br>{trend.description or ""}'
                f'<br><span class="meta">Articles: {trend.article_count} | Category: {trend.category}</span>'
                f'</div>'
            )

        sections.append('</body></html>')
        return "\n".join(sections)

    @staticmethod
    def _build_thematic_sections(articles: list[Article]) -> list[dict]:
        """
        REQ-06: Group articles into 3-5 thematic sections using the LLM.

        Each section contains:
          - title:       Short thematic heading
          - central_idea: 2-4 sentence summary of the common thread
          - articles:    List of Article objects in this section

        Falls back to category-based grouping if the LLM is unavailable.
        """
        if not articles or not settings.openai_api_key:
            return []

        # Build a compact article list for the clustering prompt
        article_lines = "\n".join(
            f"{i+1}. [{a.category}] {a.title or '(no title)'}"
            for i, a in enumerate(articles[:40])  # limit to avoid token overrun
        )

        prompt = f"""You are a PM content strategist. Group the following articles into 3–5 thematic sections.
For each section, provide:
- A short title (4–7 words)
- A central_idea: 2–4 sentences describing the common thread for that section
- article_indices: a JSON array of the 1-based article numbers that belong

Articles:
{article_lines}

Return ONLY a valid JSON array. Example format:
[
  {{
    "title": "AI-Driven Project Delivery",
    "central_idea": "These articles explore how AI and LLMs are transforming how PMs plan, estimate, and track projects. The central theme is using automation to reduce manual overhead while improving forecast accuracy.",
    "article_indices": [1, 4, 7]
  }}
]"""

        try:
            from openai import OpenAI
            client = OpenAI(api_key=settings.openai_api_key)
            response = client.chat.completions.create(
                model=settings.openai_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1200,
                temperature=0.3,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content
            # The response is a JSON object wrapping the array
            parsed = json.loads(raw)
            # Handle both {"sections": [...]} and direct array forms
            if isinstance(parsed, list):
                sections_data = parsed
            elif isinstance(parsed, dict):
                sections_data = next(
                    (v for v in parsed.values() if isinstance(v, list)), []
                )
            else:
                sections_data = []

            result = []
            for sec in sections_data[:5]:
                indices = [i - 1 for i in sec.get("article_indices", []) if 1 <= i <= len(articles)]
                sec_articles = [articles[i] for i in indices if i < len(articles)]
                if sec_articles:
                    result.append({
                        "title": sec.get("title", ""),
                        "central_idea": sec.get("central_idea", ""),
                        "articles": sec_articles,
                    })
            if result:
                return result

        except openai.RateLimitError:
            logger.warning("REQ-06: rate limit hit during thematic clustering – skipping")
        except Exception as exc:
            logger.warning("REQ-06: thematic clustering failed (%s) – skipping", exc)

        return []
