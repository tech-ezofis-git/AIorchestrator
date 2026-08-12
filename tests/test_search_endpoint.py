"""End-to-end: ingest fixture docs into the (fake) vector store, then hit
POST /chat with a search-triggering message and confirm a synthesized,
chunk-cited answer comes back — proving Intent Router -> Agent Router ->
Search Agent -> Hybrid Search -> Response Composer synthesis all wire up
correctly, with mocked embedding + LLM adapters (no real API keys, no real
Postgres/pgvector).
"""
import asyncio
from pathlib import Path

import app.main as main_module
from app.knowledge.ingestion import IngestionPipeline

_FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Crude bag-of-words "embedding" over a small fixed vocabulary — good
# enough to give cosine similarity a real signal to rank on without a real
# embedding provider.
_VOCAB = ["pto", "vacation", "policy", "expense", "reimbursement", "receipt"]


def _fake_embed_vector(text: str) -> list[float]:
    lowered = text.lower()
    return [float(lowered.count(term)) for term in _VOCAB]


def _install_fake_embeddings(monkeypatch):
    async def fake_embed(self, texts):
        return [_fake_embed_vector(t) for t in texts]

    monkeypatch.setattr("app.llm.embedding_adapter.EmbeddingAdapter.embed", fake_embed)


def _install_fake_llm_for_synthesis(monkeypatch):
    async def fake_chat_completion(self, messages):
        return {
            "content": "Based on the excerpts, PTO accrues at 1.5 days/month. [1]",
            "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)


def test_search_intent_returns_synthesized_answer_with_chunk_ids(client, monkeypatch):
    _install_fake_embeddings(monkeypatch)
    _install_fake_llm_for_synthesis(monkeypatch)

    pipeline = IngestionPipeline(
        main_module.app.state.vector_store,
        main_module.app.state.embedding_adapter,
        chunk_size_tokens=50,
        overlap_tokens=5,
    )
    pto_text = (_FIXTURES_DIR / "pto_policy.txt").read_text()
    expense_text = (_FIXTURES_DIR / "expense_policy.txt").read_text()
    asyncio.run(pipeline.ingest_text(source="test-fixture", title="PTO Policy", text=pto_text))
    asyncio.run(pipeline.ingest_text(source="test-fixture", title="Expense Policy", text=expense_text))

    response = client.post("/chat", json={"session_id": "s-search", "message": "search for the PTO policy"})

    assert response.status_code == 200
    body = response.json()
    assert body["chunk_ids"], "expected at least one cited chunk id"
    assert "[1]" in body["reply"]
    assert body["token_usage"]["total_tokens"] == 28


def test_chat_intent_has_no_chunk_ids(client, monkeypatch):
    """Chat responses must keep the Phase 1 pass-through shape — chunk_ids
    stays absent/None, proving Search's addition didn't disturb Chat."""

    async def fake_chat_completion(self, messages):
        return {"content": "hello there", "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)

    response = client.post("/chat", json={"session_id": "s-chat", "message": "hello"})

    assert response.status_code == 200
    assert response.json()["chunk_ids"] is None


def test_search_with_no_ingested_documents_returns_no_results_message(client, monkeypatch):
    _install_fake_embeddings(monkeypatch)
    _install_fake_llm_for_synthesis(monkeypatch)

    response = client.post("/chat", json={"session_id": "s-empty-search", "message": "find the onboarding guide"})

    assert response.status_code == 200
    body = response.json()
    assert body["chunk_ids"] == []
    assert "couldn't find" in body["reply"].lower()
