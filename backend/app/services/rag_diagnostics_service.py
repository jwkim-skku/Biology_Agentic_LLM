from __future__ import annotations

import re
from collections import Counter
from statistics import mean
from typing import Any

from app.services.rag_regression_service import evaluate_rag_regression
from app.services.rag_embedding_service import rag_embedding_status
from app.services.rag_service import CHUNKING_POLICY, EMBEDDING_DIMENSIONS, RETRIEVAL_MODEL, load_rag_index, rag_status
from app.services.rag_vector_store_service import rag_vector_store_status


def rag_diagnostics() -> dict[str, Any]:
    index = load_rag_index()
    chunks = index.get("chunks", [])
    regression = evaluate_rag_regression()
    distributions = _metadata_distributions(chunks)
    token_stats = _token_stats(chunks)
    embedding_stats = _embedding_stats(chunks)
    embedding_backend = rag_embedding_status()
    vector_runtime = rag_vector_store_status(index)
    vector_store = _vector_store_readiness(chunks, index, vector_runtime)
    weak_cases = [
        {
            "case_id": result["case_id"],
            "status": result["status"],
            "recall_at_k": result["recall_at_k"],
            "ndcg_at_k": result["ndcg_at_k"],
            "source_coverage": result["source_coverage"],
            "collection_coverage": result["collection_coverage"],
            "message": (result.get("errors") or result.get("warnings") or ["pass"])[0],
        }
        for result in regression.get("results", [])
        if result.get("status") != "pass"
    ]
    warnings = _diagnostic_warnings(chunks, distributions, token_stats, embedding_stats, regression)
    all_warnings = warnings + list(vector_store.get("warnings") or []) + list(embedding_backend.get("warnings") or [])
    return {
        "diagnostics_schema": "agentic-rag-diagnostics-v1",
        "status": "warning" if all_warnings or regression.get("status") != "pass" or vector_store.get("status") != "pass" else "pass",
        "retrieval_model": RETRIEVAL_MODEL,
        "embedding_backend": embedding_backend,
        "rag": rag_status(),
        "index": {
            "chunk_count": len(chunks),
            "document_count": len({chunk.get("document_id") for chunk in chunks}),
            "embedding_model": index.get("embedding_model"),
            "embedding_dimensions": index.get("embedding_dimensions", EMBEDDING_DIMENSIONS),
            "structured_manifest_hash": index.get("structured_manifest_hash"),
            "chunking_policy": index.get("chunking_policy", CHUNKING_POLICY),
        },
        "distributions": distributions,
        "token_stats": token_stats,
        "embedding_stats": embedding_stats,
        "vector_store_readiness": vector_store,
        "regression": {
            "status": regression.get("status"),
            "case_count": regression.get("case_count"),
            "pass_count": regression.get("pass_count"),
            "warning_count": regression.get("warning_count"),
            "fail_count": regression.get("fail_count"),
            "cases_hash": regression.get("cases_hash"),
            "macro": regression.get("macro"),
            "weak_cases": weak_cases,
        },
        "recommendations": _recommendations(all_warnings, regression, distributions, vector_store, embedding_backend),
        "warnings": all_warnings,
    }


def _metadata_distributions(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "collections": _top_counter(chunk.get("metadata", {}).get("collection") for chunk in chunks),
        "sources": _top_counter(chunk.get("metadata", {}).get("source") for chunk in chunks),
        "evidence_classes": _top_counter(chunk.get("metadata", {}).get("evidence_class") for chunk in chunks),
        "confidence": _top_counter(chunk.get("metadata", {}).get("confidence") for chunk in chunks),
        "species": _top_counter(item for chunk in chunks for item in chunk.get("metadata", {}).get("species", [])),
        "regions": _top_counter(item for chunk in chunks for item in chunk.get("metadata", {}).get("regions", [])),
        "cell_types": _top_counter(item for chunk in chunks for item in chunk.get("metadata", {}).get("cell_types", [])),
        "modalities": _top_counter(item for chunk in chunks for item in chunk.get("metadata", {}).get("modalities", [])),
    }


def _token_stats(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    lengths = [len(_tokens(chunk.get("text", ""))) for chunk in chunks]
    if not lengths:
        return {"min": 0, "max": 0, "mean": 0.0, "empty_chunks": 0}
    return {
        "min": min(lengths),
        "max": max(lengths),
        "mean": round(mean(lengths), 2),
        "empty_chunks": sum(1 for length in lengths if length == 0),
        "short_chunks": sum(1 for length in lengths if 0 < length < 8),
        "long_chunks": sum(1 for length in lengths if length > 140),
    }


def _embedding_stats(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    dimensions = [len(chunk.get("embedding", [])) for chunk in chunks]
    nonzero = [sum(1 for value in chunk.get("embedding", []) if value) for chunk in chunks]
    if not chunks:
        return {"dimension_mismatch_count": 0, "mean_nonzero_dimensions": 0.0, "empty_embedding_count": 0}
    return {
        "dimension_mismatch_count": sum(1 for dimension in dimensions if dimension != EMBEDDING_DIMENSIONS),
        "mean_nonzero_dimensions": round(mean(nonzero), 2) if nonzero else 0.0,
        "empty_embedding_count": sum(1 for count in nonzero if count == 0),
    }


def _diagnostic_warnings(
    chunks: list[dict[str, Any]],
    distributions: dict[str, Any],
    token_stats: dict[str, Any],
    embedding_stats: dict[str, Any],
    regression: dict[str, Any],
) -> list[str]:
    warnings: list[str] = []
    if not chunks:
        warnings.append("RAG index has no chunks.")
    if embedding_stats["dimension_mismatch_count"]:
        warnings.append("Some chunks have embedding dimension mismatches.")
    if embedding_stats["empty_embedding_count"]:
        warnings.append("Some chunks have empty embeddings.")
    if token_stats["empty_chunks"]:
        warnings.append("Some chunks have no lexical tokens.")
    if token_stats["long_chunks"]:
        warnings.append("Some chunks exceed the expected token window.")
    for key in ["collections", "sources", "evidence_classes"]:
        if not distributions[key]:
            warnings.append(f"RAG index has no {key} metadata distribution.")
    if regression.get("status") == "fail":
        warnings.append("RAG regression suite has failing cases.")
    elif regression.get("status") == "warning":
        warnings.append("RAG regression suite has warning cases.")
    return warnings


def _vector_store_readiness(chunks: list[dict[str, Any]], index: dict[str, Any], vector_runtime: dict[str, Any]) -> dict[str, Any]:
    required_metadata = ["title", "collection", "source", "source_url", "evidence_class", "confidence"]
    optional_facets = ["species", "regions", "cell_types", "modalities", "topics"]
    missing_required = 0
    facet_completeness: dict[str, float] = {}
    for chunk in chunks:
        metadata = chunk.get("metadata") or {}
        if any(field not in metadata for field in required_metadata):
            missing_required += 1
    for facet in optional_facets:
        populated = sum(1 for chunk in chunks if (chunk.get("metadata") or {}).get(facet))
        facet_completeness[facet] = round(populated / len(chunks), 4) if chunks else 0.0
    dimensions = int(index.get("embedding_dimensions") or 0)
    backend_candidates = [
        {
            "backend": "local_json",
            "status": "active",
            "fit": "small corpus, deterministic local development, zero external services",
        },
        {
            "backend": "pgvector",
            "status": "ready" if chunks and dimensions > 0 and missing_required == 0 else "blocked",
            "fit": "production Postgres co-location, SQL metadata filters, HNSW/IVFFlat migration path",
        },
        {
            "backend": "qdrant",
            "status": "ready" if chunks and dimensions > 0 and missing_required == 0 else "blocked",
            "fit": "larger corpora, payload filtering, hybrid search service separation",
        },
    ]
    warnings = []
    if missing_required:
        warnings.append(f"{missing_required} chunks are missing required vector payload metadata.")
    if dimensions <= 0:
        warnings.append("Embedding dimensions are not populated.")
    if chunks and len(chunks) < 1000:
        recommended_backend = "local_json"
    elif chunks and len(chunks) < 50000:
        recommended_backend = "pgvector"
    else:
        recommended_backend = "qdrant"
    return {
        "readiness_schema": "agentic-rag-vector-store-readiness-v1",
        "status": "warning" if warnings else "pass",
        "active_backend": vector_runtime.get("active_backend", "local_json"),
        "target_backend": vector_runtime.get("target_backend", "local_json"),
        "runtime": vector_runtime,
        "recommended_backend": recommended_backend,
        "chunk_count": len(chunks),
        "embedding_dimensions": dimensions,
        "embedding_model": index.get("embedding_model"),
        "retrieval_model": index.get("retrieval_model"),
        "required_metadata_missing_chunks": missing_required,
        "facet_completeness": facet_completeness,
        "backend_candidates": backend_candidates,
        "warnings": warnings,
        "migration_contract": {
            "id_field": "chunk_id",
            "vector_field": "embedding",
            "payload_field": "metadata",
            "text_field": "text",
            "distance": "cosine",
            "required_payload_fields": required_metadata,
            "filter_payload_fields": optional_facets,
        },
    }


def _recommendations(
    warnings: list[str],
    regression: dict[str, Any],
    distributions: dict[str, Any],
    vector_store: dict[str, Any],
    embedding_backend: dict[str, Any],
) -> list[str]:
    recommendations: list[str] = []
    if any("expected token window" in warning for warning in warnings):
        recommendations.append("Inspect long chunks and re-ingest or re-chunk unusually dense documents if retrieval quality drops.")
    if any("embedding" in warning.lower() or "lexical tokens" in warning.lower() for warning in warnings):
        recommendations.append("Run /api/v1/rag/rebuild after structured data or document ingestion changes.")
    if regression.get("status") != "pass":
        recommendations.append("Inspect /api/v1/rag/regression for weak cases and add release-pinned evidence for missing facets.")
    if len(distributions.get("sources", [])) < 3:
        recommendations.append("Add more independent evidence sources before relying on retrieval synthesis.")
    if vector_store.get("recommended_backend") != "local_json":
        recommendations.append(f"Prepare {vector_store.get('recommended_backend')} for the current RAG corpus scale.")
    if vector_store.get("status") != "pass":
        recommendations.append("Complete vector payload metadata before moving RAG chunks into pgvector or Qdrant.")
    if not embedding_backend.get("production_ready"):
        recommendations.append("Pin a biomedical sentence-transformers embedding model and rerun the RAG regression suite before production retrieval promotion.")
    if not recommendations:
        recommendations.append("Current RAG diagnostics are clean under the configured local regression suite.")
    return recommendations


def _top_counter(values: Any, limit: int = 12) -> list[dict[str, Any]]:
    counter = Counter(str(value) for value in values if value)
    return [{"value": value, "count": count} for value, count in counter.most_common(limit)]


def _tokens(text: str) -> list[str]:
    return [token for token in re.split(r"[^a-zA-Z0-9]+", text.lower()) if len(token) >= 3]
