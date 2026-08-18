"""Application configuration, loaded from environment / .env via pydantic-settings."""
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- LLM ---------------------------------------------------------
    llm_model: str = "gpt-4.1-mini"
    # Optional custom/self-hosted or Azure OpenAI-compatible endpoint —
    # set both to route LLM calls at a specific base URL with a specific
    # key instead of a standard provider's default routing (see
    # app/llm/adapter.py). Also runtime-reconfigurable from the Test
    # Console (GET/POST /console/llm-config) without restarting the app —
    # these two only set the STARTUP value.
    llm_api_base: Optional[str] = None
    llm_api_key: Optional[str] = None
    # Azure OpenAI keys for Test Console model presets
    # (app/llm/model_presets.py). Kept out of git via .env — see .env.example.
    azure_south_india_api_key: Optional[str] = None
    azure_east_us_api_key: Optional[str] = None
    # OpenAI-compatible Qwen host (ezqwenmac ACI) — default console preset.
    qwen_mac_api_key: Optional[str] = None
    # Eval harness (Phase 5c) LLM-judge scoring — see app/evals/scoring.py.
    # Defaults to `llm_model` when unset (rule 4), so a stronger/different
    # model can judge than the one under test without requiring a second
    # env var to be set for the harness to work at all. Only read by
    # app/evals/runner.py — never by the running service.
    judge_model: Optional[str] = None

    # --- Embeddings / Search (Phase 2) ----------------------------------
    # Same pattern as llm_model: provider is selected purely by this string
    # (via LiteLLM) — no provider-specific branching in code.
    embedding_model: str = "text-embedding-3-small"
    # Chunking defaults (tokens ~= whitespace-delimited words — see
    # app/knowledge/ingestion.py). 500/50 is a reasonable starting point,
    # not tuned.
    chunk_size_tokens: int = 500
    chunk_overlap_tokens: int = 50
    # How many chunks HybridSearch returns to the Search agent.
    search_top_n: int = 5

    # --- Infra ---------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "postgresql://orchestrator:orchestrator@localhost:5432/orchestrator"
    # Keep the asyncpg pool tiny on shared Azure Flexible Server SKUs
    # (B1ms ≈ 50 max_connections server-wide). asyncpg's default min_size=10
    # exhausts the server when agents crash-restarts, taking the whole
    # multi-container App Service down with it.
    database_pool_min_size: int = 1
    database_pool_max_size: int = 3

    # --- App -----------------------------------------------------------
    app_name: str = "ai-orchestrator"
    log_level: str = "INFO"
    session_ttl_seconds: int = 60 * 60 * 24  # 24h
    # How long a Mail draft's pending action stays confirmable before it
    # expires unconfirmed (Phase 3d) — see app/core/pending_actions.py.
    pending_action_ttl_seconds: int = 60 * 10  # 10 minutes

    # --- Guardrails (Phase 4a) -------------------------------------------
    # Fixed-window rate limit, keyed by session_id — see
    # app/control/rate_limiter.py. Applies to both /chat and
    # /actions/{action_id}/confirm.
    rate_limit_max_requests: int = 20
    rate_limit_window_seconds: int = 60

    # --- Response caching (Phase 5b) --------------------------------------
    # See app/control/response_cache.py for the full reasoning. Embedding
    # a given text under a given model is a pure function — long TTL is
    # safe since there's nothing to go stale. Search's full result and
    # Forecast's narration each reflect a point-in-time view over data
    # that can change (the document index; the forecast source), so they
    # get a short TTL instead — the TTL itself is this phase's only
    # staleness mitigation (no cache-bust on document ingestion).
    embedding_cache_ttl_seconds: int = 60 * 60 * 24  # 24h
    search_result_cache_ttl_seconds: int = 60 * 5  # 5 minutes
    forecast_narration_cache_ttl_seconds: int = 60 * 5  # 5 minutes

    # --- OCR document extraction (blob / upload → extract_text → JSON) ---
    ocr_extract_url: Optional[str] = (
        "https://ez-container-app.calmsmoke-6661997a.southindia.azurecontainerapps.io/api/extract_text"
    )
    ocr_engine: str = "paddle"
    ocr_default_model: Optional[str] = None
    ocr_fallback_model: Optional[str] = None
    ocr_max_pages: int = 5
    ocr_max_recommended_fields: int = 15
    ocr_allowed_host_suffixes: str = ".blob.core.windows.net"
    ocr_download_timeout_seconds: float = 60.0
    ocr_max_file_bytes: int = 25 * 1024 * 1024  # 25 MiB
    # Hard cap for LLM provider calls — prevents /chat from hanging forever
    # when a preset endpoint is unreachable.
    llm_request_timeout_seconds: float = 60.0
    azure_storage_connection_string: Optional[str] = None
    azure_blob_container_prefix: str = "ezts"

    # --- Agent skill packs (SKILL.md + rules/*.mdc for Summary / OCR / Insight / Prompt) ---
    # Defaults to <repo>/skills. Override root or a single agent pack so
    # customers can drop in their own instructions without code changes.
    agent_skills_root: Optional[str] = None
    summary_skill_dir: Optional[str] = None
    ocr_skill_dir: Optional[str] = None
    insight_skill_dir: Optional[str] = None
    prompt_skill_dir: Optional[str] = None
    # Local sample: SQLite path for tenant Summary extras (custom rules only).
    # Defaults stay on disk; not used in Docker unless set explicitly.
    tenant_skills_sqlite_path: Optional[str] = None

    # --- Ezofis cloud API (AP skills: auth, credits, PO/vendor masters) ---
    ezofis_api_base: str = "https://cloud.ezofis.com/api"
    ezofis_login_email: Optional[str] = None
    ezofis_login_password: Optional[str] = None
    ezofis_env: str = "trial"
    ezofis_timeout_seconds: float = 30.0
    ap_llm_planner: bool = False
    ap_amount_tolerance: float = 0.02
    ap_approved_threshold: int = 80
    ap_partial_threshold: int = 50
    # AP tables live in ezofis_Tenant_{first 8 of tenant_id} on the same
    # server as DATABASE_URL. Empty prefix disables routing (main DB only).
    ap_tenant_db_prefix: str = "ezofis_Tenant_"
    # Workflow step name used to resolve ActivityId from workflow.WorkflowSteps
    # (same default as apagentv6). Env: AP_AGENT_WORKFLOW_STEP_NAME.
    ap_agent_workflow_step_name: str = "AP AGENT 1"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Provider API keys (OPENAI_API_KEY, ANTHROPIC_API_KEY, ...) live in
        # the environment for LiteLLM to pick up directly — ignore any extra
        # env vars here rather than failing validation on them.
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor — safe to call repeatedly/as a FastAPI dependency."""
    return Settings()
