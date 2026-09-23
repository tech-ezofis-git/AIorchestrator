"""FTL Quote Estimator package."""
from app.ftl.quote_estimator.agent import run_quote_estimation
from app.ftl.quote_estimator.extract import (
    build_candidate_text,
    extract_docx_text,
    extract_pdf_text,
    parse_eml_bytes,
    render_candidate_text_for_model,
)
from app.ftl.quote_estimator.pricelist_store import (
    car_door_panel_prices,
    door_operator_prices,
    governor_prices,
    known_product_codes,
    panel_set_prices,
    reindex_pricelist,
    search_pricelist,
    status as pricelist_status,
)
from app.ftl.quote_estimator.quote_pdf import generate_quote_pdf
from app.ftl.quote_estimator.quote_template import compute_totals, render_quote_html
from app.ftl.quote_estimator.quotes_store import (
    append_quote,
    get_quote,
    list_quotes,
    next_estimate_number,
)
from app.ftl.quote_estimator.skill_store import (
    get_skill,
    save_instructions,
    save_reference,
    save_template,
)

__all__ = [
    "run_quote_estimation",
    "build_candidate_text",
    "extract_docx_text",
    "extract_pdf_text",
    "parse_eml_bytes",
    "render_candidate_text_for_model",
    "car_door_panel_prices",
    "door_operator_prices",
    "governor_prices",
    "known_product_codes",
    "panel_set_prices",
    "reindex_pricelist",
    "search_pricelist",
    "pricelist_status",
    "generate_quote_pdf",
    "compute_totals",
    "render_quote_html",
    "append_quote",
    "get_quote",
    "list_quotes",
    "next_estimate_number",
    "get_skill",
    "save_instructions",
    "save_reference",
    "save_template",
]
