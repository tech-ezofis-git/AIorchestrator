"""OCR agent skills + rules (reusable; selected after OCR intent)."""

from app.ocr_skills import rules
from app.ocr_skills.extract_fields import SKILL_ID, run as extract_fields

__all__ = ["SKILL_ID", "extract_fields", "rules"]
