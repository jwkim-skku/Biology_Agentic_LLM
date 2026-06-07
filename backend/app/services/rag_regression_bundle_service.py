from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
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
    "diagnostics.json",
    "ranking_policy.json",
    "rag_status.json",
    "structured_manifest.json",
}


def build_rag_regression_bundle() -> bytes:
    regression = evaluate_rag_regression()
    cases = rag_regression_cases()
    diagnostics = rag_diagnostics()
    rag = rag_status()
    metadata = {
        "bundle_schema": "agentic-rag-regression-bundle-v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "status": regression.get("status"),
        "case_count": regression.get("case_count"),
        "cases_hash": regression.get("cases_hash"),
        "retrieval_model": regression.get("retrieval_model"),
        "structured_manifest_hash": rag.get("structured_manifest_hash"),
    }
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        bundle = ManifestedZip(archive, "rag_regression_bundle", metadata)
        bundle.writestr("bundle_manifest.json", _json(metadata))
        bundle.writestr("regression.json", _json(regression))
        bundle.writestr("cases.json", _json(cases))
        bundle.writestr("case_metrics.csv", _case_metrics_csv(regression.get("results") or []))
        bundle.writestr("weak_cases.json", _json(_weak_cases(regression.get("results") or [])))
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
                    "diagnostics": _read_json(archive, "diagnostics.json"),
                    "ranking_policy": _read_json(archive, "ranking_policy.json"),
                    "rag_status": _read_json(archive, "rag_status.json"),
                    "structured_manifest": _read_json(archive, "structured_manifest.json"),
                }
                metric_rows = list(csv.DictReader(StringIO(archive.read("case_metrics.csv").decode("utf-8"))))
    except (BadZipFile, json.JSONDecodeError, UnicodeDecodeError, csv.Error) as exc:
        semantic_errors.append(f"Invalid RAG regression bundle: {exc}")
        payloads = {}
        metric_rows = []

    manifest = payloads.get("bundle_manifest") or {}
    regression = payloads.get("regression") or {}
    cases = payloads.get("cases") or {}
    diagnostics = payloads.get("diagnostics") or {}
    ranking_policy = payloads.get("ranking_policy") or {}
    rag = payloads.get("rag_status") or {}
    structured = payloads.get("structured_manifest") or {}
    weak_cases = payloads.get("weak_cases") or {}
    metadata = base.get("bundle_metadata") or {}

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
