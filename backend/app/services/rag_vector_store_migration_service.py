from __future__ import annotations

import importlib.util
import json
import uuid
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Iterable
from urllib.error import URLError
from urllib.request import Request, urlopen

from app.config import get_settings
from app.services.rag_service import EMBEDDING_DIMENSIONS, load_rag_index
from app.services.rag_vector_store_service import rag_vector_store_status
from app.services.storage_service import postgres_schema_sql, redact_database_url


RAG_VECTOR_MIGRATION_SCHEMA = "agentic-rag-vector-store-migration-v1"


def rag_vector_store_import_plan(target_backend: str | None = None) -> dict[str, Any]:
    index = load_rag_index()
    target = _target_backend(target_backend)
    source = _source_summary(index)
    readiness = rag_vector_store_status(index)
    return {
        "migration_schema": RAG_VECTOR_MIGRATION_SCHEMA,
        "status": "planned",
        "dry_run": True,
        "target_backend": target,
        "source_backend": "local_json",
        "planned_at": _utc_now(),
        "source": source,
        "target": _target_summary(target),
        "runtime": readiness,
        "steps": _migration_steps(target),
        "warnings": _plan_warnings(target, source),
    }


def import_rag_vector_store(*, target_backend: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    plan = rag_vector_store_import_plan(target_backend)
    plan["dry_run"] = dry_run
    if dry_run:
        return plan
    target = plan["target_backend"]
    if target == "pgvector":
        imported = _import_pgvector()
    elif target == "qdrant":
        imported = _import_qdrant()
    else:
        return {**plan, "status": "blocked", "error": "local_json is already the source backend; choose pgvector or qdrant for import."}
    return {
        **plan,
        "status": imported.get("status", "imported"),
        "imported": imported,
        "parity": rag_vector_store_parity_report(target_backend=target),
    }


def rag_vector_store_parity_report(target_backend: str | None = None) -> dict[str, Any]:
    index = load_rag_index()
    target = _target_backend(target_backend)
    source = _source_summary(index)
    if target == "pgvector":
        target_summary = _pgvector_target_summary()
    elif target == "qdrant":
        target_summary = _qdrant_target_summary()
    else:
        target_summary = {
            "status": "source",
            "backend": "local_json",
            "records": source["records"],
            "row_fingerprint": source["row_fingerprint"],
        }
    target_available = target_summary.get("status") in {"available", "source"}
    count_match = target_available and target_summary.get("records") == source["records"]
    row_hash_match = target_available and (target_summary.get("row_fingerprint") or {}).get("combined_row_hash") == source["row_fingerprint"]["combined_row_hash"]
    status = "pass" if count_match and row_hash_match else "warning"
    return {
        "migration_schema": RAG_VECTOR_MIGRATION_SCHEMA,
        "status": status,
        "checked_at": _utc_now(),
        "source_backend": "local_json",
        "target_backend": target,
        "source": source,
        "target": target_summary,
        "comparison": {
            "target_available": target_available,
            "record_count_match": count_match,
            "row_hash_match": row_hash_match,
            "source_records": source["records"],
            "target_records": target_summary.get("records"),
            "source_row_hash": source["row_fingerprint"]["combined_row_hash"],
            "target_row_hash": (target_summary.get("row_fingerprint") or {}).get("combined_row_hash"),
        },
        "notes": _parity_notes(target_available, target),
    }


def _source_summary(index: dict[str, Any]) -> dict[str, Any]:
    rows = [_canonical_vector_row(chunk, index.get("structured_manifest_hash")) for chunk in index.get("chunks") or []]
    return {
        "index_version": index.get("index_version"),
        "embedding_model": index.get("embedding_model"),
        "retrieval_model": index.get("retrieval_model"),
        "embedding_dimensions": index.get("embedding_dimensions"),
        "structured_manifest_hash": index.get("structured_manifest_hash"),
        "records": len(rows),
        "row_fingerprint": _row_fingerprint(rows),
    }


def _import_pgvector() -> dict[str, Any]:
    settings = get_settings()
    if not settings.database_url:
        return {"status": "blocked", "error": "DATABASE_URL is required for pgvector import."}
    if importlib.util.find_spec("psycopg") is None:
        return {"status": "blocked", "error": "psycopg is not installed."}
    rows = [_canonical_vector_row(chunk, load_rag_index().get("structured_manifest_hash")) for chunk in load_rag_index().get("chunks") or []]
    import psycopg

    table = _identifier(settings.rag_pgvector_table)
    with psycopg.connect(settings.database_url) as conn:
        conn.execute(postgres_schema_sql())
        with conn.cursor() as cur:
            cur.executemany(
                f"""
                insert into {table} (
                    chunk_id, document_id, text, embedding, payload, structured_manifest_hash
                )
                values (%s, %s, %s, %s::vector, %s::jsonb, %s)
                on conflict(chunk_id) do update set
                    document_id = excluded.document_id,
                    text = excluded.text,
                    embedding = excluded.embedding,
                    payload = excluded.payload,
                    structured_manifest_hash = excluded.structured_manifest_hash,
                    indexed_at = now()
                """,
                [
                    (
                        row["chunk_id"],
                        row["document_id"],
                        row["text"],
                        _vector_literal(row["embedding"]),
                        json.dumps(row["payload"], ensure_ascii=False, sort_keys=True),
                        row["structured_manifest_hash"],
                    )
                    for row in rows
                ],
            )
    return {"status": "imported", "backend": "pgvector", "records": len(rows), "table": settings.rag_pgvector_table}


def _import_qdrant() -> dict[str, Any]:
    settings = get_settings()
    if not settings.qdrant_url:
        return {"status": "blocked", "error": "QDRANT_URL is required for Qdrant import."}
    index = load_rag_index()
    rows = [_canonical_vector_row(chunk, index.get("structured_manifest_hash")) for chunk in index.get("chunks") or []]
    _qdrant_request(
        f"/collections/{settings.qdrant_collection}",
        "PUT",
        {
            "vectors": {
                "size": int(index.get("embedding_dimensions") or EMBEDDING_DIMENSIONS),
                "distance": "Cosine",
            }
        },
    )
    for batch in _batches(rows, 128):
        _qdrant_request(
            f"/collections/{settings.qdrant_collection}/points?wait=true",
            "PUT",
            {
                "points": [
                    {
                        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, row["chunk_id"])),
                        "vector": row["embedding"],
                        "payload": {
                            "chunk_id": row["chunk_id"],
                            "document_id": row["document_id"],
                            "text": row["text"],
                            "metadata": row["payload"],
                            "structured_manifest_hash": row["structured_manifest_hash"],
                        },
                    }
                    for row in batch
                ]
            },
        )
    return {"status": "imported", "backend": "qdrant", "records": len(rows), "collection": settings.qdrant_collection}


def _pgvector_target_summary() -> dict[str, Any]:
    settings = get_settings()
    if not settings.database_url:
        return {"status": "unconfigured", "backend": "pgvector", "database_url_configured": False}
    if importlib.util.find_spec("psycopg") is None:
        return {"status": "driver_missing", "backend": "pgvector", "database_url_configured": True}
    try:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
            conn.execute(postgres_schema_sql())
            rows = conn.execute(
                f"""
                select chunk_id, document_id, text, embedding::text as embedding, payload, structured_manifest_hash
                from {_identifier(settings.rag_pgvector_table)}
                order by chunk_id
                """
            ).fetchall()
        canonical = [
            {
                "chunk_id": row["chunk_id"],
                "document_id": row["document_id"],
                "text": row["text"],
                "embedding": _parse_vector_text(row["embedding"]),
                "payload": row["payload"] or {},
                "structured_manifest_hash": row["structured_manifest_hash"],
            }
            for row in rows
        ]
        return {
            "status": "available",
            "backend": "pgvector",
            "database_url_configured": True,
            "database_url_redacted": redact_database_url(settings.database_url),
            "table": settings.rag_pgvector_table,
            "records": len(canonical),
            "row_fingerprint": _row_fingerprint(canonical),
        }
    except Exception as exc:  # noqa: BLE001 - external DB errors are surfaced as status payloads.
        return {
            "status": "unavailable",
            "backend": "pgvector",
            "database_url_configured": True,
            "database_url_redacted": redact_database_url(settings.database_url),
            "error": str(exc),
        }


def _qdrant_target_summary() -> dict[str, Any]:
    settings = get_settings()
    if not settings.qdrant_url:
        return {"status": "unconfigured", "backend": "qdrant", "url_configured": False}
    try:
        rows: list[dict[str, Any]] = []
        offset: Any = None
        while True:
            payload: dict[str, Any] = {"limit": 256, "with_payload": True, "with_vector": True}
            if offset is not None:
                payload["offset"] = offset
            response = _qdrant_request(f"/collections/{settings.qdrant_collection}/points/scroll", "POST", payload)
            result = response.get("result") or {}
            for point in result.get("points") or []:
                point_payload = point.get("payload") or {}
                rows.append(
                    {
                        "chunk_id": point_payload.get("chunk_id") or str(point.get("id")),
                        "document_id": point_payload.get("document_id") or "",
                        "text": point_payload.get("text") or "",
                        "embedding": point.get("vector") or [],
                        "payload": point_payload.get("metadata") or {},
                        "structured_manifest_hash": point_payload.get("structured_manifest_hash"),
                    }
                )
            offset = result.get("next_page_offset")
            if not offset:
                break
        rows.sort(key=lambda item: item["chunk_id"])
        return {
            "status": "available",
            "backend": "qdrant",
            "url_configured": True,
            "collection": settings.qdrant_collection,
            "records": len(rows),
            "row_fingerprint": _row_fingerprint(rows),
        }
    except Exception as exc:  # noqa: BLE001 - external service errors are surfaced as status payloads.
        return {"status": "unavailable", "backend": "qdrant", "url_configured": True, "collection": settings.qdrant_collection, "error": str(exc)}


def _target_summary(target: str) -> dict[str, Any]:
    settings = get_settings()
    if target == "pgvector":
        return {
            "backend": "pgvector",
            "configured": bool(settings.database_url),
            "database_url_redacted": redact_database_url(settings.database_url) if settings.database_url else None,
            "table": settings.rag_pgvector_table,
            "requires": ["DATABASE_URL", "psycopg", "Postgres vector extension"],
        }
    if target == "qdrant":
        return {
            "backend": "qdrant",
            "configured": bool(settings.qdrant_url),
            "url_configured": bool(settings.qdrant_url),
            "collection": settings.qdrant_collection,
            "requires": ["QDRANT_URL"],
        }
    return {"backend": "local_json", "configured": True}


def _canonical_vector_row(chunk: dict[str, Any], structured_manifest_hash: str | None) -> dict[str, Any]:
    return {
        "chunk_id": str(chunk.get("chunk_id") or ""),
        "document_id": str(chunk.get("document_id") or ""),
        "text": str(chunk.get("text") or ""),
        "embedding": [round(float(value), 8) for value in (chunk.get("embedding") or [])],
        "payload": chunk.get("metadata") or chunk.get("payload") or {},
        "structured_manifest_hash": structured_manifest_hash or "",
    }


def _row_fingerprint(rows: list[dict[str, Any]]) -> dict[str, Any]:
    row_hashes = [
        sha256(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        for row in sorted(rows, key=lambda item: item["chunk_id"])
    ]
    return {
        "row_count": len(rows),
        "combined_row_hash": sha256("\n".join(row_hashes).encode("utf-8")).hexdigest(),
        "sample_row_hashes": row_hashes[:5],
    }


def _target_backend(target_backend: str | None) -> str:
    normalized = (target_backend or get_settings().rag_vector_backend or "local_json").strip().lower()
    return normalized if normalized in {"local_json", "pgvector", "qdrant"} else "local_json"


def _plan_warnings(target: str, source: dict[str, Any]) -> list[str]:
    warnings = []
    if source["records"] == 0:
        warnings.append("Local RAG index has no vector chunks to import.")
    if target == "local_json":
        warnings.append("local_json is the source backend; choose pgvector or qdrant for a persistent vector-store import.")
    return warnings


def _migration_steps(target: str) -> list[str]:
    if target == "pgvector":
        return [
            "Apply Postgres schema SQL with vector extension.",
            "Upsert local RAG chunks into rag_chunks with JSONB payload metadata.",
            "Run vector-store parity and smoke retrieval checks.",
            "Set RAG_VECTOR_BACKEND=pgvector after parity passes.",
        ]
    if target == "qdrant":
        return [
            "Create or update the Qdrant collection with cosine vector size.",
            "Upsert local RAG chunks as points with metadata payload.",
            "Run vector-store parity and smoke retrieval checks.",
            "Set RAG_VECTOR_BACKEND=qdrant after parity passes.",
        ]
    return ["Use local JSON RAG index directly."]


def _parity_notes(target_available: bool, target: str) -> list[str]:
    if target_available:
        return ["Target vector store is reachable and row-level hashes were compared."]
    if target == "local_json":
        return ["local_json parity compares the source index to itself."]
    return [f"{target} is not reachable or not configured; run import with dry_run=false after configuring the target service."]


def _qdrant_request(path: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(f"{settings.qdrant_url}{path}", data=body, headers={"Content-Type": "application/json"}, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except (OSError, URLError) as exc:
        raise RuntimeError(f"Qdrant request failed: {exc}") from exc
    return json.loads(raw) if raw else {}


def _parse_vector_text(value: str) -> list[float]:
    stripped = str(value or "").strip().strip("[]")
    if not stripped:
        return []
    return [round(float(item), 8) for item in stripped.split(",") if item.strip()]


def _vector_literal(values: Iterable[float]) -> str:
    return "[" + ",".join(f"{float(value):.8f}" for value in values) + "]"


def _identifier(value: str) -> str:
    parts = [part for part in value.split(".") if part]
    return ".".join('"' + part.replace('"', '""') + '"' for part in parts)


def _batches(rows: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for index in range(0, len(rows), size):
        yield rows[index : index + size]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
