from __future__ import annotations

import json
from datetime import datetime, timezone
from io import BytesIO
from typing import Any
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from app.services.export_manifest_service import ManifestedZip, verify_artifact_bundle
from app.services.rag_diagnostics_service import rag_diagnostics
from app.services.rag_embedding_service import rag_embedding_status
from app.services.rag_service import load_rag_index, rag_status
from app.services.structured_data_service import structured_manifest


REQUIRED_VECTOR_INDEX_FILES = {
    "bundle_manifest.json",
    "vector_chunks.jsonl",
    "payload_schema.json",
    "embedding_status.json",
    "pgvector_schema.sql",
    "qdrant_collection.json",
    "rag_status.json",
    "rag_diagnostics.json",
    "structured_manifest.json",
}


def build_rag_vector_index_bundle() -> bytes:
    index = load_rag_index()
    diagnostics = rag_diagnostics()
    embedding = rag_embedding_status()
    readiness = diagnostics.get("vector_store_readiness") or {}
    metadata = {
        "bundle_schema": "agentic-rag-vector-index-bundle-v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "chunk_count": len(index.get("chunks") or []),
        "embedding_model": index.get("embedding_model"),
        "embedding_dimensions": index.get("embedding_dimensions"),
        "embedding_backend": embedding.get("active_backend"),
        "embedding_backend_status": embedding.get("status"),
        "retrieval_model": index.get("retrieval_model"),
        "structured_manifest_hash": index.get("structured_manifest_hash"),
        "active_backend": readiness.get("active_backend"),
        "recommended_backend": readiness.get("recommended_backend"),
    }
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        bundle = ManifestedZip(archive, "rag_vector_index_bundle", metadata)
        bundle.writestr("bundle_manifest.json", _json(metadata))
        bundle.writestr("vector_chunks.jsonl", _vector_chunks_jsonl(index.get("chunks") or []))
        bundle.writestr("payload_schema.json", _json(readiness.get("migration_contract") or {}))
        bundle.writestr("embedding_status.json", _json(embedding))
        bundle.writestr("pgvector_schema.sql", _pgvector_schema_sql(metadata, readiness))
        bundle.writestr("qdrant_collection.json", _json(_qdrant_collection_config(metadata, readiness)))
        bundle.writestr("rag_status.json", _json(rag_status()))
        bundle.writestr("rag_diagnostics.json", _json(diagnostics))
        bundle.writestr("structured_manifest.json", _json(structured_manifest()))
        bundle.write_artifact_manifest()
    return buffer.getvalue()


def verify_rag_vector_index_bundle(bundle: bytes) -> dict[str, Any]:
    base = verify_artifact_bundle(bundle)
    errors = list(base.get("errors") or [])
    warnings = list(base.get("warnings") or [])
    semantic_errors: list[str] = []
    semantic_warnings: list[str] = []
    semantic_checks: dict[str, str] = {}

    if base.get("artifact_type") != "rag_vector_index_bundle":
        semantic_errors.append("Artifact manifest artifact_type must be rag_vector_index_bundle.")
        semantic_checks["artifact_type"] = "fail"
    else:
        semantic_checks["artifact_type"] = "pass"

    try:
        with ZipFile(BytesIO(bundle), "r") as archive:
            names = set(archive.namelist())
            missing = sorted(REQUIRED_VECTOR_INDEX_FILES - names)
            if missing:
                semantic_errors.append(f"Required RAG vector index files are missing: {', '.join(missing)}.")
                semantic_checks["required_files"] = "fail"
                manifest: dict[str, Any] = {}
                chunks: list[dict[str, Any]] = []
                diagnostics: dict[str, Any] = {}
                embedding_status: dict[str, Any] = {}
                status: dict[str, Any] = {}
                payload_schema: dict[str, Any] = {}
            else:
                semantic_checks["required_files"] = "pass"
                manifest = _read_json(archive, "bundle_manifest.json")
                chunks = _read_jsonl(archive, "vector_chunks.jsonl")
                diagnostics = _read_json(archive, "rag_diagnostics.json")
                embedding_status = _read_json(archive, "embedding_status.json")
                status = _read_json(archive, "rag_status.json")
                payload_schema = _read_json(archive, "payload_schema.json")
                _read_json(archive, "qdrant_collection.json")
                archive.read("pgvector_schema.sql").decode("utf-8")
    except (BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as exc:
        semantic_errors.append(f"Invalid RAG vector index bundle: {exc}")
        manifest = {}
        chunks = []
        diagnostics = {}
        embedding_status = {}
        status = {}
        payload_schema = {}

    expected_count = int(manifest.get("chunk_count") or -1)
    if expected_count != len(chunks):
        semantic_errors.append("bundle_manifest.json chunk_count does not match vector_chunks.jsonl rows.")
        semantic_checks["chunk_count"] = "fail"
    else:
        semantic_checks["chunk_count"] = "pass"

    dimensions = int(manifest.get("embedding_dimensions") or 0)
    bad_dimensions = [row.get("chunk_id") for row in chunks if len(row.get("embedding") or []) != dimensions]
    if dimensions <= 0 or bad_dimensions:
        semantic_errors.append("One or more vector chunks have invalid embedding dimensions.")
        semantic_checks["embedding_dimensions"] = "fail"
    else:
        semantic_checks["embedding_dimensions"] = "pass"

    duplicate_ids = _duplicates(str(row.get("chunk_id") or "") for row in chunks)
    if duplicate_ids:
        semantic_errors.append(f"Duplicate vector chunk IDs detected: {', '.join(duplicate_ids[:10])}.")
        semantic_checks["unique_chunk_ids"] = "fail"
    else:
        semantic_checks["unique_chunk_ids"] = "pass"

    required_payload = payload_schema.get("required_payload_fields") or []
    missing_payload_rows = [
        row.get("chunk_id")
        for row in chunks
        if any(field not in (row.get("payload") or {}) for field in required_payload)
    ]
    if missing_payload_rows:
        semantic_warnings.append(f"{len(missing_payload_rows)} chunks are missing required vector payload fields.")
        semantic_checks["payload_completeness"] = "warning"
    else:
        semantic_checks["payload_completeness"] = "pass"

    readiness = diagnostics.get("vector_store_readiness") or {}
    if readiness.get("readiness_schema") != "agentic-rag-vector-store-readiness-v1":
        semantic_errors.append("rag_diagnostics.json vector_store_readiness schema is invalid.")
        semantic_checks["vector_readiness_schema"] = "fail"
    else:
        semantic_checks["vector_readiness_schema"] = "pass"

    if embedding_status.get("embedding_schema") != "agentic-rag-embedding-backend-v1":
        semantic_errors.append("embedding_status.json embedding_schema is invalid.")
        semantic_checks["embedding_status_schema"] = "fail"
    else:
        semantic_checks["embedding_status_schema"] = "pass"

    embedding_models = {
        str(value)
        for value in [
            manifest.get("embedding_model"),
            embedding_status.get("embedding_model"),
            status.get("embedding_model"),
            (diagnostics.get("index") or {}).get("embedding_model"),
        ]
        if value
    }
    if len(embedding_models) > 1:
        semantic_errors.append("Embedding model metadata disagrees across RAG vector index files.")
        semantic_checks["embedding_model"] = "fail"
    else:
        semantic_checks["embedding_model"] = "pass" if embedding_models else "warning"

    manifest_hashes = {
        str(value)
        for value in [
            manifest.get("structured_manifest_hash"),
            status.get("structured_manifest_hash"),
            (diagnostics.get("index") or {}).get("structured_manifest_hash"),
        ]
        if value
    }
    if len(manifest_hashes) > 1:
        semantic_errors.append("Structured manifest hashes disagree across RAG vector index files.")
        semantic_checks["structured_manifest_hash"] = "fail"
    else:
        semantic_checks["structured_manifest_hash"] = "pass" if manifest_hashes else "warning"

    semantic_status = "fail" if semantic_errors else "warning" if semantic_warnings else "pass"
    status_value = "fail" if errors or semantic_errors else "warning" if warnings or semantic_warnings else "pass"
    return {
        **base,
        "status": status_value,
        "errors": errors + semantic_errors,
        "warnings": warnings + semantic_warnings,
        "semantic_status": semantic_status,
        "semantic_checks": semantic_checks,
        "chunk_count": len(chunks),
        "embedding_dimensions": dimensions,
        "embedding_model": manifest.get("embedding_model"),
        "retrieval_model": manifest.get("retrieval_model"),
        "recommended_backend": manifest.get("recommended_backend"),
        "structured_manifest_hash": next(iter(manifest_hashes), None),
    }


def _vector_chunks_jsonl(chunks: list[dict[str, Any]]) -> str:
    rows = []
    for chunk in chunks:
        metadata = chunk.get("metadata") or {}
        rows.append(
            {
                "chunk_id": chunk.get("chunk_id"),
                "document_id": chunk.get("document_id"),
                "text": chunk.get("text"),
                "embedding": chunk.get("embedding") or [],
                "payload": metadata,
            }
        )
    return "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + ("\n" if rows else "")


def _pgvector_schema_sql(metadata: dict[str, Any], readiness: dict[str, Any]) -> str:
    dimensions = int(metadata.get("embedding_dimensions") or 0)
    payload_fields = ", ".join(readiness.get("migration_contract", {}).get("required_payload_fields") or [])
    return f"""-- Agentic RAG vector index export
-- embedding_model: {metadata.get("embedding_model")}
-- retrieval_model: {metadata.get("retrieval_model")}
-- required_payload_fields: {payload_fields}
create extension if not exists vector;

create table if not exists rag_chunks (
    chunk_id text primary key,
    document_id text not null,
    text text not null,
    embedding vector({dimensions}) not null,
    payload jsonb not null,
    structured_manifest_hash text,
    indexed_at timestamptz default now()
);

create index if not exists idx_rag_chunks_embedding_hnsw
    on rag_chunks using hnsw (embedding vector_cosine_ops);
create index if not exists idx_rag_chunks_payload_gin
    on rag_chunks using gin (payload);
"""


def _qdrant_collection_config(metadata: dict[str, Any], readiness: dict[str, Any]) -> dict[str, Any]:
    return {
        "collection_name": "agentic_rag_chunks",
        "vectors": {
            "size": int(metadata.get("embedding_dimensions") or 0),
            "distance": "Cosine",
        },
        "payload_schema": {
            field: "keyword"
            for field in (readiness.get("migration_contract", {}).get("required_payload_fields") or [])
        },
        "recommended_backend": metadata.get("recommended_backend"),
        "embedding_model": metadata.get("embedding_model"),
        "retrieval_model": metadata.get("retrieval_model"),
    }


def _duplicates(values: Any) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if not value:
            continue
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _read_json(archive: ZipFile, name: str) -> dict[str, Any]:
    payload = json.loads(archive.read(name).decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def _read_jsonl(archive: ZipFile, name: str) -> list[dict[str, Any]]:
    rows = []
    for line in archive.read(name).decode("utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
