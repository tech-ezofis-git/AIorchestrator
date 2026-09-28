"""Choose which Chatbot search tools run from the user message.

Edit the keyword lists below to change the routing:

1. Document words (document, file, pdf, ...) select repositories. They are not the file keyword.
   The other word, such as APEX, is the file keyword.
2. Request / ticket words → workflows (definitions, tickets, and comments).
3. Invoice, PO, or a document number → repository documents and workflows.
4. Anything else → repository documents, comments, and tickets.
   Forms, master forms, the repository-name table, and the workflow
   definition table are not queried.
"""
from __future__ import annotations

import re
from typing import Literal

from app.chatbot.query_rewrite import rewrite_search_query
from app.global_search.sql_search import normalize_query

SearchScope = Literal["repository", "workflow", "both", "other"]

# Rule 3 is checked first so "document number" is not treated as rule 1.
_BOTH_RE = re.compile(
    r"\b(?:invoices?|purchase\s+orders?)\b"
    r"|\bp\.?o\.?\b"
    r"|\b(?:document|doc)\s*(?:number|no\.?|#)\b"
    r"|\b(?:inv|po|doc)[\s\-_/]*\d",
    re.IGNORECASE,
)

# Rule 1 — look inside repositories (item metadata + RAG), not the name catalog.
_DOCUMENT_RE = re.compile(
    r"\b(?:documents?|docs?|files?|pdfs?|attachments?|contents?)\b",
    re.IGNORECASE,
)

# Rule 2 — workflow definitions, tickets, and comments.
_WORKFLOW_RE = re.compile(
    r"\b(?:requests?|tickets?|workflows?)\b",
    re.IGNORECASE,
)

_SCOPE_TOOLS: dict[SearchScope, tuple[str, ...]] = {
    "repository": (
        "search_repo_metadata",
        "search_repo_rag",
    ),
    "workflow": (
        "search_workflows",
        "search_tickets",
        "search_comments",
    ),
    "both": (
        "search_repo_metadata",
        "search_repo_rag",
        "search_workflows",
        "search_tickets",
        "search_comments",
    ),
    "other": (
        "search_repo_metadata",
        "search_repo_rag",
        "search_comments",
        "search_tickets",
    ),
}


def classify_search_scope(message: str) -> SearchScope:
    """Map a Chatbot search message to repository, workflow, both, or other."""
    text = (message or "").strip()
    if not text:
        return "other"
    if _BOTH_RE.search(text):
        return "both"
    has_documents = _DOCUMENT_RE.search(text) is not None
    has_workflow = _WORKFLOW_RE.search(text) is not None
    if has_documents and has_workflow:
        return "both"
    if has_documents:
        return "repository"
    if has_workflow:
        return "workflow"
    return "other"


def tools_for_scope(scope: SearchScope) -> tuple[str, ...]:
    return _SCOPE_TOOLS[scope]


# Words that choose a scope. They are not the value to look up.
_DOCUMENT_QUERY_WORDS = frozenset(
    {
        "document",
        "documents",
        "doc",
        "docs",
        "file",
        "files",
        "pdf",
        "pdfs",
        "attachment",
        "attachments",
        "content",
        "contents",
    }
)
_WORKFLOW_QUERY_WORDS = frozenset(
    {"request", "requests", "ticket", "tickets", "workflow", "workflows"}
)
_BOTH_QUERY_WORDS = _DOCUMENT_QUERY_WORDS | _WORKFLOW_QUERY_WORDS | frozenset(
    {"invoice", "invoices", "po", "p.o", "p.o.", "purchase", "order", "orders", "number", "no"}
)
_SCOPE_QUERY_WORDS: dict[SearchScope, frozenset[str]] = {
    "repository": _DOCUMENT_QUERY_WORDS,
    "workflow": _WORKFLOW_QUERY_WORDS,
    "both": _BOTH_QUERY_WORDS,
    "other": frozenset(),
}


def search_text_for_scope(message: str, scope: SearchScope) -> str:
    """File or ticket keyword after scope words are removed.

    'documents' selects repositories. 'Find the documents from APEX' → 'APEX'.
    'Search my documents' has no file keyword and returns an empty string.
    """
    rewritten = rewrite_search_query(message)
    if not rewritten:
        return ""
    # 'documents' is never the file keyword, including when the scope is other.
    drop = _SCOPE_QUERY_WORDS[scope] | _DOCUMENT_QUERY_WORDS
    kept: list[str] = []
    for token in rewritten.split():
        cleaned = token.strip("?.!,;:\"'()[]{}").lower()
        if cleaned in drop:
            continue
        kept.append(token.strip("?.!,;:\"'()[]{}"))
    return normalize_query(" ".join(kept))
