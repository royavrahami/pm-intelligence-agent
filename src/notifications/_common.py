"""Shared notification helpers used across the email/slack/console channels."""

from __future__ import annotations

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
