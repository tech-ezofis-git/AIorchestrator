"""Shared test fakes for Phase 2 (Search/RAG), Phase 4b (audit
persistence), Phase 5a (Chat memory), and AP document jobs — a tiny
in-memory stand-in for the asyncpg pool so tests never need a live
Postgres(+pgvector) instance.

Understands only the fixed set of query shapes app/knowledge/vector_store.py,
app/control/audit_store.py, app/control/memory_store.py, and
app/ap_skills/store.py issue (matched by distinctive substrings) — not a
general SQL engine.
"""
import json
import math
import re
import uuid
from datetime import datetime, timezone
from typing import Any


def _parse_vector_literal(literal: str) -> list[float]:
    return [float(x) for x in literal.strip("[]").split(",") if x]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a)) or 1e-9
    norm_b = math.sqrt(sum(x * x for x in b)) or 1e-9
    return dot / (norm_a * norm_b)


def _json_val(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


class FakeDBPool:
    def __init__(self):
        self.documents: dict[str, dict[str, Any]] = {}
        self.chunks: dict[str, dict[str, Any]] = {}
        self.audit_log: list[dict[str, Any]] = []
        self.memories: list[dict[str, Any]] = []
        self.ap_runs: dict[str, dict[str, Any]] = {}
        self.ap_skill_artifacts: list[dict[str, Any]] = []
        self.ap_tenant_plans: dict[str, dict[str, Any]] = {}
        self.ap_credit_ledger: list[dict[str, Any]] = []
        self.workflow_steps: list[dict[str, Any]] = []

    async def fetchrow(self, query: str, *args: Any):
        if "INSERT INTO documents" in query:
            source, title, metadata_json = args
            doc_id = uuid.uuid4()
            row = {"id": doc_id, "source": source, "title": title, "metadata": metadata_json}
            self.documents[str(doc_id)] = row
            return row
        if "INSERT INTO ap_runs" in query:
            run_id, session_id, tenant_id, item_key, requested_skills, status = args
            row = {
                "id": run_id,
                "session_id": session_id,
                "tenant_id": tenant_id,
                "item_key": item_key,
                "requested_skills": _json_val(requested_skills),
                "status": status,
                "decision": None,
                "credits_charged": 0,
            }
            self.ap_runs[str(run_id)] = row
            return {"id": run_id}
        if "FROM ap_tenant_plans" in query:
            tenant_id = args[0]
            return self.ap_tenant_plans.get(str(tenant_id))
        if "WorkflowSteps" in query or "workflowsteps" in query:
            step_name = str(args[0]) if args else ""
            workflow_id = str(args[1]) if len(args) > 1 and args[1] is not None else None
            matches = [
                row
                for row in self.workflow_steps
                if row.get("name") == step_name
                and (not workflow_id or str(row.get("workflow_id") or "") == workflow_id)
            ]
            matches.sort(key=lambda row: int(row.get("order") or 0))
            if not matches:
                return None
            return {"activity_id": matches[0].get("activity_id")}
        raise AssertionError(f"FakeDBPool.fetchrow: unrecognized query: {query!r}")

    async def executemany(self, query: str, args_list: list[tuple]):
        if "INSERT INTO chunks" in query:
            for chunk_id, document_id, chunk_index, text, embedding_literal in args_list:
                self.chunks[str(chunk_id)] = {
                    "id": chunk_id,
                    "document_id": document_id,
                    "chunk_index": chunk_index,
                    "text": text,
                    "embedding": _parse_vector_literal(embedding_literal) if embedding_literal else None,
                }
            return
        raise AssertionError(f"FakeDBPool.executemany: unrecognized query: {query!r}")

    async def fetch(self, query: str, *args: Any):
        if "embedding <=>" in query:
            query_embedding = _parse_vector_literal(args[0])
            top_n = args[1]
            scored = [
                (c, _cosine_similarity(query_embedding, c["embedding"]))
                for c in self.chunks.values()
                if c["embedding"] is not None
            ]
            scored.sort(key=lambda pair: pair[1], reverse=True)
            return [
                {"id": c["id"], "document_id": c["document_id"], "chunk_index": c["chunk_index"], "text": c["text"], "score": score}
                for c, score in scored[:top_n]
            ]
        if "FROM memories" in query:
            user_id, limit = args
            matches = [m for m in self.memories if m["user_id"] == user_id]
            matches.reverse()
            return [{"fact": m["fact"]} for m in matches[:limit]]
        if "FROM ap_skill_artifacts" in query:
            if "AND skill_id" in query:
                tenant_id, skill_id = args
                return [
                    {"item_key": row["item_key"], "result_json": row["result_json"]}
                    for row in self.ap_skill_artifacts
                    if row["tenant_id"] == tenant_id and row["skill_id"] == skill_id
                ]
            tenant_id, item_key = args
            return [
                {
                    "skill_id": row["skill_id"],
                    "result_json": row["result_json"],
                    "created_at": row["created_at"],
                }
                for row in self.ap_skill_artifacts
                if row["tenant_id"] == tenant_id and row["item_key"] == item_key
            ]
        if "text_search @@" in query:
            search_query, top_n = args
            terms = [t.lower() for t in re.findall(r"\w+", search_query)]
            scored = []
            for c in self.chunks.values():
                text_lower = c["text"].lower()
                hits = sum(text_lower.count(t) for t in terms)
                if hits > 0:
                    scored.append((c, float(hits)))
            scored.sort(key=lambda pair: pair[1], reverse=True)
            return [
                {"id": c["id"], "document_id": c["document_id"], "chunk_index": c["chunk_index"], "text": c["text"], "score": score}
                for c, score in scored[:top_n]
            ]
        raise AssertionError(f"FakeDBPool.fetch: unrecognized query: {query!r}")

    async def execute(self, query: str, *args: Any):
        if "INSERT INTO memories" in query:
            user_id, fact = args
            self.memories.append({"user_id": user_id, "fact": fact})
            return
        if "INSERT INTO audit_log" in query:
            (
                correlation_id,
                session_id,
                intent,
                event_type,
                status,
                latency_ms,
                redacted_request_snippet,
                redacted_response_snippet,
            ) = args
            self.audit_log.append(
                {
                    "correlation_id": correlation_id,
                    "session_id": session_id,
                    "intent": intent,
                    "event_type": event_type,
                    "status": status,
                    "latency_ms": latency_ms,
                    "redacted_request_snippet": redacted_request_snippet,
                    "redacted_response_snippet": redacted_response_snippet,
                }
            )
            return
        if "UPDATE ap_runs" in query:
            run_id, status, decision, credits_charged = args
            row = self.ap_runs.get(str(run_id))
            if row is not None:
                row["status"] = status
                row["decision"] = decision
                row["credits_charged"] = credits_charged
            return
        if "INSERT INTO ap_skill_artifacts" in query:
            run_id, tenant_id, item_key, skill_id, result_json = args
            self.ap_skill_artifacts.append(
                {
                    "run_id": run_id,
                    "tenant_id": tenant_id,
                    "item_key": item_key,
                    "skill_id": skill_id,
                    "result_json": _json_val(result_json),
                    "created_at": datetime.now(timezone.utc),
                }
            )
            return
        if "INSERT INTO ap_credit_ledger" in query:
            run_id, tenant_id, skill_id, credits, identify, status = args
            self.ap_credit_ledger.append(
                {
                    "run_id": run_id,
                    "tenant_id": tenant_id,
                    "skill_id": skill_id,
                    "credits": credits,
                    "identify": identify,
                    "status": status,
                }
            )
            return
        raise AssertionError(f"FakeDBPool.execute: unrecognized query: {query!r}")

    async def close(self):
        pass
