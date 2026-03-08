"""
Notifier – PM Intelligence Agent.
Sends the PM Intelligence Report via configured channels:
  - Email  : full rendered HTML report sent directly in the email body
             (CSS variables are resolved; the same report you open in the browser)
  - Slack  : summary + alert trends via Bot API
  - Console: rich formatted summary (always active)

Gmail App Password setup:
  1. Enable 2-Step Verification: https://myaccount.google.com/security
  2. Create App Password: https://myaccount.google.com/apppasswords
  3. Use the 16-char password as SMTP_PASSWORD in .env
"""

from __future__ import annotations

import json
import logging
import re
import smtplib
from collections import defaultdict
from datetime import datetime, timezone
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.config.settings import settings
from src.storage.models import Article, Trend

logger = logging.getLogger(__name__)
console = Console()

# PM category display metadata – used by Slack blocks and digest email
_CATEGORY_META: dict[str, dict[str, str]] = {
    "project_management": {"label": "Project Management",    "icon": "📋", "color": "#1d4ed8", "bg": "#dbeafe"},
    "program_management": {"label": "Program Management",    "icon": "🗂️", "color": "#7c3aed", "bg": "#ede9fe"},
    "agile":              {"label": "Agile & Scrum",          "icon": "🔄", "color": "#065f46", "bg": "#d1fae5"},
    "leadership":         {"label": "Engineering Leadership", "icon": "🎯", "color": "#92400e", "bg": "#fef3c7"},
    "strategy":           {"label": "Strategy & OKRs",        "icon": "🧭", "color": "#9f1239", "bg": "#ffe4e6"},
    "ai_pm":              {"label": "AI for PM",              "icon": "🤖", "color": "#155e75", "bg": "#cffafe"},
    "tools":              {"label": "PM Tools",               "icon": "🛠️", "color": "#4a1d96", "bg": "#f3e8ff"},
    "general":            {"label": "General Tech",           "icon": "📰", "color": "#374151", "bg": "#f3f4f6"},
}


def _score_color(score: float) -> str:
    """Return a hex colour representing the relevance score band."""
    if score >= 75:
        return "#059669"
    if score >= 55:
        return "#d97706"
    return "#6b7280"


class Notifier:
    """
    Multi-channel notification dispatcher for the PM Intelligence Agent.

    Activated channels (auto-detected from settings):
      - Email : SMTP_USER + SMTP_PASSWORD + NOTIFY_EMAIL all set
      - Slack : SLACK_BOT_TOKEN set
    """

    def send(
        self,
        alert_trends: list[Trend],
        all_trends: Optional[list[Trend]] = None,
        articles: Optional[list[Article]] = None,
        report_path: Optional[Path] = None,
    ) -> None:
        """
        Dispatch PM intelligence notifications to all configured channels.

        Args:
            alert_trends: High-momentum trends requiring immediate attention.
            all_trends:   All detected trends (unused here, kept for API compat).
            articles:     Scored articles (unused here, kept for API compat).
            report_path:  Path to the generated HTML report file (sent as email body).
        """
        self._console_output(alert_trends, report_path)

        if settings.smtp_user and settings.smtp_password and settings.notify_email:
            self._send_email(
                alert_trends=alert_trends,
                all_trends=all_trends or [],
                articles=articles or [],
                report_path=report_path,
            )
        else:
            logger.info(
                "Email not configured – set SMTP_USER, SMTP_PASSWORD, NOTIFY_EMAIL to enable"
            )

        if settings.slack_bot_token:
            self._send_slack(alert_trends, report_path)

    def send_digest(
        self,
        digest_articles: list,
        stats,
        alert_trends: list[Trend],
        report_path: Optional[Path] = None,
    ) -> None:
        """
        Send the PM daily digest report via all configured channels.

        Args:
            digest_articles: List of DigestArticle objects.
            stats:           DigestStats summary object.
            alert_trends:    Alert-level trends.
            report_path:     Path to the generated HTML digest file.
        """
        self._console_digest_output(stats, alert_trends, report_path)

        if settings.smtp_user and settings.smtp_password and settings.notify_email:
            self._send_digest_email(digest_articles, stats, alert_trends, report_path)
        else:
            logger.info("Email not configured – PM digest email skipped")

    # ── Console ───────────────────────────────────────────────────────────────

    @staticmethod
    def _console_output(alert_trends: list[Trend], report_path: Optional[Path]) -> None:
        """Print a rich-formatted PM summary to the terminal."""
        console.print()

        if alert_trends:
            table = Table(
                title="🚨 PM ALERTS – Immediate Attention Required",
                box=box.ROUNDED,
                style="bold red",
                header_style="bold white on red",
            )
            table.add_column("Trend", style="bold white")
            table.add_column("Category", style="cyan")
            table.add_column("Momentum", style="yellow", justify="right")
            table.add_column("Articles", justify="right")
            for trend in alert_trends:
                table.add_row(
                    trend.name,
                    trend.category,
                    f"{trend.momentum_score:.1f}",
                    str(trend.article_count),
                )
            console.print(table)
        else:
            console.print(Panel(
                "[green]✓ No critical PM alerts this cycle[/green]",
                title="Alert Status",
                border_style="green",
            ))

        if report_path:
            console.print(Panel(
                f"[bold blue]Report:[/bold blue] {report_path}",
                title="📄 PM Report Generated",
                border_style="blue",
            ))
        console.print()

    def _console_digest_output(self, stats, alert_trends, report_path) -> None:
        """Print PM digest summary to terminal."""
        table = Table(
            title=f"📋 PM Daily Digest – {stats.date_str}",
            box=box.ROUNDED,
            header_style="bold white on #0f3460",
        )
        table.add_column("Metric")
        table.add_column("Value", justify="right", style="bold")
        table.add_row("Articles collected", str(stats.total_articles))
        table.add_row("Average score", str(stats.avg_relevance))
        table.add_row("Alert trends", str(stats.alert_count))
        table.add_row("Categories", str(len(stats.category_counts)))
        console.print(table)
        if report_path:
            console.print(Panel(
                f"[bold blue]PM Digest saved:[/bold blue] {report_path}",
                border_style="blue",
            ))

    # ── Email ─────────────────────────────────────────────────────────────────

    def _send_email(
        self,
        alert_trends: list[Trend],
        all_trends: list[Trend],
        articles: list[Article],
        report_path: Optional[Path] = None,
    ) -> None:
        """
        Build and send the PM Intelligence Report using the professional
        table-based email template — same structure as the QA agent.
        The full HTML report file is attached for browser viewing.
        """
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%d %b %Y")
        subject = self._build_subject(alert_trends, date_str)

        email_body = self._build_professional_email(
            alert_trends=alert_trends,
            all_trends=all_trends,
            articles=articles,
            date_str=date_str,
        )

        # Use "mixed" so we can attach the HTML report file alongside the inline body
        msg = MIMEMultipart("mixed")
        msg["Subject"] = subject
        msg["From"] = f"PM Intelligence Agent <{settings.smtp_user}>"
        msg["To"] = settings.notify_email

        alt_part = MIMEMultipart("alternative")
        alt_part.attach(MIMEText(email_body, "html", "utf-8"))
        msg.attach(alt_part)

        # Attach the full HTML report file
        if report_path and report_path.exists():
            report_html = report_path.read_text(encoding="utf-8")
            attachment = MIMEBase("text", "html")
            attachment.set_payload(report_html.encode("utf-8"))
            encoders.encode_base64(attachment)
            attachment.add_header(
                "Content-Disposition", "attachment", filename=report_path.name
            )
            msg.attach(attachment)
            logger.info("Attached full PM report: %s", report_path.name)

        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.login(settings.smtp_user, settings.smtp_password)
                server.sendmail(settings.smtp_user, settings.notify_email, msg.as_string())
            logger.info("PM email report sent to %s (subject: %s)", settings.notify_email, subject)
        except smtplib.SMTPAuthenticationError:
            logger.error(
                "Email authentication failed. "
                "For Gmail, use an App Password: https://myaccount.google.com/apppasswords"
            )
        except Exception as exc:
            logger.error("Failed to send PM email: %s", exc)

    @staticmethod
    def _build_subject(alert_trends: list[Trend], date_str: str) -> str:
        if alert_trends:
            return f"🚨 [{date_str}] PM Intelligence – {len(alert_trends)} trend alert{'s' if len(alert_trends) != 1 else ''} require attention"
        return f"📋 [{date_str}] PM Intelligence Report – New update ready"

    @staticmethod
    def _make_email_safe(html: str) -> str:
        """
        Preprocess the Jinja-rendered report HTML for Gmail / Outlook compatibility.

        1. Resolve all CSS custom properties (var(--name)) to their hardcoded values.
        2. Remove the :root { } declaration that defines those variables.
        3. Strip transition / :hover / @media rules that email clients ignore.

        The <style> block is preserved – Gmail has supported it since 2016, so
        class-based styles continue to work without inlining.
        """
        _CSS_VARS = {
            "--primary":  "#0f3460",
            "--accent":   "#e94560",
            "--light":    "#f5f7fa",
            "--card-bg":  "#ffffff",
            "--border":   "#e0e6ed",
            "--text":     "#1a1a2e",
            "--muted":    "#6b7280",
            "--success":  "#10b981",
            "--warning":  "#f59e0b",
        }
        for name, value in _CSS_VARS.items():
            html = html.replace(f"var({name})", value)

        html = re.sub(r":root\s*\{[^}]*\}", "", html, flags=re.DOTALL)
        html = re.sub(r"transition\s*:[^;]+;", "", html)
        html = re.sub(r"[^{}]+:hover\s*\{[^}]*\}", "", html, flags=re.DOTALL)
        html = re.sub(r"@media[^{]*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", "", html, flags=re.DOTALL)
        return html

    @staticmethod
    def _build_fallback_html(alert_trends: list[Trend], date_str: str) -> str:
        """Minimal compact card – shown only when the report file is unavailable."""
        status = (
            f"🚨 {len(alert_trends)} active alert{'s' if len(alert_trends) != 1 else ''}"
            if alert_trends else "✅ All clear — no active PM alerts"
        )
        color = "#b91c1c" if alert_trends else "#166534"
        bg    = "#fef2f2" if alert_trends else "#f0fdf4"
        return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:24px;background:#f0f4f8;font-family:'Segoe UI',Arial,sans-serif;">
  <div style="max-width:600px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;
              box-shadow:0 2px 12px rgba(0,0,0,0.1);">
    <div style="background:linear-gradient(135deg,#0f3460,#1e40af);padding:28px 32px;">
      <h1 style="margin:0;color:#fff;font-size:22px;">📋 PM Intelligence Report</h1>
      <p style="margin:6px 0 0;color:rgba(255,255,255,0.7);font-size:13px;">{date_str}</p>
    </div>
    <div style="padding:24px 32px;">
      <div style="background:{bg};border:1px solid;border-radius:8px;padding:14px 18px;">
        <span style="font-size:14px;font-weight:600;color:{color};">{status}</span>
      </div>
      <p style="color:#6b7280;font-size:13px;margin-top:16px;">
        The report file was not available for this run. Check the reports/ directory on the server.
      </p>
    </div>
    <div style="background:#f8fafc;border-top:1px solid #e5e7eb;padding:12px 32px;text-align:center;">
      <p style="margin:0;color:#9ca3af;font-size:11px;">PM Intelligence Agent · {date_str}</p>
    </div>
  </div>
</body></html>"""

    # ── (removed: _build_professional_email – replaced by _make_email_safe) ──

    @staticmethod
    def _build_professional_email(
        alert_trends: list[Trend],
        all_trends: list[Trend],
        articles: list[Article],
        date_str: str,
    ) -> str:
        """
        Render the full professional PM Intelligence email.

        Uses table-based layout with inline styles only – compatible with
        Gmail, Outlook 2016+, Apple Mail, and all major email clients.
        No external fonts, no CSS variables, no JavaScript.
        
        UI/UX optimizations:
        - Limited to top 5 articles (prevents email bloat)
        - Max 5 alerts (reduces alert fatigue)
        - Compact article cards for better readability
        """
        # ── Derived data ──────────────────────────────────────────────────────
        # Limit to top 5 articles for email (full list in web report)
        top_articles = sorted(articles, key=lambda a: a.relevance_score, reverse=True)[:5]
        remaining_articles = len(articles) - 5 if len(articles) > 5 else 0
        
        # Limit alerts to max 5 to prevent alert fatigue
        display_alerts = alert_trends[:5] if alert_trends else []
        extra_alerts = len(alert_trends) - 5 if len(alert_trends) > 5 else 0

        # Group all articles by category (sorted by score within each category)
        categorised: dict[str, list[Article]] = defaultdict(list)
        for a in articles:
            categorised[a.category or "general"].append(a)
        for cat in categorised:
            categorised[cat].sort(key=lambda a: a.relevance_score, reverse=True)

        total_categories = len(categorised)
        sorted_trends = sorted(all_trends, key=lambda t: t.momentum_score, reverse=True)[:7]

        # ── ① Executive stats bar ─────────────────────────────────────────────
        stats_cells = [
            (str(len(articles)),         "Articles"),
            (str(len(all_trends)),        "Trends"),
            (str(len(alert_trends)),      "Alerts"),
            (str(total_categories),       "Categories"),
        ]
        stats_html = "".join(f"""
          <td align="center" style="padding:0 12px;">
            <div style="font-size:28px;font-weight:800;color:#ffffff;line-height:1;">{num}</div>
            <div style="font-size:10px;color:rgba(255,255,255,0.65);text-transform:uppercase;
                        letter-spacing:0.8px;margin-top:4px;">{lbl}</div>
          </td>""" for num, lbl in stats_cells)

        # ── ② Alert / all-clear section (limited to 5 max) ──────────────────
        if display_alerts:
            alert_rows = ""
            for t in display_alerts:
                meta = _CATEGORY_META.get(t.category or "general", _CATEGORY_META["general"])
                desc_row = (
                    f'<tr><td style="padding:0 0 8px;">'
                    f'<span style="color:#374151;font-size:13px;line-height:1.5;">{t.description}</span>'
                    f'</td></tr>'
                ) if t.description else ""
                alert_rows += f"""
          <tr>
            <td style="padding:0 0 10px;">
              <table width="100%" cellpadding="0" cellspacing="0"
                     style="background:#fff8f8;border:1px solid #fecaca;
                            border-left:5px solid #e94560;border-radius:8px;">
                <tr>
                  <td style="padding:16px 18px;">
                    <table width="100%" cellpadding="0" cellspacing="0">
                      <tr>
                        <td>
                          <span style="font-size:16px;font-weight:700;color:#1a1a2e;">
                            {meta['icon']} {t.name}
                          </span>
                          &nbsp;
                          <span style="background:#fde68a;color:#92400e;border-radius:4px;
                                       padding:2px 8px;font-size:11px;font-weight:700;">
                            🔥 ALERT
                          </span>
                        </td>
                      </tr>
                      {desc_row}
                      <tr>
                        <td style="padding-top:8px;">
                          <span style="background:#d1fae5;color:#065f46;border-radius:4px;
                                       padding:3px 9px;font-size:11px;font-weight:600;">
                            📈 Momentum: {t.momentum_score:.1f}
                          </span>
                          &nbsp;
                          <span style="background:#dbeafe;color:#1d4ed8;border-radius:4px;
                                       padding:3px 9px;font-size:11px;font-weight:600;">
                            📰 {t.article_count} articles
                          </span>
                          &nbsp;
                          <span style="background:{meta['bg']};color:{meta['color']};border-radius:4px;
                                       padding:3px 9px;font-size:11px;">
                            {meta['label']}
                          </span>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>
            </td>
          </tr>"""

            extra_note = f'<span style="font-size:12px;color:#78350f;margin-left:8px;">(+{extra_alerts} more in full report)</span>' if extra_alerts > 0 else ""
            alerts_section = f"""
        <tr>
          <td style="padding:24px 32px 8px;">
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="background:#fff3cd;border:1px solid #fbbf24;border-radius:8px;">
              <tr><td style="padding:12px 18px;">
                <span style="font-size:14px;font-weight:700;color:#92400e;">
                  🚨 {len(alert_trends)} Active Trend Alert{'s' if len(alert_trends) != 1 else ''}  – Immediate Attention Required
                </span>
                {extra_note}
              </td></tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:0 32px 8px;">
            <table width="100%" cellpadding="0" cellspacing="0">{alert_rows}</table>
          </td>
        </tr>"""
        else:
            alerts_section = """
        <tr>
          <td style="padding:24px 32px 12px;">
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="background:#f0fdf4;border:1px solid #86efac;border-radius:8px;">
              <tr><td style="padding:14px 20px;">
                <table cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="width:36px;vertical-align:middle;">
                      <span style="font-size:22px;">✅</span>
                    </td>
                    <td style="vertical-align:middle;padding-left:10px;">
                      <div style="font-size:14px;font-weight:700;color:#166534;">
                        All clear — no active PM alerts this cycle
                      </div>
                      <div style="font-size:12px;color:#166534;opacity:0.8;margin-top:3px;">
                        All monitored trends are within normal momentum thresholds
                      </div>
                    </td>
                  </tr>
                </table>
              </td></tr>
            </table>
          </td>
        </tr>"""

        # ── ③ Top 5 articles with full detail structure ─────────────────────────
        top_article_rows = ""
        for i, article in enumerate(top_articles, 1):
            meta = _CATEGORY_META.get(article.category or "general", _CATEGORY_META["general"])
            score = article.relevance_score
            sc = _score_color(score)
            stars = max(1, min(5, round(score / 20)))
            filled_stars = '<span style="color:#fbbf24;font-size:13px;">★</span>' * stars
            empty_stars  = '<span style="color:#e5e7eb;font-size:13px;">★</span>' * (5 - stars)
            star_html = filled_stars + empty_stars
            pub = f" &nbsp;·&nbsp; {article.published_at.strftime('%d %b %Y')}" if article.published_at else ""

            # ── Summary ────────────────────────────────────────────────────────
            summary_row = ""
            if article.summary:
                summary_row = f'''<tr><td style="padding:12px 0 0;">
                  <div style="background:#f8fafc;border-left:4px solid #0f3460;border-radius:0 8px 8px 0;padding:14px 18px;">
                    <div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;font-weight:600;">Summary</div>
                    <div style="font-size:13px;color:#374151;line-height:1.65;">{article.summary[:400]}{"…" if len(article.summary or "") > 400 else ""}</div>
                  </div>
                </td></tr>'''
            elif article.raw_content:
                snippet = article.raw_content[:200].replace('\n', ' ').strip()
                summary_row = f'''<tr><td style="padding:12px 0 0;">
                  <div style="background:#f9fafb;border-left:3px solid #9ca3af;border-radius:0 8px 8px 0;padding:12px 16px;">
                    <div style="font-size:12px;color:#6b7280;line-height:1.5;font-style:italic;">{snippet}{"…" if len(article.raw_content or "") > 200 else ""}</div>
                  </div>
                </td></tr>'''

            # ── Key Insights ───────────────────────────────────────────────────
            insights_row = ""
            insights = []
            if article.key_insights:
                try:
                    insights = json.loads(article.key_insights)
                except Exception:
                    insights = []
            if insights:
                items_html = "".join(
                    f'<tr><td style="padding:4px 0;"><span style="font-size:13px;color:#1e40af;">▸ {ins}</span></td></tr>'
                    for ins in insights[:3]
                )
                insights_row = f'''<tr><td style="padding:8px 0 0;">
                  <table width="100%" cellpadding="0" cellspacing="0"
                         style="background:#eff6ff;border-left:4px solid #3b82f6;border-radius:0 8px 8px 0;">
                    <tr><td style="padding:10px 14px;">
                      <div style="font-size:11px;color:#1d4ed8;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;font-weight:600;">Key Insights</div>
                      <table width="100%" cellpadding="0" cellspacing="0">{items_html}</table>
                    </td></tr>
                  </table>
                </td></tr>'''

            # ── PM Relevance ───────────────────────────────────────────────────
            relevance_row = ""
            if article.pm_relevance:
                relevance_row = f'''<tr><td style="padding:8px 0 0;">
                  <table width="100%" cellpadding="0" cellspacing="0"
                         style="background:#f0fdf4;border:1px solid #86efac;border-radius:8px;">
                    <tr><td style="padding:12px 16px;">
                      <div style="font-size:11px;color:#166534;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;font-weight:600;">🗂️ Why This Matters for Program Managers</div>
                      <div style="font-size:13px;color:#166534;line-height:1.55;">{article.pm_relevance[:400]}{"…" if len(article.pm_relevance or "") > 400 else ""}</div>
                    </td></tr>
                  </table>
                </td></tr>'''

            divider = '<tr><td style="padding:16px 0;"><div style="height:1px;background:linear-gradient(90deg,transparent,#e5e7eb,transparent);"></div></td></tr>' if i < len(top_articles) else ""

            top_article_rows += f"""
          <tr><td style="padding:8px 0;">
            <table width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;">
              <tr><td style="padding:20px 24px;">
                <table width="100%" cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="width:36px;vertical-align:top;">
                      <div style="background:linear-gradient(135deg,#0f3460,#1e40af);color:#fff;width:32px;height:32px;
                                  border-radius:50%;text-align:center;line-height:32px;font-size:13px;font-weight:700;">{i}</div>
                    </td>
                    <td style="padding-left:12px;vertical-align:top;">
                      <a href="{article.url}" target="_blank" rel="noopener"
                         style="font-size:16px;font-weight:700;color:#0f3460;text-decoration:none;line-height:1.4;display:block;">
                        {article.title or "(No title)"}
                      </a>
                      <div style="margin-top:8px;">
                        <span style="background:{meta['bg']};color:{meta['color']};border-radius:6px;
                                     padding:4px 12px;font-size:12px;font-weight:600;display:inline-block;">
                          {meta['icon']} {meta['label']}
                        </span>
                        <span style="font-size:12px;color:#9ca3af;margin-left:8px;">{pub}</span>
                      </div>
                    </td>
                    <td align="right" style="width:72px;vertical-align:top;padding-left:12px;">
                      <div style="border:2px solid {sc};border-radius:8px;padding:6px 10px;text-align:center;white-space:nowrap;">
                        <div style="font-size:20px;font-weight:800;color:{sc};line-height:1;">{score:.0f}</div>
                        <div style="margin-top:3px;">{star_html}</div>
                      </div>
                    </td>
                  </tr>
                </table>
                <table width="100%" cellpadding="0" cellspacing="0">
                  {summary_row}{insights_row}{relevance_row}
                </table>
              </td></tr>
            </table>
          </td></tr>
          {divider}"""

        # ── ④ Category badges (clickable) + compact article sections ──────────
        cat_badges = ""
        cat_sections = ""
        for cat, cat_articles in categorised.items():
            meta = _CATEGORY_META.get(cat, _CATEGORY_META["general"])
            cat_id = f"cat-{cat.replace('_', '-')}"
            cat_badges += (
                f'<a href="#{cat_id}" style="display:inline-block;background:{meta["bg"]};'
                f'color:{meta["color"]};border-radius:16px;padding:6px 14px;font-size:12px;'
                f'font-weight:600;margin:4px 6px 4px 0;text-decoration:none;">'
                f'{meta["icon"]} {meta["label"]} ({len(cat_articles)})</a>'
            )
            article_links = ""
            for a in cat_articles[:5]:
                sc = _score_color(a.relevance_score)
                pub = f'<span style="font-size:10px;color:#9ca3af;margin-left:6px;">{a.published_at.strftime("%d %b")}</span>' if a.published_at else ""
                article_links += f"""
                <tr><td style="padding:8px 0;border-bottom:1px solid #f3f4f6;">
                  <table width="100%" cellpadding="0" cellspacing="0"><tr>
                    <td>
                      <a href="{a.url}" target="_blank" rel="noopener"
                         style="font-size:13px;font-weight:600;color:#0f3460;text-decoration:none;line-height:1.4;">
                        {a.title or "(No title)"}
                      </a>
                      {pub}
                    </td>
                    <td align="right" style="width:36px;padding-left:8px;vertical-align:top;">
                      <span style="background:{sc};color:#fff;border-radius:4px;padding:1px 6px;font-size:11px;font-weight:700;">{a.relevance_score:.0f}</span>
                    </td>
                  </tr></table>
                </td></tr>"""
            more_note = f'<tr><td style="padding:8px 0;"><span style="font-size:11px;color:#9ca3af;font-style:italic;">+{len(cat_articles)-5} more articles in this category</span></td></tr>' if len(cat_articles) > 5 else ""
            cat_sections += f"""
          <tr><td id="{cat_id}" style="padding:0 0 20px;">
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="border:1px solid #e5e7eb;border-radius:10px;overflow:hidden;">
              <tr><td style="background:{meta['bg']};padding:10px 18px;border-bottom:1px solid #e5e7eb;">
                <span style="font-size:14px;font-weight:700;color:{meta['color']};">{meta['icon']} {meta['label']}</span>
                <span style="font-size:11px;color:#6b7280;margin-left:8px;background:#fff;padding:1px 8px;border-radius:10px;">{len(cat_articles)} articles</span>
              </td></tr>
              <tr><td style="padding:0 18px 8px;background:#fff;">
                <table width="100%" cellpadding="0" cellspacing="0">{article_links}{more_note}</table>
              </td></tr>
            </table>
          </td></tr>"""


        # ── ⑤ Trend landscape ─────────────────────────────────────────────────
        if sorted_trends:
            trend_rows = ""
            for t in sorted_trends:
                tmeta = _CATEGORY_META.get(t.category or "general", _CATEGORY_META["general"])
                bar_pct = min(int(t.momentum_score), 100)
                alert_badge = (
                    ' &nbsp;<span style="background:#fde68a;color:#92400e;border-radius:3px;'
                    'padding:1px 6px;font-size:10px;font-weight:700;">🔥 ALERT</span>'
                ) if t.is_alert else ""
                desc_line = f'<div style="font-size:12px;color:#6b7280;margin-top:3px;">{t.description}</div>' if t.description else ""
                trend_rows += f"""
              <tr>
                <td style="padding:10px 0;border-bottom:1px solid #f3f4f6;">
                  <table width="100%" cellpadding="0" cellspacing="0">
                    <tr>
                      <td style="width:32px;vertical-align:top;">
                        <span style="font-size:18px;">{tmeta['icon']}</span>
                      </td>
                      <td style="padding-left:10px;">
                        <div style="font-size:13px;font-weight:700;color:#0f3460;">
                          {t.name}{alert_badge}
                        </div>
                        {desc_line}
                        <div style="margin-top:6px;background:#f3f4f6;border-radius:3px;height:5px;overflow:hidden;">
                          <div style="width:{bar_pct}%;height:100%;
                                      background:linear-gradient(90deg,#10b981,#e94560);
                                      border-radius:3px;"></div>
                        </div>
                        <div style="font-size:11px;color:#9ca3af;margin-top:4px;">
                          Momentum: {t.momentum_score:.1f} &nbsp;·&nbsp;
                          {t.article_count} articles &nbsp;·&nbsp;
                          {tmeta['label']}
                        </div>
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>"""

            trends_section = f"""
        <!-- ── Trend Landscape ──────────────────────────────── -->
        <tr>
          <td style="padding:32px 32px 0;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr><td style="padding-bottom:16px;">
                <table cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="width:4px;background:#e94560;border-radius:2px;">&nbsp;</td>
                    <td style="padding-left:12px;">
                      <span style="font-size:16px;font-weight:800;color:#0f3460;
                                   text-transform:uppercase;letter-spacing:0.5px;">
                        📈 Trend Landscape
                      </span>
                    </td>
                  </tr>
                </table>
              </td></tr>
            </table>
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="border:1px solid #e5e7eb;border-radius:10px;background:#ffffff;">
              <tr><td style="padding:0 18px;">
                <table width="100%" cellpadding="0" cellspacing="0">
                  {trend_rows}
                </table>
              </td></tr>
            </table>
          </td>
        </tr>"""
        else:
            trends_section = """
        <tr>
          <td style="padding:32px 32px 0;">
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;">
              <tr><td style="padding:16px 18px;">
                <span style="font-size:13px;color:#9ca3af;font-style:italic;">
                  📈 No trends detected yet — trends appear after multiple articles cluster around the same theme across several runs.
                </span>
              </td></tr>
            </table>
          </td>
        </tr>"""

        # ── Assemble full email ───────────────────────────────────────────────
        alert_badge_color = "#fef2f2" if alert_trends else "#f0fdf4"
        alert_text_color  = "#b91c1c" if alert_trends else "#166534"
        alert_border      = "#fecaca" if alert_trends else "#86efac"
        alert_label = (
            f"🚨 {len(alert_trends)} active alert{'s' if len(alert_trends) != 1 else ''}"
            if alert_trends else "✅ All clear — no active PM alerts"
        )

        return f"""<!DOCTYPE html>
<html lang="en" dir="ltr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>PM Intelligence Report – {date_str}</title>
</head>
<body dir="ltr" style="margin:0;padding:0;background:#eef2f7;font-family:'Segoe UI',Helvetica,Arial,sans-serif;direction:ltr;">

<table width="100%" cellpadding="0" cellspacing="0" dir="ltr" style="background:#eef2f7;padding:28px 0 40px;">
  <tr>
    <td align="center" style="padding:0 16px;">

      <!-- ════════════════ Card wrapper ════════════════ -->
      <table width="680" cellpadding="0" cellspacing="0" dir="ltr"
             style="max-width:680px;width:100%;background:#ffffff;
                    border-radius:16px;overflow:hidden;margin:0 auto;
                    box-shadow:0 4px 24px rgba(15,52,96,0.12);direction:ltr;text-align:left;">

        <!-- ── ① Header ───────────────────────────────────── -->
        <tr>
          <td style="background:linear-gradient(135deg,#0f3460 0%,#1e40af 60%,#0f3460 100%);
                     padding:32px 36px 28px;text-align:left;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="text-align:left;">
                  <div style="font-size:24px;font-weight:800;color:#ffffff;
                               letter-spacing:-0.5px;line-height:1.1;text-align:left;">
                    📋 PM Intelligence Report
                  </div>
                  <div style="font-size:13px;color:rgba(255,255,255,0.70);margin-top:5px;text-align:left;">
                    {date_str}
                  </div>
                </td>
              </tr>
              <!-- Stats bar -->
              <tr>
                <td style="padding-top:22px;text-align:left;">
                  <table cellpadding="0" cellspacing="0">
                    <tr>
                      {stats_html}
                    </tr>
                  </table>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- ── Alert status pill ──────────────────────────── -->
        <tr>
          <td style="background:#f8fafc;padding:14px 36px;border-bottom:1px solid #e5e7eb;">
            <table cellpadding="0" cellspacing="0">
              <tr>
                <td>
                  <span style="background:{alert_badge_color};color:{alert_text_color};
                               border:1px solid {alert_border};border-radius:999px;
                               padding:5px 18px;font-size:13px;font-weight:600;
                               display:inline-block;">
                    {alert_label}
                  </span>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- ── ② Alerts / all-clear ──────────────────────── -->
        {alerts_section}

        <!-- ── ③ Top 5 Articles ──────────────────────────── -->
        <tr>
          <td style="padding:32px 36px 0;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr><td style="padding-bottom:12px;">
                <table cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="width:4px;background:#e94560;border-radius:2px;">&nbsp;</td>
                    <td style="padding-left:12px;">
                      <span style="font-size:16px;font-weight:800;color:#0f3460;
                                   text-transform:uppercase;letter-spacing:0.5px;">
                        ⭐ Top 5 Articles
                      </span>
                      <span style="font-size:12px;color:#6b7280;margin-left:10px;">
                        {"(+" + str(remaining_articles) + " more articles in this report)" if remaining_articles else ""}
                      </span>
                    </td>
                  </tr>
                </table>
              </td></tr>
            </table>
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="border:1px solid #e5e7eb;border-radius:10px;
                          background:#ffffff;padding:12px 20px;">
              <tr><td>
                <table width="100%" cellpadding="0" cellspacing="0">
                  {top_article_rows}
                </table>
              </td></tr>
            </table>
          </td>
        </tr>

        <!-- ── ④ Articles by Category: clickable badges + article lists ─── -->
        <tr>
          <td style="padding:24px 36px 0;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr><td style="padding-bottom:12px;">
                <table cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="width:4px;background:#e94560;border-radius:2px;">&nbsp;</td>
                    <td style="padding-left:12px;">
                      <span style="font-size:16px;font-weight:800;color:#0f3460;text-transform:uppercase;letter-spacing:0.5px;">
                        📂 Articles by Category
                      </span>
                    </td>
                  </tr>
                </table>
              </td></tr>
              <tr><td style="padding-bottom:16px;">{cat_badges}</td></tr>
              {cat_sections}
            </table>
          </td>
        </tr>

        <!-- ── ⑤ Trend Landscape ──────────────────────────── -->
        {trends_section}

        <!-- ── ⑥ Footer ──────────────────────────────────── -->
        <tr>
          <td style="background:#f8fafc;border-top:1px solid #e5e7eb;
                     padding:24px 36px;margin-top:32px;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr><td style="text-align:center;padding-bottom:12px;">
                <span style="font-size:13px;font-weight:600;color:#374151;">PM Intelligence Agent</span>
                <span style="font-size:13px;color:#6b7280;"> &nbsp;·&nbsp; Product & Project Management</span>
              </td></tr>
              <tr><td style="text-align:center;">
                <span style="font-size:11px;color:#9ca3af;">
                  Report generated on {date_str} &nbsp;·&nbsp; Auto-generated daily digest
                </span>
              </td></tr>
              <tr><td style="text-align:center;padding-top:12px;">
                <span style="font-size:10px;color:#d1d5db;">
                  To modify delivery preferences, update your .env configuration
                </span>
              </td></tr>
            </table>
          </td>
        </tr>

      </table>
      <!-- ════════════════ end card ════════════════ -->

    </td>
  </tr>
</table>

</body>
</html>"""

    # ── Digest Email ──────────────────────────────────────────────────────────

    def _send_digest_email(self, digest_articles, stats, alert_trends, report_path) -> None:
        """Send the full PM digest as a professional HTML email."""
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%d %b %Y")
        subject = f"📋 [{date_str}] PM Daily Digest – {stats.total_articles} articles | {stats.alert_count} alerts"

        alert_block = ""
        if alert_trends:
            items = "".join(
                f"<li style='margin:6px 0;'><strong style='color:#e94560;'>{t.name}</strong> "
                f"<span style='color:#6b7280;font-size:0.85em;'>({t.category}) momentum: {t.momentum_score:.1f}</span></li>"
                for t in alert_trends
            )
            alert_block = f"""
            <div style="background:#fff8f8;border-left:4px solid #e94560;border-radius:6px;
                        padding:14px 18px;margin:20px 0;">
              <strong style="color:#e94560;">🚨 Alert Trends</strong>
              <ul style="margin:8px 0 0 16px;">{items}</ul>
            </div>"""

        kw_str = " ".join(
            f'<span style="background:#dbeafe;color:#1e40af;border-radius:9px;'
            f'padding:2px 8px;margin:2px;font-size:0.8em;">{kw}</span>'
            for kw, _ in stats.top_keywords[:15]
        )

        table_rows = ""
        for i, a in enumerate(digest_articles[:50], 1):
            kws = ", ".join(a.keywords[:3])
            sc = _score_color(a.relevance_score)
            table_rows += f"""
            <tr style="border-bottom:1px solid #f3f4f6;">
              <td style="padding:8px 10px;color:#9ca3af;font-size:0.82em;">{i}</td>
              <td style="padding:8px 10px;">
                <a href="{a.url}" style="color:#0f3460;font-weight:600;text-decoration:none;
                                         font-size:0.88em;">{a.title[:65]}</a>
              </td>
              <td style="padding:8px 10px;font-size:0.78em;color:#6b7280;">{a.category}</td>
              <td style="padding:8px 10px;font-size:0.78em;color:#374151;">{kws}</td>
              <td style="padding:8px 10px;font-size:0.76em;color:#9ca3af;white-space:nowrap;">{a.published_date}</td>
              <td style="padding:8px 10px;font-size:0.76em;color:#9ca3af;white-space:nowrap;">{a.collected_date}</td>
              <td style="padding:8px 10px;text-align:center;font-weight:700;
                         color:{sc};font-size:0.88em;">{a.relevance_score}</td>
            </tr>"""

        email_body = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="font-family:system-ui,sans-serif;background:#eef2f7;margin:0;padding:0;">
  <div style="background:linear-gradient(135deg,#0f3460 0%,#1e40af 100%);color:white;padding:28px 32px;">
    <h1 style="margin:0;font-size:1.5rem;">📋 PM Daily Digest – {date_str}</h1>
    <p style="margin:6px 0 0;opacity:0.75;font-size:0.88rem;">
      {stats.total_articles} articles &nbsp;|&nbsp; avg score: {stats.avg_relevance} &nbsp;|&nbsp; {stats.alert_count} alerts
    </p>
  </div>
  <div style="max-width:960px;margin:0 auto;padding:24px 20px;">
    {alert_block}
    <div style="margin:20px 0;">
      <strong style="color:#0f3460;">🔤 Top PM Keywords:</strong><br><br>{kw_str}
    </div>
    <h2 style="color:#0f3460;border-bottom:3px solid #e94560;padding-bottom:8px;">📊 Article Summary Table</h2>
    <div style="overflow-x:auto;border-radius:10px;box-shadow:0 1px 4px rgba(0,0,0,0.08);">
      <table style="width:100%;border-collapse:collapse;background:white;">
        <thead>
          <tr style="background:#0f3460;color:white;">
            <th style="padding:10px;font-size:0.78em;">#</th>
            <th style="padding:10px;text-align:left;font-size:0.78em;">Title</th>
            <th style="padding:10px;text-align:left;font-size:0.78em;">Category</th>
            <th style="padding:10px;text-align:left;font-size:0.78em;">Keywords</th>
            <th style="padding:10px;font-size:0.78em;">Published</th>
            <th style="padding:10px;font-size:0.78em;">Collected</th>
            <th style="padding:10px;font-size:0.78em;">Score</th>
          </tr>
        </thead>
        <tbody>{table_rows}</tbody>
      </table>
    </div>
  </div>
  <div style="text-align:center;padding:16px;color:#9ca3af;font-size:0.75em;border-top:1px solid #e5e7eb;">
    PM Intelligence Agent – Daily Digest – {date_str}
  </div>
</body></html>"""

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"PM Intelligence Agent <{settings.smtp_user}>"
        msg["To"] = settings.notify_email
        msg.attach(MIMEText(email_body, "html", "utf-8"))

        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.login(settings.smtp_user, settings.smtp_password)
                server.sendmail(settings.smtp_user, settings.notify_email, msg.as_string())
            logger.info("PM daily digest email sent to %s", settings.notify_email)
        except smtplib.SMTPAuthenticationError:
            logger.error("Email auth failed. Use Gmail App Password: https://myaccount.google.com/apppasswords")
        except Exception as exc:
            logger.error("Failed to send PM digest email: %s", exc)

    # ── Slack ─────────────────────────────────────────────────────────────────

    def _send_slack(
        self,
        alert_trends: list[Trend],
        report_path: Optional[Path],
    ) -> None:
        """Post a Slack notification with PM alert summary."""
        try:
            from slack_sdk import WebClient
        except ImportError:
            logger.warning("slack_sdk not installed – Slack notifications disabled")
            return

        client = WebClient(token=settings.slack_bot_token)
        blocks = self._build_slack_blocks(alert_trends, report_path)

        try:
            client.chat_postMessage(
                channel=settings.slack_channel,
                blocks=blocks,
                text="PM Intelligence Report",
            )
            logger.info("Slack PM notification sent to %s", settings.slack_channel)
        except Exception as exc:
            logger.error("Failed to send Slack PM notification: %s", exc)

    @staticmethod
    def _build_slack_blocks(alert_trends: list[Trend], report_path: Optional[Path]) -> list[dict]:
        """Build Slack Block Kit payload for PM alerts."""
        now = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
        category_icons = {k: v["icon"] for k, v in _CATEGORY_META.items()}

        blocks: list[dict] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "📋 PM Intelligence Report", "emoji": True},
            },
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"Generated: {now}"}],
            },
        ]

        if alert_trends:
            alert_text = "\n".join(
                f"• *{category_icons.get(t.category or 'general', '📌')} {t.name}* "
                f"({t.category}) — momentum: `{t.momentum_score:.1f}` | articles: `{t.article_count}`"
                for t in alert_trends
            )
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"🚨 *PM Alert Trends – Immediate Attention:*\n{alert_text}"},
            })
        else:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": "✅ *No critical PM alerts this cycle*"},
            })

        if report_path:
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"📄 *Report file:* `{report_path.name}`"},
            })

        return blocks
