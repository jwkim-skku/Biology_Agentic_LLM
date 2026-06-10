from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from app.config import get_settings
from app.services.agent_memory_service import agent_memory_summary
from app.services.artifact_archive_service import (
    archive_summary,
    data_refresh_plan_archive_summary,
    data_release_archive_summary,
    data_snapshot_archive_summary,
    optimizer_benchmark_archive_summary,
    qc_bundle_archive_semantic_summary,
    rag_evaluation_archive_summary,
    rag_regression_archive_summary,
    rag_vector_index_archive_summary,
    structured_import_archive_summary,
    verify_artifact_ledger,
    workflow_trace_archive_summary,
)
from app.services.artifact_object_store_service import artifact_object_store_status, plan_artifact_object_store_mirror
from app.services.data_provenance_service import data_provenance_audit
from app.services.data_release_bundle_service import build_data_release_bundle, verify_data_release_bundle
from app.services.data_snapshot_service import build_data_snapshot_bundle, verify_data_snapshot_bundle
from app.services.governance_service import build_governance_attestation_bundle, verify_governance_attestation_bundle
from app.services.optimizer_benchmark_service import evaluate_optimizer_benchmark
from app.services.rag_embedding_service import rag_embedding_status
from app.services.rag_regression_service import evaluate_rag_regression
from app.services.rag_service import rag_status
from app.services.rna_folding_service import rna_folding_status
from app.services.signature_service import signing_status
from app.services.storage_service import storage_status
from app.services.structured_data_service import structured_status, validate_structured_records
from app.services.structured_quality_service import structured_quality_gate
from app.services.workflow_trace_bundle_service import workflow_runtime_status


def deployment_readiness(openapi_spec: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    structured = structured_status()
    validation = validate_structured_records()
    structured_quality = structured_quality_gate()
    provenance = data_provenance_audit()
    data_release_bundle = verify_data_release_bundle(build_data_release_bundle())
    rag = rag_status()
    embedding = rag_embedding_status()
    rag_regression = evaluate_rag_regression()
    optimizer = evaluate_optimizer_benchmark()
    folding = rna_folding_status()
    memory = agent_memory_summary()
    snapshot_verification = verify_data_snapshot_bundle(build_data_snapshot_bundle())
    storage = storage_status()
    signing = signing_status()
    signing_ready = _signing_ready(signing)
    production_storage_ready = (
        storage["status"] == "pass"
        and storage["target_backend"] == "postgres"
        and storage["active_runtime_adapter"] == "postgres"
        and storage["database_url_configured"]
    )
    production_security_ready = (
        settings.auth_enabled
        and bool(settings.api_key_roles)
        and len(settings.api_keys) >= 1
        and settings.rate_limit_per_minute > 0
    )
    governance = verify_governance_attestation_bundle(build_governance_attestation_bundle(openapi_spec))
    archive = archive_summary()
    object_store = artifact_object_store_status()
    object_store_mirror_plan = _object_store_mirror_plan_summary(plan_artifact_object_store_mirror(limit=500))
    ledger = verify_artifact_ledger()
    qc_archive = qc_bundle_archive_semantic_summary(limit=3, verify_files=False)
    data_refresh_plan_archive = data_refresh_plan_archive_summary(limit=3, verify_files=False)
    data_release_archive = data_release_archive_summary(limit=3, verify_files=False)
    data_snapshot_archive = data_snapshot_archive_summary(limit=3, verify_files=False)
    import_archive = structured_import_archive_summary(limit=3, verify_files=False)
    rag_archive = rag_evaluation_archive_summary(limit=3, verify_files=False)
    rag_regression_archive = rag_regression_archive_summary(limit=3, verify_files=False)
    rag_vector_index_archive = rag_vector_index_archive_summary(limit=3, verify_files=False)
    optimizer_archive = optimizer_benchmark_archive_summary(limit=3, verify_files=False)
    workflow_trace_archive = workflow_trace_archive_summary(limit=3, verify_files=False)
    workflow_runtime = workflow_runtime_status()

    gates = [
        _gate(
            "structured_data",
            validation["error_count"] == 0 and structured["records"] > 0,
            _warning=validation["warning_count"] == 0,
            details={
                "records": structured["records"],
                "datasets": structured["datasets"],
                "manifest_hash": structured["manifest_hash"],
                "validation_errors": validation["error_count"],
                "validation_warnings": validation["warning_count"],
            },
            fail_message="Structured data must have records and zero validation errors.",
            warn_message="Structured data has validation warnings.",
        ),
        _gate(
            "data_provenance",
            provenance["status"] in {"pass", "warning"},
            _warning=provenance["status"] == "pass",
            details={
                "status": provenance["status"],
                "manifest_hash": provenance["manifest_hash"],
                "failed_checks": sum(1 for check in provenance["checks"] if check["result"] != "pass"),
                "release_lock_status": (provenance.get("release_lock") or {}).get("status"),
                "trna_prior_caveats": {
                    "status": (provenance.get("trna_prior_caveats") or {}).get("status"),
                    "caveat_count": (provenance.get("trna_prior_caveats") or {}).get("caveat_count", 0),
                    "blocking_production_use": (provenance.get("trna_prior_caveats") or {}).get("blocking_production_use", False),
                    "operator_action": (provenance.get("trna_prior_caveats") or {}).get("operator_action"),
                },
            },
            fail_message="Data provenance audit failed.",
            warn_message="Data provenance has warnings or an unpinned release lock.",
        ),
        _gate(
            "structured_quality",
            structured_quality["status"] in {"pass", "warning"},
            _warning=structured_quality["status"] == "pass",
            details={
                "status": structured_quality["status"],
                "quality_schema": structured_quality["quality_schema"],
                "manifest_hash": structured_quality["manifest_hash"],
                "live_record_fraction": structured_quality["coverage"]["live_record_fraction"],
                "release_pinned_fraction": structured_quality["coverage"]["release_pinned_fraction"],
                "blocking_count": structured_quality["summary"]["blocking_count"],
                "operator_actions": structured_quality["operator_actions"],
            },
            fail_message="Structured data quality gate failed.",
            warn_message="Structured data has seed/local priors, missing live coverage, or source coverage gaps.",
        ),
        _gate(
            "data_release_bundle",
            data_release_bundle["status"] in {"pass", "warning"} and data_release_bundle["semantic_status"] in {"pass", "warning"},
            _warning=data_release_bundle["status"] == "pass"
            and data_release_bundle["semantic_status"] == "pass"
            and data_release_bundle["promotion_status"] == "pass",
            details={
                "status": data_release_bundle["status"],
                "semantic_status": data_release_bundle["semantic_status"],
                "promotion_status": data_release_bundle["promotion_status"],
                "record_count": data_release_bundle["record_count"],
                "record_source_summary_hash": data_release_bundle.get("record_source_summary_hash"),
                "dataset_count": data_release_bundle.get("dataset_count"),
                "source_file_count": data_release_bundle.get("source_file_count"),
                "release_handoff_hash": data_release_bundle.get("release_handoff_hash"),
                "structured_manifest_hash": data_release_bundle["structured_manifest_hash"],
                "rag_structured_manifest_hash": data_release_bundle.get("rag_structured_manifest_hash"),
                "rag_index_hash": data_release_bundle.get("rag_index_hash"),
                "structured_source_file_count": data_release_bundle["structured_source_file_count"],
                "external_snapshot_reference_count": data_release_bundle["external_snapshot_reference_count"],
                "external_snapshot_file_count": data_release_bundle["external_snapshot_file_count"],
                "required_files": data_release_bundle["semantic_checks"].get("required_files"),
                "record_rows": data_release_bundle["semantic_checks"].get("record_rows"),
                "structured_source_bytes": data_release_bundle["semantic_checks"].get("structured_source_bytes"),
                "external_snapshot_bytes": data_release_bundle["semantic_checks"].get("external_snapshot_bytes"),
            },
            fail_message="Data release evidence bundle verification failed.",
            warn_message="Data release bundle is semantically valid but still has promotion caveats.",
        ),
        _gate(
            "rag_regression",
            rag["chunks"] > 0 and rag_regression["status"] in {"pass", "warning"},
            _warning=rag_regression["status"] == "pass",
            details={
                "chunks": rag["chunks"],
                "documents": rag["documents"],
                "retrieval_model": rag["retrieval_model"],
                "status": rag_regression["status"],
                "cases_hash": rag_regression.get("cases_hash"),
                "results_hash": rag_regression.get("results_hash"),
                "quality_summary_hash": rag_regression.get("quality_summary_hash"),
                "quality_status": (rag_regression.get("quality_summary") or {}).get("status"),
                "top_source_count": (rag_regression.get("quality_summary") or {}).get("top_source_count"),
                "missing_term_case_count": (rag_regression.get("quality_summary") or {}).get("missing_term_case_count"),
                "recall_at_k": rag_regression.get("macro", {}).get("recall_at_k"),
                "ndcg_at_k": rag_regression.get("macro", {}).get("ndcg_at_k"),
            },
            fail_message="RAG index or regression suite is not passing.",
            warn_message="RAG regression suite has warnings.",
        ),
        _gate(
            "rag_embedding_backend",
            embedding["status"] in {"pass", "warning"},
            _warning=embedding["production_ready"],
            details={
                "status": embedding["status"],
                "requested_backend": embedding["requested_backend"],
                "active_backend": embedding["active_backend"],
                "fallback_active": embedding["fallback_active"],
                "embedding_model": embedding["embedding_model"],
                "embedding_dimensions": embedding["embedding_dimensions"],
                "model_fingerprint_hash": embedding.get("model_fingerprint_hash"),
                "model_fingerprint": embedding.get("model_fingerprint", {}),
                "production_requirements": {
                    "accepted_backends": ["sentence_transformers", "openai"],
                    "sentence_transformers": {
                        "rag_embedding_backend": "sentence_transformers",
                        "model_load_policy": "local_files_only",
                    },
                    "openai": {
                        "rag_embedding_backend": "openai",
                        "api_key_configured": True,
                        "price_configured": True,
                        "budget_configured": True,
                        "within_budget": True,
                    },
                    "production_ready": True,
                },
                "warnings": embedding["warnings"],
            },
            fail_message="RAG embedding backend status is invalid.",
            warn_message="RAG retrieval is not using a production embedding backend; pin a biomedical embedding model or configure OpenAI embedding with budget guardrails.",
        ),
        _gate(
            "optimizer_benchmark",
            optimizer["status"] in {"pass", "warning"},
            _warning=optimizer["status"] == "pass",
            details={
                "status": optimizer["status"],
                "case_count": optimizer["case_count"],
                "cases_hash": optimizer.get("cases_hash"),
                "results_hash": optimizer.get("results_hash"),
                "macro": optimizer.get("macro", {}),
            },
            fail_message="Optimizer benchmark failed.",
            warn_message="Optimizer benchmark has warnings.",
        ),
        _gate(
            "rna_folding_backend",
            folding["status"] in {"ready", "proxy", "fallback"},
            _warning=folding["production_ready"],
            details={
                "status": folding["status"],
                "requested_backend": folding["requested_backend"],
                "active_backend": folding["active_backend"],
                "fallback_active": folding["fallback_active"],
                "executable": folding["executable"],
                "executable_path": folding["executable_path"],
                "window_nt": folding["window_nt"],
                "evidence_capabilities": folding.get("evidence_capabilities", {}),
                "production_requirements": {
                    "rna_folding_backend": "rnafold",
                    "validated_backend": True,
                },
            },
            fail_message="RNA folding backend status is invalid.",
            warn_message="Optimizer structure scoring is using deterministic proxy evidence; configure ViennaRNA RNAfold for production.",
        ),
        _gate(
            "agent_memory",
            memory["status"] in {"pass", "warning"},
            _warning=memory["memory_count"] > 0,
            details={
                "status": memory["status"],
                "memory_schema": memory["memory_schema"],
                "memory_count": memory["memory_count"],
                "distinct_genes": memory["distinct_genes"],
                "distinct_brain_regions": memory["distinct_brain_regions"],
                "distinct_cell_types": memory["distinct_cell_types"],
                "latest_updated_at": memory["latest_updated_at"],
                "latest_memory_hash": memory.get("latest_memory_hash"),
                "memory_hash_count": memory.get("memory_hash_count"),
                "memory_hash_aggregate": memory.get("memory_hash_aggregate"),
                "by_run_type": memory.get("by_run_type", {}),
            },
            fail_message="Agent memory store is not readable.",
            warn_message="Agent memory store is available but no persisted design memories have been indexed yet.",
        ),
        _gate(
            "workflow_runtime",
            workflow_runtime["status"] == "pass",
            _warning=workflow_runtime["status"] == "pass",
            details={
                "status": workflow_runtime["status"],
                "runtime_schema": workflow_runtime["runtime_schema"],
                "active_runtime": workflow_runtime["active_runtime"],
                "agent_count": len(workflow_runtime["agent_roles"]),
                "trace_contract": workflow_runtime["trace_contract"],
                "external_runtime": workflow_runtime["external_runtime"],
            },
            fail_message="Workflow runtime contract is not available.",
            warn_message="Workflow runtime has warnings.",
        ),
        _gate(
            "workflow_trace_archive_semantics",
            workflow_trace_archive["status"] in {"pass", "warning"},
            _warning=workflow_trace_archive["status"] == "pass",
            details={
                "status": workflow_trace_archive["status"],
                "checked_count": workflow_trace_archive["checked_count"],
                "semantic_pass_count": workflow_trace_archive["semantic_pass_count"],
                "semantic_warning_count": workflow_trace_archive["semantic_warning_count"],
                "semantic_fail_count": workflow_trace_archive["semantic_fail_count"],
                "latest_trace_step_count": (workflow_trace_archive.get("latest_artifacts") or [{}])[0].get("trace_step_count"),
                "latest_trace_hash": (workflow_trace_archive.get("latest_artifacts") or [{}])[0].get("trace_hash"),
                "latest_task_type": (workflow_trace_archive.get("latest_artifacts") or [{}])[0].get("task_type"),
                "latest_workflow_id": (workflow_trace_archive.get("latest_artifacts") or [{}])[0].get("workflow_id"),
                "latest_structured_manifest_hash": (workflow_trace_archive.get("latest_artifacts") or [{}])[0].get("structured_manifest_hash"),
                **_archive_freshness_details(workflow_trace_archive),
            },
            fail_message="Archived workflow trace semantic verification failed.",
            warn_message="Archived workflow trace bundles are missing, stale, or have semantic warnings.",
        ),
        _gate(
            "qc_snapshot_export",
            snapshot_verification["status"] in {"pass", "warning"},
            _warning=snapshot_verification["status"] == "pass",
            details={
                "status": snapshot_verification["status"],
                "file_count": snapshot_verification["file_count"],
                "checked_files": snapshot_verification["checked_files"],
                "manifest_hash": snapshot_verification["manifest_hash"],
                "signature_status": snapshot_verification.get("signature", {}).get("status"),
            },
            fail_message="Data snapshot export verification failed.",
            warn_message="Data snapshot export verification has warnings.",
        ),
        _gate(
            "storage",
            storage["status"] in {"pass", "warning", "ready"},
            _warning=production_storage_ready,
            details={
                "status": storage["status"],
                "target_backend": storage["target_backend"],
                "active_runtime_adapter": storage["active_runtime_adapter"],
                "database_url_configured": storage["database_url_configured"],
                "postgres_schema_hash": storage["postgres"]["schema_hash"],
                "warnings": storage.get("warnings", []),
                "production_requirements": {
                    "target_backend": "postgres",
                    "active_runtime_adapter": "postgres",
                    "database_url_configured": True,
                },
            },
            fail_message="Runtime storage is not ready.",
            warn_message="Runtime storage is usable but not fully production configured.",
        ),
        _gate(
            "security",
            settings.rate_limit_per_minute >= 0,
            _warning=production_security_ready,
            details={
                "auth_enabled": settings.auth_enabled,
                "rbac_enabled": bool(settings.api_key_roles),
                "configured_keys": len(settings.api_keys),
                "rate_limit_per_minute": settings.rate_limit_per_minute,
                "production_requirements": {
                    "auth_enabled": True,
                    "rbac_enabled": True,
                    "configured_keys_min": 1,
                    "rate_limit_per_minute_min": 1,
                },
            },
            fail_message="Security settings are invalid.",
            warn_message="API auth, RBAC, or rate limiting is not fully enabled for production.",
        ),
        _gate(
            "artifact_signing",
            True,
            _warning=signing_ready,
            details={**signing, "status": "ready" if signing_ready else "disabled"},
            fail_message="Artifact signing status is invalid.",
            warn_message="Artifact signing is not fully enabled.",
        ),
        _gate(
            "governance_attestation",
            governance["status"] in {"pass", "warning"},
            _warning=governance["status"] == "pass",
            details={
                "status": governance["status"],
                "attestation_hash": governance["attestation_hash"],
                "file_count": governance["artifact_verification"]["file_count"],
                "checked_files": governance["artifact_verification"]["checked_files"],
                "signature_status": governance.get("signature", {}).get("status"),
            },
            fail_message="Governance attestation verification failed.",
            warn_message="Governance attestation has warnings.",
        ),
        _gate(
            "artifact_archive",
            archive["total_artifacts"] >= 0 and ledger["status"] in {"pass", "warning"},
            _warning=ledger["status"] == "pass",
            details={
                "total_artifacts": archive["total_artifacts"],
                "total_bytes": archive["total_bytes"],
                "ledger_status": ledger["status"],
                "ledger_entries": ledger["entry_count"],
                "missing_from_ledger_count": ledger.get("missing_from_ledger_count", 0),
            },
            fail_message="Artifact archive ledger verification failed.",
            warn_message="Artifact archive ledger has warnings.",
        ),
        _gate(
            "artifact_object_store",
            object_store["status"] in {"disabled", "ready"},
            _warning=object_store["status"] == "ready" and object_store_mirror_plan["candidate_count"] == 0,
            details={
                "status": object_store["status"],
                "enabled": object_store["enabled"],
                "configured": object_store["configured"],
                "bucket": object_store["bucket"],
                "prefix": object_store["prefix"],
                "region": object_store["region"],
                "missing_settings": object_store["missing_settings"],
                "external_timestamp": object_store.get("external_timestamp", {}),
                "external_timestamp_hash": object_store.get("external_timestamp_hash"),
                "lifecycle_policy": object_store.get("lifecycle_policy", {}),
                "lifecycle_policy_hash": object_store.get("lifecycle_policy_hash"),
                "mirror_plan_status": object_store_mirror_plan["status"],
                "mirror_plan_candidate_count": object_store_mirror_plan["candidate_count"],
                "mirror_plan_candidate_bytes": object_store_mirror_plan["candidate_bytes"],
                "mirror_plan_limit": object_store_mirror_plan["limit"],
                "mirror_plan_candidate_hash": object_store_mirror_plan.get("candidate_hash"),
                "mirror_plan_hash": object_store_mirror_plan.get("plan_hash"),
                "recommendation": object_store["recommendation"],
            },
            fail_message="Artifact object-store mirror is enabled but misconfigured.",
            warn_message="Artifact archive is local-only or has pending object-store mirror candidates.",
        ),
        _gate(
            "qc_bundle_archive_semantics",
            qc_archive["status"] in {"pass", "warning"},
            _warning=qc_archive["status"] == "pass",
            details={
                "status": qc_archive["status"],
                "checked_count": qc_archive["checked_count"],
                "semantic_pass_count": qc_archive["semantic_pass_count"],
                "semantic_warning_count": qc_archive["semantic_warning_count"],
                "semantic_fail_count": qc_archive["semantic_fail_count"],
                "latest_optimizer_manifest_hash": (qc_archive.get("latest_artifacts") or [{}])[0].get("optimizer_manifest_hash"),
                "latest_request_hash": (qc_archive.get("latest_artifacts") or [{}])[0].get("request_hash"),
                "latest_qc_report_hash": (qc_archive.get("latest_artifacts") or [{}])[0].get("qc_report_hash"),
                "latest_report_formats_summary_hash": (qc_archive.get("latest_artifacts") or [{}])[0].get("report_formats_summary_hash"),
                "latest_candidate_ranking_hash": (qc_archive.get("latest_artifacts") or [{}])[0].get("candidate_ranking_hash"),
                "latest_recommendation_audit_hash": (qc_archive.get("latest_artifacts") or [{}])[0].get("recommendation_audit_hash"),
                "latest_recommendation_readiness_hash": (qc_archive.get("latest_artifacts") or [{}])[0].get("recommendation_readiness_hash"),
                "latest_recommendation_readiness_status": (qc_archive.get("latest_artifacts") or [{}])[0].get("recommendation_readiness_status"),
                "latest_recommendation_release_ready": (qc_archive.get("latest_artifacts") or [{}])[0].get("recommendation_release_ready"),
                "latest_recommendation_readiness_candidate_id": (qc_archive.get("latest_artifacts") or [{}])[0].get("recommendation_readiness_candidate_id"),
                "latest_candidate_folding_audit_hash": (qc_archive.get("latest_artifacts") or [{}])[0].get("candidate_folding_audit_hash"),
                "latest_candidate_folding_audit_selection_signal": (qc_archive.get("latest_artifacts") or [{}])[0].get("candidate_folding_audit_selection_signal"),
                "latest_candidate_folding_audit_validated_backend_count": (qc_archive.get("latest_artifacts") or [{}])[0].get("candidate_folding_audit_validated_backend_count"),
                "latest_recommended_folding_evidence_hash": (qc_archive.get("latest_artifacts") or [{}])[0].get("recommended_folding_evidence_hash"),
                "latest_recommended_folding_status": (qc_archive.get("latest_artifacts") or [{}])[0].get("recommended_folding_status"),
                "latest_recommended_folding_backend": (qc_archive.get("latest_artifacts") or [{}])[0].get("recommended_folding_backend"),
                "latest_recommended_folding_candidate_id": (qc_archive.get("latest_artifacts") or [{}])[0].get("recommended_folding_candidate_id"),
                "latest_retrieval_quality_status": (qc_archive.get("latest_artifacts") or [{}])[0].get("retrieval_quality_status"),
                "latest_retrieval_quality_source_count": (qc_archive.get("latest_artifacts") or [{}])[0].get("retrieval_quality_source_count"),
                "latest_retrieval_quality_rank_evidence_count": (qc_archive.get("latest_artifacts") or [{}])[0].get("retrieval_quality_rank_evidence_count"),
                "latest_retrieval_quality_rank_evidence_hash": (qc_archive.get("latest_artifacts") or [{}])[0].get("retrieval_quality_rank_evidence_hash"),
                "latest_data_quality_status": (qc_archive.get("latest_artifacts") or [{}])[0].get("data_quality_status"),
                "latest_optimizer_stress_status": (qc_archive.get("latest_artifacts") or [{}])[0].get("optimizer_stress_status"),
                "latest_objective_count": (qc_archive.get("latest_artifacts") or [{}])[0].get("objective_count"),
                **_archive_freshness_details(qc_archive),
            },
            fail_message="Archived QC bundle semantic verification failed.",
            warn_message="Archived QC bundles have semantic warnings.",
        ),
        _gate(
            "data_release_archive_semantics",
            data_release_archive["status"] in {"pass", "warning"},
            _warning=data_release_archive["status"] == "pass",
            details={
                "status": data_release_archive["status"],
                "checked_count": data_release_archive["checked_count"],
                "semantic_pass_count": data_release_archive["semantic_pass_count"],
                "semantic_warning_count": data_release_archive["semantic_warning_count"],
                "semantic_fail_count": data_release_archive["semantic_fail_count"],
                "latest_record_count": (data_release_archive.get("latest_artifacts") or [{}])[0].get("record_count"),
                "latest_records_hash": (data_release_archive.get("latest_artifacts") or [{}])[0].get("records_hash"),
                "latest_records_csv_hash": (data_release_archive.get("latest_artifacts") or [{}])[0].get("records_csv_hash"),
                "latest_record_source_summary_hash": (data_release_archive.get("latest_artifacts") or [{}])[0].get("record_source_summary_hash"),
                "latest_dataset_count": (data_release_archive.get("latest_artifacts") or [{}])[0].get("dataset_count"),
                "latest_source_file_count": (data_release_archive.get("latest_artifacts") or [{}])[0].get("source_file_count"),
                "latest_release_handoff_hash": (data_release_archive.get("latest_artifacts") or [{}])[0].get("release_handoff_hash"),
                "latest_rag_structured_manifest_hash": (data_release_archive.get("latest_artifacts") or [{}])[0].get("rag_structured_manifest_hash"),
                "latest_rag_index_hash": (data_release_archive.get("latest_artifacts") or [{}])[0].get("rag_index_hash"),
                "latest_trna_caveat_count": (data_release_archive.get("latest_artifacts") or [{}])[0].get("trna_caveat_count"),
                "latest_trna_blocking_production_use": (data_release_archive.get("latest_artifacts") or [{}])[0].get("trna_blocking_production_use"),
                "latest_external_snapshot_referenced_count": (data_release_archive.get("latest_artifacts") or [{}])[0].get("external_snapshot_referenced_count"),
                "latest_external_snapshot_contained_count": (data_release_archive.get("latest_artifacts") or [{}])[0].get("external_snapshot_contained_count"),
                "latest_external_snapshot_missing_count": (data_release_archive.get("latest_artifacts") or [{}])[0].get("external_snapshot_missing_count"),
                **_archive_freshness_details(data_release_archive),
            },
            fail_message="Archived data release semantic verification failed.",
            warn_message="Archived data release bundles have semantic warnings.",
        ),
        _gate(
            "data_snapshot_archive_semantics",
            data_snapshot_archive["status"] in {"pass", "warning"},
            _warning=data_snapshot_archive["status"] == "pass",
            details={
                "status": data_snapshot_archive["status"],
                "checked_count": data_snapshot_archive["checked_count"],
                "semantic_pass_count": data_snapshot_archive["semantic_pass_count"],
                "semantic_warning_count": data_snapshot_archive["semantic_warning_count"],
                "semantic_fail_count": data_snapshot_archive["semantic_fail_count"],
                "latest_snapshot_manifest_hash": (data_snapshot_archive.get("latest_artifacts") or [{}])[0].get("snapshot_manifest_hash"),
                "latest_structured_manifest_hash": (data_snapshot_archive.get("latest_artifacts") or [{}])[0].get("structured_manifest_hash"),
                "latest_rag_index_hash": (data_snapshot_archive.get("latest_artifacts") or [{}])[0].get("rag_index_hash"),
                "latest_external_snapshot_file_count": (data_snapshot_archive.get("latest_artifacts") or [{}])[0].get("external_snapshot_file_count"),
                "latest_snapshot_file_count": (data_snapshot_archive.get("latest_artifacts") or [{}])[0].get("snapshot_file_count"),
                **_archive_freshness_details(data_snapshot_archive),
            },
            fail_message="Archived data snapshot semantic verification failed.",
            warn_message="Archived data snapshot bundles are missing, stale, or have semantic warnings.",
        ),
        _gate(
            "data_refresh_plan_archive_semantics",
            data_refresh_plan_archive["status"] in {"pass", "warning"},
            _warning=data_refresh_plan_archive["status"] == "pass",
            details={
                "status": data_refresh_plan_archive["status"],
                "checked_count": data_refresh_plan_archive["checked_count"],
                "semantic_pass_count": data_refresh_plan_archive["semantic_pass_count"],
                "semantic_warning_count": data_refresh_plan_archive["semantic_warning_count"],
                "semantic_fail_count": data_refresh_plan_archive["semantic_fail_count"],
                "latest_operation_count": (data_refresh_plan_archive.get("latest_artifacts") or [{}])[0].get("operation_count"),
                "latest_request_hash": (data_refresh_plan_archive.get("latest_artifacts") or [{}])[0].get("request_hash"),
                "latest_operations_hash": (data_refresh_plan_archive.get("latest_artifacts") or [{}])[0].get("operations_hash"),
                "latest_data_catalog_hash": (data_refresh_plan_archive.get("latest_artifacts") or [{}])[0].get("data_catalog_hash"),
                "latest_external_sources_hash": (data_refresh_plan_archive.get("latest_artifacts") or [{}])[0].get("external_sources_hash"),
                "latest_structured_quality_hash": (data_refresh_plan_archive.get("latest_artifacts") or [{}])[0].get("structured_quality_hash"),
                "latest_data_provenance_hash": (data_refresh_plan_archive.get("latest_artifacts") or [{}])[0].get("data_provenance_hash"),
                "latest_rag_status_hash": (data_refresh_plan_archive.get("latest_artifacts") or [{}])[0].get("rag_status_hash"),
                "latest_validation_status": (data_refresh_plan_archive.get("latest_artifacts") or [{}])[0].get("validation_status"),
                "latest_dataset_id": (data_refresh_plan_archive.get("latest_artifacts") or [{}])[0].get("dataset_id"),
                **_archive_freshness_details(data_refresh_plan_archive),
            },
            fail_message="Archived data refresh plan semantic verification failed.",
            warn_message="Archived data refresh plan bundles have semantic warnings.",
        ),
        _gate(
            "structured_import_archive_semantics",
            import_archive["status"] in {"pass", "warning"},
            _warning=import_archive["status"] == "pass",
            details={
                "status": import_archive["status"],
                "checked_count": import_archive["checked_count"],
                "semantic_pass_count": import_archive["semantic_pass_count"],
                "semantic_warning_count": import_archive["semantic_warning_count"],
                "semantic_fail_count": import_archive["semantic_fail_count"],
                "latest_structured_manifest_hash": (import_archive.get("latest_artifacts") or [{}])[0].get("structured_manifest_hash"),
                "latest_checked_files": (import_archive.get("latest_artifacts") or [{}])[0].get("checked_files"),
                "latest_file_count": (import_archive.get("latest_artifacts") or [{}])[0].get("file_count"),
                **_archive_freshness_details(import_archive),
            },
            fail_message="Archived structured import audit semantic verification failed.",
            warn_message="Archived structured import audit bundles have semantic warnings.",
        ),
        _gate(
            "rag_evaluation_archive_semantics",
            rag_archive["status"] in {"pass", "warning"},
            _warning=rag_archive["status"] == "pass",
            details={
                "status": rag_archive["status"],
                "checked_count": rag_archive["checked_count"],
                "semantic_pass_count": rag_archive["semantic_pass_count"],
                "semantic_warning_count": rag_archive["semantic_warning_count"],
                "semantic_fail_count": rag_archive["semantic_fail_count"],
                "latest_source_provenance_hash": (rag_archive.get("latest_artifacts") or [{}])[0].get("source_provenance_hash"),
                "latest_source_provenance_count": (rag_archive.get("latest_artifacts") or [{}])[0].get("source_provenance_count"),
                "latest_source_payload_hash_count": (rag_archive.get("latest_artifacts") or [{}])[0].get("source_payload_hash_count"),
                "latest_source_snapshot_count": (rag_archive.get("latest_artifacts") or [{}])[0].get("source_snapshot_count"),
                **_archive_freshness_details(rag_archive),
            },
            fail_message="Archived RAG evaluation bundle semantic verification failed.",
            warn_message="Archived RAG evaluation bundles have semantic warnings.",
        ),
        _gate(
            "rag_regression_archive_semantics",
            rag_regression_archive["status"] in {"pass", "warning"},
            _warning=rag_regression_archive["status"] == "pass",
            details={
                "status": rag_regression_archive["status"],
                "checked_count": rag_regression_archive["checked_count"],
                "semantic_pass_count": rag_regression_archive["semantic_pass_count"],
                "semantic_warning_count": rag_regression_archive["semantic_warning_count"],
                "semantic_fail_count": rag_regression_archive["semantic_fail_count"],
                "latest_results_hash": (rag_regression_archive.get("latest_artifacts") or [{}])[0].get("results_hash"),
                "latest_quality_summary_hash": (rag_regression_archive.get("latest_artifacts") or [{}])[0].get("quality_summary_hash"),
                "latest_case_metrics_hash": (rag_regression_archive.get("latest_artifacts") or [{}])[0].get("case_metrics_hash"),
                "latest_quality_status": (rag_regression_archive.get("latest_artifacts") or [{}])[0].get("quality_status"),
                "latest_source_provenance_summary_hash": (rag_regression_archive.get("latest_artifacts") or [{}])[0].get("source_provenance_summary_hash"),
                "latest_source_provenance_case_count": (rag_regression_archive.get("latest_artifacts") or [{}])[0].get("source_provenance_case_count"),
                "latest_source_provenance_source_count": (rag_regression_archive.get("latest_artifacts") or [{}])[0].get("source_provenance_source_count"),
                "latest_source_snapshot_case_count": (rag_regression_archive.get("latest_artifacts") or [{}])[0].get("source_snapshot_case_count"),
                "latest_top_source_count": (rag_regression_archive.get("latest_artifacts") or [{}])[0].get("top_source_count"),
                "latest_missing_term_case_count": (rag_regression_archive.get("latest_artifacts") or [{}])[0].get("missing_term_case_count"),
                **_archive_freshness_details(rag_regression_archive),
            },
            fail_message="Archived RAG regression bundle semantic verification failed.",
            warn_message="Archived RAG regression bundles have semantic warnings.",
        ),
        _gate(
            "rag_vector_index_archive_semantics",
            rag_vector_index_archive["status"] in {"pass", "warning"},
            _warning=rag_vector_index_archive["status"] == "pass",
            details={
                "status": rag_vector_index_archive["status"],
                "checked_count": rag_vector_index_archive["checked_count"],
                "semantic_pass_count": rag_vector_index_archive["semantic_pass_count"],
                "semantic_warning_count": rag_vector_index_archive["semantic_warning_count"],
                "semantic_fail_count": rag_vector_index_archive["semantic_fail_count"],
                "latest_chunk_count": (rag_vector_index_archive.get("latest_artifacts") or [{}])[0].get("chunk_count"),
                "latest_embedding_dimensions": (rag_vector_index_archive.get("latest_artifacts") or [{}])[0].get("embedding_dimensions"),
                "latest_recommended_backend": (rag_vector_index_archive.get("latest_artifacts") or [{}])[0].get("recommended_backend"),
                "latest_migration_target_backend": (rag_vector_index_archive.get("latest_artifacts") or [{}])[0].get("migration_target_backend"),
                "latest_parity_status": (rag_vector_index_archive.get("latest_artifacts") or [{}])[0].get("parity_status"),
                "latest_vector_row_hash": (rag_vector_index_archive.get("latest_artifacts") or [{}])[0].get("vector_row_hash"),
                "latest_structured_manifest_hash": (rag_vector_index_archive.get("latest_artifacts") or [{}])[0].get("structured_manifest_hash"),
                **_archive_freshness_details(rag_vector_index_archive),
            },
            fail_message="Archived RAG vector index bundle semantic verification failed.",
            warn_message="Archived RAG vector index bundles have semantic warnings.",
        ),
        _gate(
            "optimizer_benchmark_archive_semantics",
            optimizer_archive["status"] in {"pass", "warning"},
            _warning=optimizer_archive["status"] == "pass",
            details={
                "status": optimizer_archive["status"],
                "checked_count": optimizer_archive["checked_count"],
                "semantic_pass_count": optimizer_archive["semantic_pass_count"],
                "semantic_warning_count": optimizer_archive["semantic_warning_count"],
                "semantic_fail_count": optimizer_archive["semantic_fail_count"],
                "latest_benchmark_status": (optimizer_archive.get("latest_artifacts") or [{}])[0].get("benchmark_status"),
                "latest_diagnostics_status": (optimizer_archive.get("latest_artifacts") or [{}])[0].get("diagnostics_status"),
                "latest_stress_status": (optimizer_archive.get("latest_artifacts") or [{}])[0].get("stress_status"),
                "latest_case_count": (optimizer_archive.get("latest_artifacts") or [{}])[0].get("case_count"),
                "latest_cases_hash": (optimizer_archive.get("latest_artifacts") or [{}])[0].get("cases_hash"),
                "latest_results_hash": (optimizer_archive.get("latest_artifacts") or [{}])[0].get("results_hash"),
                "latest_benchmark_hash": (optimizer_archive.get("latest_artifacts") or [{}])[0].get("benchmark_hash"),
                "latest_diagnostics_hash": (optimizer_archive.get("latest_artifacts") or [{}])[0].get("diagnostics_hash"),
                "latest_case_metrics_hash": (optimizer_archive.get("latest_artifacts") or [{}])[0].get("case_metrics_hash"),
                "latest_candidate_diagnostics_hash": (optimizer_archive.get("latest_artifacts") or [{}])[0].get("candidate_diagnostics_hash"),
                "latest_recommendation_summary_hash": (optimizer_archive.get("latest_artifacts") or [{}])[0].get("recommendation_summary_hash"),
                "latest_recommendation_summary_status": (optimizer_archive.get("latest_artifacts") or [{}])[0].get("recommendation_summary_status"),
                "latest_recommended_on_pareto_front_count": (optimizer_archive.get("latest_artifacts") or [{}])[0].get("recommended_on_pareto_front_count"),
                "latest_recommendation_max_regret": (optimizer_archive.get("latest_artifacts") or [{}])[0].get("recommendation_max_regret"),
                "latest_case_provenance_hash": (optimizer_archive.get("latest_artifacts") or [{}])[0].get("case_provenance_hash"),
                "latest_case_fingerprint_count": (optimizer_archive.get("latest_artifacts") or [{}])[0].get("case_fingerprint_count"),
                "latest_recommended_folding_evidence_hash": (optimizer_archive.get("latest_artifacts") or [{}])[0].get("recommended_folding_evidence_hash"),
                "latest_recommended_folding_evidence_count": (optimizer_archive.get("latest_artifacts") or [{}])[0].get("recommended_folding_evidence_count"),
                "latest_recommended_folding_candidate_match_count": (optimizer_archive.get("latest_artifacts") or [{}])[0].get("recommended_folding_candidate_match_count"),
                **_archive_freshness_details(optimizer_archive),
            },
            fail_message="Archived optimizer benchmark bundle semantic verification failed.",
            warn_message="Archived optimizer benchmark bundles have semantic warnings.",
        ),
    ]

    counts = {
        "pass": sum(1 for gate in gates if gate["status"] == "pass"),
        "warning": sum(1 for gate in gates if gate["status"] == "warning"),
        "fail": sum(1 for gate in gates if gate["status"] == "fail"),
    }
    status = "fail" if counts["fail"] else "warning" if counts["warning"] else "pass"
    required_actions = _required_actions(gates)
    return {
        "status": status,
        "deployment_ready": counts["fail"] == 0,
        "production_ready": counts["fail"] == 0 and counts["warning"] == 0,
        "summary": counts,
        "attention_gates": [gate["name"] for gate in gates if gate["status"] != "pass"],
        "attention_gates_hash": _hash_payload([gate["name"] for gate in gates if gate["status"] != "pass"]),
        "required_actions": required_actions,
        "required_actions_hash": _hash_payload(required_actions),
        "gates": gates,
    }


def _archive_freshness_details(summary: dict[str, Any]) -> dict[str, Any]:
    policy = summary.get("freshness_policy") or {}
    return {
        "freshness_status": summary.get("freshness_status"),
        "latest_created_at": summary.get("latest_created_at"),
        "latest_age_hours": summary.get("latest_age_hours"),
        "freshness_warning_hours": policy.get("warning_hours"),
    }


def _object_store_mirror_plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": plan.get("status"),
        "candidate_count": int(plan.get("candidate_count") or 0),
        "candidate_bytes": int(plan.get("candidate_bytes") or 0),
        "limit": (plan.get("filters") or {}).get("limit"),
        "candidate_hash": plan.get("candidate_hash"),
        "plan_hash": plan.get("plan_hash"),
    }


def _gate(
    name: str,
    condition: bool,
    *,
    _warning: bool,
    details: dict[str, Any],
    fail_message: str,
    warn_message: str,
) -> dict[str, Any]:
    if not condition:
        status = "fail"
        message = fail_message
    elif not _warning:
        status = "warning"
        message = warn_message
    else:
        status = "pass"
        message = "Gate passed."
    return {"name": name, "status": status, "message": message, "details": details}


def _required_actions(gates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions = []
    for gate in gates:
        if gate["status"] == "pass":
            continue
        actions.append(
            {
                "gate": gate["name"],
                "status": gate["status"],
                "priority": "blocking" if gate["status"] == "fail" else "promotion",
                "message": gate["message"],
                "action": _gate_action(gate),
                "detail_hash": _hash_payload(gate.get("details") or {}),
            }
        )
    return actions


def _gate_action(gate: dict[str, Any]) -> str:
    name = gate["name"]
    details = gate.get("details") or {}
    if name == "structured_data":
        return "Resolve structured validation errors and warnings, then rebuild manifests and RAG evidence."
    if name == "data_provenance":
        caveats = details.get("trna_prior_caveats") or {}
        return str(caveats.get("operator_action") or "Refresh provenance, release locks, and external source snapshots.")
    if name == "structured_quality":
        actions = details.get("operator_actions") or []
        return str(actions[0]) if actions else "Replace seed/local priors with release-pinned structured data."
    if name == "data_release_bundle":
        return "Regenerate the data release bundle after resolving promotion caveats and missing snapshot evidence."
    if name == "rag_embedding_backend":
        return "Configure a pinned biomedical sentence-transformers model or OpenAI embedding backend, rebuild the RAG index, and rerun regression."
    if name == "rna_folding_backend":
        return "Install ViennaRNA RNAfold, set RNA_FOLDING_BACKEND=rnafold, and rerun optimizer diagnostics."
    if name == "storage":
        return "Use STORAGE_BACKEND=postgres with DATABASE_URL and verify storage migration parity."
    if name == "security":
        return "Enable API auth, explicit API_KEY_ROLES, and positive RATE_LIMIT_PER_MINUTE."
    if name == "artifact_signing":
        return "Configure HMAC or Ed25519 artifact signing before promotion."
    if name == "artifact_object_store":
        return str(details.get("recommendation") or "Configure managed object-store mirroring for immutable archive retention.")
    if name == "qc_bundle_archive_semantics":
        return "Export a fresh QC report bundle, verify retrieval/data/stress evidence, and archive it before promotion."
    if name == "data_release_archive_semantics":
        return "Export and archive a fresh data release bundle with record hashes, RAG index hash, tRNA caveats, and source snapshots."
    if name == "data_snapshot_archive_semantics":
        return "Export and archive a fresh data snapshot bundle with snapshot manifest, structured manifest, RAG index, and external snapshot bytes."
    if name == "data_refresh_plan_archive_semantics":
        return "Export and archive a fresh data refresh plan bundle with planned operations, catalog/source hashes, validation, and release-lock evidence."
    if name == "structured_import_archive_semantics":
        return "Archive a semantically verified structured import audit bundle for the latest source ingest."
    if name == "rag_evaluation_archive_semantics":
        return "Export and archive a RAG evaluation bundle with retrieval trace, evidence sufficiency, facet-gap, query-term, and source-hash evidence."
    if name == "rag_regression_archive_semantics":
        return "Export and archive a RAG regression bundle with case/result/quality-summary hashes and weak-case evidence."
    if name == "rag_vector_index_archive_semantics":
        return "Export and archive a RAG vector index bundle with chunk, embedding, migration, parity, and structured-manifest evidence."
    if name == "workflow_trace_archive_semantics":
        return "Export and archive workflow trace evidence with task plan, role contract, trace hash, and structured-manifest hash."
    if name == "optimizer_benchmark_archive_semantics":
        return "Export and archive an optimizer benchmark bundle with result, diagnostics, stress, case, Pareto, candidate-diagnostics, recommendation-summary, and folding-evidence hashes."
    if name.endswith("_archive_semantics"):
        return "Export a fresh semantic artifact bundle and verify archive freshness before promotion."
    if name == "agent_memory":
        return "Persist at least one reviewed design run so agent memory has reusable design evidence."
    return gate["message"]


def _signing_ready(signing: dict[str, Any]) -> bool:
    hmac = signing.get("hmac") or {}
    ed25519 = signing.get("ed25519") or {}
    return bool(
        hmac.get("signing_enabled")
        or hmac.get("verification_enabled")
        or ed25519.get("signing_enabled")
        or ed25519.get("verification_enabled")
    )


def _hash_payload(payload: Any) -> str:
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
