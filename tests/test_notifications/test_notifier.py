"""
Unit tests for the decomposed PM notifier channels.

Covers the email subject/body builders, the Slack block builder, the console
output, and the thin Notifier dispatcher — verifying the per-channel split
behaves correctly and the public API (Notifier().send / .send_digest) wires up.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from src.config.settings import settings as _settings
from src.notifications._common import _CATEGORY_META
from src.notifications.email_renderer import EmailRenderer
from src.notifications.notifier import Notifier
from src.notifications.slack_notifier import SlackNotifier
from src.storage.models import Article, Trend

_PUB = datetime(2026, 5, 1, 9, 30, tzinfo=timezone.utc)


def _trend(name: str, category: str = "agile", *, alert: bool = True) -> Trend:
    return Trend(
        name=name,
        category=category,
        description="A trend description.",
        momentum_score=82.5,
        article_count=7,
        is_alert=alert,
    )


def _article(i: int) -> Article:
    return Article(
        title=f"PM Article {i}",
        url=f"https://example.com/a-{i}",
        category="agile" if i % 2 else "leadership",
        key_insights=json.dumps([f"Insight {i}A", f"Insight {i}B"]),
        published_at=_PUB,
        pm_relevance=f"Why it matters to PMs: {i}.",
        raw_content=f"Body {i} " * 4,
        summary=f"Summary of article {i}.",
        relevance_score=80 - i * 6,
    )


def _disable_email_and_slack(monkeypatch) -> None:
    for attr in ("smtp_user", "smtp_password", "notify_email", "slack_bot_token"):
        monkeypatch.setattr(_settings, attr, None, raising=False)


def test_build_subject_alert_vs_no_alert() -> None:
    with_alerts = EmailRenderer.build_subject([_trend("T1")], "01 May 2026")
    no_alerts = EmailRenderer.build_subject([], "01 May 2026")
    assert "01 May 2026" in with_alerts
    assert "alert" in with_alerts.lower()
    assert "01 May 2026" in no_alerts
    assert with_alerts != no_alerts


def test_build_professional_email_contains_article_and_trend() -> None:
    html = EmailRenderer.build_professional_email(
        alert_trends=[_trend("Surge in Agile adoption")],
        all_trends=[_trend("Surge in Agile adoption")],
        articles=[_article(1), _article(2)],
        date_str="01 May 2026",
    )
    assert isinstance(html, str)
    assert "PM Intelligence" in html
    assert "PM Article 1" in html
    assert "Surge in Agile adoption" in html


def test_build_slack_blocks_includes_alert_icon() -> None:
    blocks = SlackNotifier.build_slack_blocks([_trend("Surge", "agile")], None)
    assert isinstance(blocks, list)
    text = json.dumps(blocks, ensure_ascii=False)
    assert "Surge" in text
    assert _CATEGORY_META["agile"]["icon"] in text


def test_notifier_send_console_only_does_not_raise(monkeypatch) -> None:
    _disable_email_and_slack(monkeypatch)
    Notifier().send(
        alert_trends=[_trend("T1")],
        all_trends=[],
        articles=[],
        report_path=None,
    )


def test_notifier_send_digest_console_only_does_not_raise(monkeypatch) -> None:
    _disable_email_and_slack(monkeypatch)
    stats = SimpleNamespace(
        date_str="01 May 2026",
        total_articles=3,
        avg_relevance=55.0,
        alert_count=1,
        category_counts={"agile": 2},
    )
    Notifier().send_digest(
        digest_articles=[],
        stats=stats,
        alert_trends=[_trend("T1")],
        report_path=None,
    )


def test_send_email_builds_body_and_attempts_send(monkeypatch) -> None:
    monkeypatch.setattr(_settings, "smtp_user", "from@example.com", raising=False)
    monkeypatch.setattr(_settings, "smtp_password", "app-password", raising=False)
    monkeypatch.setattr(_settings, "notify_email", "to@example.com", raising=False)
    monkeypatch.setattr(_settings, "smtp_host", "smtp.example.com", raising=False)
    monkeypatch.setattr(_settings, "smtp_port", 587, raising=False)

    captured: dict[str, str] = {}

    class FakeSMTP:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> bool:
            return False

        def ehlo(self) -> None:
            pass

        def starttls(self) -> None:
            pass

        def login(self, *args) -> None:
            pass

        def sendmail(self, from_addr, to_addr, body) -> None:
            captured["body"] = body

    monkeypatch.setattr("src.notifications.email_renderer.smtplib.SMTP", FakeSMTP)

    EmailRenderer.send_email(
        alert_trends=[_trend("T1")],
        all_trends=[],
        articles=[_article(1)],
        report_path=None,
    )

    assert "body" in captured
    assert "PM Intelligence" in captured["body"]


def _digest_article(i: int):
    return SimpleNamespace(
        title=f"Digest Article {i}",
        url=f"https://example.com/d-{i}",
        category="agile",
        keywords=["kw1", "kw2", "kw3"],
        relevance_score=70 - i,
        published_date="01 May 2026",
        collected_date="02 May 2026",
    )


def _digest_stats():
    return SimpleNamespace(
        date_str="01 May 2026",
        total_articles=2,
        avg_relevance=60.0,
        alert_count=1,
        category_counts={"agile": 2},
        top_keywords=[("agile", 5), ("okrs", 3)],
    )


def test_send_digest_email_builds_and_sends(monkeypatch) -> None:
    monkeypatch.setattr(_settings, "smtp_user", "from@example.com", raising=False)
    monkeypatch.setattr(_settings, "smtp_password", "pw", raising=False)
    monkeypatch.setattr(_settings, "notify_email", "to@example.com", raising=False)
    monkeypatch.setattr(_settings, "smtp_host", "smtp.example.com", raising=False)
    monkeypatch.setattr(_settings, "smtp_port", 587, raising=False)

    captured: dict[str, str] = {}

    class FakeSMTP:
        def __init__(self, *a, **k) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a) -> bool:
            return False

        def ehlo(self) -> None:
            pass

        def starttls(self) -> None:
            pass

        def login(self, *a) -> None:
            pass

        def sendmail(self, frm, to, body) -> None:
            captured["body"] = body

    monkeypatch.setattr("src.notifications.email_renderer.smtplib.SMTP", FakeSMTP)

    EmailRenderer.send_digest_email(
        [_digest_article(1), _digest_article(2)],
        _digest_stats(),
        [_trend("Surge in Agile")],
        None,
    )

    # HTML body is base64-encoded in the MIME message; assert plaintext headers.
    assert "body" in captured
    assert "PM Intelligence Agent" in captured["body"]  # plaintext From header
    assert len(captured["body"]) > 500


def test_send_slack_posts_message(monkeypatch) -> None:
    import slack_sdk

    monkeypatch.setattr(_settings, "slack_bot_token", "xoxb-test", raising=False)
    monkeypatch.setattr(_settings, "slack_channel", "#pm", raising=False)

    posted: dict[str, object] = {}

    class FakeWebClient:
        def __init__(self, token=None) -> None:
            posted["token"] = token

        def chat_postMessage(self, channel, blocks, text):
            posted["channel"] = channel
            posted["blocks"] = blocks

    monkeypatch.setattr(slack_sdk, "WebClient", FakeWebClient)

    SlackNotifier.send_slack([_trend("Surge in Agile")], None)

    assert posted["channel"] == "#pm"
    assert isinstance(posted["blocks"], list) and posted["blocks"]
