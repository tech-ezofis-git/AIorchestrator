"""Built-in agent rows seeded into catalog_agents. Handlers stay in code."""

BUILTIN_AGENTS: list[dict[str, str]] = [
    {"slug": "chat", "name": "Chat", "description": "General EZOFIS assistant."},
    {"slug": "search", "name": "Search", "description": "Search indexed documents."},
    {"slug": "summary", "name": "Summary", "description": "Summarize a document."},
    {"slug": "classification", "name": "Classification", "description": "Classify a document type from OCR or a file."},
    {"slug": "insight", "name": "Insight", "description": "Analyze a document or report."},
    {"slug": "ocr", "name": "OCR", "description": "Extract and structure text from a document."},
    {"slug": "forecast", "name": "Forecast", "description": "Narrate a numeric forecast."},
    {"slug": "ap", "name": "Accounts Payable", "description": "Invoice / AP document jobs."},
    {"slug": "mail", "name": "Mail", "description": "Draft an email (confirm before send)."},
    {"slug": "prompt", "name": "Prompt", "description": "Run a raw prompt through the current model."},
    {
        "slug": "global_search",
        "name": "Global Search",
        "description": "Find repositories, workflows, and documents in the tenant database.",
    },
    {
        "slug": "chatbot",
        "name": "Chatbot",
        "description": "Tenant chat search and actions (workflow, upload, tickets, users).",
    },
    {
        "slug": "dashboard",
        "name": "Dashboard",
        "description": "Prompts, schema, and live HTML from a tenant repository or workflow (Postgres, first 50 rows).",
    },
    {
        "slug": "report",
        "name": "Report Agent",
        "description": "Discover live schema, plan SQL, and generate executive business reports.",
    },
]

RESERVED_SLUGS: frozenset[str] = frozenset(row["slug"] for row in BUILTIN_AGENTS)
