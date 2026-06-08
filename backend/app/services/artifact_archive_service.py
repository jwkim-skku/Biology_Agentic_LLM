from __future__ import annotations

import json
import os
import sqlite3
import stat
import uuid
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.services.export_manifest_service import verify_artifact_bundle
from app.services.data_snapshot_service import verify_data_snapshot_bundle
from app.services.data_release_bundle_service import verify_data_release_bundle
from app.services.data_refresh_plan_bundle_service import verify_data_refresh_plan_bundle
from app.services.optimizer_benchmark_bundle_service import verify_optimizer_benchmark_bundle
from app.services.qc_report_bundle_service import verify_qc_report_bundle
from app.services.rag_evaluation_bundle_service import verify_rag_evaluation_bundle
from app.services.rag_regression_bundle_service import verify_rag_regression_bundle
from app.services.rag_vector_index_bundle_service import verify_rag_vector_index_bundle
from app.services.structured_import_audit_service import verify_structured_import_audit_bundle
from app.services.workflow_trace_bundle_service import verify_workflow_trace_bundle


DATA_DIR = get_settings().data_dir
ARCHIVE_DB_PATH = DATA_DIR / "runtime" / "artifact_archive.sqlite3"
ARCHIVE_DIR = DATA_DIR / "runtime" / "artifact_archive"
ARCHIVE_LEDGER_PATH = DATA_DIR / "runtime" / "artifact_archive_ledger.jsonl"
ARCHIVE_SEMANTIC_FRESHNESS_WARNING_HOURS = 24 * 30


def archive_artifact_bundle(
    content: bytes,
    *,
    action: str,
    resource_type: str,
    resource_id: str,
    filename: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ensure_schema()
    digest = sha256(content).hexdigest()
    duplicate = _get_by_sha256(digest)
    if duplicate:
        return {**duplicate, "archive_status": "existing"}

    verification = _verify_export_bundle(content)
    enriched_metadata = _archive_metadata(metadata, verification)
    artifact_id = f"artifact_{uuid.uuid4().hex[:16]}"
    storage_path = ARCHIVE_DIR / f"{artifact_id}.zip"
    created_at = _utc_now()
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    _write_immutable(storage_path, content)

    row = {
        "artifact_id": artifact_id,
        "created_at": created_at,
        "artifact_type": verification.get("artifact_type"),
        "resource_type": resource_type,
        "resource_id": resource_id,
        "action": action,
        "filename": filename,
        "media_type": "application/zip",
        "bytes": len(content),
        "sha256": digest,
        "manifest_hash": verification.get("manifest_hash"),
        "verification_status": verification.get("status"),
        "storage_path": str(storage_path),
        "metadata": enriched_metadata,
        "archive_status": "stored",
    }
    with _connect() as conn:
        conn.execute(
            """
            insert into archived_artifacts (
                artifact_id, created_at, artifact_type, resource_type, resource_id,
                action, filename, media_type, bytes, sha256, manifest_hash,
                verification_status, storage_path, metadata_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["artifact_id"],
                row["created_at"],
                row["artifact_type"],
                row["resource_type"],
                row["resource_id"],
                row["action"],
                row["filename"],
                row["media_type"],
                row["bytes"],
                row["sha256"],
                row["manifest_hash"],
                row["verification_status"],
                row["storage_path"],
                _json_dumps(row["metadata"]),
            ),
        )
    _append_ledger_entry(row)
    return row


def list_archived_artifacts(
    *,
    limit: int = 50,
    resource_type: str | None = None,
    resource_id: str | None = None,
    artifact_type: str | None = None,
) -> dict[str, Any]:
    _ensure_schema()
    clauses: list[str] = []
    params: list[Any] = []
    if resource_type:
        clauses.append("resource_type = ?")
        params.append(resource_type)
    if resource_id:
        clauses.append("resource_id = ?")
        params.append(resource_id)
    if artifact_type:
        clauses.append("artifact_type = ?")
        params.append(artifact_type)
    where_sql = f"where {' and '.join(clauses)}" if clauses else ""
    params.append(max(1, min(limit, 500)))
    with _connect() as conn:
        rows = conn.execute(
            f"""
            select *
            from archived_artifacts
            {where_sql}
            order by created_at desc
            limit ?
            """,
            params,
        ).fetchall()
    return {
        "artifacts": [_row_to_archive(row) for row in rows],
        "limit": params[-1],
        "filters": {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "artifact_type": artifact_type,
        },
        "store_path": str(ARCHIVE_DB_PATH),
        "archive_dir": str(ARCHIVE_DIR),
    }


def get_archived_artifact(artifact_id: str) -> dict[str, Any] | None:
    _ensure_schema()
    with _connect() as conn:
        row = conn.execute("select * from archived_artifacts where artifact_id = ?", (artifact_id,)).fetchone()
    return _row_to_archive(row) if row else None


def update_archived_artifact_metadata(artifact_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
    _ensure_schema()
    with _connect() as conn:
        conn.execute(
            "update archived_artifacts set metadata_json = ? where artifact_id = ?",
            (_json_dumps(metadata), artifact_id),
        )
    artifact = get_archived_artifact(artifact_id)
    if not artifact:
        raise FileNotFoundError(artifact_id)
    return artifact


def read_archived_artifact(artifact_id: str) -> bytes:
    artifact = get_archived_artifact(artifact_id)
    if not artifact:
        raise FileNotFoundError(artifact_id)
    path = Path(artifact["storage_path"])
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(str(path))
    return path.read_bytes()


def verify_archived_artifact(artifact_id: str) -> dict[str, Any]:
    artifact = get_archived_artifact(artifact_id)
    if not artifact:
        raise FileNotFoundError(artifact_id)
    content = read_archived_artifact(artifact_id)
    digest = sha256(content).hexdigest()
    verification = _verify_export_bundle(content)
    errors = list(verification.get("errors") or [])
    if digest != artifact["sha256"]:
        errors.append("Archived file sha256 does not match archive index.")
    if verification.get("manifest_hash") != artifact.get("manifest_hash"):
        errors.append("Archived artifact manifest_hash does not match archive index.")
    status = "fail" if errors else verification.get("status", "unknown")
    return {
        "artifact": artifact,
        "status": status,
        "errors": errors,
        "warnings": verification.get("warnings") or [],
        "sha256": digest,
        "manifest_hash": verification.get("manifest_hash"),
        "bundle_verification": verification,
    }


def qc_bundle_archive_semantic_summary(limit: int = 20, *, verify_files: bool = True) -> dict[str, Any]:
    sample_limit = max(1, min(limit, 100))
    artifacts = list_archived_artifacts(limit=sample_limit, artifact_type="qc_report_bundle")["artifacts"]
    checked: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    for artifact in artifacts:
        artifact_id = artifact["artifact_id"]
        if not verify_files:
            item = _semantic_summary_item_from_metadata(artifact)
            if item:
                checked.append(item)
                _collect_semantic_summary_message(item, warnings)
                continue
            warnings.append(f"{artifact_id}: QC semantic metadata is not indexed; run deep verification before reuse.")
            checked.append(_legacy_semantic_summary_item(artifact))
            continue

        try:
            verification = verify_archived_artifact(artifact_id)
        except FileNotFoundError as exc:
            errors.append(f"{artifact_id}: archived file missing ({exc}).")
            checked.append(_missing_semantic_summary_item(artifact_id))
            continue
        item = _semantic_summary_item_from_verification(artifact, verification)
        checked.append(item)
        _collect_semantic_summary_message(item, warnings)

    if not artifacts:
        warnings.append("No archived QC report bundles were available for semantic verification.")

    semantic_pass_count = sum(1 for item in checked if item.get("semantic_status") == "pass")
    freshness = _semantic_summary_freshness(checked, label="QC report bundle", warnings=warnings)
    return {
        "status": "fail" if errors else "warning" if warnings else "pass",
        "errors": errors,
        "warnings": warnings,
        "verification_mode": "deep" if verify_files else "indexed",
        "sample_limit": sample_limit,
        "checked_count": len(checked),
        "semantic_pass_count": semantic_pass_count,
        "semantic_warning_count": sum(1 for item in checked if item.get("semantic_status") == "warning"),
        "semantic_fail_count": sum(1 for item in checked if item.get("semantic_status") == "fail"),
        "latest_artifacts": checked,
        **freshness,
    }


def structured_import_archive_summary(limit: int = 20, *, verify_files: bool = True) -> dict[str, Any]:
    sample_limit = max(1, min(limit, 100))
    artifacts = list_archived_artifacts(limit=sample_limit, artifact_type="structured_import_audit_bundle")["artifacts"]
    checked: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    for artifact in artifacts:
        artifact_id = artifact["artifact_id"]
        if not verify_files:
            item = _structured_import_summary_item_from_metadata(artifact)
            if item:
                checked.append(item)
                _collect_structured_import_summary_message(item, warnings)
                continue
            warnings.append(f"{artifact_id}: structured import semantic metadata is not indexed; run deep verification before reuse.")
            checked.append(_legacy_structured_import_summary_item(artifact))
            continue

        try:
            verification = verify_archived_artifact(artifact_id)
        except FileNotFoundError as exc:
            errors.append(f"{artifact_id}: archived file missing ({exc}).")
            checked.append(_missing_structured_import_summary_item(artifact_id))
            continue
        item = _structured_import_summary_item_from_verification(artifact, verification)
        checked.append(item)
        _collect_structured_import_summary_message(item, warnings)

    if not artifacts:
        warnings.append("No archived structured import audit bundles were available.")

    semantic_pass_count = sum(1 for item in checked if item.get("semantic_status") == "pass")
    freshness = _semantic_summary_freshness(checked, label="structured import audit bundle", warnings=warnings)
    return {
        "status": "fail" if errors else "warning" if warnings else "pass",
        "errors": errors,
        "warnings": warnings,
        "verification_mode": "deep" if verify_files else "indexed",
        "sample_limit": sample_limit,
        "checked_count": len(checked),
        "semantic_pass_count": semantic_pass_count,
        "semantic_warning_count": sum(1 for item in checked if item.get("semantic_status") == "warning"),
        "semantic_fail_count": sum(1 for item in checked if item.get("semantic_status") == "fail"),
        "latest_artifacts": checked,
        **freshness,
    }


def rag_evaluation_archive_summary(limit: int = 20, *, verify_files: bool = True) -> dict[str, Any]:
    return _bundle_archive_semantic_summary(
        limit=limit,
        verify_files=verify_files,
        artifact_type="rag_evaluation_bundle",
        metadata_key="rag_evaluation_semantic_verification",
        label="RAG evaluation bundle",
        extra_fields=(
            "query_fingerprint",
            "evaluation_hash",
            "retrieval_trace_hash",
            "evidence_sufficiency_hash",
            "facet_gap_analysis_hash",
            "query_term_coverage_hash",
            "top_sources_hash",
            "source_provenance_hash",
            "source_provenance_count",
            "source_payload_hash_count",
            "source_snapshot_count",
            "score_breakdown_hash",
            "chunks_hash",
            "result_count",
            "chunk_count",
            "structured_manifest_hash",
        ),
    )


def rag_vector_index_archive_summary(limit: int = 20, *, verify_files: bool = True) -> dict[str, Any]:
    return _bundle_archive_semantic_summary(
        limit=limit,
        verify_files=verify_files,
        artifact_type="rag_vector_index_bundle",
        metadata_key="rag_vector_index_semantic_verification",
        label="RAG vector index bundle",
        extra_fields=(
            "chunk_count",
            "embedding_dimensions",
            "embedding_model",
            "retrieval_model",
            "recommended_backend",
            "migration_target_backend",
            "parity_status",
            "vector_row_hash",
            "structured_manifest_hash",
        ),
    )


def optimizer_benchmark_archive_summary(limit: int = 20, *, verify_files: bool = True) -> dict[str, Any]:
    return _bundle_archive_semantic_summary(
        limit=limit,
        verify_files=verify_files,
        artifact_type="optimizer_benchmark_bundle",
        metadata_key="optimizer_benchmark_semantic_verification",
        label="optimizer benchmark bundle",
        extra_fields=(
            "benchmark_status",
            "diagnostics_status",
            "stress_status",
            "case_count",
            "cases_hash",
            "results_hash",
            "benchmark_hash",
            "diagnostics_hash",
            "case_metrics_hash",
            "candidate_diagnostics_hash",
            "case_provenance_hash",
            "case_fingerprint_count",
            "recommended_folding_evidence_hash",
            "recommended_folding_evidence_count",
            "structured_manifest_hash",
        ),
    )


def rag_regression_archive_summary(limit: int = 20, *, verify_files: bool = True) -> dict[str, Any]:
    return _bundle_archive_semantic_summary(
        limit=limit,
        verify_files=verify_files,
        artifact_type="rag_regression_bundle",
        metadata_key="rag_regression_semantic_verification",
        label="RAG regression bundle",
        extra_fields=(
            "regression_status",
            "case_count",
            "cases_hash",
            "results_hash",
            "quality_summary_hash",
            "quality_status",
            "top_source_count",
            "missing_term_case_count",
            "weak_case_count",
            "structured_manifest_hash",
        ),
    )


def data_release_archive_summary(limit: int = 20, *, verify_files: bool = True) -> dict[str, Any]:
    return _bundle_archive_semantic_summary(
        limit=limit,
        verify_files=verify_files,
        artifact_type="data_release_bundle",
        metadata_key="data_release_semantic_verification",
        label="data release bundle",
        extra_fields=(
            "promotion_status",
            "record_count",
            "records_hash",
            "records_csv_hash",
            "release_handoff_hash",
            "structured_manifest_hash",
            "rag_structured_manifest_hash",
            "rag_index_hash",
            "trna_caveat_count",
            "trna_blocking_production_use",
            "external_snapshot_reference_count",
            "external_snapshot_referenced_count",
            "external_snapshot_contained_count",
            "external_snapshot_missing_count",
        ),
    )


def data_snapshot_archive_summary(limit: int = 20, *, verify_files: bool = True) -> dict[str, Any]:
    return _bundle_archive_semantic_summary(
        limit=limit,
        verify_files=verify_files,
        artifact_type="data_snapshot_bundle",
        metadata_key="data_snapshot_semantic_verification",
        label="data snapshot bundle",
        extra_fields=(
            "snapshot_manifest_hash",
            "structured_manifest_hash",
            "rag_index_hash",
            "external_snapshot_file_count",
            "snapshot_file_count",
        ),
    )


def data_refresh_plan_archive_summary(limit: int = 20, *, verify_files: bool = True) -> dict[str, Any]:
    return _bundle_archive_semantic_summary(
        limit=limit,
        verify_files=verify_files,
        artifact_type="data_refresh_plan_bundle",
        metadata_key="data_refresh_plan_semantic_verification",
        label="data refresh plan bundle",
        extra_fields=(
            "operation_count",
            "request_hash",
            "operations_hash",
            "data_catalog_hash",
            "external_sources_hash",
            "structured_quality_hash",
            "data_provenance_hash",
            "rag_status_hash",
            "validation_status",
            "structured_manifest_hash",
            "release_lock_status",
            "dataset_id",
        ),
    )


def workflow_trace_archive_summary(limit: int = 20, *, verify_files: bool = True) -> dict[str, Any]:
    return _bundle_archive_semantic_summary(
        limit=limit,
        verify_files=verify_files,
        artifact_type="workflow_trace_bundle",
        metadata_key="workflow_trace_semantic_verification",
        label="workflow trace bundle",
        extra_fields=(
            "workflow_id",
            "run_id",
            "task_type",
            "trace_hash",
            "trace_step_count",
            "structured_manifest_hash",
        ),
    )


def _verify_export_bundle(content: bytes) -> dict[str, Any]:
    verification = verify_artifact_bundle(content)
    if verification.get("artifact_type") == "data_snapshot_bundle":
        return verify_data_snapshot_bundle(content)
    if verification.get("artifact_type") == "data_release_bundle":
        return verify_data_release_bundle(content)
    if verification.get("artifact_type") == "qc_report_bundle":
        return verify_qc_report_bundle(content)
    if verification.get("artifact_type") == "structured_import_audit_bundle":
        return verify_structured_import_audit_bundle(content)
    if verification.get("artifact_type") == "data_refresh_plan_bundle":
        return verify_data_refresh_plan_bundle(content)
    if verification.get("artifact_type") == "rag_evaluation_bundle":
        return verify_rag_evaluation_bundle(content)
    if verification.get("artifact_type") == "rag_regression_bundle":
        return verify_rag_regression_bundle(content)
    if verification.get("artifact_type") == "rag_vector_index_bundle":
        return verify_rag_vector_index_bundle(content)
    if verification.get("artifact_type") == "optimizer_benchmark_bundle":
        return verify_optimizer_benchmark_bundle(content)
    if verification.get("artifact_type") == "workflow_trace_bundle":
        return verify_workflow_trace_bundle(content)
    return verification


def _archive_metadata(metadata: dict[str, Any] | None, verification: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(metadata or {})
    if verification.get("artifact_type") == "qc_report_bundle":
        enriched["qc_bundle_semantic_verification"] = _semantic_metadata(verification)
    if verification.get("artifact_type") == "structured_import_audit_bundle":
        enriched["structured_import_semantic_verification"] = _structured_import_semantic_metadata(verification)
    if verification.get("artifact_type") == "data_refresh_plan_bundle":
        enriched["data_refresh_plan_semantic_verification"] = _data_refresh_plan_semantic_metadata(verification)
    if verification.get("artifact_type") == "data_snapshot_bundle":
        enriched["data_snapshot_semantic_verification"] = _data_snapshot_semantic_metadata(verification)
    if verification.get("artifact_type") == "data_release_bundle":
        enriched["data_release_semantic_verification"] = _data_release_semantic_metadata(verification)
    if verification.get("artifact_type") == "rag_evaluation_bundle":
        enriched["rag_evaluation_semantic_verification"] = _rag_evaluation_semantic_metadata(verification)
    if verification.get("artifact_type") == "rag_regression_bundle":
        enriched["rag_regression_semantic_verification"] = _rag_regression_semantic_metadata(verification)
    if verification.get("artifact_type") == "rag_vector_index_bundle":
        enriched["rag_vector_index_semantic_verification"] = _rag_vector_index_semantic_metadata(verification)
    if verification.get("artifact_type") == "optimizer_benchmark_bundle":
        enriched["optimizer_benchmark_semantic_verification"] = _optimizer_benchmark_semantic_metadata(verification)
    if verification.get("artifact_type") == "workflow_trace_bundle":
        enriched["workflow_trace_semantic_verification"] = _workflow_trace_semantic_metadata(verification)
    return enriched


def _semantic_metadata(verification: dict[str, Any]) -> dict[str, Any]:
    semantic_checks = verification.get("semantic_checks") or {}
    return {
        "status": verification.get("status"),
        "semantic_status": verification.get("semantic_status"),
        "optimizer_manifest_hash": verification.get("optimizer_manifest_hash"),
        "request_hash": verification.get("request_hash"),
        "qc_report_hash": verification.get("qc_report_hash"),
        "candidate_ranking_hash": verification.get("candidate_ranking_hash"),
        "recommendation_audit_hash": verification.get("recommendation_audit_hash"),
        "recommended_folding_evidence_hash": verification.get("recommended_folding_evidence_hash"),
        "recommended_folding_status": verification.get("recommended_folding_status"),
        "recommended_folding_backend": verification.get("recommended_folding_backend"),
        "recommended_folding_fallback_active": verification.get("recommended_folding_fallback_active"),
        "data_quality_status": verification.get("data_quality_status"),
        "optimizer_stress_status": verification.get("optimizer_stress_status"),
        "objective_count": verification.get("objective_count"),
        "retrieval_quality_status": verification.get("retrieval_quality_status"),
        "retrieval_quality_record_count": verification.get("retrieval_quality_record_count"),
        "retrieval_quality_source_count": verification.get("retrieval_quality_source_count"),
        "retrieval_quality_collection_count": verification.get("retrieval_quality_collection_count"),
        "retrieval_quality_high_confidence_count": verification.get("retrieval_quality_high_confidence_count"),
        "retrieval_model": verification.get("retrieval_model"),
        "embedding_model": verification.get("embedding_model"),
        "request_payload_status": semantic_checks.get("request_payload"),
        "request_target_checks": {
            key.removeprefix("request_target_"): value
            for key, value in semantic_checks.items()
            if key.startswith("request_target_")
        },
        "checked_files": verification.get("checked_files"),
        "file_count": verification.get("file_count"),
        "manifest_hash": verification.get("manifest_hash"),
        "semantic_errors": verification.get("semantic_errors") or [],
        "semantic_warnings": verification.get("semantic_warnings") or [],
        "indexed_at": _utc_now(),
        "index_schema": "agentic-rag-qc-bundle-semantic-index-v1",
    }


def _structured_import_semantic_metadata(verification: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": verification.get("status"),
        "semantic_status": verification.get("semantic_status"),
        "structured_manifest_hash": verification.get("structured_manifest_hash"),
        "validation_error_count": verification.get("validation_error_count"),
        "validation_warning_count": verification.get("validation_warning_count"),
        "checked_files": verification.get("checked_files"),
        "file_count": verification.get("file_count"),
        "manifest_hash": verification.get("manifest_hash"),
        "semantic_errors": verification.get("semantic_errors") or [],
        "semantic_warnings": verification.get("semantic_warnings") or [],
        "indexed_at": _utc_now(),
        "index_schema": "agentic-rag-structured-import-semantic-index-v1",
    }


def _data_refresh_plan_semantic_metadata(verification: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": verification.get("status"),
        "semantic_status": verification.get("semantic_status"),
        "operation_count": verification.get("operation_count"),
        "request_hash": verification.get("request_hash"),
        "operations_hash": verification.get("operations_hash"),
        "data_catalog_hash": verification.get("data_catalog_hash"),
        "external_sources_hash": verification.get("external_sources_hash"),
        "structured_quality_hash": verification.get("structured_quality_hash"),
        "data_provenance_hash": verification.get("data_provenance_hash"),
        "rag_status_hash": verification.get("rag_status_hash"),
        "validation_status": verification.get("validation_status"),
        "structured_manifest_hash": verification.get("structured_manifest_hash"),
        "release_lock_status": verification.get("release_lock_status"),
        "dataset_id": verification.get("dataset_id"),
        "checked_files": verification.get("checked_files"),
        "file_count": verification.get("file_count"),
        "manifest_hash": verification.get("manifest_hash"),
        "semantic_errors": verification.get("semantic_errors") or [],
        "semantic_warnings": verification.get("semantic_warnings") or [],
        "indexed_at": _utc_now(),
        "index_schema": "agentic-rag-data-refresh-plan-semantic-index-v1",
    }


def _workflow_trace_semantic_metadata(verification: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": verification.get("status"),
        "semantic_status": verification.get("semantic_status"),
        "workflow_id": verification.get("workflow_id"),
        "run_id": verification.get("run_id"),
        "task_type": verification.get("task_type"),
        "trace_hash": verification.get("trace_hash"),
        "trace_step_count": verification.get("trace_step_count"),
        "structured_manifest_hash": verification.get("structured_manifest_hash"),
        "checked_files": verification.get("checked_files"),
        "file_count": verification.get("file_count"),
        "manifest_hash": verification.get("manifest_hash"),
        "semantic_errors": verification.get("semantic_errors") or [],
        "semantic_warnings": verification.get("semantic_warnings") or [],
        "indexed_at": _utc_now(),
        "index_schema": "agentic-rag-workflow-trace-semantic-index-v1",
    }


def _data_release_semantic_metadata(verification: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": verification.get("status"),
        "semantic_status": verification.get("semantic_status"),
        "promotion_status": verification.get("promotion_status"),
        "record_count": verification.get("record_count"),
        "records_hash": verification.get("records_hash"),
        "records_csv_hash": verification.get("records_csv_hash"),
        "release_handoff_hash": verification.get("release_handoff_hash"),
        "structured_manifest_hash": verification.get("structured_manifest_hash"),
        "rag_structured_manifest_hash": verification.get("rag_structured_manifest_hash"),
        "rag_index_hash": verification.get("rag_index_hash"),
        "quality_status": verification.get("quality_status"),
        "provenance_status": verification.get("provenance_status"),
        "trna_caveat_count": verification.get("trna_caveat_count"),
        "trna_blocking_production_use": verification.get("trna_blocking_production_use"),
        "release_lock_status": verification.get("release_lock_status"),
        "external_snapshot_reference_count": verification.get("external_snapshot_reference_count"),
        "external_snapshot_referenced_count": verification.get("external_snapshot_referenced_count"),
        "external_snapshot_contained_count": verification.get("external_snapshot_contained_count"),
        "external_snapshot_missing_count": verification.get("external_snapshot_missing_count"),
        "external_snapshot_file_count": verification.get("external_snapshot_file_count"),
        "checked_files": verification.get("checked_files"),
        "file_count": verification.get("file_count"),
        "manifest_hash": verification.get("manifest_hash"),
        "semantic_errors": verification.get("semantic_errors") or [],
        "semantic_warnings": verification.get("semantic_warnings") or [],
        "indexed_at": _utc_now(),
        "index_schema": "agentic-rag-data-release-semantic-index-v1",
    }


def _data_snapshot_semantic_metadata(verification: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": verification.get("status"),
        "semantic_status": verification.get("semantic_status"),
        "snapshot_manifest_hash": verification.get("snapshot_manifest_hash"),
        "structured_manifest_hash": verification.get("structured_manifest_hash"),
        "rag_index_hash": verification.get("rag_index_hash"),
        "external_snapshot_file_count": verification.get("external_snapshot_file_count"),
        "snapshot_file_count": verification.get("snapshot_file_count"),
        "checked_files": verification.get("checked_files"),
        "file_count": verification.get("file_count"),
        "manifest_hash": verification.get("manifest_hash"),
        "semantic_errors": verification.get("semantic_errors") or [],
        "semantic_warnings": verification.get("semantic_warnings") or [],
        "indexed_at": _utc_now(),
        "index_schema": "agentic-rag-data-snapshot-semantic-index-v1",
    }


def _rag_evaluation_semantic_metadata(verification: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": verification.get("status"),
        "semantic_status": verification.get("semantic_status"),
        "query_fingerprint": verification.get("query_fingerprint"),
        "evaluation_hash": verification.get("evaluation_hash"),
        "retrieval_trace_hash": verification.get("retrieval_trace_hash"),
        "evidence_sufficiency_hash": verification.get("evidence_sufficiency_hash"),
        "facet_gap_analysis_hash": verification.get("facet_gap_analysis_hash"),
        "query_term_coverage_hash": verification.get("query_term_coverage_hash"),
        "top_sources_hash": verification.get("top_sources_hash"),
        "source_provenance_hash": verification.get("source_provenance_hash"),
        "source_provenance_count": verification.get("source_provenance_count"),
        "source_payload_hash_count": verification.get("source_payload_hash_count"),
        "source_snapshot_count": verification.get("source_snapshot_count"),
        "score_breakdown_hash": verification.get("score_breakdown_hash"),
        "chunks_hash": verification.get("chunks_hash"),
        "result_count": verification.get("result_count"),
        "chunk_count": verification.get("chunk_count"),
        "structured_manifest_hash": verification.get("structured_manifest_hash"),
        "checked_files": verification.get("checked_files"),
        "file_count": verification.get("file_count"),
        "manifest_hash": verification.get("manifest_hash"),
        "semantic_errors": verification.get("semantic_errors") or [],
        "semantic_warnings": verification.get("semantic_warnings") or [],
        "indexed_at": _utc_now(),
        "index_schema": "agentic-rag-evaluation-semantic-index-v1",
    }


def _rag_regression_semantic_metadata(verification: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": verification.get("status"),
        "semantic_status": verification.get("semantic_status"),
        "regression_status": verification.get("regression_status"),
        "case_count": verification.get("case_count"),
        "cases_hash": verification.get("cases_hash"),
        "results_hash": verification.get("results_hash"),
        "quality_summary_hash": verification.get("quality_summary_hash"),
        "quality_status": verification.get("quality_status"),
        "top_source_count": verification.get("top_source_count"),
        "missing_term_case_count": verification.get("missing_term_case_count"),
        "weak_case_count": verification.get("weak_case_count"),
        "structured_manifest_hash": verification.get("structured_manifest_hash"),
        "checked_files": verification.get("checked_files"),
        "file_count": verification.get("file_count"),
        "manifest_hash": verification.get("manifest_hash"),
        "semantic_errors": verification.get("semantic_errors") or [],
        "semantic_warnings": verification.get("semantic_warnings") or [],
        "indexed_at": _utc_now(),
        "index_schema": "agentic-rag-regression-semantic-index-v1",
    }


def _rag_vector_index_semantic_metadata(verification: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": verification.get("status"),
        "semantic_status": verification.get("semantic_status"),
        "chunk_count": verification.get("chunk_count"),
        "embedding_dimensions": verification.get("embedding_dimensions"),
        "embedding_model": verification.get("embedding_model"),
        "retrieval_model": verification.get("retrieval_model"),
        "recommended_backend": verification.get("recommended_backend"),
        "migration_target_backend": verification.get("migration_target_backend"),
        "parity_status": verification.get("parity_status"),
        "vector_row_hash": verification.get("vector_row_hash"),
        "structured_manifest_hash": verification.get("structured_manifest_hash"),
        "checked_files": verification.get("checked_files"),
        "file_count": verification.get("file_count"),
        "manifest_hash": verification.get("manifest_hash"),
        "semantic_errors": verification.get("semantic_errors") or [],
        "semantic_warnings": verification.get("semantic_warnings") or [],
        "indexed_at": _utc_now(),
        "index_schema": "agentic-rag-vector-index-semantic-index-v1",
    }


def _optimizer_benchmark_semantic_metadata(verification: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": verification.get("status"),
        "semantic_status": verification.get("semantic_status"),
        "benchmark_status": verification.get("benchmark_status"),
        "diagnostics_status": verification.get("diagnostics_status"),
        "stress_status": verification.get("stress_status"),
        "case_count": verification.get("case_count"),
        "cases_hash": verification.get("cases_hash"),
        "results_hash": verification.get("results_hash"),
        "benchmark_hash": verification.get("benchmark_hash"),
        "diagnostics_hash": verification.get("diagnostics_hash"),
        "case_metrics_hash": verification.get("case_metrics_hash"),
        "candidate_diagnostics_hash": verification.get("candidate_diagnostics_hash"),
        "case_provenance_hash": verification.get("case_provenance_hash"),
        "case_fingerprint_count": verification.get("case_fingerprint_count"),
        "recommended_folding_evidence_hash": verification.get("recommended_folding_evidence_hash"),
        "recommended_folding_evidence_count": verification.get("recommended_folding_evidence_count"),
        "structured_manifest_hash": verification.get("structured_manifest_hash"),
        "checked_files": verification.get("checked_files"),
        "file_count": verification.get("file_count"),
        "manifest_hash": verification.get("manifest_hash"),
        "semantic_errors": verification.get("semantic_errors") or [],
        "semantic_warnings": verification.get("semantic_warnings") or [],
        "indexed_at": _utc_now(),
        "index_schema": "agentic-rag-optimizer-benchmark-semantic-index-v1",
    }


def _semantic_summary_item_from_metadata(artifact: dict[str, Any]) -> dict[str, Any] | None:
    metadata = artifact.get("metadata") or {}
    semantic = metadata.get("qc_bundle_semantic_verification") or {}
    if not semantic:
        return None
    return {
        "artifact_id": artifact.get("artifact_id"),
        "created_at": artifact.get("created_at"),
        "resource_id": artifact.get("resource_id"),
        "status": semantic.get("status") or artifact.get("verification_status"),
        "semantic_status": semantic.get("semantic_status") or "not_indexed",
        "optimizer_manifest_hash": semantic.get("optimizer_manifest_hash"),
        "request_hash": semantic.get("request_hash"),
        "qc_report_hash": semantic.get("qc_report_hash"),
        "candidate_ranking_hash": semantic.get("candidate_ranking_hash"),
        "recommendation_audit_hash": semantic.get("recommendation_audit_hash"),
        "recommended_folding_evidence_hash": semantic.get("recommended_folding_evidence_hash"),
        "recommended_folding_status": semantic.get("recommended_folding_status"),
        "recommended_folding_backend": semantic.get("recommended_folding_backend"),
        "recommended_folding_fallback_active": semantic.get("recommended_folding_fallback_active"),
        "data_quality_status": semantic.get("data_quality_status"),
        "optimizer_stress_status": semantic.get("optimizer_stress_status"),
        "objective_count": semantic.get("objective_count"),
        "retrieval_quality_status": semantic.get("retrieval_quality_status"),
        "retrieval_quality_record_count": semantic.get("retrieval_quality_record_count"),
        "retrieval_quality_source_count": semantic.get("retrieval_quality_source_count"),
        "retrieval_quality_collection_count": semantic.get("retrieval_quality_collection_count"),
        "retrieval_quality_high_confidence_count": semantic.get("retrieval_quality_high_confidence_count"),
        "retrieval_model": semantic.get("retrieval_model"),
        "embedding_model": semantic.get("embedding_model"),
        "request_payload_status": semantic.get("request_payload_status"),
        "request_target_checks": semantic.get("request_target_checks") or {},
        "checked_files": semantic.get("checked_files"),
        "file_count": semantic.get("file_count"),
        "manifest_hash": semantic.get("manifest_hash") or artifact.get("manifest_hash"),
        "metadata_indexed": True,
    }


def _structured_import_summary_item_from_metadata(artifact: dict[str, Any]) -> dict[str, Any] | None:
    metadata = artifact.get("metadata") or {}
    semantic = metadata.get("structured_import_semantic_verification") or {}
    if not semantic:
        return None
    return {
        "artifact_id": artifact.get("artifact_id"),
        "created_at": artifact.get("created_at"),
        "resource_id": artifact.get("resource_id"),
        "status": semantic.get("status") or artifact.get("verification_status"),
        "semantic_status": semantic.get("semantic_status") or "not_indexed",
        "structured_manifest_hash": semantic.get("structured_manifest_hash"),
        "validation_error_count": semantic.get("validation_error_count"),
        "validation_warning_count": semantic.get("validation_warning_count"),
        "checked_files": semantic.get("checked_files"),
        "file_count": semantic.get("file_count"),
        "manifest_hash": semantic.get("manifest_hash") or artifact.get("manifest_hash"),
        "metadata_indexed": True,
    }


def _semantic_summary_item_from_verification(artifact: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
    bundle_verification = verification.get("bundle_verification") or {}
    return {
        "artifact_id": artifact.get("artifact_id"),
        "created_at": artifact.get("created_at"),
        "resource_id": artifact.get("resource_id"),
        "status": verification.get("status"),
        "semantic_status": bundle_verification.get("semantic_status") or "not_applicable",
        "optimizer_manifest_hash": bundle_verification.get("optimizer_manifest_hash"),
        "request_hash": bundle_verification.get("request_hash"),
        "qc_report_hash": bundle_verification.get("qc_report_hash"),
        "candidate_ranking_hash": bundle_verification.get("candidate_ranking_hash"),
        "recommendation_audit_hash": bundle_verification.get("recommendation_audit_hash"),
        "recommended_folding_evidence_hash": bundle_verification.get("recommended_folding_evidence_hash"),
        "recommended_folding_status": bundle_verification.get("recommended_folding_status"),
        "recommended_folding_backend": bundle_verification.get("recommended_folding_backend"),
        "recommended_folding_fallback_active": bundle_verification.get("recommended_folding_fallback_active"),
        "data_quality_status": bundle_verification.get("data_quality_status"),
        "optimizer_stress_status": bundle_verification.get("optimizer_stress_status"),
        "objective_count": bundle_verification.get("objective_count"),
        "retrieval_quality_status": bundle_verification.get("retrieval_quality_status"),
        "retrieval_quality_record_count": bundle_verification.get("retrieval_quality_record_count"),
        "retrieval_quality_source_count": bundle_verification.get("retrieval_quality_source_count"),
        "retrieval_quality_collection_count": bundle_verification.get("retrieval_quality_collection_count"),
        "retrieval_quality_high_confidence_count": bundle_verification.get("retrieval_quality_high_confidence_count"),
        "retrieval_model": bundle_verification.get("retrieval_model"),
        "embedding_model": bundle_verification.get("embedding_model"),
        "request_payload_status": (bundle_verification.get("semantic_checks") or {}).get("request_payload"),
        "request_target_checks": {
            key.removeprefix("request_target_"): value
            for key, value in (bundle_verification.get("semantic_checks") or {}).items()
            if key.startswith("request_target_")
        },
        "checked_files": bundle_verification.get("checked_files"),
        "file_count": bundle_verification.get("file_count"),
        "manifest_hash": verification.get("manifest_hash"),
        "metadata_indexed": False,
    }


def _structured_import_summary_item_from_verification(artifact: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
    bundle_verification = verification.get("bundle_verification") or {}
    return {
        "artifact_id": artifact.get("artifact_id"),
        "created_at": artifact.get("created_at"),
        "resource_id": artifact.get("resource_id"),
        "status": verification.get("status"),
        "semantic_status": bundle_verification.get("semantic_status") or "not_applicable",
        "structured_manifest_hash": bundle_verification.get("structured_manifest_hash"),
        "validation_error_count": bundle_verification.get("validation_error_count"),
        "validation_warning_count": bundle_verification.get("validation_warning_count"),
        "checked_files": bundle_verification.get("checked_files"),
        "file_count": bundle_verification.get("file_count"),
        "manifest_hash": verification.get("manifest_hash"),
        "metadata_indexed": False,
    }


def _legacy_semantic_summary_item(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_id": artifact.get("artifact_id"),
        "created_at": artifact.get("created_at"),
        "resource_id": artifact.get("resource_id"),
        "status": artifact.get("verification_status"),
        "semantic_status": "not_indexed",
        "optimizer_manifest_hash": None,
        "checked_files": None,
        "file_count": None,
        "manifest_hash": artifact.get("manifest_hash"),
        "metadata_indexed": False,
    }


def _legacy_structured_import_summary_item(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_id": artifact.get("artifact_id"),
        "created_at": artifact.get("created_at"),
        "resource_id": artifact.get("resource_id"),
        "status": artifact.get("verification_status"),
        "semantic_status": "not_indexed",
        "structured_manifest_hash": None,
        "validation_error_count": None,
        "validation_warning_count": None,
        "checked_files": None,
        "file_count": None,
        "manifest_hash": artifact.get("manifest_hash"),
        "metadata_indexed": False,
    }


def _missing_semantic_summary_item(artifact_id: str) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "status": "fail",
        "semantic_status": "fail",
        "optimizer_manifest_hash": None,
        "checked_files": 0,
        "file_count": 0,
        "metadata_indexed": False,
    }


def _missing_structured_import_summary_item(artifact_id: str) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "status": "fail",
        "semantic_status": "fail",
        "structured_manifest_hash": None,
        "validation_error_count": None,
        "validation_warning_count": None,
        "checked_files": 0,
        "file_count": 0,
        "metadata_indexed": False,
    }


def _collect_semantic_summary_message(item: dict[str, Any], warnings: list[str]) -> None:
    artifact_id = item.get("artifact_id")
    semantic_status = item.get("semantic_status")
    status = item.get("status")
    if status == "fail" or semantic_status == "fail":
        warnings.append(f"{artifact_id}: QC bundle semantic verification failed; inspect the artifact before reuse.")
    elif status == "warning" or semantic_status in {"warning", "not_indexed", "not_applicable"}:
        warnings.append(f"{artifact_id}: QC bundle semantic verification has warnings.")


def _collect_structured_import_summary_message(item: dict[str, Any], warnings: list[str]) -> None:
    artifact_id = item.get("artifact_id")
    semantic_status = item.get("semantic_status")
    status = item.get("status")
    if status == "fail" or semantic_status == "fail":
        warnings.append(f"{artifact_id}: structured import audit verification failed; inspect the artifact before reuse.")
    elif status == "warning" or semantic_status in {"warning", "not_indexed", "not_applicable"}:
        warnings.append(f"{artifact_id}: structured import audit verification has warnings.")


def _bundle_archive_semantic_summary(
    *,
    limit: int,
    verify_files: bool,
    artifact_type: str,
    metadata_key: str,
    label: str,
    extra_fields: tuple[str, ...],
) -> dict[str, Any]:
    sample_limit = max(1, min(limit, 100))
    artifacts = list_archived_artifacts(limit=sample_limit, artifact_type=artifact_type)["artifacts"]
    checked: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    for artifact in artifacts:
        artifact_id = artifact["artifact_id"]
        if not verify_files:
            item = _bundle_summary_item_from_metadata(artifact, metadata_key=metadata_key, extra_fields=extra_fields)
            if item:
                checked.append(item)
                _collect_bundle_summary_message(item, warnings, label=label)
                continue
            warnings.append(f"{artifact_id}: {label} semantic metadata is not indexed; run deep verification before reuse.")
            checked.append(_legacy_bundle_summary_item(artifact, extra_fields=extra_fields))
            continue

        try:
            verification = verify_archived_artifact(artifact_id)
        except FileNotFoundError as exc:
            errors.append(f"{artifact_id}: archived file missing ({exc}).")
            checked.append(_missing_bundle_summary_item(artifact_id, extra_fields=extra_fields))
            continue
        item = _bundle_summary_item_from_verification(artifact, verification, extra_fields=extra_fields)
        checked.append(item)
        _collect_bundle_summary_message(item, warnings, label=label)

    if not artifacts:
        warnings.append(f"No archived {label}s were available.")

    semantic_pass_count = sum(1 for item in checked if item.get("semantic_status") == "pass")
    freshness = _semantic_summary_freshness(checked, label=label, warnings=warnings)
    return {
        "status": "fail" if errors else "warning" if warnings else "pass",
        "errors": errors,
        "warnings": warnings,
        "verification_mode": "deep" if verify_files else "indexed",
        "sample_limit": sample_limit,
        "checked_count": len(checked),
        "semantic_pass_count": semantic_pass_count,
        "semantic_warning_count": sum(1 for item in checked if item.get("semantic_status") == "warning"),
        "semantic_fail_count": sum(1 for item in checked if item.get("semantic_status") == "fail"),
        "latest_artifacts": checked,
        **freshness,
    }


def _semantic_summary_freshness(checked: list[dict[str, Any]], *, label: str, warnings: list[str]) -> dict[str, Any]:
    latest = _latest_semantic_summary_item(checked)
    latest_created_at = latest.get("created_at") if latest else None
    latest_age_hours = _age_hours(latest_created_at)
    if not checked:
        status = "empty"
    elif latest_age_hours is None:
        status = "unknown"
        warnings.append(f"Latest archived {label} is missing created_at; freshness could not be verified.")
    elif latest_age_hours > ARCHIVE_SEMANTIC_FRESHNESS_WARNING_HOURS:
        status = "stale"
        warnings.append(
            f"Latest archived {label} is {latest_age_hours:.1f}h old; refresh evidence before production promotion."
        )
    else:
        status = "fresh"
    return {
        "freshness_status": status,
        "latest_created_at": latest_created_at,
        "latest_age_hours": latest_age_hours,
        "freshness_policy": {
            "warning_hours": ARCHIVE_SEMANTIC_FRESHNESS_WARNING_HOURS,
        },
    }


def _latest_semantic_summary_item(checked: list[dict[str, Any]]) -> dict[str, Any] | None:
    latest: dict[str, Any] | None = None
    latest_dt: datetime | None = None
    for item in checked:
        created_at = _parse_utc_datetime(item.get("created_at"))
        if created_at is None:
            continue
        if latest_dt is None or created_at > latest_dt:
            latest = item
            latest_dt = created_at
    return latest or (checked[0] if checked else None)


def _age_hours(value: Any) -> float | None:
    created_at = _parse_utc_datetime(value)
    if created_at is None:
        return None
    return round(max(0.0, (datetime.now(timezone.utc) - created_at).total_seconds() / 3600), 3)


def _parse_utc_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bundle_summary_item_from_metadata(
    artifact: dict[str, Any],
    *,
    metadata_key: str,
    extra_fields: tuple[str, ...],
) -> dict[str, Any] | None:
    metadata = artifact.get("metadata") or {}
    semantic = metadata.get(metadata_key) or {}
    if not semantic:
        return None
    item = {
        "artifact_id": artifact.get("artifact_id"),
        "created_at": artifact.get("created_at"),
        "resource_id": artifact.get("resource_id"),
        "status": semantic.get("status") or artifact.get("verification_status"),
        "semantic_status": semantic.get("semantic_status") or "not_indexed",
        "checked_files": semantic.get("checked_files"),
        "file_count": semantic.get("file_count"),
        "manifest_hash": semantic.get("manifest_hash") or artifact.get("manifest_hash"),
        "metadata_indexed": True,
    }
    for field in extra_fields:
        item[field] = semantic.get(field)
    return item


def _bundle_summary_item_from_verification(
    artifact: dict[str, Any],
    verification: dict[str, Any],
    *,
    extra_fields: tuple[str, ...],
) -> dict[str, Any]:
    bundle_verification = verification.get("bundle_verification") or {}
    item = {
        "artifact_id": artifact.get("artifact_id"),
        "created_at": artifact.get("created_at"),
        "resource_id": artifact.get("resource_id"),
        "status": verification.get("status"),
        "semantic_status": bundle_verification.get("semantic_status") or "not_applicable",
        "checked_files": bundle_verification.get("checked_files"),
        "file_count": bundle_verification.get("file_count"),
        "manifest_hash": verification.get("manifest_hash"),
        "metadata_indexed": False,
    }
    for field in extra_fields:
        item[field] = bundle_verification.get(field)
    return item


def _legacy_bundle_summary_item(artifact: dict[str, Any], *, extra_fields: tuple[str, ...]) -> dict[str, Any]:
    item = {
        "artifact_id": artifact.get("artifact_id"),
        "created_at": artifact.get("created_at"),
        "resource_id": artifact.get("resource_id"),
        "status": artifact.get("verification_status"),
        "semantic_status": "not_indexed",
        "checked_files": None,
        "file_count": None,
        "manifest_hash": artifact.get("manifest_hash"),
        "metadata_indexed": False,
    }
    for field in extra_fields:
        item[field] = None
    return item


def _missing_bundle_summary_item(artifact_id: str, *, extra_fields: tuple[str, ...]) -> dict[str, Any]:
    item = {
        "artifact_id": artifact_id,
        "status": "fail",
        "semantic_status": "fail",
        "checked_files": 0,
        "file_count": 0,
        "metadata_indexed": False,
    }
    for field in extra_fields:
        item[field] = None
    return item


def _collect_bundle_summary_message(item: dict[str, Any], warnings: list[str], *, label: str) -> None:
    artifact_id = item.get("artifact_id")
    semantic_status = item.get("semantic_status")
    status = item.get("status")
    if status == "fail" or semantic_status == "fail":
        warnings.append(f"{artifact_id}: {label} semantic verification failed; inspect the artifact before reuse.")
    elif status == "warning" or semantic_status in {"warning", "not_indexed", "not_applicable"}:
        warnings.append(f"{artifact_id}: {label} semantic verification has warnings.")


def artifact_ledger(limit: int = 50) -> dict[str, Any]:
    _ensure_schema()
    entries = _read_ledger_entries()
    return {
        "entries": entries[-max(1, min(limit, 500)) :],
        "total_entries": len(entries),
        "latest_entry": entries[-1] if entries else None,
        "ledger_path": str(ARCHIVE_LEDGER_PATH),
    }


def backfill_artifact_ledger(*, dry_run: bool = True) -> dict[str, Any]:
    _ensure_schema()
    entries = _read_ledger_entries()
    ledger_artifact_ids = {
        str(entry.get("artifact_id") or "")
        for entry in entries
        if _is_store_event(str(entry.get("event_type") or "store")) and entry.get("artifact_id")
    }
    archived = list_archived_artifacts(limit=500)["artifacts"]
    missing = [artifact for artifact in archived if artifact["artifact_id"] not in ledger_artifact_ids]
    if dry_run:
        return {
            "status": "ready",
            "dry_run": True,
            "candidate_count": len(missing),
            "backfilled_count": 0,
            "candidates": missing,
            "ledger": verify_artifact_ledger(),
        }

    backfilled = []
    for artifact in missing:
        entry = _append_ledger_entry(
            artifact,
            event_type="backfill_store",
            detail={"reason": "reconcile archived artifact created before ledger support"},
        )
        backfilled.append({"artifact_id": artifact["artifact_id"], "entry_id": entry["entry_id"], "entry_hash": entry["entry_hash"]})

    return {
        "status": "pass",
        "dry_run": False,
        "candidate_count": len(missing),
        "backfilled_count": len(backfilled),
        "backfilled": backfilled,
        "ledger": verify_artifact_ledger(),
    }


def verify_artifact_ledger() -> dict[str, Any]:
    _ensure_schema()
    entries = _read_ledger_entries()
    errors: list[str] = []
    warnings: list[str] = []
    previous_hash = "GENESIS"
    seen_entry_ids: set[str] = set()
    seen_artifact_ids: set[str] = set()
    deleted_artifact_ids = {
        str(entry.get("artifact_id") or "")
        for entry in entries
        if str(entry.get("event_type") or "store") == "retention_delete" and entry.get("artifact_id")
    }

    for index, entry in enumerate(entries):
        entry_id = str(entry.get("entry_id") or "")
        artifact_id = str(entry.get("artifact_id") or "")
        event_type = str(entry.get("event_type") or "store")
        if not entry_id:
            errors.append(f"Ledger entry {index} is missing entry_id.")
        elif entry_id in seen_entry_ids:
            errors.append(f"Duplicate ledger entry_id: {entry_id}.")
        seen_entry_ids.add(entry_id)

        if not artifact_id:
            errors.append(f"Ledger entry {index} is missing artifact_id.")
        elif _is_store_event(event_type) and artifact_id in seen_artifact_ids:
            errors.append(f"Duplicate ledger artifact_id: {artifact_id}.")
        if _is_store_event(event_type):
            seen_artifact_ids.add(artifact_id)

        if entry.get("previous_hash") != previous_hash:
            errors.append(f"Ledger previous_hash mismatch at entry {entry_id or index}.")
        expected_hash = _hash_ledger_entry(entry)
        if entry.get("entry_hash") != expected_hash:
            errors.append(f"Ledger entry_hash mismatch at entry {entry_id or index}.")
        previous_hash = str(entry.get("entry_hash") or "")

        artifact = get_archived_artifact(artifact_id) if artifact_id else None
        if not artifact:
            if event_type == "retention_delete" or artifact_id in deleted_artifact_ids:
                continue
            errors.append(f"Ledger references missing archived artifact: {artifact_id}.")
            continue
        if event_type == "retention_delete":
            warnings.append(f"Ledger retention tombstone still has archive index row: {artifact_id}.")
            continue
        for field in ["sha256", "manifest_hash", "bytes"]:
            if entry.get(field) != artifact.get(field):
                errors.append(f"Ledger {field} does not match archive index for {artifact_id}.")

    archived_ids = {item["artifact_id"] for item in list_archived_artifacts(limit=500)["artifacts"]}
    missing_from_ledger = sorted(archived_ids - seen_artifact_ids)
    if missing_from_ledger:
        warnings.append(f"{len(missing_from_ledger)} archived artifacts are not present in the ledger, likely created before ledger support.")

    return {
        "status": "fail" if errors else "warning" if warnings else "pass",
        "errors": errors,
        "warnings": warnings,
        "ledger_path": str(ARCHIVE_LEDGER_PATH),
        "entry_count": len(entries),
        "latest_hash": entries[-1]["entry_hash"] if entries else "GENESIS",
        "missing_from_ledger_count": len(missing_from_ledger),
    }


def _is_store_event(event_type: str) -> bool:
    return event_type in {"store", "backfill_store"}


def archive_summary() -> dict[str, Any]:
    _ensure_schema()
    settings = get_settings()
    with _connect() as conn:
        total = conn.execute("select count(*) as count from archived_artifacts").fetchone()["count"]
        total_bytes = conn.execute("select coalesce(sum(bytes), 0) as bytes from archived_artifacts").fetchone()["bytes"]
        by_artifact_type = {
            row["artifact_type"] or "unknown": row["count"]
            for row in conn.execute(
                """
                select artifact_type, count(*) as count
                from archived_artifacts
                group by artifact_type
                order by artifact_type
                """
            ).fetchall()
        }
        by_status = {
            row["verification_status"] or "unknown": row["count"]
            for row in conn.execute(
                """
                select verification_status, count(*) as count
                from archived_artifacts
                group by verification_status
                order by verification_status
                """
            ).fetchall()
        }
        latest = conn.execute("select * from archived_artifacts order by created_at desc limit 1").fetchone()
    return {
        "total_artifacts": total,
        "total_bytes": total_bytes,
        "by_artifact_type": by_artifact_type,
        "by_status": by_status,
        "latest_artifact": _row_to_archive(latest) if latest else None,
        "ledger": verify_artifact_ledger(),
        "retention_policy": {
            "retention_days": settings.artifact_retention_days,
            "keep_min": settings.artifact_retention_keep_min,
            "enabled": settings.artifact_retention_days > 0,
        },
        "object_store": {
            "enabled": settings.artifact_object_store_enabled,
            "configured": bool(
                settings.artifact_object_store_enabled
                and settings.artifact_object_store_endpoint
                and settings.artifact_object_store_bucket
                and settings.artifact_object_store_access_key_id
                and settings.artifact_object_store_secret_access_key
            ),
            "bucket": settings.artifact_object_store_bucket or None,
            "prefix": settings.artifact_object_store_prefix,
            "region": settings.artifact_object_store_region,
        },
        "store_path": str(ARCHIVE_DB_PATH),
        "archive_dir": str(ARCHIVE_DIR),
    }


def plan_artifact_retention(
    *,
    retention_days: int | None = None,
    keep_min: int | None = None,
    resource_type: str | None = None,
    artifact_type: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    effective_days = settings.artifact_retention_days if retention_days is None else max(0, retention_days)
    effective_keep_min = settings.artifact_retention_keep_min if keep_min is None else max(0, keep_min)
    _ensure_schema()
    artifacts = list_archived_artifacts(
        limit=500,
        resource_type=resource_type,
        artifact_type=artifact_type,
    )["artifacts"]
    artifacts = sorted(artifacts, key=lambda item: item["created_at"], reverse=True)
    preserved_ids = {item["artifact_id"] for item in artifacts[:effective_keep_min]}
    cutoff = None
    if effective_days > 0:
        cutoff_timestamp = datetime.now(timezone.utc).timestamp() - (effective_days * 86400)
        cutoff = datetime.fromtimestamp(cutoff_timestamp, timezone.utc)

    candidates: list[dict[str, Any]] = []
    for artifact in artifacts:
        if artifact["artifact_id"] in preserved_ids:
            continue
        created_at = _parse_datetime(artifact["created_at"])
        if cutoff is None or created_at >= cutoff:
            continue
        candidates.append(artifact)

    return {
        "status": "ready",
        "retention_days": effective_days,
        "keep_min": effective_keep_min,
        "enabled": effective_days > 0,
        "cutoff": cutoff.isoformat() if cutoff else None,
        "filters": {"resource_type": resource_type, "artifact_type": artifact_type},
        "candidate_count": len(candidates),
        "candidate_bytes": sum(int(item.get("bytes") or 0) for item in candidates),
        "preserved_count": min(len(artifacts), effective_keep_min),
        "total_considered": len(artifacts),
        "candidates": candidates,
    }


def apply_artifact_retention(
    *,
    retention_days: int | None = None,
    keep_min: int | None = None,
    resource_type: str | None = None,
    artifact_type: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    plan = plan_artifact_retention(
        retention_days=retention_days,
        keep_min=keep_min,
        resource_type=resource_type,
        artifact_type=artifact_type,
    )
    if dry_run:
        return {**plan, "dry_run": True, "deleted_count": 0, "deleted_bytes": 0, "deleted": []}

    deleted: list[dict[str, Any]] = []
    errors: list[str] = []
    for artifact in plan["candidates"]:
        artifact_id = artifact["artifact_id"]
        path = Path(artifact["storage_path"])
        try:
            if path.exists():
                path.chmod(stat.S_IWRITE | stat.S_IREAD)
                path.unlink()
            with _connect() as conn:
                conn.execute("delete from archived_artifacts where artifact_id = ?", (artifact_id,))
            _append_ledger_entry(artifact, event_type="retention_delete", detail={"retention_days": plan["retention_days"]})
            deleted.append(artifact)
        except OSError as exc:
            errors.append(f"{artifact_id}: {exc}")

    return {
        **plan,
        "dry_run": False,
        "status": "fail" if errors else "pass",
        "deleted_count": len(deleted),
        "deleted_bytes": sum(int(item.get("bytes") or 0) for item in deleted),
        "deleted": deleted,
        "errors": errors,
        "ledger": verify_artifact_ledger(),
    }


def _ensure_schema() -> None:
    ARCHIVE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            create table if not exists archived_artifacts (
                artifact_id text primary key,
                created_at text not null,
                artifact_type text,
                resource_type text not null,
                resource_id text not null,
                action text not null,
                filename text not null,
                media_type text not null,
                bytes integer not null,
                sha256 text not null unique,
                manifest_hash text,
                verification_status text,
                storage_path text not null,
                metadata_json text not null
            )
            """
        )
        conn.execute("create index if not exists idx_archive_created_at on archived_artifacts(created_at)")
        conn.execute("create index if not exists idx_archive_resource on archived_artifacts(resource_type, resource_id)")
        conn.execute("create index if not exists idx_archive_manifest_hash on archived_artifacts(manifest_hash)")


def _get_by_sha256(digest: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("select * from archived_artifacts where sha256 = ?", (digest,)).fetchone()
    return _row_to_archive(row) if row else None


def _write_immutable(path: Path, content: bytes) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd = os.open(path, flags)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    path.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)


def _append_ledger_entry(
    artifact: dict[str, Any],
    *,
    event_type: str = "store",
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ARCHIVE_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    previous = _read_ledger_entries()
    entry = {
        "ledger_schema": "agentic-rag-artifact-ledger-v1",
        "entry_id": f"ledger_{uuid.uuid4().hex[:16]}",
        "event_type": event_type,
        "timestamp": _utc_now(),
        "artifact_id": artifact["artifact_id"],
        "artifact_type": artifact.get("artifact_type"),
        "resource_type": artifact["resource_type"],
        "resource_id": artifact["resource_id"],
        "action": artifact["action"],
        "bytes": artifact["bytes"],
        "sha256": artifact["sha256"],
        "manifest_hash": artifact.get("manifest_hash"),
        "verification_status": artifact.get("verification_status"),
        "detail": detail or {},
        "previous_hash": previous[-1]["entry_hash"] if previous else "GENESIS",
    }
    entry["entry_hash"] = _hash_ledger_entry(entry)
    with ARCHIVE_LEDGER_PATH.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(_json_dumps(entry) + "\n")
    return entry


def _read_ledger_entries() -> list[dict[str, Any]]:
    if not ARCHIVE_LEDGER_PATH.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line_no, line in enumerate(ARCHIVE_LEDGER_PATH.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            payload = {
                "entry_id": f"invalid_line_{line_no}",
                "entry_hash": "",
                "previous_hash": "",
                "artifact_id": "",
                "parse_error": f"Invalid JSON on ledger line {line_no}",
            }
        entries.append(payload)
    return entries


def _hash_ledger_entry(entry: dict[str, Any]) -> str:
    payload = {key: value for key, value in entry.items() if key != "entry_hash"}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(ARCHIVE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_archive(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "artifact_id": row["artifact_id"],
        "created_at": row["created_at"],
        "artifact_type": row["artifact_type"],
        "resource_type": row["resource_type"],
        "resource_id": row["resource_id"],
        "action": row["action"],
        "filename": row["filename"],
        "media_type": row["media_type"],
        "bytes": row["bytes"],
        "sha256": row["sha256"],
        "manifest_hash": row["manifest_hash"],
        "verification_status": row["verification_status"],
        "storage_path": row["storage_path"],
        "metadata": _json_loads(row["metadata_json"]) or {},
    }


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _json_loads(payload: str | None) -> Any:
    if not payload:
        return None
    return json.loads(payload)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
