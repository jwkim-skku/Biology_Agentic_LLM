from __future__ import annotations

import importlib.util
import json
import math
from typing import Any, Iterable
from urllib.error import URLError
from urllib.request import Request, urlopen

from app.config import get_settings


VECTOR_STORE_SCHEMA = "agentic-rag-vector-store-runtime-v1"


def rag_vector_store_status(index: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = get_settings()
    chunks = list((index or {}).get("chunks") or [])
    dimensions = int((index or {}).get("embedding_dimensions") or 0)
    pgvector_configured = bool(settings.database_url)
    pgvector_driver_available = importlib.util.find_spec("psycopg") is not None
    qdrant_configured = bool(settings.qdrant_url)
    target = settings.rag_vector_backend
    active = _active_backend(target, pgvector_configured, pgvector_driver_available, qdrant_configured)
    warnings: list[str] = []
    if target == "pgvector" and not pgvector_configured:
        warnings.append("RAG_VECTOR_BACKEND=pgvector requires DATABASE_URL.")
    if target == "pgvector" and pgvector_configured and not pgvector_driver_available:
        warnings.append("RAG_VECTOR_BACKEND=pgvector requires the psycopg dependency.")
    if target == "qdrant" and not qdrant_configured:
        warnings.append("RAG_VECTOR_BACKEND=qdrant requires QDRANT_URL.")
    if target != active:
        warnings.append(f"RAG vector backend fell back from {target} to {active}.")
    qdrant_health = _qdrant_health(settings.qdrant_url) if target == "qdrant" and qdrant_configured else {"checked": False}
    return {
        "runtime_schema": VECTOR_STORE_SCHEMA,
        "status": "warning" if warnings else "pass",
        "target_backend": target,
        "active_backend": active,
        "fallback_active": target != active,
        "chunk_count": len(chunks),
        "embedding_dimensions": dimensions,
        "local_json": {
            "available": True,
            "fit": "deterministic local development and small corpora",
        },
        "pgvector": {
            "configured": pgvector_configured,
            "driver_available": pgvector_driver_available,
            "table": settings.rag_pgvector_table,
            "database_url_configured": pgvector_configured,
            "search_mode": "ann_cosine_with_payload_jsonb",
        },
        "qdrant": {
            "configured": qdrant_configured,
            "url_configured": qdrant_configured,
            "collection": settings.qdrant_collection,
            "health": qdrant_health,
            "search_mode": "external_payload_filtered_vector_search",
        },
        "warnings": warnings,
    }


def vector_search_candidates(
    *,
    query_embedding: list[float],
    chunks: list[dict[str, Any]],
    limit: int,
) -> dict[str, Any]:
    settings = get_settings()
    status = rag_vector_store_status({"chunks": chunks, "embedding_dimensions": len(query_embedding)})
    if status["active_backend"] == "pgvector":
        pgvector = _pgvector_candidates(query_embedding, limit=max(limit * 12, 80))
        if pgvector.get("status") == "pass":
            return {**status, "candidate_source": "pgvector", "candidates": pgvector["candidates"], "warnings": status["warnings"]}
        status["warnings"] = list(status["warnings"]) + [pgvector.get("error") or "pgvector candidate search failed; falling back to local_json."]
    if settings.rag_vector_backend == "qdrant" and status["active_backend"] == "qdrant":
        qdrant = _qdrant_candidates(query_embedding, limit=max(limit * 12, 80))
        if qdrant.get("status") == "pass":
            return {**status, "candidate_source": "qdrant", "candidates": qdrant["candidates"], "warnings": status["warnings"]}
        status["warnings"] = list(status["warnings"]) + [qdrant.get("error") or "Qdrant candidate search failed; falling back to local_json."]
    return {
        **status,
        "active_backend": "local_json",
        "candidate_source": "local_json",
        "fallback_active": settings.rag_vector_backend != "local_json",
        "candidates": [
            {
                "chunk": chunk,
                "vector_score": _cosine_similarity(query_embedding, chunk.get("embedding") or []),
            }
            for chunk in chunks
        ],
    }


def _active_backend(target: str, pgvector_configured: bool, pgvector_driver_available: bool, qdrant_configured: bool) -> str:
    if target == "pgvector" and pgvector_configured and pgvector_driver_available:
        return "pgvector"
    if target == "qdrant" and qdrant_configured:
        return "qdrant"
    return "local_json"


def _pgvector_candidates(query_embedding: list[float], limit: int) -> dict[str, Any]:
    settings = get_settings()
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        return {"status": "fail", "error": f"psycopg unavailable: {exc}"}
    try:
        with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    select
                        chunk_id,
                        document_id,
                        text,
                        payload,
                        1 - (embedding <=> %s::vector) as vector_score
                    from {_identifier(settings.rag_pgvector_table)}
                    order by embedding <=> %s::vector
                    limit %s
                    """,
                    (_vector_literal(query_embedding), _vector_literal(query_embedding), limit),
                )
                rows = cur.fetchall()
    except Exception as exc:  # pragma: no cover - exercised only with external pgvector.
        return {"status": "fail", "error": f"pgvector query failed: {exc}"}
    return {
        "status": "pass",
        "candidates": [
            {
                "chunk": {
                    "chunk_id": row["chunk_id"],
                    "document_id": row["document_id"],
                    "text": row["text"],
                    "metadata": row["payload"] or {},
                    "embedding": query_embedding,
                },
                "vector_score": float(row["vector_score"] or 0),
            }
            for row in rows
        ],
    }


def _qdrant_candidates(query_embedding: list[float], limit: int) -> dict[str, Any]:
    settings = get_settings()
    endpoint = f"{settings.qdrant_url}/collections/{settings.qdrant_collection}/points/search"
    payload = json.dumps({"vector": query_embedding, "limit": limit, "with_payload": True}).encode("utf-8")
    request = Request(endpoint, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError) as exc:  # pragma: no cover - external service only.
        return {"status": "fail", "error": f"Qdrant query failed: {exc}"}
    results = body.get("result") or []
    return {
        "status": "pass",
        "candidates": [
            {
                "chunk": {
                    "chunk_id": str(item.get("id")),
                    "document_id": (item.get("payload") or {}).get("document_id") or str(item.get("id")),
                    "text": (item.get("payload") or {}).get("text") or "",
                    "metadata": (item.get("payload") or {}).get("metadata") or (item.get("payload") or {}),
                    "embedding": query_embedding,
                },
                "vector_score": float(item.get("score") or 0),
            }
            for item in results
        ],
    }


def _qdrant_health(base_url: str) -> dict[str, Any]:
    try:
        with urlopen(f"{base_url}/healthz", timeout=2) as response:
            return {"checked": True, "status_code": response.status, "healthy": 200 <= response.status < 300}
    except (OSError, URLError) as exc:
        return {"checked": True, "healthy": False, "error": str(exc)}


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)


def _vector_literal(values: Iterable[float]) -> str:
    return "[" + ",".join(f"{float(value):.8f}" for value in values) + "]"


def _identifier(value: str) -> str:
    parts = [part for part in value.split(".") if part]
    return ".".join('"' + part.replace('"', '""') + '"' for part in parts)
