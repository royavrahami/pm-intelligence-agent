"""
PM Intelligence Agent – Application Settings

All configuration is driven by environment variables (12-factor app pattern).
Copy .env.example → .env and fill in the required values.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Centralised settings object – instantiated once as a module-level singleton.
    Every subsystem imports `from src.config.settings import settings`.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai_api_key: str = Field(default="", description="OpenAI API key (required for AI features)")
    openai_model: str = Field(default="gpt-4o-mini", description="OpenAI model for summarisation")
    openai_max_tokens: int = Field(default=600, description="Max tokens per summarisation call")

    # ── GitHub ────────────────────────────────────────────────────────────────
    github_token: str = Field(default="", description="GitHub PAT – raises API rate limit from 60 to 5000/hr")

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="sqlite:///./data/pm_agent.db",
        description="SQLAlchemy database URL (SQLite default, PostgreSQL supported)",
    )

    # ── Scheduler ─────────────────────────────────────────────────────────────
    schedule_interval_hours: int = Field(
        default=6,
        ge=1,
        le=168,
        description="How often the agent runs (hours)",
    )

    # ── Relevance ─────────────────────────────────────────────────────────────
    min_relevance_score: int = Field(
        default=55,
        ge=0,
        le=100,
        description="Minimum score (0–100) to include an article in reports/summaries",
    )

    # ── SMTP Notifications ────────────────────────────────────────────────────
    smtp_host: str = Field(default="smtp.gmail.com", description="SMTP server hostname")
    smtp_port: int = Field(default=587, description="SMTP port (587=STARTTLS, 465=SSL)")
    smtp_user: str = Field(default="", description="SMTP login username")
    smtp_password: str = Field(default="", description="SMTP login password or app-password")
    notify_email: str = Field(default="", description="Recipient email for alert notifications")

    # ── Slack Notifications ───────────────────────────────────────────────────
    slack_bot_token: str = Field(default="", description="Slack Bot OAuth token (xoxb-...)")
    slack_channel: str = Field(default="#pm-intelligence", description="Slack channel for alerts")

    # ── Paths ─────────────────────────────────────────────────────────────────
    reports_dir: Path = Field(
        default=Path("./reports"),
        description="Directory where HTML/MD reports are written",
    )
    log_dir: Path = Field(
        default=Path("./logs"),
        description="Directory for log files",
    )


# Module-level singleton – import this everywhere
settings = Settings()
