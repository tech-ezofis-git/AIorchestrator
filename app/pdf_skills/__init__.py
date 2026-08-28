"""PDF skills package — JSON-to-PDF rendering and document compilation."""
from app.pdf_skills.pdf_generator import (
    generate_pdf_from_json,
    list_available_templates,
    load_template,
    PdfGenerationResult,
)
from app.pdf_skills.template_renderer import is_pdfme_template, render_pdfme_template_to_pdf

__all__ = [
    "generate_pdf_from_json",
    "list_available_templates",
    "load_template",
    "is_pdfme_template",
    "render_pdfme_template_to_pdf",
    "PdfGenerationResult",
]
