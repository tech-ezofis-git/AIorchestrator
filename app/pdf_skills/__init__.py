"""PDF skills package — JSON-to-PDF rendering and document compilation."""
from app.pdf_skills.pdf_generator import generate_pdf_from_json, PdfGenerationResult

__all__ = ["generate_pdf_from_json", "PdfGenerationResult"]
