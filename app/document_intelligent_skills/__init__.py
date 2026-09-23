"""Document Intelligent skills + rules."""

from app.document_intelligent_skills import rules
from app.document_intelligent_skills.match_repository import SKILL_ID, run as match_repository

__all__ = ["SKILL_ID", "match_repository", "rules"]
