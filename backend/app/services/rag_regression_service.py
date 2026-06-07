from __future__ import annotations

import json
import math
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.services.rag_service import RETRIEVAL_MODEL, rag_search, rag_status


RAG_REGRESSION_CASES_PATH = get_settings().data_dir / "rag_regression_cases.json"


def rag_regression_cases() -> dict[str, Any]:
    cases = _load_cases()
    return {
        "cases_path": str(RAG_REGRESSION_CASES_PATH),
        "case_count": len(cases),
        "cases_hash": _hash_payload(cases),
        "cases": cases,
    }


def evaluate_rag_regression() -> dict[str, Any]:
    cases = _load_cases()
    results = [_evaluate_case(case) for case in cases]
    failed = [result for result in results if result["status"] == "fail"]
    warnings = [result for result in results if result["status"] == "warning"]
    macro = _macro_metrics(results)
    return {
        "status": "fail" if failed else "warning" if warnings else "pass",
        "retrieval_model": RETRIEVAL_MODEL,
        "rag": rag_status(),
        "case_count": len(results),
        "pass_count": sum(1 for result in results if result["status"] == "pass"),
        "warning_count": len(warnings),
        "fail_count": len(failed),
        "cases_hash": _hash_payload(cases),
        "macro": macro,
        "results": results,
    }


def _evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    limit = int(case.get("limit") or 8)
    search = rag_search(str(case["query"]), case.get("filters") or {}, limit)
    chunks = search["chunks"]
    retrieved_document_ids = [chunk["document_id"] for chunk in chunks]
    retrieved_collections = sorted({chunk["metadata"].get("collection") for chunk in chunks if chunk["metadata"].get("collection")})
    retrieved_sources = sorted({chunk["metadata"].get("source") for chunk in chunks if chunk["metadata"].get("source")})
    required_document_ids = [str(item) for item in case.get("required_document_ids", [])]
    required_collections = [str(item) for item in case.get("required_collections", [])]
    required_sources = [str(item) for item in case.get("required_sources", [])]
    required_terms = [str(item) for item in case.get("required_terms", [])]
    missing_documents = [item for item in required_document_ids if item not in retrieved_document_ids]
    missing_collections = [item for item in required_collections if item not in retrieved_collections]
    missing_sources = [item for item in required_sources if not any(_loose_equal(item, source) for source in retrieved_sources)]
    missing_terms = _missing_terms(chunks, required_terms)
    recall_at_k = _recall(required_document_ids, retrieved_document_ids)
    ndcg_at_k = _ndcg(chunks, case)
    source_coverage = _coverage(required_sources, retrieved_sources, loose=True)
    collection_coverage = _coverage(required_collections, retrieved_collections)
    errors = []
    warnings = []
    if missing_documents:
        errors.append(f"Missing required documents: {', '.join(missing_documents)}.")
    if missing_collections:
        errors.append(f"Missing required collections: {', '.join(missing_collections)}.")
    if missing_sources:
        errors.append(f"Missing required sources: {', '.join(missing_sources)}.")
    if missing_terms:
        warnings.append(f"Missing required terms in retrieved text/metadata: {', '.join(missing_terms)}.")
    if len(chunks) < max(1, int(case.get("min_results") or 1)):
        errors.append("Retrieved result count is below min_results.")
    return {
        "case_id": case["case_id"],
        "status": "fail" if errors else "warning" if warnings else "pass",
        "query": case["query"],
        "filters": case.get("filters") or {},
        "limit": limit,
        "result_count": len(chunks),
        "recall_at_k": recall_at_k,
        "ndcg_at_k": ndcg_at_k,
        "source_coverage": source_coverage,
        "collection_coverage": collection_coverage,
        "errors": errors,
        "warnings": warnings,
        "missing": {
            "documents": missing_documents,
            "collections": missing_collections,
            "sources": missing_sources,
            "terms": missing_terms,
        },
        "top_results": [
            {
                "rank": idx + 1,
                "document_id": chunk["document_id"],
                "chunk_id": chunk["chunk_id"],
                "collection": chunk["metadata"].get("collection"),
                "source": chunk["metadata"].get("source"),
                "score": chunk["score"],
                "relevance": _relevance(chunk, case),
            }
            for idx, chunk in enumerate(chunks[:limit])
        ],
    }


def _load_cases() -> list[dict[str, Any]]:
    if not RAG_REGRESSION_CASES_PATH.exists():
        return []
    payload = json.loads(RAG_REGRESSION_CASES_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("RAG regression cases must be a JSON list.")
    return [case for case in payload if isinstance(case, dict) and case.get("case_id") and case.get("query")]


def _macro_metrics(results: list[dict[str, Any]]) -> dict[str, float]:
    if not results:
        return {"recall_at_k": 0.0, "ndcg_at_k": 0.0, "source_coverage": 0.0, "collection_coverage": 0.0}
    return {
        "recall_at_k": round(sum(result["recall_at_k"] for result in results) / len(results), 4),
        "ndcg_at_k": round(sum(result["ndcg_at_k"] for result in results) / len(results), 4),
        "source_coverage": round(sum(result["source_coverage"] for result in results) / len(results), 4),
        "collection_coverage": round(sum(result["collection_coverage"] for result in results) / len(results), 4),
    }


def _recall(required: list[str], retrieved: list[str]) -> float:
    if not required:
        return 1.0
    return round(len(set(required) & set(retrieved)) / len(set(required)), 4)


def _coverage(required: list[str], retrieved: list[str], *, loose: bool = False) -> float:
    if not required:
        return 1.0
    if loose:
        hits = sum(1 for item in required if any(_loose_equal(item, candidate) for candidate in retrieved))
    else:
        hits = len(set(required) & set(retrieved))
    return round(hits / len(set(required)), 4)


def _ndcg(chunks: list[dict[str, Any]], case: dict[str, Any]) -> float:
    gains = [_relevance(chunk, case) for chunk in chunks]
    dcg = sum((2**gain - 1) / math.log2(rank + 2) for rank, gain in enumerate(gains))
    ideal = sorted(gains, reverse=True)
    idcg = sum((2**gain - 1) / math.log2(rank + 2) for rank, gain in enumerate(ideal)) or 1.0
    return round(dcg / idcg, 4)


def _relevance(chunk: dict[str, Any], case: dict[str, Any]) -> int:
    metadata = chunk["metadata"]
    score = 0
    if chunk["document_id"] in case.get("required_document_ids", []):
        score += 4
    if metadata.get("collection") in case.get("required_collections", []):
        score += 2
    if any(_loose_equal(str(source), str(metadata.get("source") or "")) for source in case.get("required_sources", [])):
        score += 2
    text = _chunk_text(chunk)
    score += sum(1 for term in case.get("required_terms", []) if _contains_term(text, str(term)))
    return score


def _missing_terms(chunks: list[dict[str, Any]], terms: list[str]) -> list[str]:
    text = " ".join(_chunk_text(chunk) for chunk in chunks)
    return [term for term in terms if not _contains_term(text, term)]


def _chunk_text(chunk: dict[str, Any]) -> str:
    metadata = chunk.get("metadata") or {}
    return " ".join(
        str(value)
        for value in [
            chunk.get("text"),
            metadata.get("title"),
            metadata.get("collection"),
            metadata.get("source"),
            metadata.get("summary"),
            metadata.get("release"),
        ]
        if value
    )


def _contains_term(text: str, term: str) -> bool:
    return _normalize(term) in _normalize(text)


def _loose_equal(left: str, right: str) -> bool:
    left_norm = _normalize(left)
    right_norm = _normalize(right)
    return left_norm == right_norm or left_norm in right_norm or right_norm in left_norm


def _normalize(value: str) -> str:
    return value.lower().replace("_", " ").replace("-", " ").strip()


def _hash_payload(payload: Any) -> str:
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
