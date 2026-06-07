from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO, StringIO
from typing import Any
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from app.services.export_manifest_service import ManifestedZip, verify_artifact_bundle
from app.services.rag_service import evaluate_rag_query, rag_search, rag_status
from app.services.structured_data_service import structured_manifest


REQUIRED_RAG_EVALUATION_FILES = {
    "bundle_manifest.json",
    "request.json",
    "evaluation.json",
    "retrieval_trace.json",
    "evidence_sufficiency.json",
    "score_breakdown.csv",
    "chunks.jsonl",
    "rag_status.json",
    "structured_manifest.json",
}


def build_rag_evaluation_bundle(request_payload: dict[str, Any]) -> bytes:
    query = str(request_payload.get("query") or "")
    filters = request_payload.get("filters") or {}
    limit = int(request_payload.get("limit") or 10)
    evaluation = evaluate_rag_query(query, filters, limit)
    search = rag_search(query, filters, limit)
    evaluation_json = _json(evaluation)
    score_breakdown_csv = _score_breakdown_csv(evaluation.get("score_breakdown") or [])
    chunks_jsonl = _chunks_jsonl(search.get("chunks") or [])
    metadata = {
        "bundle_schema": "agentic-rag-evaluation-bundle-v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "query_fingerprint": evaluation.get("query_fingerprint"),
        "retrieval_model": (evaluation.get("index") or {}).get("retrieval_model"),
        "ranking_policy": (evaluation.get("ranking_policy") or {}).get("version"),
        "result_count": evaluation.get("result_count"),
        "evaluation_hash": _hash_text(evaluation_json),
        "score_breakdown_hash": _hash_text(score_breakdown_csv),
        "chunks_hash": _hash_text(chunks_jsonl),
        "structured_manifest_hash": rag_status().get("structured_manifest_hash"),
    }

    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        bundle = ManifestedZip(archive, "rag_evaluation_bundle", metadata)
        bundle.writestr("bundle_manifest.json", _json(metadata))
        bundle.writestr("request.json", _json(_request_summary(request_payload)))
        bundle.writestr("evaluation.json", evaluation_json)
        bundle.writestr("retrieval_trace.json", _json(evaluation.get("retrieval_trace") or {}))
        bundle.writestr("evidence_sufficiency.json", _json(evaluation.get("evidence_sufficiency") or {}))
        bundle.writestr("score_breakdown.csv", score_breakdown_csv)
        bundle.writestr("chunks.jsonl", chunks_jsonl)
        bundle.writestr("rag_status.json", _json(rag_status()))
        bundle.writestr("structured_manifest.json", _json(structured_manifest()))
        bundle.write_artifact_manifest()
    return buffer.getvalue()


def verify_rag_evaluation_bundle(bundle: bytes) -> dict[str, Any]:
    base = verify_artifact_bundle(bundle)
    errors = list(base.get("errors") or [])
    warnings = list(base.get("warnings") or [])
    semantic_errors: list[str] = []
    semantic_warnings: list[str] = []
    semantic_checks: dict[str, str] = {}

    if base.get("artifact_type") != "rag_evaluation_bundle":
        semantic_errors.append("Artifact manifest artifact_type must be rag_evaluation_bundle.")
        semantic_checks["artifact_type"] = "fail"
    else:
        semantic_checks["artifact_type"] = "pass"

    try:
        with ZipFile(BytesIO(bundle), "r") as archive:
            names = set(archive.namelist())
            missing = sorted(REQUIRED_RAG_EVALUATION_FILES - names)
            if missing:
                semantic_errors.append(f"Required RAG evaluation files are missing: {', '.join(missing)}.")
                semantic_checks["required_files"] = "fail"
                payloads: dict[str, Any] = {}
                score_rows: list[dict[str, str]] = []
                chunk_rows: list[dict[str, Any]] = []
                evaluation_text = ""
                score_breakdown_text = ""
                chunks_text = ""
            else:
                semantic_checks["required_files"] = "pass"
                evaluation_text = archive.read("evaluation.json").decode("utf-8")
                score_breakdown_text = archive.read("score_breakdown.csv").decode("utf-8")
                chunks_text = archive.read("chunks.jsonl").decode("utf-8")
                payloads = {
                    "bundle_manifest": _read_json(archive, "bundle_manifest.json"),
                    "request": _read_json(archive, "request.json"),
                    "evaluation": _json_from_text(evaluation_text),
                    "retrieval_trace": _read_json(archive, "retrieval_trace.json"),
                    "evidence_sufficiency": _read_json(archive, "evidence_sufficiency.json"),
                    "rag_status": _read_json(archive, "rag_status.json"),
                    "structured_manifest": _read_json(archive, "structured_manifest.json"),
                }
                score_rows = list(csv.DictReader(StringIO(score_breakdown_text)))
                chunk_rows = _jsonl_from_text(chunks_text)
    except (BadZipFile, json.JSONDecodeError, UnicodeDecodeError, csv.Error) as exc:
        semantic_errors.append(f"Invalid RAG evaluation bundle: {exc}")
        payloads = {}
        score_rows = []
        chunk_rows = []
        evaluation_text = ""
        score_breakdown_text = ""
        chunks_text = ""

    manifest = payloads.get("bundle_manifest") or {}
    evaluation = payloads.get("evaluation") or {}
    trace = payloads.get("retrieval_trace") or {}
    sufficiency = payloads.get("evidence_sufficiency") or {}
    request = payloads.get("request") or {}
    rag = payloads.get("rag_status") or {}
    structured = payloads.get("structured_manifest") or {}
    metadata = base.get("bundle_metadata") or {}

    if manifest and manifest.get("bundle_schema") != "agentic-rag-evaluation-bundle-v1":
        semantic_errors.append("bundle_manifest.json bundle_schema is invalid.")
        semantic_checks["bundle_schema"] = "fail"
    elif manifest:
        semantic_checks["bundle_schema"] = "pass"

    expected_fingerprints = {
        str(value)
        for value in [
            manifest.get("query_fingerprint"),
            metadata.get("query_fingerprint"),
            evaluation.get("query_fingerprint"),
        ]
        if value
    }
    if len(expected_fingerprints) != 1:
        semantic_errors.append("Query fingerprints are missing or disagree across RAG bundle files.")
        semantic_checks["query_fingerprint"] = "fail"
    else:
        semantic_checks["query_fingerprint"] = "pass"
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "evaluation_hash",
        manifest.get("evaluation_hash"),
        _hash_text(evaluation_text),
        "bundle_manifest.json evaluation_hash does not match evaluation.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "score_breakdown_hash",
        manifest.get("score_breakdown_hash"),
        _hash_text(score_breakdown_text),
        "bundle_manifest.json score_breakdown_hash does not match score_breakdown.csv.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "chunks_hash",
        manifest.get("chunks_hash"),
        _hash_text(chunks_text),
        "bundle_manifest.json chunks_hash does not match chunks.jsonl.",
    )

    if trace.get("trace_schema") != "agentic-rag-retrieval-trace-v1":
        semantic_errors.append("retrieval_trace.json trace_schema is invalid.")
        semantic_checks["retrieval_trace_schema"] = "fail"
    else:
        semantic_checks["retrieval_trace_schema"] = "pass"
    if sufficiency.get("sufficiency_schema") != "agentic-rag-evidence-sufficiency-v1":
        semantic_errors.append("evidence_sufficiency.json sufficiency_schema is invalid.")
        semantic_checks["evidence_sufficiency_schema"] = "fail"
    elif (evaluation.get("evidence_sufficiency") or {}).get("status") != sufficiency.get("status"):
        semantic_errors.append("evidence_sufficiency.json status does not match evaluation.json.")
        semantic_checks["evidence_sufficiency_schema"] = "fail"
    else:
        semantic_checks["evidence_sufficiency_schema"] = "pass"
    facet_gap = evaluation.get("facet_gap_analysis") or {}
    if facet_gap.get("analysis_schema") != "agentic-rag-facet-gap-analysis-v1":
        semantic_errors.append("evaluation.json facet_gap_analysis schema is invalid.")
        semantic_checks["facet_gap_analysis"] = "fail"
    else:
        semantic_checks["facet_gap_analysis"] = "pass"
    term_coverage = evaluation.get("query_term_coverage") or {}
    if term_coverage.get("coverage_schema") != "agentic-rag-query-term-coverage-v1":
        semantic_errors.append("evaluation.json query_term_coverage schema is invalid.")
        semantic_checks["query_term_coverage"] = "fail"
    else:
        semantic_checks["query_term_coverage"] = "pass"

    result_count = int(evaluation.get("result_count") or 0)
    if result_count != len(score_rows) or result_count != len(chunk_rows):
        semantic_errors.append("result_count does not match score_breakdown.csv and chunks.jsonl row counts.")
        semantic_checks["result_count"] = "fail"
    else:
        semantic_checks["result_count"] = "pass"

    score_chunk_ids = {row.get("chunk_id") for row in score_rows if row.get("chunk_id")}
    chunk_ids = {row.get("chunk_id") for row in chunk_rows if row.get("chunk_id")}
    if score_chunk_ids != chunk_ids:
        semantic_errors.append("score_breakdown.csv chunk IDs do not match chunks.jsonl chunk IDs.")
        semantic_checks["chunk_id_consistency"] = "fail"
    elif score_chunk_ids:
        semantic_checks["chunk_id_consistency"] = "pass"
    else:
        semantic_warnings.append("RAG evaluation bundle contains no retrieved chunks.")
        semantic_checks["chunk_id_consistency"] = "warning"

    manifest_hashes = {
        str(value)
        for value in [
            manifest.get("structured_manifest_hash"),
            metadata.get("structured_manifest_hash"),
            rag.get("structured_manifest_hash"),
            structured.get("manifest_hash"),
        ]
        if value
    }
    if len(manifest_hashes) > 1:
        semantic_errors.append("Structured manifest hashes disagree across RAG bundle files.")
        semantic_checks["structured_manifest_hash"] = "fail"
    elif manifest_hashes:
        semantic_checks["structured_manifest_hash"] = "pass"
    else:
        semantic_warnings.append("Structured manifest hash is not recorded in the RAG bundle.")
        semantic_checks["structured_manifest_hash"] = "warning"

    if not request.get("query"):
        semantic_errors.append("request.json does not include a query.")
        semantic_checks["request_query"] = "fail"
    else:
        semantic_checks["request_query"] = "pass"

    semantic_status = "fail" if semantic_errors else "warning" if semantic_warnings else "pass"
    status = "fail" if errors or semantic_errors else "warning" if warnings or semantic_warnings else "pass"
    return {
        **base,
        "status": status,
        "errors": errors + semantic_errors,
        "warnings": warnings + semantic_warnings,
        "semantic_status": semantic_status,
        "semantic_errors": semantic_errors,
        "semantic_warnings": semantic_warnings,
        "semantic_checks": semantic_checks,
        "query_fingerprint": next(iter(expected_fingerprints), None),
        "evaluation_hash": manifest.get("evaluation_hash"),
        "score_breakdown_hash": manifest.get("score_breakdown_hash"),
        "chunks_hash": manifest.get("chunks_hash"),
        "result_count": result_count,
        "chunk_count": len(chunk_rows),
        "structured_manifest_hash": next(iter(manifest_hashes), None),
    }


def _request_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "query": payload.get("query"),
        "filters": payload.get("filters") or {},
        "limit": payload.get("limit"),
    }


def _score_breakdown_csv(rows: list[dict[str, Any]]) -> str:
    fields = [
        "rank",
        "chunk_id",
        "document_id",
        "title",
        "collection",
        "source",
        "score",
        "vector_score",
        "bm25_score",
        "rerank_score",
        "facet_score",
        "field_match_score",
        "source_priority_score",
        "matched_facets",
        "rationale",
    ]
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for rank, row in enumerate(rows, start=1):
        writer.writerow(
            {
                "rank": rank,
                "chunk_id": row.get("chunk_id"),
                "document_id": row.get("document_id"),
                "title": row.get("title"),
                "collection": row.get("collection"),
                "source": row.get("source"),
                "score": row.get("score"),
                "vector_score": row.get("vector_score"),
                "bm25_score": row.get("bm25_score"),
                "rerank_score": row.get("rerank_score"),
                "facet_score": row.get("facet_score"),
                "field_match_score": row.get("field_match_score"),
                "source_priority_score": row.get("source_priority_score"),
                "matched_facets": "; ".join(row.get("matched_facets") or []),
                "rationale": " | ".join(row.get("rationale") or []),
            }
        )
    return output.getvalue()


def _chunks_jsonl(chunks: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(_chunk_summary(chunk), ensure_ascii=False, sort_keys=True) for chunk in chunks) + ("\n" if chunks else "")


def _chunk_summary(chunk: dict[str, Any]) -> dict[str, Any]:
    metadata = chunk.get("metadata") or {}
    return {
        "chunk_id": chunk.get("chunk_id"),
        "document_id": chunk.get("document_id"),
        "score": chunk.get("score"),
        "text": chunk.get("text"),
        "metadata": metadata,
        "retrieval_scores": {
            "vector_score": chunk.get("vector_score"),
            "bm25_score": chunk.get("bm25_score"),
            "rerank_score": chunk.get("rerank_score"),
            "facet_score": chunk.get("facet_score"),
            "field_match_score": chunk.get("field_match_score"),
            "source_priority_score": chunk.get("source_priority_score"),
        },
    }


def _read_json(archive: ZipFile, name: str) -> dict[str, Any]:
    payload = json.loads(archive.read(name).decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def _read_jsonl(archive: ZipFile, name: str) -> list[dict[str, Any]]:
    return _jsonl_from_text(archive.read(name).decode("utf-8"))


def _json_from_text(text: str) -> dict[str, Any]:
    payload = json.loads(text)
    return payload if isinstance(payload, dict) else {}


def _jsonl_from_text(text: str) -> list[dict[str, Any]]:
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _hash_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _expect_equal(
    checks: dict[str, str],
    errors: list[str],
    name: str,
    actual: Any,
    expected: Any,
    message: str,
) -> None:
    passed = actual == expected and actual is not None
    checks[name] = "pass" if passed else "fail"
    if not passed:
        errors.append(message)
