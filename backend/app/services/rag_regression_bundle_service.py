from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO, StringIO
from typing import Any
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from app.services.export_manifest_service import ManifestedZip, verify_artifact_bundle
from app.services.rag_diagnostics_service import rag_diagnostics
from app.services.rag_regression_service import evaluate_rag_regression, rag_regression_cases
from app.services.rag_service import RANKING_POLICY, rag_status
from app.services.structured_data_service import structured_manifest


REQUIRED_RAG_REGRESSION_FILES = {
    "bundle_manifest.json",
    "regression.json",
    "cases.json",
    "case_metrics.csv",
    "weak_cases.json",
    "quality_summary.json",
    "source_provenance_summary.json",
    "diagnostics.json",
    "ranking_policy.json",
    "rag_status.json",
    "structured_manifest.json",
}

REQUIRED_CASE_METRIC_COLUMNS = {
    "case_id",
    "status",
    "result_count",
    "recall_at_k",
    "ndcg_at_k",
    "source_coverage",
    "collection_coverage",
    "missing_documents",
    "missing_collections",
    "missing_sources",
    "missing_terms",
    "top_document_id",
    "top_score",
    "errors",
    "warnings",
}


def build_rag_regression_bundle() -> bytes:
    regression = evaluate_rag_regression()
    cases = rag_regression_cases()
    diagnostics = rag_diagnostics()
    rag = rag_status()
    source_provenance = _source_provenance_summary(regression.get("results") or [])
    source_provenance_json = _json(source_provenance)
    case_metrics_csv = _case_metrics_csv(regression.get("results") or [])
    metadata = {
        "bundle_schema": "agentic-rag-regression-bundle-v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "status": regression.get("status"),
        "case_count": regression.get("case_count"),
        "cases_hash": regression.get("cases_hash"),
        "results_hash": regression.get("results_hash"),
        "quality_summary_hash": regression.get("quality_summary_hash"),
        "case_metrics_hash": _hash_text(case_metrics_csv),
        "quality_status": (regression.get("quality_summary") or {}).get("status"),
        "source_provenance_summary_hash": _hash_payload(source_provenance),
        "source_provenance_case_count": source_provenance.get("case_count"),
        "source_provenance_source_count": source_provenance.get("source_count"),
        "source_snapshot_case_count": source_provenance.get("source_snapshot_case_count"),
        "retrieval_model": regression.get("retrieval_model"),
        "structured_manifest_hash": rag.get("structured_manifest_hash"),
    }
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        bundle = ManifestedZip(archive, "rag_regression_bundle", metadata)
        bundle.writestr("bundle_manifest.json", _json(metadata))
        bundle.writestr("regression.json", _json(regression))
        bundle.writestr("cases.json", _json(cases))
        bundle.writestr("case_metrics.csv", case_metrics_csv)
        bundle.writestr("weak_cases.json", _json(_weak_cases(regression.get("results") or [])))
        bundle.writestr("quality_summary.json", _json(regression.get("quality_summary") or {}))
        bundle.writestr("source_provenance_summary.json", source_provenance_json)
        bundle.writestr("diagnostics.json", _json(diagnostics))
        bundle.writestr("ranking_policy.json", _json(RANKING_POLICY))
        bundle.writestr("rag_status.json", _json(rag))
        bundle.writestr("structured_manifest.json", _json(structured_manifest()))
        bundle.write_artifact_manifest()
    return buffer.getvalue()


def verify_rag_regression_bundle(bundle: bytes) -> dict[str, Any]:
    base = verify_artifact_bundle(bundle)
    errors = list(base.get("errors") or [])
    warnings = list(base.get("warnings") or [])
    semantic_errors: list[str] = []
    semantic_warnings: list[str] = []
    semantic_checks: dict[str, str] = {}

    if base.get("artifact_type") != "rag_regression_bundle":
        semantic_errors.append("Artifact manifest artifact_type must be rag_regression_bundle.")
        semantic_checks["artifact_type"] = "fail"
    else:
        semantic_checks["artifact_type"] = "pass"

    try:
        with ZipFile(BytesIO(bundle), "r") as archive:
            names = set(archive.namelist())
            missing = sorted(REQUIRED_RAG_REGRESSION_FILES - names)
            if missing:
                semantic_errors.append(f"Required RAG regression bundle files are missing: {', '.join(missing)}.")
                semantic_checks["required_files"] = "fail"
                payloads: dict[str, Any] = {}
                metric_rows: list[dict[str, str]] = []
            else:
                semantic_checks["required_files"] = "pass"
                payloads = {
                    "bundle_manifest": _read_json(archive, "bundle_manifest.json"),
                    "regression": _read_json(archive, "regression.json"),
                    "cases": _read_json(archive, "cases.json"),
                    "weak_cases": _read_json(archive, "weak_cases.json"),
                    "quality_summary": _read_json(archive, "quality_summary.json"),
                    "source_provenance_summary": _read_json(archive, "source_provenance_summary.json"),
                    "diagnostics": _read_json(archive, "diagnostics.json"),
                    "ranking_policy": _read_json(archive, "ranking_policy.json"),
                    "rag_status": _read_json(archive, "rag_status.json"),
                    "structured_manifest": _read_json(archive, "structured_manifest.json"),
                }
                case_metrics_text = archive.read("case_metrics.csv").decode("utf-8")
                metric_reader = csv.DictReader(StringIO(case_metrics_text))
                metric_rows = list(metric_reader)
                metric_columns = set(metric_reader.fieldnames or [])
    except (BadZipFile, json.JSONDecodeError, UnicodeDecodeError, csv.Error) as exc:
        semantic_errors.append(f"Invalid RAG regression bundle: {exc}")
        payloads = {}
        metric_rows = []
        metric_columns = set()
        case_metrics_text = ""

    manifest = payloads.get("bundle_manifest") or {}
    regression = payloads.get("regression") or {}
    cases = payloads.get("cases") or {}
    diagnostics = payloads.get("diagnostics") or {}
    ranking_policy = payloads.get("ranking_policy") or {}
    rag = payloads.get("rag_status") or {}
    structured = payloads.get("structured_manifest") or {}
    weak_cases = payloads.get("weak_cases") or {}
    quality_summary = payloads.get("quality_summary") or {}
    source_provenance = payloads.get("source_provenance_summary") or {}
    metadata = base.get("bundle_metadata") or {}
    case_metrics_text = locals().get("case_metrics_text", "")
    metric_columns = locals().get("metric_columns", set())

    _expect_equal(
        semantic_checks,
        semantic_errors,
        "bundle_schema",
        manifest.get("bundle_schema"),
        "agentic-rag-regression-bundle-v1",
        "bundle_manifest.json bundle_schema is not recognized.",
    )
    case_count = int(regression.get("case_count") or 0)
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "case_count",
        case_count,
        int(cases.get("case_count") or -1),
        "regression.json case_count does not match cases.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "case_metrics_rows",
        len(metric_rows),
        case_count,
        "case_metrics.csv row count does not match regression.json case_count.",
    )
    missing_metric_columns = sorted(REQUIRED_CASE_METRIC_COLUMNS - set(metric_columns))
    if missing_metric_columns:
        semantic_errors.append(f"case_metrics.csv is missing required columns: {', '.join(missing_metric_columns)}.")
        semantic_checks["case_metric_columns"] = "fail"
    else:
        semantic_checks["case_metric_columns"] = "pass"
    case_metric_hashes = {
        str(value)
        for value in [manifest.get("case_metrics_hash"), metadata.get("case_metrics_hash"), _hash_text(case_metrics_text)]
        if value
    }
    if len(case_metric_hashes) != 1:
        semantic_errors.append("RAG regression case_metrics_hash values are missing or disagree.")
        semantic_checks["case_metrics_hash"] = "fail"
    else:
        semantic_checks["case_metrics_hash"] = "pass"

    case_hashes = {
        str(value)
        for value in [manifest.get("cases_hash"), metadata.get("cases_hash"), regression.get("cases_hash"), cases.get("cases_hash")]
        if value
    }
    if len(case_hashes) != 1:
        semantic_errors.append("RAG regression case hashes are missing or disagree.")
        semantic_checks["cases_hash"] = "fail"
    else:
        semantic_checks["cases_hash"] = "pass"

    result_hashes = {
        str(value)
        for value in [manifest.get("results_hash"), metadata.get("results_hash"), regression.get("results_hash")]
        if value
    }
    if len(result_hashes) != 1:
        semantic_errors.append("RAG regression result hashes are missing or disagree.")
        semantic_checks["results_hash"] = "fail"
    else:
        semantic_checks["results_hash"] = "pass"

    quality_hashes = {
        str(value)
        for value in [manifest.get("quality_summary_hash"), metadata.get("quality_summary_hash"), regression.get("quality_summary_hash")]
        if value
    }
    if len(quality_hashes) != 1:
        semantic_errors.append("RAG regression quality summary hashes are missing or disagree.")
        semantic_checks["quality_summary_hash"] = "fail"
    elif next(iter(quality_hashes)) != _hash_payload(quality_summary):
        semantic_errors.append("RAG regression quality_summary_hash does not match quality_summary.json.")
        semantic_checks["quality_summary_hash"] = "fail"
    else:
        semantic_checks["quality_summary_hash"] = "pass"

    if quality_summary.get("quality_summary_schema") != "agentic-rag-regression-quality-summary-v1":
        semantic_errors.append("quality_summary.json schema is invalid.")
        semantic_checks["quality_summary_schema"] = "fail"
    else:
        semantic_checks["quality_summary_schema"] = "pass"
    if quality_summary.get("case_count") != case_count:
        semantic_errors.append("quality_summary.json case_count does not match regression.json.")
        semantic_checks["quality_summary_case_count"] = "fail"
    else:
        semantic_checks["quality_summary_case_count"] = "pass"
    if quality_summary.get("status") not in {"pass", "warning", "fail"}:
        semantic_errors.append("quality_summary.json status is invalid.")
        semantic_checks["quality_summary_status"] = "fail"
    else:
        semantic_checks["quality_summary_status"] = "pass"

    expected_source_provenance = _source_provenance_summary(regression.get("results") or [])
    if source_provenance.get("provenance_schema") != "agentic-rag-regression-source-provenance-summary-v1":
        semantic_errors.append("source_provenance_summary.json schema is invalid.")
        semantic_checks["source_provenance_summary_schema"] = "fail"
    elif source_provenance != expected_source_provenance:
        semantic_errors.append("source_provenance_summary.json does not match regression.json top result metadata.")
        semantic_checks["source_provenance_summary_consistency"] = "fail"
    else:
        semantic_checks["source_provenance_summary_schema"] = "pass"
        semantic_checks["source_provenance_summary_consistency"] = "pass"
    source_provenance_hashes = {
        str(value)
        for value in [
            manifest.get("source_provenance_summary_hash"),
            metadata.get("source_provenance_summary_hash"),
            _hash_payload(source_provenance),
        ]
        if value
    }
    if len(source_provenance_hashes) != 1:
        semantic_errors.append("RAG regression source provenance summary hashes are missing or disagree.")
        semantic_checks["source_provenance_summary_hash"] = "fail"
    else:
        semantic_checks["source_provenance_summary_hash"] = "pass"
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "source_provenance_case_count",
        manifest.get("source_provenance_case_count"),
        source_provenance.get("case_count"),
        "bundle_manifest.json source_provenance_case_count does not match source_provenance_summary.json.",
    )

    _expect_equal(
        semantic_checks,
        semantic_errors,
        "ranking_policy",
        ranking_policy.get("version"),
        ((regression.get("rag") or {}).get("retrieval_model") and "hybrid-score-policy-v2"),
        "ranking_policy.json version is not recognized.",
    )
    if diagnostics.get("diagnostics_schema") != "agentic-rag-diagnostics-v1":
        semantic_errors.append("diagnostics.json diagnostics_schema is invalid.")
        semantic_checks["diagnostics_schema"] = "fail"
    else:
        semantic_checks["diagnostics_schema"] = "pass"

    if any(row.get("status") not in {"pass", "warning", "fail"} for row in metric_rows):
        semantic_errors.append("case_metrics.csv contains invalid status values.")
        semantic_checks["case_metric_status"] = "fail"
    elif metric_rows:
        semantic_checks["case_metric_status"] = "pass"
    else:
        semantic_warnings.append("RAG regression bundle contains no case metric rows.")
        semantic_checks["case_metric_status"] = "warning"

    expected_weak = sum(1 for result in regression.get("results") or [] if result.get("status") != "pass")
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "weak_case_count",
        weak_cases.get("weak_case_count"),
        expected_weak,
        "weak_cases.json weak_case_count does not match regression results.",
    )

    manifest_hashes = {
        str(value)
        for value in [manifest.get("structured_manifest_hash"), metadata.get("structured_manifest_hash"), rag.get("structured_manifest_hash"), structured.get("manifest_hash")]
        if value
    }
    if len(manifest_hashes) > 1:
        semantic_errors.append("Structured manifest hashes disagree across RAG regression bundle files.")
        semantic_checks["structured_manifest_hash"] = "fail"
    elif manifest_hashes:
        semantic_checks["structured_manifest_hash"] = "pass"
    else:
        semantic_warnings.append("Structured manifest hash is not recorded in the RAG regression bundle.")
        semantic_checks["structured_manifest_hash"] = "warning"

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
        "regression_status": regression.get("status"),
        "case_count": case_count,
        "cases_hash": next(iter(case_hashes), None),
        "results_hash": next(iter(result_hashes), None),
        "quality_summary_hash": next(iter(quality_hashes), None),
        "case_metrics_hash": next(iter(case_metric_hashes), None),
        "quality_status": quality_summary.get("status"),
        "source_provenance_summary_hash": next(iter(source_provenance_hashes), None),
        "source_provenance_case_count": source_provenance.get("case_count"),
        "source_provenance_source_count": source_provenance.get("source_count"),
        "source_snapshot_case_count": source_provenance.get("source_snapshot_case_count"),
        "top_source_count": quality_summary.get("top_source_count"),
        "missing_term_case_count": quality_summary.get("missing_term_case_count"),
        "weak_case_count": weak_cases.get("weak_case_count"),
        "structured_manifest_hash": next(iter(manifest_hashes), None),
    }


def _case_metrics_csv(results: list[dict[str, Any]]) -> str:
    fields = [
        "case_id",
        "status",
        "result_count",
        "recall_at_k",
        "ndcg_at_k",
        "source_coverage",
        "collection_coverage",
        "missing_documents",
        "missing_collections",
        "missing_sources",
        "missing_terms",
        "top_document_id",
        "top_score",
        "errors",
        "warnings",
    ]
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for result in results:
        top = (result.get("top_results") or [{}])[0]
        missing = result.get("missing") or {}
        writer.writerow(
            {
                "case_id": result.get("case_id"),
                "status": result.get("status"),
                "result_count": result.get("result_count"),
                "recall_at_k": result.get("recall_at_k"),
                "ndcg_at_k": result.get("ndcg_at_k"),
                "source_coverage": result.get("source_coverage"),
                "collection_coverage": result.get("collection_coverage"),
                "missing_documents": " | ".join(missing.get("documents") or []),
                "missing_collections": " | ".join(missing.get("collections") or []),
                "missing_sources": " | ".join(missing.get("sources") or []),
                "missing_terms": " | ".join(missing.get("terms") or []),
                "top_document_id": top.get("document_id"),
                "top_score": top.get("score"),
                "errors": " | ".join(result.get("errors") or []),
                "warnings": " | ".join(result.get("warnings") or []),
            }
        )
    return output.getvalue()


def _weak_cases(results: list[dict[str, Any]]) -> dict[str, Any]:
    weak = [result for result in results if result.get("status") != "pass"]
    return {
        "weak_case_schema": "agentic-rag-regression-weak-cases-v1",
        "weak_case_count": len(weak),
        "cases": [
            {
                "case_id": result.get("case_id"),
                "status": result.get("status"),
                "errors": result.get("errors") or [],
                "warnings": result.get("warnings") or [],
                "missing": result.get("missing") or {},
                "top_results": (result.get("top_results") or [])[:3],
            }
            for result in weak
        ],
    }


def _source_provenance_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    cases = []
    all_sources: set[str] = set()
    cases_with_hashes = 0
    cases_with_snapshots = 0
    cases_with_urls = 0
    for result in results:
        rows = []
        for item in result.get("top_results") or []:
            source = str(item.get("source") or "unknown")
            all_sources.add(source)
            source_hashes = sorted(
                {
                    str(value)
                    for value in [item.get("source_sha256"), item.get("source_payload_sha256")]
                    if value
                }
            )
            rows.append(
                {
                    "rank": item.get("rank"),
                    "document_id": item.get("document_id"),
                    "chunk_id": item.get("chunk_id"),
                    "collection": item.get("collection"),
                    "source": source,
                    "source_url": item.get("source_url") or "",
                    "source_path": item.get("source_path") or "",
                    "source_hashes": source_hashes,
                    "source_snapshot_path": item.get("source_snapshot_path") or "",
                    "has_source_url": bool(item.get("source_url")),
                    "has_source_hash": bool(source_hashes),
                    "has_source_snapshot": bool(item.get("source_snapshot_path")),
                }
            )
        if any(row["has_source_hash"] for row in rows):
            cases_with_hashes += 1
        if any(row["has_source_snapshot"] for row in rows):
            cases_with_snapshots += 1
        if any(row["has_source_url"] for row in rows):
            cases_with_urls += 1
        cases.append(
            {
                "case_id": result.get("case_id"),
                "status": result.get("status"),
                "result_count": result.get("result_count"),
                "source_count": len({row["source"] for row in rows}),
                "source_hash_count": sum(1 for row in rows if row["has_source_hash"]),
                "source_snapshot_count": sum(1 for row in rows if row["has_source_snapshot"]),
                "source_url_count": sum(1 for row in rows if row["has_source_url"]),
                "top_results": rows,
            }
        )
    return {
        "provenance_schema": "agentic-rag-regression-source-provenance-summary-v1",
        "case_count": len(cases),
        "source_count": len(all_sources),
        "source_hash_case_count": cases_with_hashes,
        "source_snapshot_case_count": cases_with_snapshots,
        "source_url_case_count": cases_with_urls,
        "cases": cases,
    }


def _expect_equal(checks: dict[str, str], errors: list[str], name: str, actual: Any, expected: Any, message: str) -> None:
    if actual == expected and actual is not None:
        checks[name] = "pass"
    else:
        checks[name] = "fail"
        errors.append(message)


def _read_json(archive: ZipFile, name: str) -> dict[str, Any]:
    payload = json.loads(archive.read(name).decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _hash_payload(payload: Any) -> str:
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _hash_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()
