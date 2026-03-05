"""
Daily Digest Report Generator – PM Intelligence Agent.

Produces a polished HTML report (+ Markdown companion) from a list of
DigestArticle objects and aggregate DigestStats.

Report sections:
  1. Header banner with day stats
  2. Alert trends
  3. Keyword cloud (top 20 terms of the day)
  4. Full article table (sortable columns)
  5. Article cards grouped by PM category (with summaries & keywords)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

if TYPE_CHECKING:
    from src.agent.daily_digest_agent import DigestArticle, DigestStats
    from src.storage.models import Trend

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"

_CATEGORY_META = {
    "project_management": {"label": "Project Management",   "icon": "📋", "color": "#0f3460"},
    "program_management": {"label": "Program Management",   "icon": "🗂️", "color": "#1e40af"},
    "agile":              {"label": "Agile & Scrum",         "icon": "🔄", "color": "#059669"},
    "leadership":         {"label": "Engineering Leadership","icon": "🎯", "color": "#7c3aed"},
    "strategy":           {"label": "Strategy & OKRs",       "icon": "🧭", "color": "#b45309"},
    "ai_pm":              {"label": "AI for PM",             "icon": "🤖", "color": "#db2777"},
    "tools":              {"label": "PM Tools",              "icon": "🛠️", "color": "#6366f1"},
    "general":            {"label": "General Tech",          "icon": "📰", "color": "#6b7280"},
}


class DailyDigestGenerator:
    """
    Generates the end-of-day PM digest report as HTML + Markdown.

    Args:
        reports_dir: Directory where reports are saved.
    """

    def __init__(self, reports_dir: Path) -> None:
        self._reports_dir = reports_dir
        self._reports_dir.mkdir(parents=True, exist_ok=True)
        self._env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(["html"]),
        )
        self._env.filters["cat_label"] = lambda c: _CATEGORY_META.get(c, {}).get("label", c)
        self._env.filters["cat_icon"] = lambda c: _CATEGORY_META.get(c, {}).get("icon", "📄")
        self._env.filters["cat_color"] = lambda c: _CATEGORY_META.get(c, {}).get("color", "#6b7280")

    def generate(
        self,
        digest_articles: list["DigestArticle"],
        stats: "DigestStats",
        alert_trends: list["Trend"],
    ) -> Optional[Path]:
        """
        Build and save the HTML + Markdown PM digest report.

        Returns:
            Path to the HTML file, or None on error.
        """
        if not digest_articles:
            logger.info("DailyDigestGenerator: no PM articles to report")
            return None

        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        date_label = now.strftime("%d %b %Y")

        html_path = self._reports_dir / f"pm_daily_digest_{timestamp}.html"
        md_path = self._reports_dir / f"pm_daily_digest_{timestamp}.md"

        categorised: dict[str, list] = {}
        for art in digest_articles:
            categorised.setdefault(art.category, []).append(art)

        context = {
            "generated_at": now,
            "date_label": date_label,
            "stats": stats,
            "alert_trends": alert_trends,
            "digest_articles": digest_articles,
            "categorised": categorised,
            "category_meta": _CATEGORY_META,
            "top_keywords": stats.top_keywords[:20],
        }

        try:
            template = self._env.get_template("daily_digest.html")
            html_content = template.render(**context)
        except Exception as exc:
            logger.warning("Template render failed (%s) – using inline HTML", exc)
            html_content = self._inline_html(context)

        html_path.write_text(html_content, encoding="utf-8")
        md_path.write_text(self._build_markdown(context), encoding="utf-8")

        logger.info(
            "PM daily digest saved: %s (%d articles)", html_path.name, len(digest_articles)
        )
        return html_path

    # ── Markdown builder ──────────────────────────────────────────────────────

    @staticmethod
    def _build_markdown(ctx: dict) -> str:
        lines: list[str] = []
        s = ctx["stats"]
        lines.append(f"# 📋 PM Daily Digest – {ctx['date_label']}")
        lines.append(
            f"\n**Articles:** {s.total_articles} | "
            f"**Avg Score:** {s.avg_relevance} | "
            f"**Alerts:** {s.alert_count}"
        )

        if ctx["top_keywords"]:
            kw_str = " • ".join(f"`{kw}`" for kw, _ in ctx["top_keywords"][:15])
            lines.append(f"\n**Top Keywords:** {kw_str}")

        if ctx["alert_trends"]:
            lines.append("\n## 🚨 Alert Trends\n")
            for t in ctx["alert_trends"]:
                lines.append(
                    f"- **{t.name}** ({t.category}) — momentum: {t.momentum_score:.1f}"
                )

        lines.append("\n## 📊 All Articles\n")
        lines.append("| # | Title | Category | Keywords | Published | Collected | Score |")
        lines.append("|---|-------|----------|----------|-----------|-----------|-------|")
        for i, a in enumerate(ctx["digest_articles"], 1):
            kws = ", ".join(a.keywords[:4])
            cat = _CATEGORY_META.get(a.category, {}).get("label", a.category)
            lines.append(
                f"| {i} | [{a.title[:55]}]({a.url}) "
                f"| {cat} | {kws} "
                f"| {a.published_date} | {a.collected_date} | {a.relevance_score} |"
            )

        lines.append("\n## 📰 Articles with PM Summaries\n")
        for a in ctx["digest_articles"]:
            if not a.has_summary:
                continue
            lines.append(f"### [{a.title}]({a.url})")
            lines.append(f"**Score:** {a.relevance_score} | **Category:** {a.category}")
            lines.append(f"**Keywords:** {', '.join(a.keywords)}")
            lines.append(f"**Published:** {a.published_date} | **Collected:** {a.collected_date}")
            if a.summary:
                lines.append(f"\n{a.summary}\n")

        return "\n".join(lines)

    # ── Inline HTML fallback ──────────────────────────────────────────────────

    @staticmethod
    def _inline_html(ctx: dict) -> str:
        """Minimal HTML built without Jinja2 (fallback if template missing)."""
        s = ctx["stats"]
        date_label = ctx["date_label"]

        kw_html = " ".join(
            f'<span style="display:inline-block;background:#dbeafe;color:#1e40af;'
            f'border-radius:9px;padding:3px 10px;margin:3px;font-size:0.85em;">'
            f'{kw} <small style="color:#3b82f6;">({cnt})</small></span>'
            for kw, cnt in ctx["top_keywords"][:20]
        )

        rows_html = ""
        for i, a in enumerate(ctx["digest_articles"], 1):
            cat_meta = _CATEGORY_META.get(a.category, {})
            icon = cat_meta.get("icon", "📄")
            color = cat_meta.get("color", "#6b7280")
            kws = ", ".join(a.keywords[:4])
            score_color = "#059669" if a.relevance_score >= 70 else "#d97706" if a.relevance_score >= 50 else "#9ca3af"
            rows_html += f"""
            <tr>
              <td style="text-align:center;color:#9ca3af;">{i}</td>
              <td><a href="{a.url}" target="_blank" style="color:#0f3460;font-weight:600;text-decoration:none;">{a.title[:70]}</a></td>
              <td><span style="background:{color}22;color:{color};border-radius:9px;padding:2px 8px;font-size:0.8em;white-space:nowrap;">{icon} {cat_meta.get('label', a.category)}</span></td>
              <td style="font-size:0.82em;color:#374151;">{kws}</td>
              <td style="font-size:0.8em;color:#6b7280;white-space:nowrap;">{a.published_date}</td>
              <td style="font-size:0.8em;color:#6b7280;white-space:nowrap;">{a.collected_date}</td>
              <td style="text-align:center;"><strong style="color:{score_color};">{a.relevance_score}</strong></td>
            </tr>"""

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>PM Daily Digest – {date_label}</title>
  <style>
    body{{font-family:system-ui,sans-serif;background:#f5f7fa;color:#1a1a2e;margin:0;padding:0;}}
    .header{{background:linear-gradient(135deg,#0f3460 0%,#1e40af 100%);color:white;padding:32px 40px;}}
    .header h1{{margin:0;font-size:1.8rem;}} .header p{{margin:6px 0 0;opacity:0.75;}}
    .stats{{display:flex;gap:20px;margin-top:20px;flex-wrap:wrap;}}
    .stat{{background:rgba(255,255,255,0.15);border-radius:10px;padding:12px 20px;text-align:center;min-width:90px;}}
    .stat .n{{font-size:1.8rem;font-weight:800;}} .stat .l{{font-size:0.72rem;opacity:0.8;text-transform:uppercase;}}
    .container{{max-width:1100px;margin:0 auto;padding:28px 20px;}}
    h2{{color:#0f3460;border-bottom:3px solid #e94560;padding-bottom:8px;margin-top:40px;}}
    table{{width:100%;border-collapse:collapse;background:white;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.07);}}
    th{{background:#0f3460;color:white;padding:10px 14px;text-align:left;font-size:0.85em;}}
    td{{padding:10px 14px;border-bottom:1px solid #f3f4f6;vertical-align:top;}}
    tr:last-child td{{border-bottom:none;}} tr:hover td{{background:#fafafa;}}
    .kw-cloud{{background:white;border-radius:10px;padding:20px;margin:20px 0;}}
    .alert-card{{background:#fff8f8;border-left:4px solid #e94560;border-radius:8px;padding:14px 18px;margin:10px 0;}}
  </style>
</head>
<body>
<div class="header">
  <h1>📋 PM Daily Digest – {date_label}</h1>
  <p>Project & Program Management Intelligence – End-of-day summary</p>
  <div class="stats">
    <div class="stat"><div class="n">{s.total_articles}</div><div class="l">Articles</div></div>
    <div class="stat"><div class="n">{s.avg_relevance}</div><div class="l">Avg Score</div></div>
    <div class="stat"><div class="n">{s.alert_count}</div><div class="l">Alerts</div></div>
    <div class="stat"><div class="n">{len(s.category_counts)}</div><div class="l">Categories</div></div>
  </div>
</div>

<div class="container">

  {''.join(f'<div class="alert-card"><strong style="color:#e94560;">🚨 {t.name}</strong> <span style="font-size:0.8em;color:#6b7280;">({t.category}) momentum: {t.momentum_score:.1f}</span><p style="margin:6px 0 0;font-size:0.9em;">{t.description or ""}</p></div>' for t in ctx["alert_trends"]) if ctx["alert_trends"] else ""}

  <h2>🔤 Keywords of the Day</h2>
  <div class="kw-cloud">{kw_html}</div>

  <h2>📊 All Articles ({s.total_articles})</h2>
  <table>
    <thead>
      <tr>
        <th>#</th><th>Title</th><th>Category</th><th>Keywords</th>
        <th>Published</th><th>Collected by Agent</th><th>Score</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>

</div>
<div style="text-align:center;padding:20px;color:#9ca3af;font-size:0.75em;border-top:1px solid #e5e7eb;margin-top:32px;">
  PM Intelligence Agent – Daily Digest – {date_label}
</div>
</body></html>"""
