"""Chatbot NL query helpers — list-catalog intents + stopword search rewrite."""
from __future__ import annotations

from typing import Literal, Optional

from app.global_search.sql_search import normalize_query

CatalogKind = Literal["repositories", "workflows", "forms"]

_REPO_WORDS = frozenset({"repo", "repos", "repository", "repositories"})
_WORKFLOW_WORDS = frozenset({"workflow", "workflows"})
_FORM_WORDS = frozenset({"form", "forms"})

# Conversational / filler words — never strip real search keywords.
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "what",
        "which",
        "where",
        "when",
        "who",
        "whom",
        "whose",
        "why",
        "how",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "am",
        "do",
        "does",
        "did",
        "can",
        "could",
        "would",
        "should",
        "may",
        "might",
        "will",
        "shall",
        "please",
        "me",
        "my",
        "mine",
        "our",
        "ours",
        "your",
        "you",
        "i",
        "we",
        "they",
        "them",
        "their",
        "this",
        "that",
        "these",
        "those",
        "there",
        "here",
        "all",
        "any",
        "some",
        "every",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "from",
        "with",
        "about",
        "into",
        "over",
        "under",
        "and",
        "or",
        "but",
        "if",
        "then",
        "so",
        "just",
        "also",
        "only",
        "available",
        "availabe",
        "availability",
        "give",
        "tell",
        "show",
        "list",
        "get",
        "find",
        "search",
        "look",
        "looking",
        "see",
        "need",
        "want",
        "like",
        "have",
        "has",
        "had",
        "tenant",
        "currently",
        "existing",
        "present",
        "possible",
        "options",
        "option",
        "ones",
        "one",
    }
)


def rewrite_search_query(message: str) -> str:
    """Drop filler words so NL questions still hit ILIKE keyword search."""
    text = normalize_query(message)
    if not text:
        return ""
    kept: list[str] = []
    for token in text.split():
        cleaned = token.strip("?.!,;:\"'()[]{}").lower()
        if not cleaned or cleaned in _STOPWORDS:
            continue
        if len(cleaned) == 1 and cleaned.isalpha():
            continue
        kept.append(token.strip("?.!,;:\"'()[]{}"))
    return normalize_query(" ".join(kept))


def detect_catalog_list(message: str) -> Optional[CatalogKind]:
    """Detect catalog-list asks (e.g. 'What are repo available?').

    After stopword strip, only entity words remain → list that catalog with
    empty ILIKE (%%). Extra keywords (e.g. 'forms invoice') stay as search.
    """
    rewritten = rewrite_search_query(message)
    if not rewritten:
        return None
    tokens = [t.lower() for t in rewritten.split()]
    if tokens and all(t in _REPO_WORDS for t in tokens):
        return "repositories"
    if tokens and all(t in _WORKFLOW_WORDS for t in tokens):
        return "workflows"
    if tokens and all(t in _FORM_WORDS for t in tokens):
        return "forms"
    return None


def catalog_tool_name(kind: CatalogKind) -> str:
    return {
        "repositories": "search_repositories",
        "workflows": "search_workflows",
        "forms": "search_forms",
    }[kind]


def catalog_label(kind: CatalogKind) -> str:
    return {
        "repositories": "Repositories",
        "workflows": "Workflows",
        "forms": "Forms",
    }[kind]
