from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.services.rag_embedding_service import DEFAULT_EMBEDDING_DIMENSIONS, HASH_BOW_MODEL, embed_text, rag_embedding_status
from app.services.ingestion_service import DOCUMENT_DIR, load_ingested_documents
from app.services.structured_data_service import STRUCTURED_DIR, structured_manifest_hash, structured_records_as_evidence
from app.services.rag_vector_store_service import rag_vector_store_status, vector_search_candidates


DATA_DIR = get_settings().data_dir
EVIDENCE_PATH = DATA_DIR / "evidence_seed.json"
RAG_INDEX_PATH = DATA_DIR / "rag_index.json"
EMBEDDING_DIMENSIONS = DEFAULT_EMBEDDING_DIMENSIONS
EMBEDDING_MODEL = HASH_BOW_MODEL
RETRIEVAL_MODEL = "hybrid-hash-bm25-facet-rerank-v2"
RANKING_POLICY = {
    "version": "hybrid-score-policy-v2",
    "weights": {
        "vector_score": 0.36,
        "bm25_score": 0.24,
        "rerank_score": 0.40,
    },
    "rerank_components": {
        "lexical": 0.34,
        "facet": 0.30,
        "field_match": 0.18,
        "source_priority": 0.10,
        "confidence": 0.08,
    },
    "diversification": "first pass keeps one top chunk per document, then fills remaining slots by score",
}
CHUNKING_POLICY = {
    "version": "lexical-window-v2",
    "max_words": 90,
    "max_lexical_tokens": 120,
    "sentence_boundary_split": True,
    "drop_empty_lexical_chunks": True,
}
BIOMEDICAL_ALIASES = {
    "aav": ["adeno", "associated", "virus", "payload", "vector"],
    "adenoassociated": ["aav", "payload", "vector"],
    "allen": ["abc", "atlas", "cell", "taxonomy"],
    "canonical": ["mane", "refseq", "ensembl", "transcript"],
    "custom": ["tissue", "aware", "codon", "translation"],
    "dopaminergic": ["dopamine", "neuron", "midbrain", "substantia", "nigra"],
    "gtex": ["tissue", "expression", "brain"],
    "mane": ["canonical", "refseq", "ensembl", "transcript", "cds"],
    "medium": ["spiny", "neuron", "striatum"],
    "nigra": ["substantia", "dopaminergic", "midbrain"],
    "striatum": ["medium", "spiny", "neuron"],
    "trna": ["codon", "availability", "translation"],
}


@dataclass(frozen=True)
class RagChunk:
    chunk_id: str
    document_id: str
    text: str
    metadata: dict[str, Any]
    embedding: list[float]


def rag_status() -> dict[str, Any]:
    index = load_rag_index()
    vector_store = rag_vector_store_status(index)
    embedding = rag_embedding_status()
    return {
        "index_version": index["index_version"],
        "embedding_model": index["embedding_model"],
        "embedding_backend": embedding,
        "retrieval_model": index.get("retrieval_model", RETRIEVAL_MODEL),
        "vector_store": vector_store,
        "chunking_policy": index.get("chunking_policy", CHUNKING_POLICY),
        "documents": len({chunk["document_id"] for chunk in index["chunks"]}),
        "chunks": len(index["chunks"]),
        "source_path": str(EVIDENCE_PATH),
        "documents_path": str(DOCUMENT_DIR),
        "structured_path": str(STRUCTURED_DIR),
        "structured_manifest_hash": structured_manifest_hash(),
        "persisted_path": str(RAG_INDEX_PATH),
    }


def rebuild_rag_index(persist: bool = True) -> dict[str, Any]:
    load_rag_index.cache_clear()
    evidence_records = _load_source_records()
    embedding_status = rag_embedding_status()
    chunks = []
    for record in evidence_records:
        for idx, text in enumerate(_chunk_record(record)):
            metadata = _metadata_from_record(record)
            embedded = _embed_result(text)
            chunks.append(
                {
                    "chunk_id": f"{record['id']}::chunk_{idx + 1:03d}",
                    "document_id": record["id"],
                    "text": text,
                    "metadata": metadata,
                    "embedding": embedded["embedding"],
                    "embedding_metadata": _embedding_metadata(embedded),
                }
            )
    actual_embedding = (chunks[0].get("embedding_metadata") if chunks else None) or {
        "embedding_schema": embedding_status.get("embedding_schema"),
        "active_backend": embedding_status.get("active_backend"),
        "requested_backend": embedding_status.get("requested_backend"),
        "fallback_active": embedding_status.get("fallback_active"),
        "embedding_model": embedding_status.get("embedding_model"),
        "embedding_dimensions": embedding_status.get("embedding_dimensions"),
        "warnings": embedding_status.get("warnings") or [],
    }
    index = {
        "index_version": "rag-mvp-1",
        "embedding_model": actual_embedding["embedding_model"],
        "embedding_backend": {**embedding_status, **actual_embedding},
        "retrieval_model": RETRIEVAL_MODEL,
        "chunking_policy": CHUNKING_POLICY,
        "embedding_dimensions": actual_embedding["embedding_dimensions"],
        "sources": [str(EVIDENCE_PATH), str(DOCUMENT_DIR), str(STRUCTURED_DIR)],
        "structured_manifest_hash": structured_manifest_hash(),
        "chunks": chunks,
    }
    if persist:
        RAG_INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    load_rag_index.cache_clear()
    return {
        "index_version": index["index_version"],
        "embedding_model": index["embedding_model"],
        "embedding_backend": index["embedding_backend"],
        "chunking_policy": index["chunking_policy"],
        "documents": len(evidence_records),
        "chunks": len(chunks),
        "persisted": persist,
    }


@lru_cache(maxsize=1)
def load_rag_index() -> dict[str, Any]:
    if RAG_INDEX_PATH.exists():
        index = json.loads(RAG_INDEX_PATH.read_text(encoding="utf-8"))
        embedding_status = rag_embedding_status()
        if (
            index.get("retrieval_model") == RETRIEVAL_MODEL
            and index.get("chunking_policy") == CHUNKING_POLICY
            and index.get("structured_manifest_hash") == structured_manifest_hash()
            and index.get("embedding_model") == embedding_status["embedding_model"]
            and int(index.get("embedding_dimensions") or 0) == int(embedding_status["embedding_dimensions"] or 0)
        ):
            return index
    rebuild_rag_index(persist=True)
    return json.loads(RAG_INDEX_PATH.read_text(encoding="utf-8"))


def rag_search(query: str, filters: dict[str, Any] | None = None, limit: int = 6) -> dict[str, Any]:
    filters = filters or {}
    index = load_rag_index()
    query_profile = _query_profile(query, filters)
    query_embedding = _embed(query)
    corpus_stats = _corpus_stats(index["chunks"])
    vector_candidates = vector_search_candidates(query_embedding=query_embedding, chunks=index["chunks"], limit=limit)
    results = []
    for candidate in vector_candidates["candidates"]:
        chunk = candidate["chunk"]
        if not _passes_filters(chunk["metadata"], filters):
            continue
        vector_score = float(candidate.get("vector_score") or 0)
        bm25_score = _bm25_score(chunk["text"], query, corpus_stats)
        rerank = _rerank_score(chunk, query, filters, query_profile)
        score = round((vector_score * 0.36) + (bm25_score * 0.24) + (rerank["rerank_score"] * 0.40), 6)
        if score > 0:
            results.append(
                {
                    **chunk,
                    "score": score,
                    "vector_score": round(vector_score, 6),
                    "bm25_score": round(bm25_score, 6),
                    **{key: round(value, 6) for key, value in rerank.items()},
                }
            )
    results = _diversify_by_document(sorted(results, key=lambda item: item["score"], reverse=True), limit)
    return {
        "query": query,
        "filters": filters,
        "index": {
            "index_version": index["index_version"],
            "embedding_model": index["embedding_model"],
            "retrieval_model": index.get("retrieval_model", RETRIEVAL_MODEL),
            "embedding_dimensions": index["embedding_dimensions"],
            "vector_store": {
                "runtime_schema": vector_candidates.get("runtime_schema"),
                "target_backend": vector_candidates.get("target_backend"),
                "active_backend": vector_candidates.get("active_backend"),
                "candidate_source": vector_candidates.get("candidate_source"),
                "fallback_active": vector_candidates.get("fallback_active"),
                "warnings": vector_candidates.get("warnings") or [],
            },
        },
        "query_analysis": query_profile,
        "chunks": results,
    }


def evaluate_rag_query(query: str, filters: dict[str, Any] | None = None, limit: int = 10) -> dict[str, Any]:
    filters = filters or {}
    search = rag_search(query, filters, limit)
    chunks = search["chunks"]
    coverage = _retrieval_coverage(chunks, filters)
    index = load_rag_index()
    facet_gaps = _facet_gap_analysis(chunks, index.get("chunks", []), filters)
    query_term_coverage = _query_term_coverage(search["query_analysis"], chunks)
    evidence_sufficiency = _evidence_sufficiency(chunks, filters, facet_gaps, query_term_coverage)
    trace = _retrieval_trace(search, chunks, filters, limit, facet_gaps=facet_gaps, query_term_coverage=query_term_coverage)
    return {
        "query": query,
        "filters": filters,
        "index": search["index"],
        "query_fingerprint": _query_fingerprint(query, filters, limit),
        "ranking_policy": RANKING_POLICY,
        "query_analysis": search["query_analysis"],
        "result_count": len(chunks),
        "coverage": coverage,
        "missing_facets": _missing_facets(coverage, filters),
        "facet_gap_analysis": facet_gaps,
        "query_term_coverage": query_term_coverage,
        "evidence_sufficiency": evidence_sufficiency,
        "retrieval_trace": trace,
        "top_sources": _top_sources(chunks),
        "score_breakdown": [
            {
                "chunk_id": chunk["chunk_id"],
                "document_id": chunk["document_id"],
                "title": chunk["metadata"]["title"],
                "collection": chunk["metadata"]["collection"],
                "source": chunk["metadata"]["source"],
                "score": chunk["score"],
                "vector_score": chunk["vector_score"],
                "bm25_score": chunk.get("bm25_score", 0),
                "rerank_score": chunk["rerank_score"],
                "facet_score": chunk.get("facet_score", 0),
                "field_match_score": chunk.get("field_match_score", 0),
                "source_priority_score": chunk.get("source_priority_score", 0),
                "matched_facets": _matched_facets(chunk["metadata"], filters),
                "rationale": _result_rationale(chunk, filters),
            }
            for chunk in chunks
        ],
        "recommended_query_terms": _recommended_query_terms(filters),
    }


def rag_chunks_to_evidence_records(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    seen: set[str] = set()
    for chunk in chunks:
        document_id = chunk["document_id"]
        if document_id in seen:
            continue
        seen.add(document_id)
        metadata = chunk["metadata"]
        records.append(
            {
                "id": document_id,
                "title": metadata["title"],
                "collection": metadata["collection"],
                "source": metadata["source"],
                "source_url": metadata["source_url"],
                "source_path": metadata.get("source_path", ""),
                "evidence_class": metadata["evidence_class"],
                "confidence": metadata["confidence"],
                "summary": metadata["summary"],
                "retrieval": {
                    "chunk_id": chunk["chunk_id"],
                    "score": chunk["score"],
                    "vector_score": chunk["vector_score"],
                    "bm25_score": chunk.get("bm25_score", 0),
                    "rerank_score": chunk["rerank_score"],
                    "facet_score": chunk.get("facet_score", 0),
                    "field_match_score": chunk.get("field_match_score", 0),
                    "source_priority_score": chunk.get("source_priority_score", 0),
                    "embedding_model": EMBEDDING_MODEL,
                    "embedding_backend": (chunk.get("embedding_metadata") or {}).get("active_backend"),
                    "retrieval_model": RETRIEVAL_MODEL,
                },
            }
        )
    return records


def _retrieval_coverage(chunks: list[dict[str, Any]], filters: dict[str, Any]) -> dict[str, Any]:
    return {
        "collections": sorted({chunk["metadata"].get("collection") for chunk in chunks if chunk["metadata"].get("collection")}),
        "evidence_classes": sorted({chunk["metadata"].get("evidence_class") for chunk in chunks if chunk["metadata"].get("evidence_class")}),
        "sources": sorted({chunk["metadata"].get("source") for chunk in chunks if chunk["metadata"].get("source")}),
        "species": _facet_available(chunks, "species", filters.get("species")),
        "brain_region": _facet_available(chunks, "regions", filters.get("brain_region")),
        "cell_type": _facet_available(chunks, "cell_types", filters.get("cell_type")),
        "modality": _facet_available(chunks, "modalities", filters.get("modality")),
        "high_confidence_results": sum(1 for chunk in chunks if chunk["metadata"].get("confidence") == "high"),
    }


def _facet_available(chunks: list[dict[str, Any]], metadata_key: str, value: Any) -> str:
    if not value:
        return "not_requested"
    if any(any(_loose_contains(str(value), item) for item in chunk["metadata"].get(metadata_key, [])) for chunk in chunks):
        return "matched"
    return "missing"


def _missing_facets(coverage: dict[str, Any], filters: dict[str, Any]) -> list[str]:
    facets = []
    for key in ["species", "brain_region", "cell_type", "modality"]:
        if filters.get(key) and coverage.get(key) == "missing":
            facets.append(key)
    return facets


def _top_sources(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for chunk in chunks:
        source = chunk["metadata"].get("source") or "unknown"
        counts[source] = counts.get(source, 0) + 1
    return [{"source": source, "chunks": count} for source, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)]


def _matched_facets(metadata: dict[str, Any], filters: dict[str, Any]) -> list[str]:
    matched = []
    for filter_key, metadata_key in [
        ("species", "species"),
        ("brain_region", "regions"),
        ("cell_type", "cell_types"),
        ("modality", "modalities"),
    ]:
        value = filters.get(filter_key)
        if value and any(_loose_contains(str(value), item) for item in metadata.get(metadata_key, [])):
            matched.append(filter_key)
    return matched


def _recommended_query_terms(filters: dict[str, Any]) -> list[str]:
    terms = ["MANE Select", "codon optimization", "QC constraints"]
    for key in ["brain_region", "cell_type", "modality"]:
        value = str(filters.get(key) or "").strip()
        if value:
            terms.append(value)
    return terms


def _facet_gap_analysis(chunks: list[dict[str, Any]], corpus_chunks: list[dict[str, Any]], filters: dict[str, Any]) -> dict[str, Any]:
    facets = {}
    for filter_key, metadata_key in [
        ("species", "species"),
        ("brain_region", "regions"),
        ("cell_type", "cell_types"),
        ("modality", "modalities"),
    ]:
        requested = str(filters.get(filter_key) or "").strip()
        if not requested:
            facets[filter_key] = {
                "requested": None,
                "status": "not_requested",
                "matched_result_count": 0,
                "corpus_available_count": 0,
                "top_available_values": [],
                "recommendation": "No filter was requested for this facet.",
            }
            continue
        matched_result_count = sum(
            1 for chunk in chunks if any(_loose_contains(requested, item) for item in chunk["metadata"].get(metadata_key, []))
        )
        corpus_matches = [
            chunk for chunk in corpus_chunks if any(_loose_contains(requested, item) for item in chunk.get("metadata", {}).get(metadata_key, []))
        ]
        top_available_values = _top_facet_values(corpus_chunks, metadata_key)
        if matched_result_count:
            status = "matched"
            recommendation = "Requested facet is represented in the retrieved result set."
        elif corpus_matches:
            status = "available_but_not_retrieved"
            recommendation = "Facet exists in the corpus but was not selected; inspect scores, raise limit, or add the facet term to the query."
        else:
            status = "missing_from_corpus"
            recommendation = "Facet is not represented in the indexed corpus; import or ingest release-pinned evidence for this target context."
        facets[filter_key] = {
            "requested": requested,
            "status": status,
            "matched_result_count": matched_result_count,
            "corpus_available_count": len(corpus_matches),
            "top_available_values": top_available_values,
            "recommendation": recommendation,
        }
    return {
        "analysis_schema": "agentic-rag-facet-gap-analysis-v1",
        "facets": facets,
        "missing_from_results": [
            key for key, value in facets.items() if value["status"] in {"available_but_not_retrieved", "missing_from_corpus"}
        ],
        "missing_from_corpus": [key for key, value in facets.items() if value["status"] == "missing_from_corpus"],
    }


def _query_term_coverage(query_analysis: dict[str, Any], chunks: list[dict[str, Any]]) -> dict[str, Any]:
    tokens = list(query_analysis.get("tokens") or [])
    expanded = list(query_analysis.get("expanded_tokens") or [])
    retrieved_tokens = _expanded_tokens(" ".join(chunk.get("text", "") for chunk in chunks))
    matched_tokens = sorted(token for token in tokens if token in retrieved_tokens)
    missing_tokens = sorted(token for token in tokens if token not in retrieved_tokens)
    matched_expanded = sorted(token for token in expanded if token in retrieved_tokens)
    return {
        "coverage_schema": "agentic-rag-query-term-coverage-v1",
        "query_token_count": len(tokens),
        "expanded_token_count": len(expanded),
        "matched_query_tokens": matched_tokens,
        "missing_query_tokens": missing_tokens,
        "matched_expanded_tokens": matched_expanded[:24],
        "coverage_fraction": round(len(matched_tokens) / max(len(tokens), 1), 4),
    }


def _evidence_sufficiency(
    chunks: list[dict[str, Any]],
    filters: dict[str, Any],
    facet_gaps: dict[str, Any],
    query_term_coverage: dict[str, Any],
) -> dict[str, Any]:
    requested_facets = [key for key in ["species", "brain_region", "cell_type", "modality"] if filters.get(key)]
    facet_statuses = (facet_gaps.get("facets") or {}) if isinstance(facet_gaps, dict) else {}
    matched_facets = [
        key
        for key in requested_facets
        if (facet_statuses.get(key) or {}).get("status") == "matched"
    ]
    source_count = len({chunk.get("metadata", {}).get("source") for chunk in chunks if chunk.get("metadata", {}).get("source")})
    collection_count = len({chunk.get("metadata", {}).get("collection") for chunk in chunks if chunk.get("metadata", {}).get("collection")})
    high_confidence_count = sum(1 for chunk in chunks if chunk.get("metadata", {}).get("confidence") == "high")
    coverage_fraction = float(query_term_coverage.get("coverage_fraction") or 0.0)
    corpus_gaps = list(facet_gaps.get("missing_from_corpus") or []) if isinstance(facet_gaps, dict) else []
    result_gaps = list(facet_gaps.get("missing_from_results") or []) if isinstance(facet_gaps, dict) else []
    checks = [
        _sufficiency_check("result_count", len(chunks) >= 3, len(chunks) > 0, f"{len(chunks)} retrieved chunks"),
        _sufficiency_check("independent_sources", source_count >= 2, source_count >= 1, f"{source_count} sources"),
        _sufficiency_check("collection_diversity", collection_count >= 2, collection_count >= 1, f"{collection_count} collections"),
        _sufficiency_check("high_confidence_evidence", high_confidence_count >= 1, bool(chunks), f"{high_confidence_count} high-confidence chunks"),
        _sufficiency_check(
            "requested_facets",
            len(matched_facets) == len(requested_facets),
            not corpus_gaps and len(matched_facets) >= max(1, len(requested_facets) - 1) if requested_facets else True,
            f"{len(matched_facets)}/{len(requested_facets)} requested facets matched",
        ),
        _sufficiency_check("query_term_coverage", coverage_fraction >= 0.6, coverage_fraction >= 0.35, f"{coverage_fraction:.2f} query-token coverage"),
    ]
    status = "fail" if any(check["status"] == "fail" for check in checks) else "warning" if any(check["status"] == "warning" for check in checks) else "pass"
    recommendations = []
    if len(chunks) < 3:
        recommendations.append("Increase retrieval limit or broaden the query before using this evidence set for production interpretation.")
    if source_count < 2:
        recommendations.append("Add at least one independent source or indexed dataset for corroboration.")
    if corpus_gaps:
        recommendations.append("Import release-pinned evidence for missing target facets: " + ", ".join(corpus_gaps) + ".")
    elif result_gaps:
        recommendations.append("Raise the retrieval limit or add facet terms so available target evidence is selected.")
    if coverage_fraction < 0.6:
        recommendations.append("Rewrite the query with missing biological terms before freezing the evidence bundle.")
    if not recommendations:
        recommendations.append("Retrieved evidence is sufficient for design-support review under the local RAG gate.")
    return {
        "sufficiency_schema": "agentic-rag-evidence-sufficiency-v1",
        "status": status,
        "result_count": len(chunks),
        "source_count": source_count,
        "collection_count": collection_count,
        "high_confidence_count": high_confidence_count,
        "requested_facets": requested_facets,
        "matched_facets": matched_facets,
        "missing_from_results": result_gaps,
        "missing_from_corpus": corpus_gaps,
        "query_term_coverage_fraction": coverage_fraction,
        "checks": checks,
        "recommendations": recommendations,
    }


def _sufficiency_check(name: str, pass_condition: bool, warning_condition: bool, detail: str) -> dict[str, Any]:
    return {
        "name": name,
        "status": "pass" if pass_condition else "warning" if warning_condition else "fail",
        "detail": detail,
    }


def _top_facet_values(chunks: list[dict[str, Any]], metadata_key: str, limit: int = 6) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for chunk in chunks:
        for value in chunk.get("metadata", {}).get(metadata_key, []):
            label = str(value).strip()
            if label:
                counts[label] = counts.get(label, 0) + 1
    return [
        {"value": value, "count": count}
        for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))[:limit]
    ]


def _query_fingerprint(query: str, filters: dict[str, Any], limit: int) -> str:
    payload = {
        "query": query.strip(),
        "filters": {key: filters[key] for key in sorted(filters)},
        "limit": limit,
        "retrieval_model": RETRIEVAL_MODEL,
        "ranking_policy": RANKING_POLICY["version"],
        "structured_manifest_hash": structured_manifest_hash(),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def _retrieval_trace(
    search: dict[str, Any],
    chunks: list[dict[str, Any]],
    filters: dict[str, Any],
    limit: int,
    *,
    facet_gaps: dict[str, Any] | None = None,
    query_term_coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "trace_schema": "agentic-rag-retrieval-trace-v1",
        "retrieval_model": search["index"].get("retrieval_model", RETRIEVAL_MODEL),
        "ranking_policy": RANKING_POLICY["version"],
        "chunking_policy": CHUNKING_POLICY["version"],
        "requested_limit": limit,
        "returned_chunks": len(chunks),
        "filters_requested": search["query_analysis"].get("filters_requested", []),
        "aliases_added_count": len(search["query_analysis"].get("aliases_added", [])),
        "score_weights": RANKING_POLICY["weights"],
        "rerank_components": RANKING_POLICY["rerank_components"],
        "diversification": RANKING_POLICY["diversification"],
        "coverage_status": _retrieval_coverage(chunks, filters),
        "facet_gap_status": {
            key: value.get("status")
            for key, value in ((facet_gaps or {}).get("facets") or {}).items()
            if value.get("status") != "not_requested"
        },
        "query_term_coverage_fraction": (query_term_coverage or {}).get("coverage_fraction"),
        "top_ranked_chunk_ids": [chunk["chunk_id"] for chunk in chunks[:5]],
    }


def _result_rationale(chunk: dict[str, Any], filters: dict[str, Any]) -> list[str]:
    metadata = chunk["metadata"]
    reasons = [
        f"hybrid score {chunk['score']:.3f} from vector {chunk['vector_score']:.2f}, bm25 {chunk.get('bm25_score', 0):.2f}, rerank {chunk['rerank_score']:.2f}",
    ]
    matched = _matched_facets(metadata, filters)
    if matched:
        reasons.append(f"matched requested facets: {', '.join(matched)}")
    if chunk.get("source_priority_score", 0) > 0:
        reasons.append(f"source priority {chunk['source_priority_score']:.2f} for {metadata.get('collection', 'collection')}")
    if chunk.get("field_match_score", 0) > 0:
        reasons.append(f"metadata field match {chunk['field_match_score']:.2f}")
    if metadata.get("confidence"):
        reasons.append(f"{metadata['confidence']} confidence evidence")
    return reasons


def _diversify_by_document(results: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_documents: set[str] = set()
    for result in results:
        if result["document_id"] in seen_documents:
            continue
        selected.append(result)
        seen_documents.add(result["document_id"])
        if len(selected) >= limit:
            return selected
    for result in results:
        if result in selected:
            continue
        selected.append(result)
        if len(selected) >= limit:
            break
    return selected


def _chunk_record(record: dict[str, Any], max_words: int = CHUNKING_POLICY["max_words"], max_tokens: int = CHUNKING_POLICY["max_lexical_tokens"]) -> list[str]:
    text = _record_text(record)
    words = text.split()
    if len(words) <= max_words:
        return _nonempty_chunks(_split_long_lexical_chunk(text, max_tokens=max_tokens), fallback=text)
    chunks: list[str] = []
    for idx in range(0, len(words), max_words):
        chunks.extend(_split_long_lexical_chunk(" ".join(words[idx : idx + max_words]), max_tokens=max_tokens))
    return _nonempty_chunks(chunks, fallback=text)


def _split_long_lexical_chunk(text: str, *, max_tokens: int) -> list[str]:
    if len(_token_list(text)) <= max_tokens:
        return [text]
    parts = re.split(r"(?<=[.!?。！？])\s+|\n+", text)
    chunks: list[str] = []
    current: list[str] = []
    for part in [item.strip() for item in parts if item.strip()]:
        candidate = " ".join([*current, part]).strip()
        if current and len(_token_list(candidate)) > max_tokens:
            chunks.extend(_split_by_words(" ".join(current), max_tokens=max_tokens))
            current = [part]
        else:
            current.append(part)
    if current:
        chunks.extend(_split_by_words(" ".join(current), max_tokens=max_tokens))
    return chunks or [text]


def _split_by_words(text: str, *, max_tokens: int) -> list[str]:
    words = text.split()
    chunks: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word]).strip()
        if current and len(_token_list(candidate)) > max_tokens:
            chunks.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        chunks.append(" ".join(current))
    return chunks


def _nonempty_chunks(chunks: list[str], *, fallback: str) -> list[str]:
    filtered = [chunk for chunk in chunks if _token_list(chunk)]
    if filtered:
        return filtered
    return [fallback]


def _metadata_from_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": record.get("title", record["id"]),
        "collection": record.get("collection", "literature"),
        "source": record.get("source", record.get("title", record["id"])),
        "source_url": record.get("source_url", ""),
        "source_path": record.get("source_path", ""),
        "evidence_class": record.get("evidence_class", "document"),
        "confidence": record.get("confidence", "medium"),
        "species": record.get("species", []),
        "topics": record.get("topics", []),
        "regions": record.get("regions", []),
        "cell_types": record.get("cell_types", []),
        "modalities": record.get("modalities", []),
        "summary": record.get("summary", ""),
        "release": record.get("release", ""),
        "source_sha256": record.get("source_sha256", record.get("_source_sha256", "")),
        "source_request_url": record.get("source_request_url", ""),
        "source_snapshot_path": record.get("source_snapshot_path", ""),
        "source_payload_sha256": record.get("source_payload_sha256", ""),
    }


def _load_source_records() -> list[dict[str, Any]]:
    seed_records = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    return seed_records + structured_records_as_evidence() + load_ingested_documents()


def _record_text(record: dict[str, Any]) -> str:
    content = record.get("content") or ""
    return " ".join(
        [
            record.get("title", record["id"]),
            record.get("summary", ""),
            content,
            f"Source: {record.get('source', '')}.",
            f"Topics: {', '.join(record.get('topics', []))}.",
            f"Regions: {', '.join(record.get('regions', []))}.",
            f"Cell types: {', '.join(record.get('cell_types', []))}.",
            f"Modalities: {', '.join(record.get('modalities', []))}.",
            f"Release: {record.get('release', '')}.",
            f"Evidence class: {record.get('evidence_class', '')}.",
        ]
    )


def _embed(text: str) -> list[float]:
    return _embed_result(text)["embedding"]


def _embed_result(text: str) -> dict[str, Any]:
    return embed_text(text, aliases=BIOMEDICAL_ALIASES)


def _embedding_metadata(embedded: dict[str, Any]) -> dict[str, Any]:
    return {
        "embedding_schema": embedded.get("embedding_schema"),
        "active_backend": embedded.get("active_backend"),
        "requested_backend": embedded.get("requested_backend"),
        "fallback_active": embedded.get("fallback_active"),
        "embedding_model": embedded.get("embedding_model"),
        "embedding_dimensions": embedded.get("embedding_dimensions"),
        "warnings": embedded.get("warnings") or [],
    }


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    return max(0.0, sum(a * b for a, b in zip(left, right)))


def _corpus_stats(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    document_frequencies: dict[str, int] = {}
    lengths = []
    for chunk in chunks:
        tokens = _token_list(chunk["text"])
        lengths.append(len(tokens))
        for token in set(tokens):
            document_frequencies[token] = document_frequencies.get(token, 0) + 1
    return {
        "documents": max(len(chunks), 1),
        "avg_length": sum(lengths) / max(len(lengths), 1),
        "document_frequencies": document_frequencies,
    }


def _bm25_score(text: str, query: str, corpus_stats: dict[str, Any]) -> float:
    query_tokens = _expanded_token_list(query)
    if not query_tokens:
        return 0.0
    tokens = _expanded_token_list(text)
    if not tokens:
        return 0.0
    frequencies: dict[str, int] = {}
    for token in tokens:
        frequencies[token] = frequencies.get(token, 0) + 1

    k1 = 1.4
    b = 0.72
    score = 0.0
    length = len(tokens)
    avg_length = corpus_stats["avg_length"] or 1.0
    document_count = corpus_stats["documents"]
    for token in query_tokens:
        frequency = frequencies.get(token, 0)
        if frequency == 0:
            continue
        df = corpus_stats["document_frequencies"].get(token, 0)
        idf = math.log(1 + (document_count - df + 0.5) / (df + 0.5))
        denominator = frequency + k1 * (1 - b + b * length / avg_length)
        score += idf * (frequency * (k1 + 1)) / denominator
    return min(1.0, score / (score + 3.0))


def _rerank_score(chunk: dict[str, Any], query: str, filters: dict[str, Any], query_profile: dict[str, Any]) -> dict[str, float]:
    metadata = chunk["metadata"]
    query_tokens = set(query_profile["expanded_tokens"])
    text_tokens = _expanded_tokens(chunk["text"])
    lexical = len(query_tokens & text_tokens) / max(len(query_tokens), 1)
    facet = _filter_score(metadata, filters)
    field_match = _metadata_field_match_score(metadata, query_tokens)
    source_priority = _source_priority_score(metadata, filters)
    confidence = 1.0 if metadata.get("confidence") == "high" else 0.62 if metadata.get("confidence") == "medium" else 0.38
    rerank_score = min(1.0, lexical * 0.34 + facet * 0.30 + field_match * 0.18 + source_priority * 0.10 + confidence * 0.08)
    return {
        "rerank_score": rerank_score,
        "facet_score": facet,
        "field_match_score": field_match,
        "source_priority_score": source_priority,
    }


def _passes_filters(metadata: dict[str, Any], filters: dict[str, Any]) -> bool:
    species = _normalize(filters.get("species"))
    metadata_species = {_normalize(item) for item in metadata.get("species", [])}
    if species and metadata_species and species not in metadata_species:
        return False
    for filter_key, metadata_key in [("collection", "collection"), ("evidence_class", "evidence_class"), ("source", "source")]:
        value = filters.get(filter_key)
        if value and not _loose_contains(str(value), str(metadata.get(metadata_key) or "")):
            return False
    return True


def _filter_score(metadata: dict[str, Any], filters: dict[str, Any]) -> float:
    hits = 0
    checks = 0
    for filter_key, metadata_key in [
        ("brain_region", "regions"),
        ("cell_type", "cell_types"),
        ("modality", "modalities"),
    ]:
        value = str(filters.get(filter_key) or "").strip()
        if not value:
            continue
        checks += 1
        if any(_loose_contains(value, item) for item in metadata.get(metadata_key, [])):
            hits += 1
    if filters.get("transcript_selection") == "mane_select" and "MANE" in metadata.get("topics", []):
        hits += 1
        checks += 1
    return hits / max(checks, 1)


def _metadata_field_match_score(metadata: dict[str, Any], query_tokens: set[str]) -> float:
    fields = [
        metadata.get("title", ""),
        metadata.get("source", ""),
        metadata.get("evidence_class", ""),
        metadata.get("collection", ""),
        metadata.get("release", ""),
        " ".join(metadata.get("topics", [])),
        " ".join(metadata.get("regions", [])),
        " ".join(metadata.get("cell_types", [])),
        " ".join(metadata.get("modalities", [])),
    ]
    field_tokens = _expanded_tokens(" ".join(str(field) for field in fields if field))
    if not query_tokens:
        return 0.0
    return min(1.0, len(query_tokens & field_tokens) / max(len(query_tokens), 1) * 1.6)


def _source_priority_score(metadata: dict[str, Any], filters: dict[str, Any]) -> float:
    collection = metadata.get("collection")
    source = str(metadata.get("source") or "").lower()
    evidence_class = metadata.get("evidence_class")
    score = 0.0
    if metadata.get("confidence") == "high":
        score += 0.25
    if filters.get("transcript_selection") == "mane_select" and collection == "canonical_transcript":
        score += 0.35
    if filters.get("cell_type") and collection == "brain_cell_atlas":
        score += 0.28
    if filters.get("brain_region") and collection == "tissue_evidence":
        score += 0.20
    if filters.get("modality") and collection == "design_rules":
        score += 0.22
    if evidence_class in {"reference_database", "cell_type_prior", "tissue_prior", "design_rule"}:
        score += 0.12
    if any(label in source for label in ["mane", "gtex", "allen", "ensembl"]):
        score += 0.10
    return min(1.0, score)


def _query_profile(query: str, filters: dict[str, Any]) -> dict[str, Any]:
    raw_terms = " ".join(str(value) for value in [query, *filters.values()] if value)
    tokens = _token_list(raw_terms)
    expanded = _expanded_token_list(raw_terms)
    return {
        "tokens": sorted(set(tokens)),
        "expanded_tokens": sorted(set(expanded)),
        "aliases_added": sorted(set(expanded) - set(tokens)),
        "filters_requested": sorted(key for key, value in filters.items() if value),
    }


def _tokens(text: str) -> set[str]:
    return set(_token_list(text))


def _expanded_tokens(text: str) -> set[str]:
    return set(_expanded_token_list(text))


def _expanded_token_list(text: str) -> list[str]:
    tokens = _token_list(text)
    expanded: list[str] = []
    for token in tokens:
        expanded.append(token)
        expanded.extend(BIOMEDICAL_ALIASES.get(token, []))
    return expanded


def _token_list(text: str) -> list[str]:
    normalized = text.lower().replace("5′", "5prime").replace("5'", "5prime")
    return [token for token in re.split(r"[^a-zA-Z0-9]+", normalized) if len(token) >= 3]


def _normalize(value: Any) -> str:
    return str(value or "").lower().replace("_", " ").strip()


def _loose_contains(left: str, right: str) -> bool:
    left_norm = _normalize(left)
    right_norm = _normalize(right)
    left_tokens = _tokens(left_norm)
    right_tokens = _tokens(right_norm)
    return bool(left_tokens & right_tokens) or right_norm in left_norm or left_norm in right_norm
