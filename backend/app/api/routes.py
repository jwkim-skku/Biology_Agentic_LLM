from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response

from app.config import get_settings
from app.optimizer.nsga2 import OptimizationConfig
from app.optimizer.scoring import ScoreConfig
from app.schemas import (
    ApiResponse,
    AllenTaxonomyImportRequest,
    BatchDesignFromGenesRequest,
    DesignFromGeneRequest,
    DataRefreshRequest,
    EvidenceRequest,
    GeneRequest,
    GtexImportRequest,
    IngestLocalDocumentRequest,
    OptimizeRequest,
    RagSearchRequest,
    ScoreRequest,
    StructuredImportRequest,
)
from app.security import extract_api_key
from app.services.design_service import score_cds
from app.services.audit_log_service import audit_summary, list_audit_events, record_audit_event
from app.services.artifact_archive_service import (
    apply_artifact_retention,
    artifact_ledger,
    archive_artifact_bundle,
    archive_summary,
    data_refresh_plan_archive_summary,
    backfill_artifact_ledger,
    data_release_archive_summary,
    get_archived_artifact,
    list_archived_artifacts,
    optimizer_benchmark_archive_summary,
    plan_artifact_retention,
    qc_bundle_archive_semantic_summary,
    rag_evaluation_archive_summary,
    rag_regression_archive_summary,
    rag_vector_index_archive_summary,
    read_archived_artifact,
    structured_import_archive_summary,
    verify_archived_artifact,
    verify_artifact_ledger,
    workflow_trace_archive_summary,
)
from app.services.artifact_object_store_service import (
    artifact_object_store_status,
    mirror_artifact_archive,
    plan_artifact_object_store_mirror,
)
from app.services.batch_design_service import run_batch_gene_design
from app.services.data_lock_service import verify_data_lockfile, write_data_lockfile
from app.services.data_provenance_service import data_provenance_audit
from app.services.data_release_lock_service import verify_data_release_lock, write_data_release_lock
from app.services.data_release_bundle_service import build_data_release_bundle, verify_data_release_bundle
from app.services.data_refresh_service import data_catalog, record_data_baseline_event, refresh_log, refresh_reference_data, validate_refresh_plan
from app.services.data_refresh_plan_bundle_service import build_data_refresh_plan_bundle, verify_data_refresh_plan_bundle
from app.services.data_snapshot_service import build_data_snapshot_bundle
from app.services.deployment_readiness_service import deployment_readiness
from app.services.ensembl_client import EnsemblClientError
from app.services.evidence_service import search_evidence
from app.services.export_manifest_service import verify_artifact_bundle
from app.services.external_data_service import (
    backfill_external_source_snapshots,
    external_source_status,
    import_allen_whb_taxonomy,
    import_gtex_gene_expression,
)
from app.services.gene_service import fetch_canonical_cds, resolve_gene
from app.services.governance_service import (
    build_governance_attestation,
    build_governance_attestation_bundle,
    verify_governance_attestation_bundle,
)
from app.services.ingestion_service import ingest_local_document, list_ingested_documents
from app.services.job_export_service import build_job_export_bundle
from app.services.job_store import complete_job, create_job, fail_job, get_job, list_jobs, mark_job_running
from app.services.agent_memory_service import agent_memory_summary, get_agent_memory, list_agent_memory
from app.services.metrics_service import metrics_prometheus, metrics_snapshot
from app.services.optimizer_benchmark_service import evaluate_optimizer_benchmark, optimizer_benchmark_cases
from app.services.optimizer_benchmark_bundle_service import build_optimizer_benchmark_bundle, verify_optimizer_benchmark_bundle
from app.services.optimizer_diagnostics_service import optimizer_diagnostics
from app.services.optimizer_stress_service import optimizer_stress_gate
from app.services.production_audit_service import (
    build_production_audit,
    build_production_audit_bundle,
    production_audit_cache_status,
    verify_production_audit_bundle,
)
from app.services.qc_report_bundle_service import build_qc_report_bundle, verify_qc_report_bundle
from app.services.rag_service import evaluate_rag_query, load_rag_index, rag_search, rag_status, rebuild_rag_index
from app.services.rag_diagnostics_service import rag_diagnostics
from app.services.rag_embedding_service import rag_embedding_status
from app.services.rag_evaluation_bundle_service import build_rag_evaluation_bundle, verify_rag_evaluation_bundle
from app.services.rag_vector_index_bundle_service import build_rag_vector_index_bundle, verify_rag_vector_index_bundle
from app.services.rag_vector_store_migration_service import (
    import_rag_vector_store,
    rag_vector_store_import_plan,
    rag_vector_store_parity_report,
)
from app.services.rag_vector_store_service import rag_vector_store_status
from app.services.rag_regression_service import evaluate_rag_regression, rag_regression_cases
from app.services.rag_regression_bundle_service import build_rag_regression_bundle, verify_rag_regression_bundle
from app.services.report_service import export_qc_report
from app.services.rna_folding_service import evaluate_rna_folding, rna_folding_status
from app.services.run_export_service import build_run_export_bundle
from app.services.run_store import (
    get_run,
    get_run_artifact,
    list_runs,
    save_run,
    save_run_artifact,
)
from app.services.signature_service import signing_status
from app.services.structured_data_service import (
    import_structured_records,
    preview_structured_import,
    search_structured_context,
    structured_coverage_matrix,
    structured_manifest,
    structured_status,
    validate_structured_records,
)
from app.services.structured_quality_service import structured_quality_gate
from app.services.storage_service import postgres_schema_sql, storage_status, write_postgres_schema_file
from app.services.storage_migration_service import (
    build_sqlite_migration_bundle,
    import_sqlite_to_postgres,
    sqlite_migration_summary,
    sqlite_postgres_parity_report,
)
from app.services.structured_import_audit_service import build_structured_import_audit_bundle
from app.services.validation_service import validate_cds
from app.services.workflow_service import (
    plan_gene_design_task,
    run_cds_design_workflow,
    run_gene_design_workflow,
    workflow_summary,
)
from app.services.workflow_trace_bundle_service import (
    build_workflow_trace_bundle,
    verify_workflow_trace_bundle,
    workflow_runtime_status,
)


router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/live")
def liveness() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/health/ready", response_model=ApiResponse)
def readiness() -> ApiResponse:
    settings = get_settings()
    validation = validate_structured_records()
    rag = rag_status()
    checks = {
        "data_dir_exists": settings.data_dir.exists(),
        "structured_validation_errors": validation["error_count"],
        "rag_chunks": rag["chunks"],
        "rag_index_version": rag["index_version"],
    }
    ready = checks["data_dir_exists"] and checks["structured_validation_errors"] == 0 and checks["rag_chunks"] > 0
    return ApiResponse(data={"status": "ready" if ready else "degraded", "checks": checks})


@router.get("/deployment/readiness", response_model=ApiResponse)
def deployment_readiness_endpoint(api_request: Request) -> ApiResponse:
    return ApiResponse(data=deployment_readiness(api_request.app.openapi()))


@router.get("/deployment/audit", response_model=ApiResponse)
def production_audit_endpoint(api_request: Request, refresh: bool = False) -> ApiResponse:
    return ApiResponse(data=build_production_audit(api_request.app.openapi(), refresh=refresh))


@router.get("/deployment/audit/cache", response_model=ApiResponse)
def production_audit_cache_endpoint() -> ApiResponse:
    return ApiResponse(data=production_audit_cache_status())


@router.get("/deployment/audit/export.zip")
def production_audit_export_endpoint(api_request: Request, refresh: bool = False) -> Response:
    content = build_production_audit_bundle(api_request.app.openapi(), refresh=refresh)
    verification = verify_production_audit_bundle(content)
    audit_hash = verification.get("audit_hash") or "unknown"
    archive = _archive_bundle(
        content,
        action="production_audit_export",
        resource_type="production_audit",
        resource_id=str(audit_hash),
        filename=f"production_audit_{str(audit_hash)[:12]}.zip",
        metadata={
            "audit_hash": audit_hash,
            "verification_status": verification.get("status"),
            "production_ready": (verification.get("summary") or {}).get("production_ready"),
        },
    )
    _audit(
        api_request,
        "deployment",
        "production_audit_export",
        outcome=verification.get("status", "unknown"),
        resource_type="production_audit",
        resource_id=str(audit_hash),
        detail={
            "bytes": len(content),
            "archive_artifact_id": archive["artifact_id"],
            "verification_status": verification.get("status"),
            "production_ready": (verification.get("summary") or {}).get("production_ready"),
        },
    )
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="production_audit_{str(audit_hash)[:12]}.zip"'},
    )


@router.get("/deployment/audit/verify", response_model=ApiResponse)
def production_audit_verify_endpoint(api_request: Request, refresh: bool = False) -> ApiResponse:
    content = build_production_audit_bundle(api_request.app.openapi(), refresh=refresh)
    return ApiResponse(data=verify_production_audit_bundle(content))


@router.get("/settings", response_model=ApiResponse)
def settings_endpoint() -> ApiResponse:
    settings = get_settings()
    return ApiResponse(
        data={
            "data_dir": str(settings.data_dir),
            "cors_origins": list(settings.cors_origins),
            "auth_enabled": settings.auth_enabled,
            "rbac_enabled": bool(settings.api_key_roles),
            "rate_limit_per_minute": settings.rate_limit_per_minute,
            "artifact_signing_enabled": settings.artifact_signing_enabled,
            "artifact_signing_key_id": settings.artifact_signing_key_id if settings.artifact_signing_enabled else None,
            "artifact_asymmetric_signing_enabled": settings.artifact_asymmetric_signing_enabled,
            "artifact_asymmetric_verification_enabled": settings.artifact_asymmetric_verification_enabled,
            "artifact_ed25519_key_id": settings.artifact_ed25519_key_id
            if (settings.artifact_asymmetric_signing_enabled or settings.artifact_asymmetric_verification_enabled)
            else None,
            "signing": signing_status(),
            "storage_backend": settings.storage_backend,
            "database_url_configured": bool(settings.database_url),
        }
    )


@router.get("/storage/status", response_model=ApiResponse)
def storage_status_endpoint() -> ApiResponse:
    return ApiResponse(data=storage_status())


@router.get("/storage/postgres/schema.sql")
def postgres_schema_endpoint() -> Response:
    return Response(content=postgres_schema_sql(), media_type="text/plain; charset=utf-8")


@router.post("/storage/postgres/schema.sql/write", response_model=ApiResponse)
def postgres_schema_write_endpoint(api_request: Request) -> ApiResponse:
    written = write_postgres_schema_file()
    _audit(
        api_request,
        "storage",
        "write_postgres_schema",
        resource_type="storage_schema",
        resource_id=written["schema_hash"],
        detail=written,
    )
    return ApiResponse(data=written)


@router.get("/storage/migration/sqlite/summary", response_model=ApiResponse)
def sqlite_migration_summary_endpoint() -> ApiResponse:
    return ApiResponse(data=sqlite_migration_summary())


@router.get("/storage/migration/sqlite/export.zip")
def sqlite_migration_export_endpoint(api_request: Request) -> Response:
    content = build_sqlite_migration_bundle()
    _audit(
        api_request,
        "storage",
        "sqlite_migration_export",
        resource_type="storage_migration",
        resource_id="sqlite_to_postgres",
        detail={"bytes": len(content)},
    )
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="sqlite_to_postgres_migration_bundle.zip"'},
    )


@router.get("/storage/migration/sqlite/parity", response_model=ApiResponse)
def sqlite_postgres_parity_endpoint() -> ApiResponse:
    return ApiResponse(data=sqlite_postgres_parity_report())


@router.post("/storage/migration/sqlite/import", response_model=ApiResponse)
def sqlite_postgres_import_endpoint(api_request: Request, dry_run: bool = True) -> ApiResponse:
    result = import_sqlite_to_postgres(dry_run=dry_run)
    _audit(
        api_request,
        "storage",
        "sqlite_to_postgres_import",
        outcome=result.get("status", "unknown"),
        resource_type="storage_migration",
        resource_id="sqlite_to_postgres",
        detail={"dry_run": dry_run, "status": result.get("status"), "total_records": result.get("total_records")},
    )
    return ApiResponse(data=result)


@router.get("/governance/attestation", response_model=ApiResponse)
def governance_attestation_endpoint(api_request: Request) -> ApiResponse:
    return ApiResponse(data=build_governance_attestation(api_request.app.openapi()))


@router.get("/governance/attestation/export.zip")
def governance_attestation_export_endpoint(api_request: Request) -> Response:
    content = build_governance_attestation_bundle(api_request.app.openapi())
    verification = verify_governance_attestation_bundle(content)
    attestation_hash = verification.get("attestation_hash") or "unknown"
    archive_artifact_bundle(
        content,
        action="governance_attestation_export",
        resource_type="governance_attestation",
        resource_id=str(attestation_hash),
        filename=f"governance_attestation_{attestation_hash[:12]}.zip",
        metadata={"attestation_hash": attestation_hash, "verification_status": verification.get("status")},
    )
    _audit(
        api_request,
        "governance",
        "governance_attestation_export",
        outcome=verification.get("status", "unknown"),
        resource_type="governance_attestation",
        resource_id=str(attestation_hash),
        detail={"bytes": len(content), "verification_status": verification.get("status")},
    )
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="governance_attestation_{attestation_hash[:12]}.zip"'},
    )


@router.get("/governance/attestation/verify", response_model=ApiResponse)
def governance_attestation_verify_endpoint(api_request: Request) -> ApiResponse:
    content = build_governance_attestation_bundle(api_request.app.openapi())
    return ApiResponse(data=verify_governance_attestation_bundle(content))


@router.get("/security/status", response_model=ApiResponse)
def security_status_endpoint() -> ApiResponse:
    settings = get_settings()
    return ApiResponse(
        data={
            "auth_enabled": settings.auth_enabled,
            "rbac_enabled": bool(settings.api_key_roles),
            "configured_keys": len(settings.api_keys),
            "configured_role_bindings": len(settings.api_key_roles),
            "roles": ["viewer", "operator", "admin"],
            "api_key_header": "X-API-Key",
            "bearer_auth_supported": True,
            "rate_limit_per_minute": settings.rate_limit_per_minute,
            "artifact_signing_enabled": settings.artifact_signing_enabled,
            "artifact_signing_algorithm": "HMAC-SHA256" if settings.artifact_signing_enabled else None,
            "artifact_signing_key_id": settings.artifact_signing_key_id if settings.artifact_signing_enabled else None,
            "artifact_asymmetric_signing_enabled": settings.artifact_asymmetric_signing_enabled,
            "artifact_asymmetric_verification_enabled": settings.artifact_asymmetric_verification_enabled,
            "artifact_ed25519_key_id": settings.artifact_ed25519_key_id
            if (settings.artifact_asymmetric_signing_enabled or settings.artifact_asymmetric_verification_enabled)
            else None,
            "signing": signing_status(),
            "public_paths": [
                "/api/v1/health",
                "/api/v1/health/live",
                "/api/v1/health/ready",
                "/api/v1/metrics",
                "/api/v1/metrics/prometheus",
                "/api/v1/settings",
                "/api/v1/security/status",
                "/docs",
                "/openapi.json",
                "/redoc",
            ],
            "role_policy": {
                "viewer": "Read non-public design, run, job, data, and RAG resources.",
                "operator": "Viewer plus design/report/job operations and bundle downloads.",
                "admin": "Operator plus data refresh/import/lock, audit, artifact archive, and security operations.",
            },
        }
    )


@router.get("/metrics", response_model=ApiResponse)
def metrics_endpoint() -> ApiResponse:
    provenance = data_provenance_audit()
    return ApiResponse(
        data={
            **metrics_snapshot(),
            "stores": _store_metrics(),
            "rag": rag_status(),
            "structured": structured_status(),
            "storage": storage_status(),
            "audit": audit_summary(),
            "artifact_archive": archive_summary(),
            "agent_memory": agent_memory_summary(),
            "data_provenance": {
                "status": provenance["status"],
                "manifest_hash": provenance["manifest_hash"],
                "failed_checks": sum(1 for check in provenance["checks"] if check["result"] != "pass"),
                "lock_status": (provenance.get("lockfile") or {}).get("status"),
            },
        }
    )


@router.get("/metrics/prometheus")
def metrics_prometheus_endpoint() -> Response:
    return Response(content=metrics_prometheus(_flat_store_metrics()), media_type="text/plain; version=0.0.4; charset=utf-8")


@router.get("/optimizer/benchmark", response_model=ApiResponse)
def optimizer_benchmark_endpoint() -> ApiResponse:
    return ApiResponse(data=evaluate_optimizer_benchmark())


@router.get("/optimizer/benchmark/export.zip")
def optimizer_benchmark_bundle_export(api_request: Request) -> Response:
    content = build_optimizer_benchmark_bundle()
    verification = verify_optimizer_benchmark_bundle(content)
    resource_id = verification.get("cases_hash") or "optimizer_benchmark"
    archive = _archive_bundle(
        content,
        action="optimizer_benchmark_bundle_export",
        resource_type="optimizer_benchmark",
        resource_id=str(resource_id),
        filename=f"{str(resource_id)[:16]}_optimizer_benchmark_bundle.zip",
        metadata={
            "verification_status": verification.get("status"),
            "semantic_status": verification.get("semantic_status"),
            "benchmark_status": verification.get("benchmark_status"),
            "diagnostics_status": verification.get("diagnostics_status"),
            "case_count": verification.get("case_count"),
            "cases_hash": verification.get("cases_hash"),
            "structured_manifest_hash": verification.get("structured_manifest_hash"),
        },
    )
    _audit(
        api_request,
        "optimizer_export",
        "optimizer_benchmark_bundle_export",
        outcome=verification.get("status", "unknown"),
        resource_type="optimizer_benchmark",
        resource_id=str(resource_id),
        detail={"bytes": len(content), "archive_artifact_id": archive["artifact_id"], "verification_status": verification.get("status")},
    )
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{str(resource_id)[:16]}_optimizer_benchmark_bundle.zip"'},
    )


@router.get("/optimizer/benchmark/export/verify", response_model=ApiResponse)
def optimizer_benchmark_bundle_verify(api_request: Request) -> ApiResponse:
    verification = verify_optimizer_benchmark_bundle(build_optimizer_benchmark_bundle())
    _audit(
        api_request,
        "artifact_verify",
        "optimizer_benchmark_bundle_verify",
        outcome=verification.get("status", "unknown"),
        resource_type="optimizer_benchmark",
        resource_id=str(verification.get("cases_hash") or "optimizer_benchmark"),
        detail={"status": verification.get("status"), "semantic_status": verification.get("semantic_status"), "file_count": verification.get("file_count")},
    )
    return ApiResponse(data=verification)


@router.get("/optimizer/diagnostics", response_model=ApiResponse)
def optimizer_diagnostics_endpoint() -> ApiResponse:
    return ApiResponse(data=optimizer_diagnostics())


@router.get("/optimizer/stress", response_model=ApiResponse)
def optimizer_stress_endpoint() -> ApiResponse:
    return ApiResponse(data=optimizer_stress_gate())


@router.get("/optimizer/rna-folding/status", response_model=ApiResponse)
def optimizer_rna_folding_status_endpoint() -> ApiResponse:
    return ApiResponse(data=rna_folding_status())


@router.post("/optimizer/rna-folding/evaluate", response_model=ApiResponse)
def optimizer_rna_folding_evaluate_endpoint(request: ScoreRequest) -> ApiResponse:
    return ApiResponse(data=evaluate_rna_folding(request.cds))


@router.get("/optimizer/benchmark/cases", response_model=ApiResponse)
def optimizer_benchmark_cases_endpoint() -> ApiResponse:
    return ApiResponse(data=optimizer_benchmark_cases())


@router.get("/audit/events", response_model=ApiResponse)
def audit_events_endpoint(
    limit: int = 50,
    event_type: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> ApiResponse:
    return ApiResponse(
        data=list_audit_events(
            limit=limit,
            event_type=event_type,
            resource_type=resource_type,
            resource_id=resource_id,
        )
    )


@router.get("/audit/summary", response_model=ApiResponse)
def audit_summary_endpoint() -> ApiResponse:
    return ApiResponse(data=audit_summary())


@router.get("/artifacts", response_model=ApiResponse)
def archived_artifacts_endpoint(
    limit: int = 50,
    resource_type: str | None = None,
    resource_id: str | None = None,
    artifact_type: str | None = None,
) -> ApiResponse:
    return ApiResponse(
        data=list_archived_artifacts(
            limit=limit,
            resource_type=resource_type,
            resource_id=resource_id,
            artifact_type=artifact_type,
        )
    )


@router.get("/artifacts/summary", response_model=ApiResponse)
def archived_artifacts_summary_endpoint() -> ApiResponse:
    return ApiResponse(data=archive_summary())


@router.get("/artifacts/qc-bundles/semantic-summary", response_model=ApiResponse)
def qc_bundle_archive_semantic_summary_endpoint(limit: int = 20, verify_files: bool = True) -> ApiResponse:
    return ApiResponse(data=qc_bundle_archive_semantic_summary(limit=limit, verify_files=verify_files))


@router.get("/artifacts/structured-imports/semantic-summary", response_model=ApiResponse)
def structured_import_archive_semantic_summary_endpoint(limit: int = 20, verify_files: bool = True) -> ApiResponse:
    return ApiResponse(data=structured_import_archive_summary(limit=limit, verify_files=verify_files))


@router.get("/artifacts/data-releases/semantic-summary", response_model=ApiResponse)
def data_release_archive_semantic_summary_endpoint(limit: int = 20, verify_files: bool = True) -> ApiResponse:
    return ApiResponse(data=data_release_archive_summary(limit=limit, verify_files=verify_files))


@router.get("/artifacts/data-refresh-plans/semantic-summary", response_model=ApiResponse)
def data_refresh_plan_archive_semantic_summary_endpoint(limit: int = 20, verify_files: bool = True) -> ApiResponse:
    return ApiResponse(data=data_refresh_plan_archive_summary(limit=limit, verify_files=verify_files))


@router.get("/artifacts/rag-evaluations/semantic-summary", response_model=ApiResponse)
def rag_evaluation_archive_semantic_summary_endpoint(limit: int = 20, verify_files: bool = True) -> ApiResponse:
    return ApiResponse(data=rag_evaluation_archive_summary(limit=limit, verify_files=verify_files))


@router.get("/artifacts/rag-regressions/semantic-summary", response_model=ApiResponse)
def rag_regression_archive_semantic_summary_endpoint(limit: int = 20, verify_files: bool = True) -> ApiResponse:
    return ApiResponse(data=rag_regression_archive_summary(limit=limit, verify_files=verify_files))


@router.get("/artifacts/rag-vector-indexes/semantic-summary", response_model=ApiResponse)
def rag_vector_index_archive_semantic_summary_endpoint(limit: int = 20, verify_files: bool = True) -> ApiResponse:
    return ApiResponse(data=rag_vector_index_archive_summary(limit=limit, verify_files=verify_files))


@router.get("/artifacts/optimizer-benchmarks/semantic-summary", response_model=ApiResponse)
def optimizer_benchmark_archive_semantic_summary_endpoint(limit: int = 20, verify_files: bool = True) -> ApiResponse:
    return ApiResponse(data=optimizer_benchmark_archive_summary(limit=limit, verify_files=verify_files))


@router.get("/artifacts/workflow-traces/semantic-summary", response_model=ApiResponse)
def workflow_trace_archive_semantic_summary_endpoint(limit: int = 20, verify_files: bool = True) -> ApiResponse:
    return ApiResponse(data=workflow_trace_archive_summary(limit=limit, verify_files=verify_files))


@router.get("/artifacts/retention/plan", response_model=ApiResponse)
def artifact_retention_plan_endpoint(
    retention_days: int | None = None,
    keep_min: int | None = None,
    resource_type: str | None = None,
    artifact_type: str | None = None,
) -> ApiResponse:
    return ApiResponse(
        data=plan_artifact_retention(
            retention_days=retention_days,
            keep_min=keep_min,
            resource_type=resource_type,
            artifact_type=artifact_type,
        )
    )


@router.get("/artifacts/object-store/status", response_model=ApiResponse)
def artifact_object_store_status_endpoint() -> ApiResponse:
    return ApiResponse(data=artifact_object_store_status())


@router.get("/artifacts/object-store/mirror/plan", response_model=ApiResponse)
def artifact_object_store_mirror_plan_endpoint(
    limit: int = 100,
    resource_type: str | None = None,
    artifact_type: str | None = None,
) -> ApiResponse:
    return ApiResponse(
        data=plan_artifact_object_store_mirror(
            limit=limit,
            resource_type=resource_type,
            artifact_type=artifact_type,
        )
    )


@router.post("/artifacts/object-store/mirror", response_model=ApiResponse)
def artifact_object_store_mirror_endpoint(
    api_request: Request,
    dry_run: bool = True,
    limit: int = 100,
    resource_type: str | None = None,
    artifact_type: str | None = None,
) -> ApiResponse:
    result = mirror_artifact_archive(
        dry_run=dry_run,
        limit=limit,
        resource_type=resource_type,
        artifact_type=artifact_type,
    )
    _audit(
        api_request,
        "artifact_archive",
        "artifact_object_store_mirror",
        outcome=result.get("status", "unknown"),
        resource_type="artifact_archive",
        resource_id="object_store",
        detail={
            "dry_run": dry_run,
            "candidate_count": result.get("candidate_count"),
            "mirrored_count": result.get("mirrored_count"),
            "artifact_type": artifact_type,
            "resource_type": resource_type,
        },
    )
    return ApiResponse(data=result)


@router.post("/artifacts/retention/apply", response_model=ApiResponse)
def artifact_retention_apply_endpoint(
    api_request: Request,
    dry_run: bool = True,
    retention_days: int | None = None,
    keep_min: int | None = None,
    resource_type: str | None = None,
    artifact_type: str | None = None,
) -> ApiResponse:
    result = apply_artifact_retention(
        dry_run=dry_run,
        retention_days=retention_days,
        keep_min=keep_min,
        resource_type=resource_type,
        artifact_type=artifact_type,
    )
    _audit(
        api_request,
        "artifact_archive",
        "artifact_retention_apply",
        outcome=result.get("status", "unknown"),
        resource_type="artifact_archive",
        resource_id="retention",
        detail={
            "dry_run": dry_run,
            "candidate_count": result.get("candidate_count"),
            "deleted_count": result.get("deleted_count"),
            "retention_days": result.get("retention_days"),
            "keep_min": result.get("keep_min"),
        },
    )
    return ApiResponse(data=result)


@router.get("/artifacts/ledger", response_model=ApiResponse)
def artifact_ledger_endpoint(limit: int = 50) -> ApiResponse:
    return ApiResponse(data=artifact_ledger(limit=limit))


@router.get("/artifacts/ledger/verify", response_model=ApiResponse)
def artifact_ledger_verify_endpoint() -> ApiResponse:
    return ApiResponse(data=verify_artifact_ledger())


@router.post("/artifacts/ledger/backfill", response_model=ApiResponse)
def artifact_ledger_backfill_endpoint(api_request: Request, dry_run: bool = True) -> ApiResponse:
    result = backfill_artifact_ledger(dry_run=dry_run)
    _audit(
        api_request,
        "artifact_archive",
        "ledger_backfill",
        outcome=result.get("status", "unknown"),
        resource_type="artifact_ledger",
        resource_id="artifact_archive_ledger",
        detail={
            "dry_run": dry_run,
            "candidate_count": result.get("candidate_count"),
            "backfilled_count": result.get("backfilled_count"),
            "ledger_status": (result.get("ledger") or {}).get("status"),
        },
    )
    return ApiResponse(data=result)


@router.get("/artifacts/{artifact_id}", response_model=ApiResponse)
def archived_artifact_endpoint(artifact_id: str) -> ApiResponse:
    artifact = get_archived_artifact(artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Archived artifact not found.")
    return ApiResponse(data=artifact)


@router.get("/artifacts/{artifact_id}/download")
def archived_artifact_download_endpoint(artifact_id: str) -> Response:
    artifact = get_archived_artifact(artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Archived artifact not found.")
    try:
        content = read_archived_artifact(artifact_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Archived artifact file not found.") from exc
    headers = {"Content-Disposition": f'attachment; filename="{artifact["filename"]}"'}
    return Response(content=content, media_type=artifact["media_type"], headers=headers)


@router.get("/artifacts/{artifact_id}/verify", response_model=ApiResponse)
def archived_artifact_verify_endpoint(artifact_id: str) -> ApiResponse:
    try:
        return ApiResponse(data=verify_archived_artifact(artifact_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Archived artifact not found.") from exc


@router.post("/score", response_model=ApiResponse)
def score(request: ScoreRequest) -> ApiResponse:
    try:
        return ApiResponse(data=score_cds(request.cds, _score_config(request.score_settings)))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/validate-cds", response_model=ApiResponse)
def validate_cds_endpoint(request: ScoreRequest) -> ApiResponse:
    try:
        return ApiResponse(data=validate_cds(request.cds, _score_config(request.score_settings)))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/resolve-gene", response_model=ApiResponse)
def resolve_gene_endpoint(request: GeneRequest) -> ApiResponse:
    try:
        return ApiResponse(data=resolve_gene(request.gene, request.species))
    except EnsemblClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/fetch-cds", response_model=ApiResponse)
def fetch_cds_endpoint(request: GeneRequest) -> ApiResponse:
    try:
        return ApiResponse(data=fetch_canonical_cds(request.gene, request.species))
    except EnsemblClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/evidence/search", response_model=ApiResponse)
def evidence_search(request: EvidenceRequest) -> ApiResponse:
    return ApiResponse(data=search_evidence(request.query, request.filters, request.limit))


@router.get("/rag/status", response_model=ApiResponse)
def rag_status_endpoint() -> ApiResponse:
    return ApiResponse(data=rag_status())


@router.get("/rag/diagnostics", response_model=ApiResponse)
def rag_diagnostics_endpoint() -> ApiResponse:
    return ApiResponse(data=rag_diagnostics())


@router.get("/rag/embedding/status", response_model=ApiResponse)
def rag_embedding_status_endpoint() -> ApiResponse:
    return ApiResponse(data=rag_embedding_status())


@router.get("/rag/vector-store/status", response_model=ApiResponse)
def rag_vector_store_status_endpoint() -> ApiResponse:
    return ApiResponse(data=rag_vector_store_status(load_rag_index()))


@router.get("/rag/vector-store/import/plan", response_model=ApiResponse)
def rag_vector_store_import_plan_endpoint(target_backend: str | None = None) -> ApiResponse:
    return ApiResponse(data=rag_vector_store_import_plan(target_backend))


@router.post("/rag/vector-store/import", response_model=ApiResponse)
def rag_vector_store_import_endpoint(api_request: Request, target_backend: str | None = None, dry_run: bool = True) -> ApiResponse:
    result = import_rag_vector_store(target_backend=target_backend, dry_run=dry_run)
    _audit(
        api_request,
        "rag_migration",
        "rag_vector_store_import",
        outcome=result.get("status", "unknown"),
        resource_type="rag_vector_store",
        resource_id=str(result.get("target_backend") or "unknown"),
        detail={
            "dry_run": dry_run,
            "target_backend": result.get("target_backend"),
            "status": result.get("status"),
            "records": (result.get("source") or {}).get("records"),
        },
    )
    return ApiResponse(data=result)


@router.get("/rag/vector-store/parity", response_model=ApiResponse)
def rag_vector_store_parity_endpoint(target_backend: str | None = None) -> ApiResponse:
    return ApiResponse(data=rag_vector_store_parity_report(target_backend))


@router.get("/rag/documents", response_model=ApiResponse)
def rag_documents_endpoint() -> ApiResponse:
    return ApiResponse(data=list_ingested_documents())


@router.post("/rag/ingest-local", response_model=ApiResponse)
def rag_ingest_local_endpoint(request: IngestLocalDocumentRequest) -> ApiResponse:
    try:
        result = ingest_local_document(
            request.source_path,
            title=request.title,
            collection=request.collection,
            source=request.source,
            source_url=request.source_url,
            evidence_class=request.evidence_class,
            confidence=request.confidence,
            species=request.species,
            topics=request.topics or None,
            regions=request.regions or None,
            cell_types=request.cell_types or None,
            modalities=request.modalities or None,
            copy_source=request.copy_source,
        )
        if request.rebuild_index:
            result["index"] = rebuild_rag_index(persist=True)
        return ApiResponse(data=result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/rag/rebuild", response_model=ApiResponse)
def rag_rebuild_endpoint() -> ApiResponse:
    return ApiResponse(data=rebuild_rag_index(persist=True))


@router.post("/rag/search", response_model=ApiResponse)
def rag_search_endpoint(request: RagSearchRequest) -> ApiResponse:
    return ApiResponse(data=rag_search(request.query, request.filters, request.limit))


@router.post("/rag/evaluate", response_model=ApiResponse)
def rag_evaluate_endpoint(request: RagSearchRequest) -> ApiResponse:
    return ApiResponse(data=evaluate_rag_query(request.query, request.filters, request.limit))


@router.post("/rag/evaluate/export.zip")
def rag_evaluation_bundle_export(request: RagSearchRequest, api_request: Request) -> Response:
    content = build_rag_evaluation_bundle(request.model_dump())
    verification = verify_rag_evaluation_bundle(content)
    resource_id = verification.get("query_fingerprint") or "rag_evaluation"
    archive = _archive_bundle(
        content,
        action="rag_evaluation_bundle_export",
        resource_type="rag_evaluation",
        resource_id=str(resource_id),
        filename=f"{resource_id}_rag_evaluation_bundle.zip",
        metadata={
            "verification_status": verification.get("status"),
            "semantic_status": verification.get("semantic_status"),
            "query_fingerprint": verification.get("query_fingerprint"),
            "result_count": verification.get("result_count"),
            "structured_manifest_hash": verification.get("structured_manifest_hash"),
        },
    )
    _audit(
        api_request,
        "rag_export",
        "rag_evaluation_bundle_export",
        outcome=verification.get("status", "unknown"),
        resource_type="rag_evaluation",
        resource_id=str(resource_id),
        detail={"bytes": len(content), "archive_artifact_id": archive["artifact_id"], "verification_status": verification.get("status")},
    )
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{resource_id}_rag_evaluation_bundle.zip"'},
    )


@router.post("/rag/evaluate/export/verify", response_model=ApiResponse)
def rag_evaluation_bundle_verify(request: RagSearchRequest, api_request: Request) -> ApiResponse:
    content = build_rag_evaluation_bundle(request.model_dump())
    verification = verify_rag_evaluation_bundle(content)
    _audit(
        api_request,
        "artifact_verify",
        "rag_evaluation_bundle_verify",
        outcome=verification.get("status", "unknown"),
        resource_type="rag_evaluation",
        resource_id=str(verification.get("query_fingerprint") or "rag_evaluation"),
        detail={"status": verification.get("status"), "semantic_status": verification.get("semantic_status"), "file_count": verification.get("file_count")},
    )
    return ApiResponse(data=verification)


@router.get("/rag/vector-index/export.zip")
def rag_vector_index_bundle_export(api_request: Request) -> Response:
    content = build_rag_vector_index_bundle()
    verification = verify_rag_vector_index_bundle(content)
    resource_id = str(verification.get("manifest_hash") or verification.get("structured_manifest_hash") or "rag_vector_index")
    filename = f"{resource_id[:16]}_rag_vector_index_bundle.zip"
    archive = _archive_bundle(
        content,
        action="rag_vector_index_bundle_export",
        resource_type="rag_vector_index",
        resource_id=resource_id,
        filename=filename,
        metadata={
            "verification_status": verification.get("status"),
            "semantic_status": verification.get("semantic_status"),
            "chunk_count": verification.get("chunk_count"),
            "embedding_model": verification.get("embedding_model"),
            "recommended_backend": verification.get("recommended_backend"),
            "structured_manifest_hash": verification.get("structured_manifest_hash"),
        },
    )
    _audit(
        api_request,
        "rag_export",
        "rag_vector_index_bundle_export",
        outcome=verification.get("status", "unknown"),
        resource_type="rag_vector_index",
        resource_id=resource_id,
        detail={
            "bytes": len(content),
            "archive_artifact_id": archive["artifact_id"],
            "verification_status": verification.get("status"),
            "semantic_status": verification.get("semantic_status"),
            "chunk_count": verification.get("chunk_count"),
        },
    )
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/rag/vector-index/export/verify", response_model=ApiResponse)
def rag_vector_index_bundle_verify(api_request: Request) -> ApiResponse:
    content = build_rag_vector_index_bundle()
    verification = verify_rag_vector_index_bundle(content)
    _audit(
        api_request,
        "artifact_verify",
        "rag_vector_index_bundle_verify",
        outcome=verification.get("status", "unknown"),
        resource_type="rag_vector_index",
        resource_id=str(verification.get("manifest_hash") or verification.get("structured_manifest_hash") or "rag_vector_index"),
        detail={
            "status": verification.get("status"),
            "semantic_status": verification.get("semantic_status"),
            "chunk_count": verification.get("chunk_count"),
            "recommended_backend": verification.get("recommended_backend"),
        },
    )
    return ApiResponse(data=verification)


@router.get("/rag/regression", response_model=ApiResponse)
def rag_regression_endpoint() -> ApiResponse:
    return ApiResponse(data=evaluate_rag_regression())


@router.get("/rag/regression/export.zip")
def rag_regression_bundle_export(api_request: Request) -> Response:
    content = build_rag_regression_bundle()
    verification = verify_rag_regression_bundle(content)
    resource_id = str(verification.get("cases_hash") or "rag_regression")
    filename = f"{resource_id[:16]}_rag_regression_bundle.zip"
    archive = _archive_bundle(
        content,
        action="rag_regression_bundle_export",
        resource_type="rag_regression",
        resource_id=resource_id,
        filename=filename,
        metadata={
            "verification_status": verification.get("status"),
            "semantic_status": verification.get("semantic_status"),
            "regression_status": verification.get("regression_status"),
            "case_count": verification.get("case_count"),
            "cases_hash": verification.get("cases_hash"),
            "weak_case_count": verification.get("weak_case_count"),
            "structured_manifest_hash": verification.get("structured_manifest_hash"),
        },
    )
    _audit(
        api_request,
        "rag_export",
        "rag_regression_bundle_export",
        outcome=verification.get("status", "unknown"),
        resource_type="rag_regression",
        resource_id=resource_id,
        detail={
            "bytes": len(content),
            "archive_artifact_id": archive["artifact_id"],
            "verification_status": verification.get("status"),
            "semantic_status": verification.get("semantic_status"),
            "case_count": verification.get("case_count"),
        },
    )
    return Response(content=content, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/rag/regression/export/verify", response_model=ApiResponse)
def rag_regression_bundle_verify(api_request: Request) -> ApiResponse:
    verification = verify_rag_regression_bundle(build_rag_regression_bundle())
    _audit(
        api_request,
        "artifact_verify",
        "rag_regression_bundle_verify",
        outcome=verification.get("status", "unknown"),
        resource_type="rag_regression",
        resource_id=str(verification.get("cases_hash") or "rag_regression"),
        detail={
            "status": verification.get("status"),
            "semantic_status": verification.get("semantic_status"),
            "case_count": verification.get("case_count"),
            "cases_hash": verification.get("cases_hash"),
        },
    )
    return ApiResponse(data=verification)


@router.get("/rag/regression/cases", response_model=ApiResponse)
def rag_regression_cases_endpoint() -> ApiResponse:
    return ApiResponse(data=rag_regression_cases())


@router.get("/runs", response_model=ApiResponse)
def runs_endpoint(limit: int = 25) -> ApiResponse:
    return ApiResponse(data=list_runs(max(1, min(limit, 100))))


@router.get("/runs/{run_id}", response_model=ApiResponse)
def run_endpoint(run_id: str) -> ApiResponse:
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    return ApiResponse(data=run)


@router.get("/runs/{run_id}/export.zip")
def run_export_bundle_endpoint(run_id: str, api_request: Request) -> Response:
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    content = build_run_export_bundle(run)
    archive = _archive_bundle(
        content,
        action="run_export_bundle",
        resource_type="run",
        resource_id=run_id,
        filename=f"{run_id}_audit_bundle.zip",
        metadata={"run_type": run.get("run_type")},
    )
    _audit(
        api_request,
        "artifact_export",
        "run_export_bundle",
        resource_type="run",
        resource_id=run_id,
        detail={
            "bytes": len(content),
            "run_type": run.get("run_type"),
            "archive_artifact_id": archive["artifact_id"],
            "archive_status": archive["archive_status"],
            "manifest_hash": archive.get("manifest_hash"),
        },
    )
    headers = {"Content-Disposition": f'attachment; filename="{run_id}_audit_bundle.zip"'}
    return Response(content=content, media_type="application/zip", headers=headers)


@router.get("/runs/{run_id}/export/verify", response_model=ApiResponse)
def run_export_bundle_verify_endpoint(run_id: str, api_request: Request) -> ApiResponse:
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    verification = verify_artifact_bundle(build_run_export_bundle(run))
    _audit(
        api_request,
        "artifact_verify",
        "run_export_verify",
        outcome=verification.get("status", "unknown"),
        resource_type="run",
        resource_id=run_id,
        detail={
            "status": verification.get("status"),
            "file_count": verification.get("file_count"),
            "checked_files": verification.get("checked_files"),
            "manifest_hash": verification.get("manifest_hash"),
        },
    )
    return ApiResponse(data=verification)


@router.get("/runs/{run_id}/workflow/export.zip")
def run_workflow_trace_bundle_endpoint(run_id: str, api_request: Request) -> Response:
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    content = build_workflow_trace_bundle(run)
    verification = verify_workflow_trace_bundle(content)
    archive = _archive_bundle(
        content,
        action="workflow_trace_bundle_export",
        resource_type="workflow_trace",
        resource_id=run_id,
        filename=f"{run_id}_workflow_trace_bundle.zip",
        metadata={
            "verification_status": verification.get("status"),
            "semantic_status": verification.get("semantic_status"),
            "workflow_id": verification.get("workflow_id"),
            "trace_hash": verification.get("trace_hash"),
        },
    )
    _audit(
        api_request,
        "workflow_export",
        "workflow_trace_bundle_export",
        outcome=verification.get("status", "unknown"),
        resource_type="workflow_trace",
        resource_id=run_id,
        detail={
            "bytes": len(content),
            "archive_artifact_id": archive["artifact_id"],
            "verification_status": verification.get("status"),
            "semantic_status": verification.get("semantic_status"),
        },
    )
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{run_id}_workflow_trace_bundle.zip"'},
    )


@router.get("/runs/{run_id}/workflow/export/verify", response_model=ApiResponse)
def run_workflow_trace_bundle_verify_endpoint(run_id: str, api_request: Request) -> ApiResponse:
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    verification = verify_workflow_trace_bundle(build_workflow_trace_bundle(run))
    _audit(
        api_request,
        "artifact_verify",
        "workflow_trace_bundle_verify",
        outcome=verification.get("status", "unknown"),
        resource_type="workflow_trace",
        resource_id=run_id,
        detail={
            "status": verification.get("status"),
            "semantic_status": verification.get("semantic_status"),
            "trace_step_count": verification.get("trace_step_count"),
        },
    )
    return ApiResponse(data=verification)


@router.get("/runs/{run_id}/artifacts/{artifact_type}")
def run_artifact_endpoint(run_id: str, artifact_type: str) -> Response:
    artifact = get_run_artifact(run_id, artifact_type)
    if not artifact:
        raise HTTPException(status_code=404, detail="Run artifact not found.")
    return Response(content=artifact["content"], media_type=artifact["media_type"])


@router.get("/agent-memory/summary", response_model=ApiResponse)
def agent_memory_summary_endpoint() -> ApiResponse:
    return ApiResponse(data=agent_memory_summary())


@router.get("/agent-memory", response_model=ApiResponse)
def agent_memory_endpoint(
    limit: int = 25,
    gene: str | None = None,
    brain_region: str | None = None,
    cell_type: str | None = None,
) -> ApiResponse:
    return ApiResponse(data=list_agent_memory(limit=limit, gene=gene, brain_region=brain_region, cell_type=cell_type))


@router.get("/agent-memory/{run_id}", response_model=ApiResponse)
def agent_memory_run_endpoint(run_id: str) -> ApiResponse:
    memory = get_agent_memory(run_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Agent memory not found.")
    return ApiResponse(data=memory)


@router.get("/jobs", response_model=ApiResponse)
def jobs_endpoint(limit: int = 25, status: str | None = None) -> ApiResponse:
    normalized_status = status if status in {None, "queued", "running", "succeeded", "failed"} else None
    return ApiResponse(data=list_jobs(limit=max(1, min(limit, 200)), status=normalized_status))


@router.post("/jobs/design-from-gene", response_model=ApiResponse)
def enqueue_gene_design_job(request: DesignFromGeneRequest, background_tasks: BackgroundTasks, api_request: Request) -> ApiResponse:
    payload = request.model_dump()
    job = create_job("gene_design", payload)
    background_tasks.add_task(_execute_gene_design_job, job["job_id"], payload)
    _audit(
        api_request,
        "job",
        "enqueue_gene_design",
        resource_type="job",
        resource_id=job["job_id"],
        detail={"job_type": job["job_type"], "gene": payload.get("gene")},
    )
    return ApiResponse(data=job)


@router.post("/jobs/batch-design-from-genes", response_model=ApiResponse)
def enqueue_batch_gene_design_job(request: BatchDesignFromGenesRequest, background_tasks: BackgroundTasks, api_request: Request) -> ApiResponse:
    payload = request.model_dump()
    job = create_job("batch_gene_design", payload)
    background_tasks.add_task(_execute_batch_gene_design_job, job["job_id"], payload)
    _audit(
        api_request,
        "job",
        "enqueue_batch_gene_design",
        resource_type="job",
        resource_id=job["job_id"],
        detail={"job_type": job["job_type"], "genes": payload.get("genes", [])},
    )
    return ApiResponse(data=job)


@router.post("/jobs/data-refresh", response_model=ApiResponse)
def enqueue_data_refresh_job(request: DataRefreshRequest, background_tasks: BackgroundTasks, api_request: Request) -> ApiResponse:
    payload = request.model_dump()
    job = create_job("data_refresh", payload)
    background_tasks.add_task(_execute_data_refresh_job, job["job_id"], payload)
    _audit(
        api_request,
        "job",
        "enqueue_data_refresh",
        resource_type="job",
        resource_id=job["job_id"],
        detail={"job_type": job["job_type"], "dry_run": payload.get("dry_run")},
    )
    return ApiResponse(data=job)


@router.get("/jobs/{job_id}/export.zip")
def job_export_bundle_endpoint(job_id: str, api_request: Request) -> Response:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    content = build_job_export_bundle(job)
    archive = _archive_bundle(
        content,
        action="job_export_bundle",
        resource_type="job",
        resource_id=job_id,
        filename=f"{job_id}_job_bundle.zip",
        metadata={"job_type": job.get("job_type"), "status": job.get("status")},
    )
    _audit(
        api_request,
        "artifact_export",
        "job_export_bundle",
        resource_type="job",
        resource_id=job_id,
        detail={
            "bytes": len(content),
            "job_type": job.get("job_type"),
            "status": job.get("status"),
            "archive_artifact_id": archive["artifact_id"],
            "archive_status": archive["archive_status"],
            "manifest_hash": archive.get("manifest_hash"),
        },
    )
    headers = {"Content-Disposition": f'attachment; filename="{job_id}_job_bundle.zip"'}
    return Response(content=content, media_type="application/zip", headers=headers)


@router.get("/jobs/{job_id}/export/verify", response_model=ApiResponse)
def job_export_bundle_verify_endpoint(job_id: str, api_request: Request) -> ApiResponse:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    verification = verify_artifact_bundle(build_job_export_bundle(job))
    _audit(
        api_request,
        "artifact_verify",
        "job_export_verify",
        outcome=verification.get("status", "unknown"),
        resource_type="job",
        resource_id=job_id,
        detail={
            "status": verification.get("status"),
            "file_count": verification.get("file_count"),
            "checked_files": verification.get("checked_files"),
            "manifest_hash": verification.get("manifest_hash"),
        },
    )
    return ApiResponse(data=verification)


@router.get("/jobs/{job_id}", response_model=ApiResponse)
def job_endpoint(job_id: str) -> ApiResponse:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return ApiResponse(data=job)


@router.get("/structured/status", response_model=ApiResponse)
def structured_status_endpoint() -> ApiResponse:
    return ApiResponse(data=structured_status())


@router.get("/structured/manifest", response_model=ApiResponse)
def structured_manifest_endpoint() -> ApiResponse:
    return ApiResponse(data=structured_manifest())


@router.get("/structured/validate", response_model=ApiResponse)
def structured_validate_endpoint() -> ApiResponse:
    return ApiResponse(data=validate_structured_records())


@router.post("/structured/import/preview", response_model=ApiResponse)
def structured_import_preview_endpoint(request: StructuredImportRequest) -> ApiResponse:
    try:
        return ApiResponse(data=preview_structured_import(request.source_path))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/data/catalog", response_model=ApiResponse)
def data_catalog_endpoint() -> ApiResponse:
    return ApiResponse(data=data_catalog())


@router.get("/data/coverage", response_model=ApiResponse)
def data_coverage_endpoint() -> ApiResponse:
    return ApiResponse(data=structured_coverage_matrix())


@router.get("/data/quality", response_model=ApiResponse)
def data_quality_endpoint() -> ApiResponse:
    return ApiResponse(data=structured_quality_gate())


@router.get("/data/provenance", response_model=ApiResponse)
def data_provenance_endpoint() -> ApiResponse:
    return ApiResponse(data=data_provenance_audit())


@router.post("/data/provenance/baseline", response_model=ApiResponse)
def data_provenance_baseline_endpoint(api_request: Request) -> ApiResponse:
    baseline = record_data_baseline_event()
    lockfile = write_data_lockfile()
    provenance = data_provenance_audit()
    _audit(
        api_request,
        "data_provenance",
        "record_baseline",
        resource_type="data",
        resource_id="baseline",
        detail={
            "baseline_status": baseline.get("status"),
            "lock_manifest_hash": lockfile.get("manifest_hash"),
            "provenance_status": provenance.get("status"),
        },
    )
    return ApiResponse(data={"baseline": baseline, "lockfile": lockfile, "provenance": provenance})


@router.get("/data/lockfile", response_model=ApiResponse)
def data_lockfile_endpoint() -> ApiResponse:
    return ApiResponse(data=verify_data_lockfile())


@router.post("/data/lockfile/write", response_model=ApiResponse)
def data_lockfile_write_endpoint(api_request: Request) -> ApiResponse:
    lockfile = write_data_lockfile()
    _audit(
        api_request,
        "data_provenance",
        "write_lockfile",
        resource_type="data",
        resource_id="lockfile",
        detail={"manifest_hash": lockfile.get("manifest_hash"), "record_count": lockfile.get("record_count")},
    )
    return ApiResponse(data=lockfile)


@router.get("/data/release-lock", response_model=ApiResponse)
def data_release_lock_endpoint() -> ApiResponse:
    return ApiResponse(data=verify_data_release_lock())


@router.post("/data/release-lock/write", response_model=ApiResponse)
def data_release_lock_write_endpoint(api_request: Request) -> ApiResponse:
    release_lock = write_data_release_lock()
    _audit(
        api_request,
        "data_provenance",
        "write_release_lock",
        resource_type="data",
        resource_id="release-lock",
        detail={
            "status": release_lock.get("status"),
            "current_hash": release_lock.get("current_hash"),
            "summary": release_lock.get("summary"),
        },
    )
    return ApiResponse(data=release_lock)


@router.get("/data/release/export.zip")
def data_release_bundle_export_endpoint(api_request: Request) -> Response:
    content = build_data_release_bundle()
    verification = verify_data_release_bundle(content)
    resource_id = verification.get("structured_manifest_hash") or "data_release"
    archive = _archive_bundle(
        content,
        action="data_release_bundle",
        resource_type="data",
        resource_id=str(resource_id),
        filename=f"agentic_rag_data_release_{str(resource_id)[:12]}.zip",
        metadata={
            "verification_status": verification.get("status"),
            "semantic_status": verification.get("semantic_status"),
            "promotion_status": verification.get("promotion_status"),
            "structured_manifest_hash": verification.get("structured_manifest_hash"),
        },
    )
    _audit(
        api_request,
        "artifact_export",
        "data_release_bundle",
        outcome=verification.get("status", "unknown"),
        resource_type="data",
        resource_id=str(resource_id),
        detail={
            "bytes": len(content),
            "archive_artifact_id": archive["artifact_id"],
            "archive_status": archive["archive_status"],
            "manifest_hash": archive.get("manifest_hash"),
            "promotion_status": verification.get("promotion_status"),
        },
    )
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="agentic_rag_data_release_{str(resource_id)[:12]}.zip"'},
    )


@router.get("/data/release/export/verify", response_model=ApiResponse)
def data_release_bundle_verify_endpoint(api_request: Request) -> ApiResponse:
    verification = verify_data_release_bundle(build_data_release_bundle())
    _audit(
        api_request,
        "artifact_verify",
        "data_release_bundle_verify",
        outcome=verification.get("status", "unknown"),
        resource_type="data",
        resource_id=str(verification.get("structured_manifest_hash") or "data_release"),
        detail={
            "status": verification.get("status"),
            "semantic_status": verification.get("semantic_status"),
            "promotion_status": verification.get("promotion_status"),
            "file_count": verification.get("file_count"),
            "checked_files": verification.get("checked_files"),
        },
    )
    return ApiResponse(data=verification)


@router.post("/data/refresh", response_model=ApiResponse)
def data_refresh_endpoint(request: DataRefreshRequest, api_request: Request) -> ApiResponse:
    result = refresh_reference_data(
        genes=request.genes or None,
        brain_regions=request.brain_regions or None,
        include_gtex=request.include_gtex,
        include_allen=request.include_allen,
        allen_query_terms=request.allen_query_terms or None,
        max_allen_records=request.max_allen_records,
        dataset_id=request.dataset_id,
        rebuild_index_after=request.rebuild_index,
        dry_run=request.dry_run,
    )
    _audit(
        api_request,
        "data_refresh",
        "refresh_reference_data",
        outcome=result.get("refresh_status", result.get("status", "unknown")),
        resource_type="data",
        resource_id=request.dataset_id,
        detail={
            "dry_run": request.dry_run,
            "include_gtex": request.include_gtex,
            "include_allen": request.include_allen,
            "manifest_hash": result.get("manifest_hash"),
            "summary": result.get("summary"),
        },
    )
    return ApiResponse(data=result)


@router.post("/data/refresh/validate", response_model=ApiResponse)
def data_refresh_validate_endpoint(request: DataRefreshRequest, api_request: Request) -> ApiResponse:
    validation = validate_refresh_plan(
        genes=request.genes or None,
        brain_regions=request.brain_regions or None,
        include_gtex=request.include_gtex,
        include_allen=request.include_allen,
        allen_query_terms=request.allen_query_terms or None,
        max_allen_records=request.max_allen_records,
        dataset_id=request.dataset_id,
    )
    _audit(
        api_request,
        "data_refresh",
        "validate_refresh_plan",
        outcome=validation.get("status", "unknown"),
        resource_type="data",
        resource_id=request.dataset_id,
        detail={
            "operation_count": (validation.get("plan") or {}).get("operation_count"),
            "sources": (validation.get("plan") or {}).get("sources"),
            "errors": validation.get("errors"),
            "warnings": validation.get("warnings"),
        },
    )
    return ApiResponse(data=validation)


@router.get("/data/refresh-log", response_model=ApiResponse)
def data_refresh_log_endpoint(limit: int = 20) -> ApiResponse:
    return ApiResponse(data=refresh_log(limit=max(1, min(limit, 200))))


@router.post("/data/refresh/plan/export.zip")
def data_refresh_plan_bundle_export_endpoint(request: DataRefreshRequest, api_request: Request) -> Response:
    content = build_data_refresh_plan_bundle(request.model_dump())
    verification = verify_data_refresh_plan_bundle(content)
    resource_id = f"{request.dataset_id}_{verification.get('structured_manifest_hash') or 'refresh_plan'}"
    archive = _archive_bundle(
        content,
        action="data_refresh_plan_bundle",
        resource_type="data_refresh",
        resource_id=resource_id,
        filename=f"{request.dataset_id}_data_refresh_plan_bundle.zip",
        metadata={
            "operation_count": verification.get("operation_count"),
            "validation_status": verification.get("validation_status"),
            "structured_manifest_hash": verification.get("structured_manifest_hash"),
        },
    )
    _audit(
        api_request,
        "artifact_export",
        "data_refresh_plan_bundle",
        outcome=verification.get("status", "unknown"),
        resource_type="data_refresh",
        resource_id=resource_id,
        detail={
            "bytes": len(content),
            "archive_artifact_id": archive["artifact_id"],
            "archive_status": archive["archive_status"],
            "manifest_hash": archive.get("manifest_hash"),
            "operation_count": verification.get("operation_count"),
            "validation_status": verification.get("validation_status"),
        },
    )
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{request.dataset_id}_data_refresh_plan_bundle.zip"'},
    )


@router.post("/data/refresh/plan/export/verify", response_model=ApiResponse)
def data_refresh_plan_bundle_verify_endpoint(request: DataRefreshRequest, api_request: Request) -> ApiResponse:
    verification = verify_data_refresh_plan_bundle(build_data_refresh_plan_bundle(request.model_dump()))
    _audit(
        api_request,
        "artifact_verify",
        "data_refresh_plan_bundle_verify",
        outcome=verification.get("status", "unknown"),
        resource_type="data_refresh",
        resource_id=request.dataset_id,
        detail={
            "status": verification.get("status"),
            "semantic_status": verification.get("semantic_status"),
            "operation_count": verification.get("operation_count"),
            "validation_status": verification.get("validation_status"),
            "manifest_hash": verification.get("manifest_hash"),
        },
    )
    return ApiResponse(data=verification)


@router.get("/data/external-sources", response_model=ApiResponse)
def external_sources_endpoint() -> ApiResponse:
    return ApiResponse(data=external_source_status())


@router.post("/data/external-sources/backfill", response_model=ApiResponse)
def external_sources_backfill_endpoint(api_request: Request, dry_run: bool = True) -> ApiResponse:
    result = backfill_external_source_snapshots(dry_run=dry_run)
    _audit(
        api_request,
        "data_provenance",
        "external_source_snapshot_backfill",
        outcome=result.get("status", "unknown"),
        resource_type="data",
        resource_id="external_sources",
        detail={
            "dry_run": dry_run,
            "candidate_file_count": result.get("candidate_file_count"),
            "candidate_record_count": result.get("candidate_record_count"),
            "backfilled_file_count": result.get("backfilled_file_count"),
            "backfilled_record_count": result.get("backfilled_record_count"),
        },
    )
    return ApiResponse(data=result)


@router.get("/data/snapshot.zip")
def data_snapshot_bundle_endpoint(api_request: Request) -> Response:
    content = build_data_snapshot_bundle()
    archive = _archive_bundle(
        content,
        action="data_snapshot_bundle",
        resource_type="data",
        resource_id="snapshot",
        filename="agentic_rag_data_snapshot.zip",
        metadata={"source": "data_snapshot"},
    )
    _audit(
        api_request,
        "artifact_export",
        "data_snapshot_bundle",
        resource_type="data",
        resource_id="snapshot",
        detail={
            "bytes": len(content),
            "archive_artifact_id": archive["artifact_id"],
            "archive_status": archive["archive_status"],
            "manifest_hash": archive.get("manifest_hash"),
        },
    )
    headers = {"Content-Disposition": 'attachment; filename="agentic_rag_data_snapshot.zip"'}
    return Response(content=content, media_type="application/zip", headers=headers)


@router.get("/data/snapshot/verify", response_model=ApiResponse)
def data_snapshot_bundle_verify_endpoint(api_request: Request) -> ApiResponse:
    verification = verify_artifact_bundle(build_data_snapshot_bundle())
    _audit(
        api_request,
        "artifact_verify",
        "data_snapshot_verify",
        outcome=verification.get("status", "unknown"),
        resource_type="data",
        resource_id="snapshot",
        detail={
            "status": verification.get("status"),
            "file_count": verification.get("file_count"),
            "checked_files": verification.get("checked_files"),
            "manifest_hash": verification.get("manifest_hash"),
        },
    )
    return ApiResponse(data=verification)


@router.post("/structured/search", response_model=ApiResponse)
def structured_search_endpoint(request: DesignFromGeneRequest) -> ApiResponse:
    target = request.target.model_dump()
    target["gene"] = request.gene
    target["species"] = request.species
    return ApiResponse(data=search_structured_context(target))


@router.post("/structured/import", response_model=ApiResponse)
def structured_import_endpoint(request: StructuredImportRequest, api_request: Request) -> ApiResponse:
    try:
        result = import_structured_records(request.source_path)
        if request.rebuild_index:
            result["index"] = rebuild_rag_index(persist=True)
        bundle = build_structured_import_audit_bundle(result, request.model_dump())
        imported_path = Path(str(result.get("imported_path") or "structured_import"))
        archive = _archive_bundle(
            bundle,
            action="structured_import_audit",
            resource_type="data",
            resource_id=imported_path.name,
            filename=f"{imported_path.stem}_import_audit.zip",
            metadata={
                "structured_manifest_hash": (result.get("status") or {}).get("manifest_hash"),
                "rebuild_index": request.rebuild_index,
            },
        )
        result["audit_bundle"] = {
            "artifact_id": archive["artifact_id"],
            "archive_status": archive["archive_status"],
            "manifest_hash": archive.get("manifest_hash"),
            "verification_status": archive.get("verification_status"),
        }
        _audit(
            api_request,
            "data_import",
            "structured_import",
            outcome="completed",
            resource_type="data",
            resource_id=imported_path.name,
            detail={
                "imported_path": result.get("imported_path"),
                "structured_manifest_hash": (result.get("status") or {}).get("manifest_hash"),
                "archive_artifact_id": archive["artifact_id"],
                "archive_status": archive["archive_status"],
                "rebuild_index": request.rebuild_index,
            },
        )
        return ApiResponse(data=result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/external/gtex/import-gene-expression", response_model=ApiResponse)
def external_gtex_import_gene_expression(request: GtexImportRequest) -> ApiResponse:
    try:
        return ApiResponse(
            data=import_gtex_gene_expression(
                request.gene,
                brain_regions=request.brain_regions,
                dataset_id=request.dataset_id,
                rebuild_index_after=request.rebuild_index,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=502, detail=f"GTEx import failed: {exc}") from exc


@router.post("/external/allen/import-whb-taxonomy", response_model=ApiResponse)
def external_allen_import_whb_taxonomy(request: AllenTaxonomyImportRequest) -> ApiResponse:
    try:
        return ApiResponse(
            data=import_allen_whb_taxonomy(
                query_terms=request.query_terms or None,
                max_records=request.max_records,
                rebuild_index_after=request.rebuild_index,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=502, detail=f"Allen import failed: {exc}") from exc


@router.post("/workflow/plan", response_model=ApiResponse)
def workflow_plan_endpoint(request: DesignFromGeneRequest) -> ApiResponse:
    return ApiResponse(data={"workflow_runtime": workflow_runtime_status(), "task": plan_gene_design_task(request.model_dump())})


@router.get("/workflow/status", response_model=ApiResponse)
def workflow_status_endpoint() -> ApiResponse:
    return ApiResponse(data=workflow_runtime_status())


@router.post("/workflow/design-from-gene", response_model=ApiResponse)
def workflow_design_from_gene_endpoint(request: DesignFromGeneRequest) -> ApiResponse:
    try:
        design = _run_gene_design(request)
        save_run(design, run_type="workflow_gene_design", request_payload=request.model_dump())
        return ApiResponse(data=workflow_summary(design))
    except EnsemblClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/optimize", response_model=ApiResponse)
def optimize(request: OptimizeRequest) -> ApiResponse:
    try:
        design = run_cds_design_workflow(
            request.cds,
            request.target.model_dump(),
            _optimization_config(request.optimization_settings),
        )
        design["saved_run"] = save_run(design, run_type="cds_optimize", request_payload=request.model_dump())
        return ApiResponse(data=design)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/design-from-gene", response_model=ApiResponse)
def design_from_gene(request: DesignFromGeneRequest) -> ApiResponse:
    try:
        design = _run_gene_design(request)
        design["saved_run"] = save_run(design, run_type="gene_design", request_payload=request.model_dump())
        return ApiResponse(data=design)
    except EnsemblClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/report-from-gene", response_model=ApiResponse)
def report_from_gene(request: DesignFromGeneRequest) -> ApiResponse:
    try:
        design = _run_gene_design(request)
        save_run(design, run_type="gene_report", request_payload=request.model_dump())
        return ApiResponse(data=design["qc_report"])
    except EnsemblClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/report-from-gene/export/{export_format}")
def report_from_gene_export(request: DesignFromGeneRequest, export_format: str, api_request: Request) -> Response:
    try:
        design = _run_gene_design(request)
        content = export_qc_report(design["qc_report"], export_format)
        save_run(design, run_type="gene_report_export", request_payload=request.model_dump())
        save_run_artifact(design["run_id"], _artifact_type(export_format), content, _media_type(export_format))
        _audit(
            api_request,
            "report_export",
            "gene_report_export",
            resource_type="run",
            resource_id=design["run_id"],
            detail={"format": export_format, "gene": request.gene, "bytes": len(content)},
        )
        return _report_response(content, export_format, design["run_id"])
    except EnsemblClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/report-from-gene/export-bundle.zip")
def report_from_gene_bundle_export(request: DesignFromGeneRequest, api_request: Request) -> Response:
    try:
        design = _run_gene_design(request)
        save_run(design, run_type="gene_report_bundle_export", request_payload=request.model_dump())
        content = build_qc_report_bundle(design, request.model_dump(), bundle_type="gene_report")
        verification = verify_qc_report_bundle(content)
        archive = _archive_bundle(
            content,
            action="gene_report_bundle_export",
            resource_type="run",
            resource_id=design["run_id"],
            filename=f"{design['run_id']}_qc_report_bundle.zip",
            metadata={"verification_status": verification.get("status"), "bundle_type": "gene_report"},
        )
        _audit(
            api_request,
            "report_export",
            "gene_report_bundle_export",
            outcome=verification.get("status", "unknown"),
            resource_type="run",
            resource_id=design["run_id"],
            detail={"bytes": len(content), "archive_artifact_id": archive["artifact_id"], "verification_status": verification.get("status")},
        )
        return Response(
            content=content,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{design["run_id"]}_qc_report_bundle.zip"'},
        )
    except EnsemblClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/report-from-gene/export-bundle/verify", response_model=ApiResponse)
def report_from_gene_bundle_verify(request: DesignFromGeneRequest, api_request: Request) -> ApiResponse:
    try:
        design = _run_gene_design(request)
        content = build_qc_report_bundle(design, request.model_dump(), bundle_type="gene_report")
        verification = verify_qc_report_bundle(content)
        _audit(
            api_request,
            "artifact_verify",
            "gene_report_bundle_verify",
            outcome=verification.get("status", "unknown"),
            resource_type="run",
            resource_id=design["run_id"],
            detail={"status": verification.get("status"), "file_count": verification.get("file_count")},
        )
        return ApiResponse(data=verification)
    except EnsemblClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/report", response_model=ApiResponse)
def report(request: OptimizeRequest, api_request: Request) -> ApiResponse:
    try:
        design = run_cds_design_workflow(
            request.cds,
            request.target.model_dump(),
            _optimization_config(request.optimization_settings),
        )
        save_run(design, run_type="cds_report", request_payload=request.model_dump())
        _audit(
            api_request,
            "design_run",
            "cds_report",
            resource_type="run",
            resource_id=design["run_id"],
            detail={
                "candidate_count": len(design.get("candidates", [])),
                "recommended_candidate_id": (design.get("recommended_candidate") or {}).get("candidate_id"),
            },
        )
        return ApiResponse(data=design["qc_report"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/report/export/{export_format}")
def report_export(request: OptimizeRequest, export_format: str, api_request: Request) -> Response:
    try:
        design = run_cds_design_workflow(
            request.cds,
            request.target.model_dump(),
            _optimization_config(request.optimization_settings),
        )
        content = export_qc_report(design["qc_report"], export_format)
        save_run(design, run_type="cds_report_export", request_payload=request.model_dump())
        save_run_artifact(design["run_id"], _artifact_type(export_format), content, _media_type(export_format))
        _audit(
            api_request,
            "report_export",
            "cds_report_export",
            resource_type="run",
            resource_id=design["run_id"],
            detail={"format": export_format, "bytes": len(content)},
        )
        return _report_response(content, export_format, design["run_id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/report/export-bundle.zip")
def report_bundle_export(request: OptimizeRequest, api_request: Request) -> Response:
    try:
        design = run_cds_design_workflow(
            request.cds,
            request.target.model_dump(),
            _optimization_config(request.optimization_settings),
        )
        save_run(design, run_type="cds_report_bundle_export", request_payload=request.model_dump())
        content = build_qc_report_bundle(design, request.model_dump(), bundle_type="cds_report")
        verification = verify_qc_report_bundle(content)
        archive = _archive_bundle(
            content,
            action="cds_report_bundle_export",
            resource_type="run",
            resource_id=design["run_id"],
            filename=f"{design['run_id']}_qc_report_bundle.zip",
            metadata={"verification_status": verification.get("status"), "bundle_type": "cds_report"},
        )
        _audit(
            api_request,
            "report_export",
            "cds_report_bundle_export",
            outcome=verification.get("status", "unknown"),
            resource_type="run",
            resource_id=design["run_id"],
            detail={"bytes": len(content), "archive_artifact_id": archive["artifact_id"], "verification_status": verification.get("status")},
        )
        return Response(
            content=content,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{design["run_id"]}_qc_report_bundle.zip"'},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/report/export-bundle/verify", response_model=ApiResponse)
def report_bundle_verify(request: OptimizeRequest, api_request: Request) -> ApiResponse:
    try:
        design = run_cds_design_workflow(
            request.cds,
            request.target.model_dump(),
            _optimization_config(request.optimization_settings),
        )
        content = build_qc_report_bundle(design, request.model_dump(), bundle_type="cds_report")
        verification = verify_qc_report_bundle(content)
        _audit(
            api_request,
            "artifact_verify",
            "cds_report_bundle_verify",
            outcome=verification.get("status", "unknown"),
            resource_type="run",
            resource_id=design["run_id"],
            detail={"status": verification.get("status"), "file_count": verification.get("file_count")},
        )
        return ApiResponse(data=verification)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _run_gene_design(request: DesignFromGeneRequest) -> dict:
    return run_gene_design_workflow(request.model_dump(), _optimization_config(request.optimization_settings))


def _execute_gene_design_job(job_id: str, payload: dict) -> None:
    mark_job_running(job_id)
    try:
        request = DesignFromGeneRequest.model_validate(payload)
        design = _run_gene_design(request)
        summary = save_run(design, run_type="gene_design_job", request_payload=payload)
        complete_job(
            job_id,
            {
                "run_id": design["run_id"],
                "saved_run": summary,
                "recommended_candidate_id": (design.get("recommended_candidate") or {}).get("candidate_id"),
                "warnings": design.get("warnings", []),
            },
        )
        _audit_system(
            "job",
            "complete_gene_design",
            resource_type="job",
            resource_id=job_id,
            detail={"run_id": design["run_id"], "gene": payload.get("gene")},
        )
    except Exception as exc:  # noqa: BLE001 - background worker must persist failures.
        fail_job(job_id, str(exc))
        _audit_system("job", "fail_gene_design", outcome="failed", resource_type="job", resource_id=job_id, detail={"error": str(exc)})


def _execute_batch_gene_design_job(job_id: str, payload: dict) -> None:
    mark_job_running(job_id)
    try:
        request = BatchDesignFromGenesRequest.model_validate(payload)
        result = run_batch_gene_design(
            request.model_dump(),
            _optimization_config(request.optimization_settings),
            persist=True,
            run_type="batch_gene_design_job",
        )
        complete_job(job_id, result)
        _audit_system(
            "job",
            "complete_batch_gene_design",
            resource_type="job",
            resource_id=job_id,
            detail={"genes": payload.get("genes", []), "run_count": len(result.get("results", []))},
        )
    except Exception as exc:  # noqa: BLE001 - background worker must persist failures.
        fail_job(job_id, str(exc))
        _audit_system("job", "fail_batch_gene_design", outcome="failed", resource_type="job", resource_id=job_id, detail={"error": str(exc)})


def _execute_data_refresh_job(job_id: str, payload: dict) -> None:
    mark_job_running(job_id)
    try:
        request = DataRefreshRequest.model_validate(payload)
        result = refresh_reference_data(
            genes=request.genes or None,
            brain_regions=request.brain_regions or None,
            include_gtex=request.include_gtex,
            include_allen=request.include_allen,
            allen_query_terms=request.allen_query_terms or None,
            max_allen_records=request.max_allen_records,
            dataset_id=request.dataset_id,
            rebuild_index_after=request.rebuild_index,
            dry_run=request.dry_run,
        )
        complete_job(
            job_id,
            {
                "refresh_status": result.get("refresh_status"),
                "summary": result.get("summary"),
                "manifest_hash": result.get("manifest_hash"),
                "status": result.get("status"),
            },
        )
        _audit_system(
            "data_refresh",
            "complete_data_refresh_job",
            outcome=result.get("refresh_status", result.get("status", "success")),
            resource_type="job",
            resource_id=job_id,
            detail={
                "dry_run": payload.get("dry_run"),
                "manifest_hash": result.get("manifest_hash"),
                "summary": result.get("summary"),
            },
        )
    except Exception as exc:  # noqa: BLE001 - background worker must persist failures.
        fail_job(job_id, str(exc))
        _audit_system("data_refresh", "fail_data_refresh_job", outcome="failed", resource_type="job", resource_id=job_id, detail={"error": str(exc)})


def _store_metrics() -> dict:
    jobs = list_jobs(limit=200)["jobs"]
    runs = list_runs(limit=200)["runs"]
    artifacts = archive_summary()
    status_counts: dict[str, int] = {}
    for job in jobs:
        status = job.get("status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "jobs_observed": len(jobs),
        "job_status_counts": status_counts,
        "runs_observed": len(runs),
        "artifacts_observed": artifacts["total_artifacts"],
        "artifact_bytes": artifacts["total_bytes"],
    }


def _flat_store_metrics() -> dict:
    stores = _store_metrics()
    output = {
        "jobs_observed": stores["jobs_observed"],
        "runs_observed": stores["runs_observed"],
        "artifacts_observed": stores["artifacts_observed"],
        "artifact_bytes": stores["artifact_bytes"],
    }
    for status, count in stores["job_status_counts"].items():
        output[f"jobs_status_{status}"] = count
    return output


def _audit(
    request: Request,
    event_type: str,
    action: str,
    *,
    outcome: str = "success",
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict | None = None,
) -> dict:
    return record_audit_event(
        event_type,
        action,
        outcome=outcome,
        actor=_audit_actor(request),
        request_id=getattr(request.state, "request_id", None),
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail,
    )


def _audit_system(
    event_type: str,
    action: str,
    *,
    outcome: str = "success",
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict | None = None,
) -> dict:
    return record_audit_event(
        event_type,
        action,
        outcome=outcome,
        actor="system",
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail,
    )


def _archive_bundle(
    content: bytes,
    *,
    action: str,
    resource_type: str,
    resource_id: str,
    filename: str,
    metadata: dict | None = None,
) -> dict:
    return archive_artifact_bundle(
        content,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        filename=filename,
        metadata=metadata,
    )


def _audit_actor(request: Request) -> str:
    api_key = extract_api_key(request)
    if api_key:
        return f"api_key:{sha256(api_key.encode('utf-8')).hexdigest()[:12]}"
    client_host = request.client.host if request.client else "unknown"
    return f"anonymous:{client_host}"


def _report_response(content: str | bytes, export_format: str, run_id: str) -> Response:
    normalized = export_format.lower()
    if normalized in {"md", "markdown"}:
        media_type = "text/markdown; charset=utf-8"
        extension = "md"
    elif normalized == "html":
        media_type = "text/html; charset=utf-8"
        extension = "html"
    elif normalized == "json":
        media_type = "application/json; charset=utf-8"
        extension = "json"
    elif normalized == "pdf":
        media_type = "application/pdf"
        extension = "pdf"
    else:
        raise ValueError("Unsupported report export format. Use markdown, html, json, or pdf.")
    headers = {"Content-Disposition": f'attachment; filename="{run_id}_qc_report.{extension}"'}
    return Response(content=content, media_type=media_type, headers=headers)


def _artifact_type(export_format: str) -> str:
    normalized = export_format.lower()
    if normalized in {"md", "markdown"}:
        return "qc_report_markdown"
    if normalized == "html":
        return "qc_report_html"
    if normalized == "json":
        return "qc_report_json"
    if normalized == "pdf":
        return "qc_report_pdf"
    raise ValueError("Unsupported report export format. Use markdown, html, json, or pdf.")


def _media_type(export_format: str) -> str:
    normalized = export_format.lower()
    if normalized in {"md", "markdown"}:
        return "text/markdown; charset=utf-8"
    if normalized == "html":
        return "text/html; charset=utf-8"
    if normalized == "json":
        return "application/json; charset=utf-8"
    if normalized == "pdf":
        return "application/pdf"
    raise ValueError("Unsupported report export format. Use markdown, html, json, or pdf.")


def _score_config(settings) -> ScoreConfig:
    return ScoreConfig(
        gc_min=settings.gc_min,
        gc_max=settings.gc_max,
        target_gc=settings.target_gc,
        aav_payload_limit_nt=settings.aav_payload_limit_nt,
        forbidden_motifs=tuple(settings.forbidden_motifs),
        polyadenylation_signals=tuple(settings.polyadenylation_signals),
        restriction_sites=tuple(settings.restriction_sites),
        cryptic_splice_motifs=tuple(settings.cryptic_splice_motifs),
        splice_donor_motifs=tuple(settings.splice_donor_motifs),
        splice_acceptor_motifs=tuple(settings.splice_acceptor_motifs),
        gc_window_size_nt=settings.gc_window_size_nt,
        codon_weight_multipliers=tuple(sorted((key.upper(), value) for key, value in settings.codon_weight_multipliers.items())),
        codon_availability_weights=tuple(sorted((key.upper(), value) for key, value in settings.codon_availability_weights.items())),
    )


def _optimization_config(settings) -> OptimizationConfig:
    return OptimizationConfig(
        population_size=settings.population_size,
        generations=settings.generations,
        mutation_rate=settings.mutation_rate,
        crossover_rate=settings.crossover_rate,
        seed=settings.seed,
        max_candidates=settings.max_candidates,
        enable_repair=settings.enable_repair,
        repair_passes=settings.repair_passes,
        score_config=_score_config(settings.score_settings),
    )
