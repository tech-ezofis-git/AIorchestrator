"""Insight agent skills — generate locked {insights: [...]} from data/text."""

from app.insight_skills.generate_insights import SKILL_ID, run as generate_insights

__all__ = ["SKILL_ID", "generate_insights"]
