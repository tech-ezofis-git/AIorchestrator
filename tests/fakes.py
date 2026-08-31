"""Shared test fakes for Phase 2 (Search/RAG), Phase 4b (audit
persistence), Phase 5a (Chat memory), and AP document jobs — a tiny
in-memory stand-in for the asyncpg pool so tests never need a live
Postgres(+pgvector) instance.

Understands only the fixed set of query shapes app/knowledge/vector_store.py,
app/control/audit_store.py, app/control/memory_store.py, app/ap_skills/store.py, and
app/catalog/store.py issue (matched by distinctive substrings) — not a
general SQL engine.
"""
import json
import math
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


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
        self.catalog_agents: dict[str, dict[str, Any]] = {}
        self.catalog_models: dict[str, dict[str, Any]] = {}
        self.catalog_tenant_models: dict[str, dict[str, Any]] = {}
        self.catalog_tenant_agent_models: dict[str, dict[str, Any]] = {}

    def _catalog_agent_by_id(self, agent_id: Any) -> Optional[dict[str, Any]]:
        return self.catalog_agents.get(str(agent_id))

    def _catalog_agent_by_slug(self, slug: str) -> Optional[dict[str, Any]]:
        for row in self.catalog_agents.values():
            if row["slug"] == slug:
                return row
        return None

    def _catalog_model_by_id(self, model_id: Any) -> Optional[dict[str, Any]]:
        return self.catalog_models.get(str(model_id))

    def _catalog_model_by_slug(self, slug: str) -> Optional[dict[str, Any]]:
        for row in self.catalog_models.values():
            if row["slug"] == slug:
                return row
        return None

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _handle_catalog_fetchrow(self, query: str, args: tuple[Any, ...]) -> Any:
        if "INSERT INTO catalog_agents" in query and "RETURNING" in query:
            (
                agent_id,
                slug,
                name,
                description,
                kind,
                enabled,
                system_prompt,
                trigger_phrases,
            ) = args
            if self._catalog_agent_by_slug(slug):
                return None
            row = {
                "id": agent_id,
                "slug": slug,
                "name": name,
                "description": description,
                "kind": kind,
                "enabled": enabled,
                "system_prompt": system_prompt,
                "trigger_phrases": list(trigger_phrases or []),
                "created_at": self._now(),
                "updated_at": self._now(),
            }
            self.catalog_agents[str(agent_id)] = row
            return row
        if "UPDATE catalog_agents" in query:
            name, description, enabled, system_prompt, trigger_phrases, agent_id = args
            row = self._catalog_agent_by_id(agent_id)
            if row is None:
                return None
            row.update(
                {
                    "name": name,
                    "description": description,
                    "enabled": enabled,
                    "system_prompt": system_prompt,
                    "trigger_phrases": list(trigger_phrases or []),
                    "updated_at": self._now(),
                }
            )
            return row
        if "DELETE FROM catalog_agents" in query:
            agent_id = args[0]
            row = self._catalog_agent_by_id(agent_id)
            if row is None or row["kind"] != "custom":
                return None
            del self.catalog_agents[str(agent_id)]
            return {"id": agent_id}
        if "FROM catalog_agents WHERE slug = $1 AND kind = 'custom' AND enabled = TRUE" in query:
            row = self._catalog_agent_by_slug(args[0])
            if row and row["kind"] == "custom" and row["enabled"]:
                return row
            return None
        if "FROM catalog_agents WHERE slug = $1" in query:
            row = self._catalog_agent_by_slug(args[0])
            if row is None:
                return None
            if "enabled" in query:
                return row
            return {"id": row["id"]}
        if "FROM catalog_agents WHERE id = $1" in query:
            return self._catalog_agent_by_id(args[0])
        if "INSERT INTO catalog_models" in query and "RETURNING" in query:
            (
                model_id,
                slug,
                label,
                model,
                api_base,
                api_key,
                api_version,
                region,
                model_version,
                enabled,
                sort_order,
            ) = args
            if self._catalog_model_by_slug(slug):
                return None
            row = {
                "id": model_id,
                "slug": slug,
                "label": label,
                "model": model,
                "api_base": api_base,
                "api_key": api_key,
                "api_version": api_version,
                "region": region,
                "model_version": model_version,
                "enabled": enabled,
                "sort_order": sort_order,
                "created_at": self._now(),
                "updated_at": self._now(),
            }
            self.catalog_models[str(model_id)] = row
            return row
        if "UPDATE catalog_models" in query:
            (
                label,
                model,
                api_base,
                api_key,
                api_version,
                region,
                model_version,
                enabled,
                sort_order,
                model_id,
            ) = args
            row = self._catalog_model_by_id(model_id)
            if row is None:
                return None
            row.update(
                {
                    "label": label,
                    "model": model,
                    "api_base": api_base,
                    "api_key": api_key,
                    "api_version": api_version,
                    "region": region,
                    "model_version": model_version,
                    "enabled": enabled,
                    "sort_order": sort_order,
                    "updated_at": self._now(),
                }
            )
            return row
        if "DELETE FROM catalog_models" in query:
            model_id = args[0]
            row = self._catalog_model_by_id(model_id)
            if row is None:
                return None
            del self.catalog_models[str(model_id)]
            return {"id": model_id}
        if "FROM catalog_models WHERE id = $1" in query:
            return self._catalog_model_by_id(args[0])
        if "FROM catalog_models WHERE slug = $1" in query:
            row = self._catalog_model_by_slug(args[0])
            return {"id": row["id"]} if row else None
        if "FROM catalog_tenant_models t" in query and "WHERE t.tenant_id = $1" in query:
            tenant = self.catalog_tenant_models.get(str(args[0]))
            if tenant is None:
                return None
            return self._join_tenant_row(tenant)
        if "FROM catalog_tenant_models" in query and "default_model_id" in query:
            model_id = str(args[0])
            for tenant in self.catalog_tenant_models.values():
                if str(tenant["default_model_id"]) == model_id or str(tenant.get("fallback_model_id") or "") == model_id:
                    return {"tenant_id": tenant["tenant_id"]}
            return None
        if "FROM catalog_tenant_agent_models t" in query and "agent_slug = $2" in query:
            key = f"{args[0]}:{args[1]}"
            row = self.catalog_tenant_agent_models.get(key)
            return self._join_tenant_agent_row(row) if row else None
        raise AssertionError(f"FakeDBPool.fetchrow: unrecognized catalog query: {query!r}")

    def _join_tenant_agent_row(self, row: dict[str, Any]) -> dict[str, Any]:
        model = self._catalog_model_by_id(row["model_id"]) if row.get("model_id") else None
        fallback = self._catalog_model_by_id(row["fallback_model_id"]) if row.get("fallback_model_id") else None
        return {
            "tenant_id": row["tenant_id"],
            "agent_slug": row["agent_slug"],
            "model_id": row.get("model_id"),
            "fallback_model_id": row.get("fallback_model_id"),
            "updated_at": row.get("updated_at"),
            "model_slug": model.get("slug") if model else None,
            "model_label": model.get("label") if model else None,
            "fallback_slug": fallback.get("slug") if fallback else None,
            "fallback_label": fallback.get("label") if fallback else None,
        }

    def _join_tenant_row(self, tenant: dict[str, Any]) -> dict[str, Any]:
        default = self._catalog_model_by_id(tenant["default_model_id"]) or {}
        fallback = self._catalog_model_by_id(tenant.get("fallback_model_id")) if tenant.get("fallback_model_id") else None
        return {
            "tenant_id": tenant["tenant_id"],
            "default_model_id": tenant["default_model_id"],
            "fallback_model_id": tenant.get("fallback_model_id"),
            "updated_at": tenant.get("updated_at"),
            "default_slug": default.get("slug"),
            "default_label": default.get("label"),
            "fallback_slug": fallback.get("slug") if fallback else None,
            "fallback_label": fallback.get("label") if fallback else None,
        }

    def _handle_catalog_fetch(self, query: str, args: tuple[Any, ...]) -> list[Any]:
        if "FROM catalog_agents WHERE kind = 'custom' AND enabled = TRUE" in query:
            rows = [
                row
                for row in self.catalog_agents.values()
                if row["kind"] == "custom" and row["enabled"]
            ]
            rows.sort(key=lambda r: r["name"])
            return rows
        if "FROM catalog_agents ORDER BY kind ASC, name ASC" in query:
            rows = list(self.catalog_agents.values())
            rows.sort(key=lambda r: (r["kind"], r["name"]))
            return rows
        if "FROM catalog_models WHERE enabled = TRUE" in query:
            rows = [row for row in self.catalog_models.values() if row["enabled"]]
            rows.sort(key=lambda r: (r["sort_order"], r["label"]))
            return rows
        if "FROM catalog_models ORDER BY sort_order ASC, label ASC" in query:
            rows = list(self.catalog_models.values())
            rows.sort(key=lambda r: (r["sort_order"], r["label"]))
            return rows
        if "FROM catalog_tenant_models t" in query:
            rows = [self._join_tenant_row(t) for t in self.catalog_tenant_models.values()]
            rows.sort(key=lambda r: r["tenant_id"])
            return rows
        if "FROM catalog_tenant_agent_models t" in query:
            rows = [self._join_tenant_agent_row(row) for row in self.catalog_tenant_agent_models.values()]
            if "WHERE t.tenant_id = $1" in query:
                rows = [row for row in rows if row["tenant_id"] == args[0]]
            rows.sort(key=lambda r: r["agent_slug"])
            return rows
        raise AssertionError(f"FakeDBPool.fetch: unrecognized catalog query: {query!r}")

    def _handle_catalog_execute(self, query: str, args: tuple[Any, ...]) -> None:
        stripped = query.strip().upper()
        if stripped.startswith("CREATE "):
            return
        if "INSERT INTO catalog_agents" in query:
            (
                agent_id,
                slug,
                name,
                description,
                kind,
                enabled,
                system_prompt,
                trigger_phrases,
            ) = args
            if self._catalog_agent_by_slug(slug):
                return
            self.catalog_agents[str(agent_id)] = {
                "id": agent_id,
                "slug": slug,
                "name": name,
                "description": description,
                "kind": kind,
                "enabled": enabled,
                "system_prompt": system_prompt,
                "trigger_phrases": list(trigger_phrases or []),
                "created_at": self._now(),
                "updated_at": self._now(),
            }
            return
        if "INSERT INTO catalog_models" in query:
            (
                model_id,
                slug,
                label,
                model,
                api_base,
                api_key,
                api_version,
                region,
                model_version,
                enabled,
                sort_order,
            ) = args
            if self._catalog_model_by_slug(slug):
                return
            self.catalog_models[str(model_id)] = {
                "id": model_id,
                "slug": slug,
                "label": label,
                "model": model,
                "api_base": api_base,
                "api_key": api_key,
                "api_version": api_version,
                "region": region,
                "model_version": model_version,
                "enabled": enabled,
                "sort_order": sort_order,
                "created_at": self._now(),
                "updated_at": self._now(),
            }
            return
        if "INSERT INTO catalog_tenant_models" in query:
            tenant_id, default_model_id, fallback_model_id = args
            self.catalog_tenant_models[str(tenant_id)] = {
                "tenant_id": tenant_id,
                "default_model_id": default_model_id,
                "fallback_model_id": fallback_model_id,
                "updated_at": self._now(),
            }
            return
        if "INSERT INTO catalog_tenant_agent_models" in query:
            tenant_id, agent_slug, model_id, fallback_model_id = args
            key = f"{tenant_id}:{agent_slug}"
            self.catalog_tenant_agent_models[key] = {
                "tenant_id": tenant_id,
                "agent_slug": agent_slug,
                "model_id": model_id,
                "fallback_model_id": fallback_model_id,
                "updated_at": self._now(),
            }
            return
        if "DELETE FROM catalog_tenant_agent_models" in query:
            key = f"{args[0]}:{args[1]}"
            self.catalog_tenant_agent_models.pop(key, None)
            return
        raise AssertionError(f"FakeDBPool.execute: unrecognized catalog query: {query!r}")

    async def fetchrow(self, query: str, *args: Any):
        if "current_database()" in query:
            return {"db": "fake"}
        if 'catalog."Tenants"' in query:
            return None
        if "catalog_agents" in query or "catalog_models" in query or "catalog_tenant_models" in query or "catalog_tenant_agent_models" in query:
            return self._handle_catalog_fetchrow(query, args)
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
        if "information_schema.tables" in query.lower():
            return None
        if "workflowinstance" in query.lower() or "repositoryitem" in query.lower():
            return None
        if query.strip().upper().startswith("SELECT * FROM") or "SELECT 1 AS ok" in query:
            return None
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
        if "catalog_agents" in query or "catalog_models" in query or "catalog_tenant_models" in query or "catalog_tenant_agent_models" in query:
            return self._handle_catalog_fetch(query, args)
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
        if "wformcontrol" in query.lower():
            return []
        if "information_schema" in query.lower():
            return []
        raise AssertionError(f"FakeDBPool.fetch: unrecognized query: {query!r}")

    async def execute(self, query: str, *args: Any):
        if query.strip().upper().startswith("CREATE "):
            return
        if "catalog_agents" in query or "catalog_models" in query or "catalog_tenant_models" in query or "catalog_tenant_agent_models" in query:
            return self._handle_catalog_execute(query, args)
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
        if "UPDATE " in query.upper() and ("ezfb_" in query.lower() or "items_" in query.lower()):
            return "UPDATE 1"
        raise AssertionError(f"FakeDBPool.execute: unrecognized query: {query!r}")

    async def close(self):
        pass
