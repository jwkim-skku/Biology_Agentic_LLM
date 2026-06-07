from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from io import BytesIO
from zipfile import ZipFile


BASE_URL = os.getenv("SMOKE_BASE_URL", "http://127.0.0.1:8000/api/v1")
API_KEY = os.getenv("SMOKE_API_KEY") or os.getenv("NEXT_PUBLIC_API_KEY") or ""
REQUIRE_SIGNING = os.getenv("SMOKE_REQUIRE_SIGNING", "").lower() in {"1", "true", "yes"}


def main() -> int:
    for _ in range(30):
        try:
            health = get_json("/health/ready")
            if health["data"]["status"] in {"ready", "degraded"}:
                break
        except OSError:
            time.sleep(0.5)
    else:
        print("Backend did not become reachable.", file=sys.stderr)
        return 1

    checks = {
        "health_ready": get_json("/health/ready")["data"]["checks"]["rag_chunks"] > 0,
        "deployment_readiness": smoke_deployment_readiness(),
        "production_audit": smoke_production_audit(),
        "settings": "data_dir" in get_json("/settings")["data"],
        "security_status": smoke_security_status(),
        "storage_status": smoke_storage_status(),
        "storage_migration": smoke_storage_migration(),
        "governance_attestation": smoke_governance_attestation(),
        "rag_status": get_json("/rag/status")["data"]["chunks"] > 0,
        "rag_diagnostics": smoke_rag_diagnostics(),
        "rag_regression": get_json("/rag/regression")["data"]["status"] in {"pass", "warning"},
        "rag_embedding_backend": get_json("/rag/embedding/status")["data"]["active_backend"] in {"hash_bow", "sentence_transformers"},
        "structured_status": get_json("/structured/status")["data"]["records"] > 0,
        "structured_import_preview": smoke_structured_import_preview(),
        "data_catalog": smoke_data_catalog(),
        "external_sources": smoke_external_sources(),
        "metrics": smoke_metrics(),
        "agent_memory": smoke_agent_memory(),
        "workflow_runtime": smoke_workflow_runtime(),
        "optimizer_benchmark": get_json("/optimizer/benchmark")["data"]["status"] in {"pass", "warning"},
        "optimizer_diagnostics": smoke_optimizer_diagnostics(),
        "report_pipeline": smoke_report_pipeline(),
        "data_release_lock": smoke_data_release_lock(),
        "data_release_bundle": smoke_data_release_bundle(),
        "data_snapshot_archive": smoke_data_snapshot_archive(),
        "data_refresh_job": smoke_data_refresh_job()["ok"],
        "artifact_archive": smoke_artifact_archive(),
        "artifact_retention": smoke_artifact_retention(),
        "artifact_ledger": smoke_artifact_ledger(),
        "audit_summary": smoke_audit_summary(),
    }
    failed = [name for name, ok in checks.items() if not ok]
    print(json.dumps({"checks": checks, "failed": failed}, indent=2))
    return 1 if failed else 0


def smoke_data_refresh_job() -> dict:
    payload = {"genes": ["SNCA"], "include_allen": False, "dry_run": True}
    created = post_json("/jobs/data-refresh", payload)["data"]
    job_id = created["job_id"]
    for _ in range(20):
        job = get_json(f"/jobs/{job_id}")["data"]
        if job["status"] == "succeeded":
            get_bytes(f"/jobs/{job_id}/export.zip")
            export_verify = get_json(f"/jobs/{job_id}/export/verify")["data"]
            return {
                "ok": job.get("result", {}).get("refresh_status") == "planned" and export_verify["status"] in {"pass", "warning"},
                "job_id": job_id,
                "export_verify": export_verify["status"],
            }
        if job["status"] == "failed":
            return {"ok": False, "job_id": job_id, "export_verify": "not_checked"}
        time.sleep(0.25)
    return {"ok": False, "job_id": job_id, "export_verify": "timeout"}


def smoke_structured_import_preview() -> bool:
    manifest = get_json("/structured/manifest")["data"]
    files = manifest.get("files") or []
    if not files:
        return False
    preview = post_json(
        "/structured/import/preview",
        {"source_path": files[0]["path"], "rebuild_index": False},
    )["data"]
    return (
        preview["status"] in {"pass", "warning"}
        and preview["source"]["sha256"] == files[0]["sha256"]
        and preview["records"]["import_count"] == files[0]["records"]
        and preview["validation"]["projected"]["error_count"] == 0
        and bool(preview["manifest"]["projected_hash"])
    )


def smoke_deployment_readiness() -> bool:
    readiness = get_json("/deployment/readiness", timeout=60)["data"]
    gates = {gate["name"]: gate for gate in readiness["gates"]}
    required = {
        "structured_data",
        "data_provenance",
        "rag_regression",
        "rag_embedding_backend",
        "optimizer_benchmark",
        "rna_folding_backend",
        "workflow_runtime",
        "agent_memory",
        "qc_snapshot_export",
        "storage",
        "security",
        "artifact_signing",
        "governance_attestation",
        "artifact_archive",
        "artifact_object_store",
        "qc_bundle_archive_semantics",
        "structured_import_archive_semantics",
        "rag_evaluation_archive_semantics",
        "rag_regression_archive_semantics",
        "optimizer_benchmark_archive_semantics",
    }
    signing_ok = True
    if REQUIRE_SIGNING:
        signing_ok = gates.get("artifact_signing", {}).get("status") == "pass"
    provenance_caveats = gates.get("data_provenance", {}).get("details", {}).get("trna_prior_caveats", {})
    return (
        readiness["status"] in {"pass", "warning"}
        and readiness["deployment_ready"] is True
        and required.issubset(gates)
        and readiness["summary"]["fail"] == 0
        and all(gate["status"] in {"pass", "warning"} for gate in gates.values())
        and provenance_caveats.get("status") in {"pass", "warning"}
        and isinstance(provenance_caveats.get("caveat_count"), int)
        and signing_ok
    )


def smoke_production_audit() -> bool:
    audit = get_json("/deployment/audit?refresh=true", timeout=180)["data"]
    cache = get_json("/deployment/audit/cache")["data"]
    verification = get_json("/deployment/audit/verify", timeout=60)["data"]
    bundle = get_bytes("/deployment/audit/export.zip", timeout=60)
    checks = {item["name"]: item for item in audit.get("checks", [])}
    qc_archive = audit.get("evidence", {}).get("qc_bundle_archive_semantics") or {}
    object_store = audit.get("evidence", {}).get("artifact_object_store") or {}
    embedding = audit.get("evidence", {}).get("rag_embedding") or {}
    folding = audit.get("evidence", {}).get("rna_folding") or {}
    workflow_runtime = audit.get("evidence", {}).get("workflow_runtime") or {}
    import_archive = audit.get("evidence", {}).get("structured_import_archive_semantics") or {}
    rag_regression_archive = audit.get("evidence", {}).get("rag_regression_archive_semantics") or {}
    timings = audit.get("evidence", {}).get("timings") or {}
    signature_ok = True
    if REQUIRE_SIGNING:
        signature_ok = (
            bool(audit.get("audit_signatures"))
            and verification.get("signature", {}).get("status") == "verified"
            and verification.get("artifact_verification", {}).get("signature", {}).get("status") == "verified"
        )
    return (
        audit["audit_schema"] == "agentic-rag-production-audit-v1"
        and audit.get("cache_policy", {}).get("ttl_seconds", 0) > 0
        and audit["summary"]["status"] in {"pass", "warning"}
        and audit["summary"]["deployment_ready"] is True
        and "qc_bundle_archive_semantics" in checks
        and "artifact_object_store" in checks
        and "rag_embedding_backend" in checks
        and "rna_folding_backend" in checks
        and "workflow_runtime" in checks
        and "structured_import_archive_semantics" in checks
        and "rag_regression_archive_semantics" in checks
        and object_store.get("status_schema") == "agentic-rag-artifact-object-store-v1"
        and object_store.get("status") in {"disabled", "ready", "misconfigured"}
        and embedding.get("embedding_schema") == "agentic-rag-embedding-backend-v1"
        and embedding.get("active_backend") in {"hash_bow", "sentence_transformers"}
        and folding.get("folding_schema") == "agentic-rag-rna-folding-v1"
        and folding.get("status") in {"ready", "proxy", "fallback"}
        and workflow_runtime.get("runtime_schema") == "agentic-rag-workflow-runtime-v1"
        and workflow_runtime.get("status") == "pass"
        and qc_archive.get("status") in {"pass", "warning"}
        and isinstance(qc_archive.get("latest_artifacts"), list)
        and import_archive.get("status") in {"pass", "warning"}
        and isinstance(import_archive.get("latest_artifacts"), list)
        and rag_regression_archive.get("status") in {"pass", "warning"}
        and isinstance(rag_regression_archive.get("latest_artifacts"), list)
        and timings.get("total_seconds", 0) > 0
        and isinstance(timings.get("slowest"), list)
        and cache.get("status") in {"hit", "expired"}
        and cache.get("audit_hash") == audit.get("audit_hash")
        and verification["status"] in {"pass", "warning"}
        and verification["audit_hash"]
        and verification["artifact_verification"]["checked_files"] == verification["artifact_verification"]["file_count"]
        and len(bundle) > 0
        and signature_ok
    )


def smoke_storage_status() -> bool:
    status = get_json("/storage/status")["data"]
    schema = get_bytes("/storage/postgres/schema.sql").decode("utf-8")
    return (
        status["active_runtime_adapter"] == "sqlite"
        and status["postgres"]["schema_hash"]
        and "create table if not exists runs" in schema
        and "create table if not exists audit_events" in schema
    )


def smoke_security_status() -> bool:
    status = get_json("/security/status")["data"]
    signing_ok = True
    if REQUIRE_SIGNING:
        signing_ok = (
            status["artifact_signing_enabled"] is True
            and status["signing"]["hmac"]["signing_enabled"] is True
            and status["signing"]["hmac"]["verification_enabled"] is True
        )
    return (
        "signing" in status
        and status["api_key_header"] == "X-API-Key"
        and status["bearer_auth_supported"] is True
        and status["rate_limit_per_minute"] >= 0
        and {"viewer", "operator", "admin"}.issubset(set(status["roles"]))
        and "/api/v1/health/ready" in status["public_paths"]
        and "admin" in status["role_policy"]
        and signing_ok
    )


def smoke_metrics() -> bool:
    metrics = get_json("/metrics")["data"]
    prometheus = get_bytes("/metrics/prometheus").decode("utf-8")
    return (
        isinstance(metrics.get("requests"), list)
        and metrics.get("uptime_seconds", 0) >= 0
        and metrics.get("stores", {}).get("runs_observed", 0) >= 0
        and metrics.get("agent_memory", {}).get("memory_schema") == "agentic-rag-agent-memory-v1"
        and metrics.get("rag", {}).get("chunks", 0) > 0
        and metrics.get("structured", {}).get("records", 0) > 0
        and "app_http_requests_total" in prometheus
        and "app_runs_observed" in prometheus
    )


def smoke_agent_memory() -> bool:
    payload = {
        "cds": "ATGGCTGACGAGTTCGCCAAGGGTTACTAA",
        "target": {
            "gene": "MEMSMOKE",
            "species": "human",
            "brain_region": "striatum",
            "cell_type": "medium spiny neuron",
            "modality": "AAV",
        },
        "optimization_settings": {
            "population_size": 8,
            "generations": 1,
            "max_candidates": 2,
            "seed": 23,
        },
    }
    design = post_json("/optimize", payload)["data"]
    run_id = design["run_id"]
    summary = get_json("/agent-memory/summary")["data"]
    listing = get_json("/agent-memory?limit=5&gene=MEMSMOKE")["data"]
    memory = get_json(f"/agent-memory/{run_id}")["data"]
    semantic = memory.get("semantic_memory") or {}
    artifact = memory.get("artifact_memory") or {}
    return (
        summary.get("memory_schema") == "agentic-rag-agent-memory-v1"
        and summary.get("memory_count", 0) >= 1
        and any(item.get("run_id") == run_id for item in listing.get("memories") or [])
        and memory.get("run_id") == run_id
        and artifact.get("recommended_candidate_id") == (design.get("recommended_candidate") or {}).get("candidate_id")
        and ((semantic.get("candidate_diagnostics") or {}).get("recommendation_audit") or {}).get("audit_schema")
        == "agentic-rag-recommendation-audit-v1"
    )


def smoke_workflow_runtime() -> bool:
    status = get_json("/workflow/status")["data"]
    plan = post_json(
        "/workflow/plan",
        {
            "gene": "SNCA",
            "species": "human",
            "target": {"brain_region": "substantia nigra", "cell_type": "dopaminergic neuron", "modality": "AAV"},
        },
    )["data"]
    return (
        status["runtime_schema"] == "agentic-rag-workflow-runtime-v1"
        and status["active_runtime"] == "local_deterministic_orchestrator"
        and any(role["agent"] == "planner" for role in status["agent_roles"])
        and plan["workflow_runtime"]["runtime_schema"] == status["runtime_schema"]
        and "canonical_transcript_resolver" in plan["task"]["required_tools"]
    )


def smoke_rag_diagnostics() -> bool:
    diagnostics = get_json("/rag/diagnostics")["data"]
    embedding_status = get_json("/rag/embedding/status")["data"]
    vector = diagnostics.get("vector_store_readiness") or {}
    vector_runtime = get_json("/rag/vector-store/status")["data"]
    vector_import_plan = get_json("/rag/vector-store/import/plan?target_backend=pgvector")["data"]
    vector_import_dry_run = post_json("/rag/vector-store/import?target_backend=pgvector&dry_run=true", {})["data"]
    vector_parity = get_json("/rag/vector-store/parity?target_backend=local_json")["data"]
    payload = {
        "query": "SNCA substantia nigra dopaminergic neuron AAV",
        "filters": {
            "species": "human",
            "brain_region": "substantia nigra",
            "cell_type": "dopaminergic neuron",
            "modality": "AAV",
        },
        "limit": 5,
    }
    evaluation = post_json(
        "/rag/evaluate",
        payload,
    )["data"]
    bundle = get_post_bytes("/rag/evaluate/export.zip", payload)
    bundle_files = _zip_names(bundle)
    bundle_verify = post_json("/rag/evaluate/export/verify", payload)["data"]
    regression_bundle = get_bytes("/rag/regression/export.zip")
    regression_bundle_files = _zip_names(regression_bundle)
    regression_bundle_verify = get_json("/rag/regression/export/verify")["data"]
    vector_bundle = get_bytes("/rag/vector-index/export.zip")
    vector_bundle_files = _zip_names(vector_bundle)
    vector_bundle_verify = get_json("/rag/vector-index/export/verify")["data"]
    return (
        diagnostics["status"] in {"pass", "warning"}
        and diagnostics["embedding_backend"]["embedding_schema"] == "agentic-rag-embedding-backend-v1"
        and embedding_status["embedding_schema"] == "agentic-rag-embedding-backend-v1"
        and embedding_status["active_backend"] in {"hash_bow", "sentence_transformers"}
        and diagnostics["embedding_backend"]["active_backend"] == embedding_status["active_backend"]
        and diagnostics["index"]["chunk_count"] > 0
        and diagnostics["index"]["chunking_policy"]["version"] == "lexical-window-v2"
        and diagnostics["token_stats"]["long_chunks"] == 0
        and diagnostics["distributions"]["sources"]
        and diagnostics["regression"]["macro"]["recall_at_k"] >= 0
        and vector.get("readiness_schema") == "agentic-rag-vector-store-readiness-v1"
        and vector_runtime.get("runtime_schema") == "agentic-rag-vector-store-runtime-v1"
        and vector_import_plan.get("migration_schema") == "agentic-rag-vector-store-migration-v1"
        and vector_import_plan.get("status") == "planned"
        and vector_import_plan.get("target_backend") == "pgvector"
        and vector_import_dry_run.get("dry_run") is True
        and vector_import_dry_run.get("source", {}).get("records") == diagnostics["index"]["chunk_count"]
        and vector_parity.get("status") == "pass"
        and vector_parity.get("comparison", {}).get("row_hash_match") is True
        and vector.get("active_backend") == "local_json"
        and vector_runtime.get("active_backend") == vector.get("active_backend")
        and vector_runtime.get("target_backend") in {"local_json", "pgvector", "qdrant"}
        and vector.get("recommended_backend") in {"local_json", "pgvector", "qdrant"}
        and vector.get("migration_contract", {}).get("distance") == "cosine"
        and bool(diagnostics["recommendations"])
        and evaluation["retrieval_trace"]["trace_schema"] == "agentic-rag-retrieval-trace-v1"
        and evaluation["facet_gap_analysis"]["analysis_schema"] == "agentic-rag-facet-gap-analysis-v1"
        and evaluation["query_term_coverage"]["coverage_schema"] == "agentic-rag-query-term-coverage-v1"
        and "brain_region" in evaluation["facet_gap_analysis"]["facets"]
        and "query_term_coverage_fraction" in evaluation["retrieval_trace"]
        and bool(evaluation["query_fingerprint"])
        and all(item["rationale"] for item in evaluation["score_breakdown"])
        and len(bundle) > 0
        and {"evaluation.json", "retrieval_trace.json", "score_breakdown.csv", "chunks.jsonl"}.issubset(set(bundle_files))
        and bundle_verify["status"] in {"pass", "warning"}
        and bundle_verify["semantic_status"] == "pass"
        and bundle_verify["query_fingerprint"] == evaluation["query_fingerprint"]
        and bundle_verify["result_count"] == evaluation["result_count"]
        and len(regression_bundle) > 0
        and {"regression.json", "cases.json", "case_metrics.csv", "weak_cases.json", "diagnostics.json"}.issubset(set(regression_bundle_files))
        and regression_bundle_verify["status"] in {"pass", "warning"}
        and regression_bundle_verify["semantic_status"] == "pass"
        and regression_bundle_verify["case_count"] == diagnostics["regression"]["case_count"]
        and regression_bundle_verify["semantic_checks"].get("cases_hash") == "pass"
        and regression_bundle_verify["semantic_checks"].get("case_metrics_rows") == "pass"
        and len(vector_bundle) > 0
        and {
            "vector_chunks.jsonl",
            "payload_schema.json",
            "embedding_status.json",
            "pgvector_schema.sql",
            "qdrant_collection.json",
            "rag_diagnostics.json",
        }.issubset(set(vector_bundle_files))
        and vector_bundle_verify["status"] in {"pass", "warning"}
        and vector_bundle_verify["semantic_status"] in {"pass", "warning"}
        and vector_bundle_verify["semantic_checks"].get("embedding_status_schema") == "pass"
        and vector_bundle_verify["chunk_count"] == diagnostics["index"]["chunk_count"]
        and vector_bundle_verify["recommended_backend"] in {"local_json", "pgvector", "qdrant"}
    )


def smoke_optimizer_diagnostics() -> bool:
    diagnostics = get_json("/optimizer/diagnostics")["data"]
    stress = get_json("/optimizer/stress")["data"]
    folding = get_json("/optimizer/rna-folding/status")["data"]
    folding_eval = post_json("/optimizer/rna-folding/evaluate", {"cds": "ATGGCTGACGAGTTCGCCAAGGGTTACTAA"})["data"]
    benchmark = get_json("/optimizer/benchmark")["data"]
    bundle = get_bytes("/optimizer/benchmark/export.zip", timeout=90)
    bundle_files = _zip_names(bundle)
    bundle_verify = get_json("/optimizer/benchmark/export/verify", timeout=90)["data"]
    return (
        diagnostics["status"] in {"pass", "warning"}
        and diagnostics["diagnostics_schema"] == "agentic-rag-optimizer-diagnostics-v1"
        and stress["stress_schema"] == "agentic-rag-optimizer-stress-gate-v1"
        and stress["status"] in {"pass", "warning", "fail"}
        and folding["folding_schema"] == "agentic-rag-rna-folding-v1"
        and folding["status"] in {"ready", "proxy", "fallback"}
        and folding_eval["folding_schema"] == "agentic-rag-rna-folding-v1"
        and folding_eval["active_backend"] in {"rnafold", "deterministic_proxy"}
        and stress["case_count"] == benchmark["case_count"]
        and diagnostics["stress_gate"]["stress_schema"] == stress["stress_schema"]
        and diagnostics["rna_folding"]["folding_schema"] == folding["folding_schema"]
        and diagnostics["benchmark"]["case_count"] >= 1
        and diagnostics["quality_bands"]["constraint_control"] in {"pass", "warning", "fail"}
        and diagnostics["quality_bands"]["recommendation_regret"] in {"pass", "warning", "fail"}
        and bool(diagnostics["recommendations"])
        and len(bundle) > 0
        and {"benchmark.json", "diagnostics.json", "stress_gate.json", "rna_folding_status.json", "case_metrics.csv", "candidate_diagnostics.json"}.issubset(set(bundle_files))
        and bundle_verify["status"] in {"pass", "warning"}
        and bundle_verify["semantic_status"] == "pass"
        and bundle_verify["semantic_checks"].get("stress_schema") == "pass"
        and bundle_verify["semantic_checks"].get("rna_folding_schema") == "pass"
        and bundle_verify["semantic_checks"].get("recommendation_audit") == "pass"
        and bundle_verify["cases_hash"] == benchmark["cases_hash"]
        and bundle_verify["case_count"] == benchmark["case_count"]
    )


def smoke_storage_migration() -> bool:
    summary = get_json("/storage/migration/sqlite/summary")["data"]
    parity = get_json("/storage/migration/sqlite/parity")["data"]
    import_plan = post_json("/storage/migration/sqlite/import?dry_run=true", {})["data"]
    bundle = get_bytes("/storage/migration/sqlite/export.zip")
    return (
        summary["status"] == "ready"
        and summary["manifest"]["total_records"] >= 1
        and all(item["row_fingerprint"]["hash_count"] == item["records"] for item in summary["manifest"]["tables"])
        and parity["source"]["total_records"] >= 1
        and all(item["source_row_hash"] for item in parity["comparisons"])
        and import_plan["status"] == "planned"
        and import_plan["total_records"] >= 1
        and summary["bytes"] > 0
        and len(bundle) > 0
    )


def smoke_governance_attestation() -> bool:
    attestation = get_json("/governance/attestation")["data"]
    verification = get_json("/governance/attestation/verify")["data"]
    bundle = get_bytes("/governance/attestation/export.zip")
    signature_ok = True
    if REQUIRE_SIGNING:
        signature_ok = (
            bool(attestation.get("attestation_signatures"))
            and verification.get("signature", {}).get("status") == "verified"
            and verification.get("artifact_verification", {}).get("signature", {}).get("status") == "verified"
        )
    return (
        bool(attestation.get("attestation_hash"))
        and attestation["openapi"]["path_count"] >= 1
        and verification["status"] in {"pass", "warning"}
        and verification["attestation_hash"]
        and len(bundle) > 0
        and signature_ok
    )


def smoke_report_pipeline() -> bool:
    payload = {
        "cds": "ATGGCTGACGAGTTCGCCAAGGGTTACTAA",
        "optimization_settings": {
            "population_size": 16,
            "generations": 2,
            "max_candidates": 2,
            "seed": 17,
        },
    }
    report = post_json("/report", payload)["data"]
    optimizer_reproducibility = report.get("optimizer_reproducibility") or {}
    data_quality = report.get("data_quality") or {}
    optimizer_stress = report.get("optimizer_stress") or {}
    recommendation_audit = report.get("recommendation_audit") or {}
    recommended = report.get("recommended_candidate") or {}
    recommended_scores = (report.get("score_summary") or {}).get("recommended") or {}
    if (
        not report.get("sequence_policy")
        or not report.get("project_metadata", {}).get("run_id")
        or not optimizer_reproducibility.get("manifest_hash")
        or len(optimizer_reproducibility.get("objective_inventory") or []) < 5
        or data_quality.get("quality_schema") != "agentic-rag-structured-quality-gate-v1"
        or data_quality.get("status") not in {"pass", "warning", "fail"}
        or optimizer_stress.get("stress_schema") != "agentic-rag-optimizer-stress-gate-v1"
        or optimizer_stress.get("status") not in {"pass", "warning", "fail"}
        or "minimize_secondary_structure_proxy" not in (optimizer_reproducibility.get("objective_inventory") or [])
        or recommendation_audit.get("audit_schema") != "agentic-rag-recommendation-audit-v1"
        or recommendation_audit.get("hard_constraint_status") not in {"pass", "warning", "fail", "missing"}
        or "secondary_structure_proxy_score" not in recommended_scores
        or "mfe_proxy_delta_g" not in recommended_scores
        or not recommended.get("selection_trace")
        or (recommended.get("constraint_risk") or {}).get("status") not in {"pass", "warning", "fail"}
    ):
        return False
    run_id = report["project_metadata"]["run_id"]
    bundle = get_post_bytes("/report/export-bundle.zip", payload)
    bundle_files = _zip_names(bundle)
    bundle_verify = post_json("/report/export-bundle/verify", payload)["data"]
    semantic_checks = bundle_verify.get("semantic_checks") or {}
    get_bytes(f"/runs/{run_id}/export.zip")
    export_verify = get_json(f"/runs/{run_id}/export/verify")["data"]
    workflow_bundle = get_bytes(f"/runs/{run_id}/workflow/export.zip")
    workflow_verify = get_json(f"/runs/{run_id}/workflow/export/verify")["data"]
    signature_ok = True
    if REQUIRE_SIGNING:
        signature_ok = export_verify.get("signature", {}).get("status") == "verified"
    return (
        export_verify["status"] in {"pass", "warning"}
        and export_verify["checked_files"] == export_verify["file_count"]
        and len(workflow_bundle) > 0
        and workflow_verify["status"] in {"pass", "warning"}
        and workflow_verify["semantic_status"] == "pass"
        and workflow_verify["semantic_checks"].get("trace_hash") == "pass"
        and workflow_verify["trace_step_count"] >= 1
        and len(bundle) > 0
        and "optimizer_reproducibility.json" in bundle_files
        and "data_quality.json" in bundle_files
        and "optimizer_stress.json" in bundle_files
        and bundle_verify["status"] in {"pass", "warning"}
        and bundle_verify.get("semantic_status") == "pass"
        and bundle_verify.get("optimizer_manifest_hash") == optimizer_reproducibility.get("manifest_hash")
        and bundle_verify.get("data_quality_status") == data_quality.get("status")
        and bundle_verify.get("optimizer_stress_status") == optimizer_stress.get("status")
        and semantic_checks.get("data_quality_schema") == "pass"
        and semantic_checks.get("optimizer_stress_schema") == "pass"
        and semantic_checks.get("data_quality_report_status") == "pass"
        and semantic_checks.get("optimizer_stress_report_status") == "pass"
        and semantic_checks.get("candidate_csv_explainability_columns") == "pass"
        and semantic_checks.get("recommended_constraint_risk_csv") == "pass"
        and semantic_checks.get("recommendation_audit") == "pass"
        and bundle_verify["checked_files"] == bundle_verify["file_count"]
        and signature_ok
    )


def smoke_data_snapshot_archive() -> bool:
    get_bytes("/data/snapshot.zip")
    verification = get_json("/data/snapshot/verify")["data"]
    signature_ok = True
    if REQUIRE_SIGNING:
        signature_ok = verification.get("signature", {}).get("status") == "verified"
    return verification["status"] in {"pass", "warning"} and signature_ok


def smoke_data_release_lock() -> bool:
    written = post_json("/data/release-lock/write", {})["data"]
    verified = get_json("/data/release-lock")["data"]
    return written["status"] == "current" and verified["status"] == "current" and written["current_hash"] == verified["current_hash"]


def smoke_data_release_bundle() -> bool:
    bundle = get_bytes("/data/release/export.zip")
    files = _zip_names(bundle)
    verification = get_json("/data/release/export/verify")["data"]
    return (
        len(bundle) > 0
        and {"release_manifest.json", "structured_quality.json", "data_provenance.json", "records.jsonl", "records.csv"}.issubset(files)
        and verification["status"] in {"pass", "warning"}
        and verification["semantic_status"] in {"pass", "warning"}
        and verification["semantic_checks"].get("required_files") == "pass"
        and verification["semantic_checks"].get("record_rows") == "pass"
        and verification["promotion_status"] in {"pass", "warning"}
    )


def smoke_data_catalog() -> bool:
    catalog = get_json("/data/catalog")["data"]
    coverage = get_json("/data/coverage")["data"]
    quality = get_json("/data/quality")["data"]
    refresh = get_json("/data/refresh-log?limit=3")["data"]
    refresh_plan_payload = {
        "genes": ["SNCA"],
        "brain_regions": ["substantia nigra"],
        "include_gtex": True,
        "include_allen": False,
        "dataset_id": "gtex_v8",
        "dry_run": True,
    }
    validation = post_json("/data/refresh/validate", refresh_plan_payload)["data"]
    refresh_bundle = get_post_bytes("/data/refresh/plan/export.zip", refresh_plan_payload)
    refresh_bundle_files = _zip_names(refresh_bundle)
    refresh_bundle_verify = post_json("/data/refresh/plan/export/verify", refresh_plan_payload)["data"]
    source_ids = {source["id"] for source in catalog["sources"]}
    return (
        {"gtex_gene_expression", "allen_whb_taxonomy", "custom_codon_priors"}.issubset(source_ids)
        and catalog["status"]["records"] > 0
        and coverage["coverage_schema"] == "agentic-rag-structured-coverage-v1"
        and quality["quality_schema"] == "agentic-rag-structured-quality-gate-v1"
        and quality["status"] in {"pass", "warning", "fail"}
        and quality["record_count"] == catalog["status"]["records"]
        and quality["summary"]["warning_count_total"] >= 0
        and bool(quality["operator_actions"])
        and coverage["record_count"] == catalog["status"]["records"]
        and coverage["production_readiness"]["status"] in {"pass", "warning"}
        and isinstance(coverage["gene_region_matrix"], list)
        and isinstance(coverage["cell_type_matrix"], list)
        and isinstance(refresh["entries"], list)
        and validation["status"] in {"pass", "warning"}
        and validation["plan"]["operation_count"] == 1
        and validation["normalized_request"]["genes"] == ["SNCA"]
        and len(refresh_bundle) > 0
        and {"refresh_plan.json", "validation.json", "operations.csv", "structured_manifest.json", "structured_quality.json"}.issubset(set(refresh_bundle_files))
        and refresh_bundle_verify["status"] in {"pass", "warning"}
        and refresh_bundle_verify["semantic_status"] in {"pass", "warning"}
        and refresh_bundle_verify["operation_count"] == 1
        and refresh_bundle_verify["semantic_checks"].get("required_files") == "pass"
        and refresh_bundle_verify["semantic_checks"].get("operation_count_csv") == "pass"
    )


def smoke_external_sources() -> bool:
    status = get_json("/data/external-sources")["data"]
    backfill = post_json("/data/external-sources/backfill?dry_run=true", {})["data"]
    coverage = status.get("coverage") or {}
    return (
        "file_count" in status
        and "source_snapshot_path_fraction" in coverage
        and backfill["dry_run"] is True
        and "candidate_record_count" in backfill
    )


def smoke_artifact_archive() -> bool:
    summary = get_json("/artifacts/summary")["data"]
    object_status = get_json("/artifacts/object-store/status")["data"]
    object_plan = get_json("/artifacts/object-store/mirror/plan?limit=5")["data"]
    object_dry_run = post_json("/artifacts/object-store/mirror?dry_run=true&limit=5", {})["data"]
    qc_semantics = get_json("/artifacts/qc-bundles/semantic-summary?limit=5&verify_files=false")["data"]
    import_semantics = get_json("/artifacts/structured-imports/semantic-summary?limit=5&verify_files=false")["data"]
    rag_semantics = get_json("/artifacts/rag-evaluations/semantic-summary?limit=5&verify_files=false")["data"]
    rag_regression_semantics = get_json("/artifacts/rag-regressions/semantic-summary?limit=5&verify_files=false")["data"]
    optimizer_semantics = get_json("/artifacts/optimizer-benchmarks/semantic-summary?limit=5&verify_files=false")["data"]
    artifacts = get_json("/artifacts?limit=5")["data"]["artifacts"]
    if summary["total_artifacts"] < 1 or not artifacts:
        return False
    artifact_id = artifacts[0]["artifact_id"]
    verified = get_json(f"/artifacts/{artifact_id}/verify")["data"]
    downloaded = get_bytes(f"/artifacts/{artifact_id}/download")
    return (
        verified["status"] in {"pass", "warning"}
        and len(downloaded) == artifacts[0]["bytes"]
        and object_status["status_schema"] == "agentic-rag-artifact-object-store-v1"
        and object_status["status"] in {"disabled", "ready", "misconfigured"}
        and object_plan["mirror_schema"] == "agentic-rag-artifact-object-store-v1"
        and "candidate_count" in object_plan
        and object_dry_run["dry_run"] is True
        and qc_semantics["status"] in {"pass", "warning"}
        and qc_semantics["verification_mode"] == "indexed"
        and isinstance(qc_semantics["latest_artifacts"], list)
        and import_semantics["status"] in {"pass", "warning"}
        and import_semantics["verification_mode"] == "indexed"
        and isinstance(import_semantics["latest_artifacts"], list)
        and rag_semantics["status"] in {"pass", "warning"}
        and rag_semantics["verification_mode"] == "indexed"
        and isinstance(rag_semantics["latest_artifacts"], list)
        and rag_regression_semantics["status"] in {"pass", "warning"}
        and rag_regression_semantics["verification_mode"] == "indexed"
        and isinstance(rag_regression_semantics["latest_artifacts"], list)
        and optimizer_semantics["status"] in {"pass", "warning"}
        and optimizer_semantics["verification_mode"] == "indexed"
        and isinstance(optimizer_semantics["latest_artifacts"], list)
    )


def smoke_artifact_retention() -> bool:
    plan = get_json("/artifacts/retention/plan?retention_days=30&keep_min=100")["data"]
    dry_run = post_json("/artifacts/retention/apply?dry_run=true&retention_days=30&keep_min=100", {})["data"]
    return (
        plan["status"] == "ready"
        and "candidate_count" in plan
        and dry_run["dry_run"] is True
        and dry_run["deleted_count"] == 0
    )


def smoke_artifact_ledger() -> bool:
    ledger = get_json("/artifacts/ledger?limit=5")["data"]
    verification = get_json("/artifacts/ledger/verify")["data"]
    backfill = post_json("/artifacts/ledger/backfill?dry_run=true", {})["data"]
    return (
        ledger["total_entries"] >= 1
        and verification["status"] in {"pass", "warning"}
        and verification["entry_count"] >= 1
        and backfill["dry_run"] is True
        and "candidate_count" in backfill
    )


def smoke_audit_summary() -> bool:
    summary = get_json("/audit/summary")["data"]
    recent = get_json("/audit/events?limit=10")["data"]
    return summary["total_events"] >= 1 and len(recent["events"]) >= 1


def get_json(path: str, *, timeout: int = 20) -> dict:
    request = urllib.request.Request(f"{BASE_URL}{path}", headers=_headers())
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_bytes(path: str, *, timeout: int = 20) -> bytes:
    request = urllib.request.Request(f"{BASE_URL}{path}", headers=_headers())
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def post_json(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers=_headers({"Content-Type": "application/json"}),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(exc.read().decode("utf-8"), file=sys.stderr)
        raise


def get_post_bytes(path: str, payload: dict) -> bytes:
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers=_headers({"Content-Type": "application/json"}),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read()


def _zip_names(content: bytes) -> set[str]:
    with ZipFile(BytesIO(content), "r") as archive:
        return set(archive.namelist())


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = dict(extra or {})
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    return headers


if __name__ == "__main__":
    raise SystemExit(main())
