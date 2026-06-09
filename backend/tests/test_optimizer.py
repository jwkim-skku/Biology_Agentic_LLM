from __future__ import annotations

import base64
import hashlib
import importlib.util
import random
import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zipfile import ZipFile

from pypdf import PdfReader

from app.optimizer.codon_table import translate
from app.config import get_settings
from app.optimizer.nsga2 import OptimizationConfig, optimize_cds
from app.optimizer.repair import repair_cds
from app.optimizer.scoring import ScoreConfig, motif_violations, score_sequence
from app.services.data_refresh_service import data_catalog, record_data_baseline_event, refresh_reference_data
from app.services.data_snapshot_service import build_data_snapshot_bundle, verify_data_snapshot_bundle
from app.services.data_provenance_service import data_provenance_audit
from app.services.data_lock_service import build_data_lockfile, verify_data_lockfile, write_data_lockfile
from app.services.data_refresh_plan_bundle_service import build_data_refresh_plan_bundle, verify_data_refresh_plan_bundle
from app.services.data_release_bundle_service import build_data_release_bundle, verify_data_release_bundle
from app.services.data_release_lock_service import build_data_release_lock, verify_data_release_lock, write_data_release_lock
from app.services.external_data_service import backfill_external_source_snapshots, external_source_coverage, external_source_status
from app.services.audit_log_service import audit_summary, list_audit_events, record_audit_event
from app.services.artifact_archive_service import (
    ARCHIVE_DB_PATH,
    apply_artifact_retention,
    artifact_ledger,
    archive_artifact_bundle,
    archive_summary,
    data_refresh_plan_archive_summary,
    data_release_archive_summary,
    data_snapshot_archive_summary,
    list_archived_artifacts,
    optimizer_benchmark_archive_summary,
    plan_artifact_retention,
    qc_bundle_archive_semantic_summary,
    rag_regression_archive_summary,
    rag_vector_index_archive_summary,
    structured_import_archive_summary,
    verify_archived_artifact,
    verify_artifact_ledger,
    workflow_trace_archive_summary,
)
from app.services.artifact_object_store_service import artifact_object_store_status
from app.services.batch_design_service import run_batch_gene_design
from app.services.design_service import optimize_design
from app.services.evidence_service import build_design_evidence, search_evidence
from app.services.export_manifest_service import ManifestedZip, verify_artifact_bundle
from app.services.gene_service import fetch_canonical_cds, resolve_gene
from app.services.governance_service import (
    build_governance_attestation,
    build_governance_attestation_bundle,
    verify_governance_attestation_bundle,
)
from app.services.job_export_service import build_job_export_bundle
from app.services.rag_service import evaluate_rag_query, rag_search, rag_status, rebuild_rag_index
from app.services.rag_diagnostics_service import rag_diagnostics
from app.services.rag_evaluation_bundle_service import build_rag_evaluation_bundle, verify_rag_evaluation_bundle
from app.services.rag_regression_bundle_service import build_rag_regression_bundle, verify_rag_regression_bundle
from app.services.rag_regression_service import evaluate_rag_regression, rag_regression_cases
from app.services.rag_vector_index_bundle_service import build_rag_vector_index_bundle, verify_rag_vector_index_bundle
from app.services.report_service import export_qc_report, generate_qc_report, synthesize_evidence
from app.services.run_export_service import build_run_export_bundle
from app.services.sequence_policy_service import audit_sequence_policy
from app.services.job_store import complete_job, create_job, get_job, list_jobs, mark_job_running
from app.services.metrics_service import metrics_prometheus, metrics_snapshot, record_request
from app.services.optimizer_benchmark_bundle_service import build_optimizer_benchmark_bundle, verify_optimizer_benchmark_bundle
from app.services.optimizer_benchmark_service import evaluate_optimizer_benchmark, optimizer_benchmark_cases
from app.services.optimizer_diagnostics_service import optimizer_diagnostics
from app.services import deployment_readiness_service, design_service, rna_folding_service
from app.services.deployment_readiness_service import deployment_readiness
from app.services.production_audit_service import (
    build_production_audit,
    build_production_audit_bundle,
    render_production_audit_markdown,
    verify_production_audit_bundle,
)
from app.services.qc_report_bundle_service import build_qc_report_bundle, verify_qc_report_bundle
from app.services.run_store import get_run, list_runs, save_run
from app.services.signature_service import signing_status
from app.security import InMemoryRateLimiter, has_required_role, is_public_path, request_id_from_headers, required_role_for_request, roles_for_api_key
from app.services.storage_service import postgres_schema_hash, postgres_schema_sql, storage_status
from app.services.storage_migration_service import (
    build_sqlite_migration_bundle,
    import_sqlite_to_postgres,
    sqlite_migration_summary,
    sqlite_postgres_parity_report,
)
from app.services.structured_import_audit_service import build_structured_import_audit_bundle, verify_structured_import_audit_bundle
from app.services.structured_data_service import preview_structured_import, structured_manifest, validate_structured_records
from app.services.validation_service import qc_gate_for_design, validate_cds
from app.services.workflow_service import plan_gene_design_task, run_cds_design_workflow, run_gene_design_workflow
from app.services.workflow_trace_bundle_service import build_workflow_trace_bundle, verify_workflow_trace_bundle


class FakeEnsemblClient:
    def lookup_symbol(self, species: str, symbol: str, *, include_mane: bool = True) -> dict:
        assert include_mane is True
        return {
            "display_name": symbol.upper(),
            "id": "ENSG_DEMO",
            "description": "demo gene",
            "biotype": "protein_coding",
            "assembly_name": "GRCh38",
            "seq_region_name": "1",
            "start": 1,
            "end": 99,
            "strand": 1,
            "canonical_transcript": "ENST_CANON.2",
            "source": "ensembl",
            "Transcript": [
                {
                    "id": "ENST_OTHER",
                    "version": 1,
                    "display_name": "DEMO-002",
                    "biotype": "protein_coding",
                    "is_canonical": 0,
                    "length": 30,
                    "source": "ensembl",
                    "MANE": [],
                    "Translation": {"id": "ENSP_OTHER", "length": 2},
                },
                {
                    "id": "ENST_CANON",
                    "version": 2,
                    "display_name": "DEMO-001",
                    "biotype": "protein_coding",
                    "is_canonical": 1,
                    "length": 30,
                    "source": "ensembl",
                    "MANE": [
                        {
                            "type": "MANE_Select",
                            "refseq_match": "NM_DEMO.1",
                            "id": "ENST_CANON",
                            "version": 2,
                            "assembly_name": "GRCh38",
                        }
                    ],
                    "Translation": {"id": "ENSP_CANON", "length": 3},
                },
            ],
        }

    def fetch_cds(self, transcript_id: str) -> str:
        assert transcript_id == "ENST_CANON"
        return "ATGGCTGCTTAA"


def test_translate_trims_terminal_stop() -> None:
    assert translate("ATGGCTTAA") == "MA"


def test_rnafold_execution_evidence_hashes_success(monkeypatch) -> None:
    stdout = "AUGGCCGCCGCCGCCGCC\n...((....)).... (-3.20)\n"

    class Completed:
        pass

    completed = Completed()
    completed.returncode = 0
    completed.stderr = ""
    completed.stdout = stdout

    monkeypatch.setattr(rna_folding_service.subprocess, "run", lambda *args, **kwargs: completed)

    folded = rna_folding_service._run_rnafold("AUGGCCGCCGCCGCCGCC", "RNAfold", 5)

    evidence = folded["execution_evidence"]
    assert folded["status"] == "pass"
    assert folded["raw_output_sha256_supported"] is True
    assert evidence["evidence_schema"] == "agentic-rag-rnafold-execution-evidence-v1"
    assert evidence["returncode"] == 0
    assert evidence["stdout_sha256"] == hashlib.sha256(stdout.encode("utf-8")).hexdigest()
    assert evidence["stderr_sha256"] == hashlib.sha256(b"").hexdigest()
    assert evidence["parsed_structure_sha256"] == hashlib.sha256(b"...((....))....").hexdigest()
    assert evidence["raw_output_persisted"] is False


def test_rnafold_execution_evidence_hashes_failure(monkeypatch) -> None:
    stderr = "RNAfold failed\n"

    class Completed:
        pass

    completed = Completed()
    completed.returncode = 2
    completed.stdout = ""
    completed.stderr = stderr

    monkeypatch.setattr(rna_folding_service.subprocess, "run", lambda *args, **kwargs: completed)

    folded = rna_folding_service._run_rnafold("AUGGCCGCCGCCGCCGCC", "RNAfold", 5)

    evidence = folded["execution_evidence"]
    assert folded["status"] == "fail"
    assert evidence["returncode"] == 2
    assert evidence["stderr_sha256"] == hashlib.sha256(stderr.encode("utf-8")).hexdigest()
    assert evidence["raw_output_persisted"] is False


def test_settings_expose_data_dir() -> None:
    settings = get_settings()
    assert settings.data_dir.name == "data"
    assert settings.cors_origins
    assert settings.rate_limit_per_minute >= 0
    assert settings.storage_backend in {"sqlite", "postgres"}
    assert isinstance(settings.database_url, str)
    assert settings.auth_enabled is bool(settings.api_keys)
    assert isinstance(settings.api_key_roles, dict)
    assert settings.artifact_signing_enabled is bool(settings.artifact_signing_key)
    assert settings.artifact_signing_key_id
    assert settings.artifact_asymmetric_signing_enabled is bool(settings.artifact_ed25519_private_key)
    assert settings.artifact_asymmetric_verification_enabled is bool(settings.artifact_ed25519_public_key or settings.artifact_ed25519_private_key)
    assert settings.artifact_ed25519_key_id
    assert isinstance(settings.openai_api_key, str)
    assert settings.openai_embedding_base_url.startswith("http")
    assert "ed25519" in signing_status()


def test_settings_parse_api_key_roles() -> None:
    previous = os.environ.get("API_KEY_ROLES")
    os.environ["API_KEY_ROLES"] = "key1=admin;key2=viewer,operator"
    try:
        roles = get_settings().api_key_roles
        assert roles["key1"] == ("admin",)
        assert roles["key2"] == ("operator", "viewer")
    finally:
        if previous is None:
            os.environ.pop("API_KEY_ROLES", None)
        else:
            os.environ["API_KEY_ROLES"] = previous


def test_openai_embedding_backend_is_opt_in_and_mockable() -> None:
    import app.services.rag_embedding_service as embeddings

    previous = {
        key: os.environ.get(key)
        for key in [
            "RAG_EMBEDDING_BACKEND",
            "RAG_EMBEDDING_MODEL",
            "RAG_EMBEDDING_DIMENSIONS",
            "OPENAI_API_KEY",
            "OPENAI_EMBEDDING_PRICE_PER_1K_TOKENS",
            "OPENAI_EMBEDDING_BUDGET_USD",
        ]
    }
    cache_path: Path | None = None

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return json.dumps({"data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]}).encode("utf-8")

    try:
        os.environ["RAG_EMBEDDING_BACKEND"] = "openai"
        os.environ["RAG_EMBEDDING_MODEL"] = "text-embedding-3-small"
        os.environ["RAG_EMBEDDING_DIMENSIONS"] = "16"
        os.environ.pop("OPENAI_API_KEY", None)
        missing_key = embeddings.rag_embedding_status()
        assert missing_key["active_backend"] == "hash_bow"
        assert missing_key["fallback_active"] is True
        assert missing_key["production_ready"] is False

        os.environ["OPENAI_API_KEY"] = "test-openai-key"
        os.environ["OPENAI_EMBEDDING_PRICE_PER_1K_TOKENS"] = "0.00002"
        os.environ["OPENAI_EMBEDDING_BUDGET_USD"] = "100"
        configured = embeddings.rag_embedding_status()
        assert configured["active_backend"] == "openai"
        assert configured["production_ready"] is True
        assert configured["model_fingerprint"]["active_backend"] == "openai"
        assert configured["model_fingerprint"]["embedding_model"] == "text-embedding-3-small"
        assert len(configured["model_fingerprint_hash"]) == 64
        assert configured["openai"]["budget"]["budget_usd"] == 100.0
        assert configured["openai"]["budget"]["price_configured"] is True
        cache_path = embeddings._openai_cache_path()
        cache_path.unlink(missing_ok=True)
        with patch("urllib.request.urlopen", return_value=FakeResponse()) as mocked:
            embedded = embeddings.embed_text("SNCA substantia nigra dopaminergic neuron")
            cached = embeddings.embed_text("SNCA substantia nigra dopaminergic neuron")
        assert mocked.call_count == 1
        assert embedded["active_backend"] == "openai"
        assert embedded["cache_status"] == "miss"
        assert cached["cache_status"] == "hit"
        assert embedded["embedding_model"] == "text-embedding-3-small"
        assert len(embedded["embedding"]) == 16
        cache_status = embeddings.rag_embedding_status()["openai"]["cache"]
        assert cache_status["cache_schema"] == "agentic-rag-openai-embedding-cache-v1"
        assert cache_status["exists"] is True
        assert cache_status["entries"] == 1
        assert len(cache_status["file_sha256"]) == 64
        assert len(cache_status["entry_keys_hash"]) == 64
        assert cache_status["models"] == {"text-embedding-3-small": 1}
        assert cache_status["dimensions"] == {"16": 1}
        assert cache_status["estimated_input_tokens"] >= 1
        assert cache_status["estimated_cost_usd"] > 0
        assert cache_status["missing_usage_entries"] == 0
        assert cache_status["invalid_entries"] == 0
        refreshed_status = embeddings.rag_embedding_status()
        assert refreshed_status["model_fingerprint"]["openai"]["cache_entries"] == 1
        assert refreshed_status["model_fingerprint"]["openai"]["cache_entry_keys_hash"] == cache_status["entry_keys_hash"]
        assert len(refreshed_status["model_fingerprint_hash"]) == 64
        budget = embeddings.rag_embedding_status()["openai"]["budget"]
        assert budget["estimated_spend_usd"] == cache_status["estimated_cost_usd"]
        assert budget["estimated_remaining_usd"] < 100.0
        assert budget["within_budget"] is True
        assert budget["budget_exceeded"] is False

        os.environ["OPENAI_EMBEDDING_PRICE_PER_1K_TOKENS"] = "1"
        os.environ["OPENAI_EMBEDDING_BUDGET_USD"] = "0.00000001"
        guarded_status = embeddings.rag_embedding_status()
        assert guarded_status["production_ready"] is False
        assert guarded_status["openai"]["budget"]["budget_exceeded"] is True
        with patch("urllib.request.urlopen", return_value=FakeResponse()) as blocked:
            guarded = embeddings.embed_text("budget guard " * 200)
        assert blocked.call_count == 0
        assert guarded["active_backend"] == "hash_bow"
        assert guarded["fallback_active"] is True
        assert "exceed configured budget" in " ".join(guarded["warnings"])
    finally:
        if cache_path is not None:
            cache_path.unlink(missing_ok=True)
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_security_helpers_cover_public_paths_and_rate_limits() -> None:
    assert is_public_path("/api/v1/health")
    assert is_public_path("/api/v1/health/ready")
    assert is_public_path("/docs/oauth2-redirect")
    assert not is_public_path("/api/v1/design-from-gene")
    assert request_id_from_headers({"x-request-id": "trace-1"}) == "trace-1"
    limiter = InMemoryRateLimiter()
    assert limiter.check("client", 2).allowed is True
    assert limiter.check("client", 2).allowed is True
    assert limiter.check("client", 2).allowed is False


def test_security_rbac_roles_and_path_requirements() -> None:
    class Settings:
        api_keys = ("viewer-key", "operator-key", "admin-key")
        api_key_roles = {
            "viewer-key": ("viewer",),
            "operator-key": ("operator",),
            "admin-key": ("admin",),
        }

    assert roles_for_api_key("viewer-key", Settings) == ("viewer",)
    assert roles_for_api_key("missing", Settings) == ()
    assert required_role_for_request("GET", "/api/v1/runs") == "viewer"
    assert required_role_for_request("POST", "/api/v1/report") == "operator"
    assert required_role_for_request("POST", "/api/v1/data/refresh") == "admin"
    assert required_role_for_request("POST", "/api/v1/storage/postgres/schema.sql/write") == "admin"
    assert required_role_for_request("GET", "/api/v1/storage/migration/sqlite/export.zip") == "admin"
    assert required_role_for_request("POST", "/api/v1/storage/migration/sqlite/import") == "admin"
    assert required_role_for_request("GET", "/api/v1/governance/attestation") == "admin"
    assert required_role_for_request("GET", "/api/v1/audit/events") == "admin"
    assert has_required_role(("operator",), "viewer")
    assert has_required_role(("operator",), "operator")
    assert not has_required_role(("operator",), "admin")


def test_metrics_registry_records_requests() -> None:
    record_request("GET", "/api/v1/jobs/job_1234567890abcdef", 200, 12.5)
    snapshot = metrics_snapshot()
    prometheus = metrics_prometheus({"jobs_observed": 1})
    assert any(item["path"] == "/api/v1/jobs/{job_id}" for item in snapshot["requests"])
    assert "app_http_requests_total" in prometheus
    assert "app_jobs_observed 1" in prometheus


def test_storage_status_exposes_postgres_migration_contract() -> None:
    status = storage_status()
    schema = postgres_schema_sql()
    assert status["active_runtime_adapter"] == "sqlite"
    assert status["postgres"]["schema_version"] == "agentic-rag-storage-postgres-v1"
    assert status["postgres"]["schema_hash"] == postgres_schema_hash()
    assert "create table if not exists runs" in schema
    assert "create table if not exists audit_events" in schema
    assert postgres_schema_hash() in schema


def test_sqlite_storage_migration_bundle_contains_manifest_and_jsonl() -> None:
    summary = sqlite_migration_summary()
    bundle = build_sqlite_migration_bundle()
    with ZipFile(BytesIO(bundle)) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("migration_manifest.json"))
        assert "postgres_schema.sql" in names
        assert "runs.jsonl" in names
        assert "jobs.jsonl" in names
        assert "audit_events.jsonl" in names
        assert manifest["postgres_schema_hash"] == postgres_schema_hash()
        assert manifest["manifest_hash"]
        assert all(item["row_fingerprint"]["combined_row_hash"] for item in manifest["tables"])
    assert summary["status"] == "ready"
    assert summary["manifest"]["postgres_schema_hash"] == postgres_schema_hash()
    parity = sqlite_postgres_parity_report()
    dry_run = import_sqlite_to_postgres(dry_run=True)
    assert parity["source"]["total_records"] >= manifest["total_records"]
    assert all(item["source_row_hash"] for item in parity["comparisons"])
    assert all("row_hash_match" in item for item in parity["comparisons"])
    assert dry_run["status"] == "planned"
    assert dry_run["schema_hash"] == postgres_schema_hash()
    assert all(item["row_fingerprint"]["hash_count"] == item["records"] for item in dry_run["tables"])


def test_governance_attestation_bundle_verifies_current_state() -> None:
    openapi = {"paths": {"/api/v1/health": {}}, "components": {"schemas": {"ApiResponse": {}}}}
    attestation = build_governance_attestation(openapi)
    bundle = build_governance_attestation_bundle(openapi)
    verification = verify_governance_attestation_bundle(bundle)
    assert attestation["attestation_hash"]
    assert attestation["openapi"]["path_count"] == 1
    assert "signing" in attestation["runtime"]
    assert verification["status"] in {"pass", "warning"}
    assert verification["attestation_hash"]
    with ZipFile(BytesIO(bundle)) as archive:
        names = set(archive.namelist())
        assert "governance_attestation.json" in names
        assert "storage_parity.json" in names
        assert "artifact_manifest.json" in names


def test_production_audit_bundle_includes_timing_evidence() -> None:
    openapi = {"paths": {"/api/v1/health": {}}, "components": {"schemas": {"ApiResponse": {}}}}
    audit = build_production_audit(openapi)
    markdown = render_production_audit_markdown(audit)
    bundle = build_production_audit_bundle(openapi)
    verification = verify_production_audit_bundle(bundle)
    timings = audit["evidence"]["timings"]
    assert timings["total_seconds"] > 0
    assert timings["slowest"]
    assert "## Timing" in markdown
    assert "## Promotion Summary" in markdown
    assert "## Production Gaps" in markdown
    assert "## Readiness Evidence" in markdown
    assert "Agent memory count" in markdown
    assert "Release handoff hash" in markdown
    assert "Release source summary" in markdown
    assert "Release datasets" in markdown
    assert "Release source files" in markdown
    assert "QC readiness candidate" in markdown
    assert "QC folding candidate" in markdown
    assert "Refresh plan dataset" in markdown
    assert "Refresh plan request hash" in markdown
    assert "Refresh plan operations hash" in markdown
    assert "Structured import manifest" in markdown
    assert "Structured import files" in markdown
    assert "RAG regression case metrics" in markdown
    assert "Optimizer recommended-front count" in markdown
    assert "Optimizer folding candidate matches" in markdown
    assert "Workflow trace hash" in markdown
    assert "Total seconds" in markdown
    assert audit["evidence_hashes"]["hash_schema"] == "agentic-rag-production-audit-evidence-hashes-v1"
    assert audit["evidence_hashes"]["evidence_count"] == len(audit["evidence"])
    assert len(audit["evidence_hashes"]["combined_hash"]) == 64
    assert audit["promotion_summary"]["summary_schema"] == "agentic-rag-production-promotion-summary-v1"
    assert audit["promotion_summary"]["items"]
    assert audit["production_gap_summary"]["gap_schema"] == "agentic-rag-production-gap-summary-v1"
    assert audit["production_gap_summary"] == audit["evidence"]["production_gap_summary"]
    assert len(audit["production_gap_summary"]["gap_summary_hash"]) == 64
    assert audit["production_gap_summary"]["gap_count"] == len(audit["production_gap_summary"]["gaps"])
    optimizer_item = next(item for item in audit["promotion_summary"]["items"] if item["area"] == "optimizer")
    assert "deterministic-tradeoff-seeds-v1" in optimizer_item["detail"]
    rag_embedding_item = next(item for item in audit["promotion_summary"]["items"] if item["area"] == "rag_embedding")
    if rag_embedding_item["status"] != "pass":
        assert "OpenAI embedding" in rag_embedding_item["action"]
        assert "budget guardrails" in rag_embedding_item["action"]
    assert "structured_import_archive_semantics" in {item["name"] for item in audit["checks"]}
    assert "structured_import_archive_semantics" in audit["evidence"]
    assert "data_release_archive_semantics" in {item["name"] for item in audit["checks"]}
    assert "data_release_archive_semantics" in audit["evidence"]
    assert "data_snapshot_archive_semantics" in {item["name"] for item in audit["checks"]}
    assert "data_snapshot_archive_semantics" in audit["evidence"]
    assert "data_refresh_plan_archive_semantics" in {item["name"] for item in audit["checks"]}
    assert "data_refresh_plan_archive_semantics" in audit["evidence"]
    assert "rag_evaluation_archive_semantics" in {item["name"] for item in audit["checks"]}
    assert "rag_evaluation_archive_semantics" in audit["evidence"]
    assert "rag_vector_index_archive_semantics" in {item["name"] for item in audit["checks"]}
    assert "rag_vector_index_archive_semantics" in audit["evidence"]
    assert "rag_regression_archive_semantics" in {item["name"] for item in audit["checks"]}
    assert "rag_regression_archive_semantics" in audit["evidence"]
    assert "optimizer_benchmark_archive_semantics" in {item["name"] for item in audit["checks"]}
    assert "optimizer_benchmark_archive_semantics" in audit["evidence"]
    assert "workflow_trace_archive_semantics" in {item["name"] for item in audit["checks"]}
    assert "workflow_trace_archive_semantics" in audit["evidence"]
    assert "promotion_summary" in audit["evidence"]
    assert "production_gap_summary" in audit["evidence"]
    rag_gate = next(gate for gate in audit["evidence"]["deployment_readiness"]["gates"] if gate["name"] == "rag_regression")
    assert len(rag_gate["details"]["results_hash"]) == 64
    rag_eval_archive_gate = next(gate for gate in audit["evidence"]["deployment_readiness"]["gates"] if gate["name"] == "rag_evaluation_archive_semantics")
    assert rag_eval_archive_gate["details"]["latest_source_provenance_hash"] is None or len(rag_eval_archive_gate["details"]["latest_source_provenance_hash"]) == 64
    rag_archive_gate = next(gate for gate in audit["evidence"]["deployment_readiness"]["gates"] if gate["name"] == "rag_regression_archive_semantics")
    assert rag_archive_gate["details"]["latest_case_metrics_hash"] is None or len(rag_archive_gate["details"]["latest_case_metrics_hash"]) == 64
    assert len(audit["evidence"]["rag_diagnostics"]["regression"]["results_hash"]) == 64
    optimizer_gate = next(gate for gate in audit["evidence"]["deployment_readiness"]["gates"] if gate["name"] == "optimizer_benchmark")
    assert len(optimizer_gate["details"]["results_hash"]) == 64
    assert len(audit["evidence"]["optimizer_diagnostics"]["benchmark"]["results_hash"]) == 64
    assert verification["status"] in {"pass", "warning"}
    assert verification["semantic_checks"]["summary_recomputed"] == "pass"
    assert verification["semantic_checks"]["markdown_recomputed"] == "pass"
    assert verification["semantic_checks"]["check_detail_hashes"] == "pass"
    assert verification["semantic_checks"]["deployment_readiness_evidence"] == "pass"
    assert verification["semantic_checks"]["agent_memory_evidence"] == "pass"
    assert verification["semantic_checks"]["agent_memory_hash_aggregate"] == "pass"
    assert verification["semantic_checks"]["promotion_summary_evidence"] == "pass"
    assert verification["semantic_checks"]["promotion_summary_recomputed"] == "pass"
    assert verification["semantic_checks"]["production_gap_summary_evidence"] == "pass"
    assert verification["semantic_checks"]["production_gap_summary_recomputed"] == "pass"
    assert verification["semantic_checks"]["production_gap_summary_hash"] == "pass"
    assert verification["semantic_checks"]["qc_bundle_archive_evidence"] == "pass"
    assert verification["semantic_checks"]["qc_bundle_archive_recommendation_candidate"] == "pass"
    assert verification["semantic_checks"]["qc_bundle_archive_folding_candidate"] == "pass"
    assert verification["semantic_checks"]["rag_vector_index_archive_evidence"] == "pass"
    assert verification["semantic_checks"]["rag_vector_index_archive_migration_backend"] == "pass"
    assert verification["semantic_checks"]["rag_vector_index_archive_parity"] == "pass"
    assert verification["semantic_checks"]["rag_vector_index_archive_row_hash"] == "pass"
    assert verification["semantic_checks"]["workflow_trace_archive_evidence"] == "pass"
    assert verification["semantic_checks"]["workflow_trace_archive_hash"] == "pass"
    assert verification["semantic_checks"]["workflow_trace_archive_steps"] == "pass"
    assert verification["semantic_checks"]["evidence_hashes_payload"] == "pass"
    assert verification["semantic_checks"]["evidence_hashes_schema"] == "pass"
    assert verification["semantic_checks"]["evidence_hashes_count"] == "pass"
    assert verification["semantic_checks"]["evidence_file_hashes"] == "pass"
    assert verification["semantic_checks"]["evidence_hashes_combined"] == "pass"
    assert verification["semantic_checks"]["attention_gates_hash"] == "pass"
    assert verification["semantic_checks"]["required_actions_hash"] == "pass"
    assert verification["semantic_checks"]["required_action_detail_hashes"] == "pass"
    assert verification["semantic_summary"]["summary_schema"] == "agentic-rag-production-audit-semantic-summary-v1"
    assert verification["semantic_summary"]["check_count"] == len(verification["semantic_checks"])
    assert verification["semantic_summary"]["pass_count"] == len(verification["semantic_checks"])
    assert verification["semantic_summary"]["fail_count"] == 0
    assert verification["semantic_summary"]["warning_count"] == 0
    assert verification["semantic_summary"]["status"] == "pass"
    assert len(verification["semantic_summary"]["summary_hash"]) == 64
    with ZipFile(BytesIO(bundle)) as archive:
        names = set(archive.namelist())
        assert "production_audit.md" in names
        assert "evidence_hashes.json" in names
        assert "evidence/promotion_summary.json" in names
        assert "evidence/production_gap_summary.json" in names
        assert "evidence/timings.json" in names
        assert "evidence/data_refresh_plan_archive_semantics.json" in names
        assert "evidence/data_release_archive_semantics.json" in names
        assert "evidence/data_snapshot_archive_semantics.json" in names
        assert "evidence/rag_evaluation_archive_semantics.json" in names
        assert "evidence/rag_regression_archive_semantics.json" in names
        assert "evidence/rag_vector_index_archive_semantics.json" in names
        assert "evidence/optimizer_benchmark_archive_semantics.json" in names
        assert "evidence/structured_import_archive_semantics.json" in names
        assert "evidence/workflow_trace_archive_semantics.json" in names
        bundled_markdown = archive.read("production_audit.md").decode("utf-8")
        assert "## Timing" in bundled_markdown
        assert "## Production Gaps" in bundled_markdown
        assert "## Readiness Evidence" in bundled_markdown
        assert "Agent memory aggregate" in bundled_markdown
        assert "Object-store lifecycle" in bundled_markdown
        assert "Object-store mirror candidates" in bundled_markdown
        assert "Object-store mirror bytes" in bundled_markdown
        assert "RAG embedding fingerprint" in bundled_markdown
        assert "Release source summary" in bundled_markdown
        assert "Release datasets" in bundled_markdown
        assert "Release source files" in bundled_markdown
        assert "Snapshot manifest" in bundled_markdown
        assert "Snapshot RAG index" in bundled_markdown
        assert "Snapshot files" in bundled_markdown
        assert "Snapshot external files" in bundled_markdown
        assert "QC readiness candidate" in bundled_markdown
        assert "QC folding candidate" in bundled_markdown
        assert "Refresh plan dataset" in bundled_markdown
        assert "Refresh plan request hash" in bundled_markdown
        assert "Refresh plan operations hash" in bundled_markdown
        assert "Structured import manifest" in bundled_markdown
        assert "Structured import files" in bundled_markdown
        assert "Readiness action hash" in bundled_markdown
        assert "Readiness attention hash" in bundled_markdown
        assert "Readiness action coverage" in bundled_markdown
        promotion = json.loads(archive.read("evidence/promotion_summary.json"))
        gap_summary = json.loads(archive.read("evidence/production_gap_summary.json"))
        deployment_readiness = json.loads(archive.read("evidence/deployment_readiness.json"))
        evidence_hashes = json.loads(archive.read("evidence_hashes.json"))
        assert promotion["summary_schema"] == "agentic-rag-production-promotion-summary-v1"
        assert gap_summary == audit["production_gap_summary"]
        assert len(deployment_readiness["required_actions_hash"]) == 64
        assert evidence_hashes == audit["evidence_hashes"]
        assert evidence_hashes["items"]["evidence/promotion_summary.json"] == audit["evidence_hashes"]["items"]["evidence/promotion_summary.json"]
        assert evidence_hashes["items"]["evidence/production_gap_summary.json"] == audit["evidence_hashes"]["items"]["evidence/production_gap_summary.json"]


def test_production_audit_bundle_rejects_tampered_action_detail_hash() -> None:
    from app.services.production_audit_service import verify_production_audit_bundle

    openapi = {"paths": {"/api/v1/health": {}}, "components": {"schemas": {"ApiResponse": {}}}}
    bundle = build_production_audit_bundle(openapi)
    source = ZipFile(BytesIO(bundle))
    audit = json.loads(source.read("production_audit.json").decode("utf-8"))
    deployment_readiness = json.loads(source.read("evidence/deployment_readiness.json").decode("utf-8"))
    assert deployment_readiness["required_actions"]

    deployment_readiness["required_actions"][0]["detail_hash"] = "0" * 64
    audit["evidence"]["deployment_readiness"] = deployment_readiness

    buffer = BytesIO()
    with ZipFile(buffer, "w") as tampered:
        for item in source.infolist():
            if item.filename == "production_audit.json":
                tampered.writestr(item, json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
            elif item.filename == "evidence/deployment_readiness.json":
                tampered.writestr(item, json.dumps(deployment_readiness, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                tampered.writestr(item, source.read(item.filename))
    source.close()

    verification = verify_production_audit_bundle(buffer.getvalue())
    assert verification["semantic_checks"]["required_action_detail_hashes"] == "fail"
    assert "detail_hash" in " ".join(verification["errors"])


def test_production_audit_bundle_rejects_missing_readiness_action_coverage() -> None:
    from app.services.production_audit_service import verify_production_audit_bundle

    openapi = {"paths": {"/api/v1/health": {}}, "components": {"schemas": {"ApiResponse": {}}}}
    bundle = build_production_audit_bundle(openapi)
    source = ZipFile(BytesIO(bundle))
    audit = json.loads(source.read("production_audit.json").decode("utf-8"))
    deployment_readiness = json.loads(source.read("evidence/deployment_readiness.json").decode("utf-8"))
    assert deployment_readiness["required_actions"]

    deployment_readiness["required_actions"] = deployment_readiness["required_actions"][:-1]
    deployment_readiness["required_actions_hash"] = deployment_readiness_service._hash_payload(deployment_readiness["required_actions"])
    audit["evidence"]["deployment_readiness"] = deployment_readiness

    buffer = BytesIO()
    with ZipFile(buffer, "w") as tampered:
        for item in source.infolist():
            if item.filename == "production_audit.json":
                tampered.writestr(item, json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
            elif item.filename == "evidence/deployment_readiness.json":
                tampered.writestr(item, json.dumps(deployment_readiness, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                tampered.writestr(item, source.read(item.filename))
    source.close()

    verification = verify_production_audit_bundle(buffer.getvalue())
    assert verification["semantic_checks"]["required_actions_hash"] == "pass"
    assert verification["semantic_checks"]["required_action_coverage"] == "fail"
    assert "required_actions do not cover" in " ".join(verification["errors"])


def test_production_audit_bundle_rejects_tampered_markdown() -> None:
    from app.services.production_audit_service import verify_production_audit_bundle

    openapi = {"paths": {"/api/v1/health": {}}, "components": {"schemas": {"ApiResponse": {}}}}
    bundle = build_production_audit_bundle(openapi)
    source = ZipFile(BytesIO(bundle))

    buffer = BytesIO()
    with ZipFile(buffer, "w") as tampered:
        for item in source.infolist():
            if item.filename == "production_audit.md":
                tampered.writestr(item, "# Tampered Production Audit Report\n")
            else:
                tampered.writestr(item, source.read(item.filename))
    source.close()

    verification = verify_production_audit_bundle(buffer.getvalue())
    assert verification["semantic_checks"]["markdown_recomputed"] == "fail"
    assert "production_audit.md" in " ".join(verification["errors"])


def test_production_audit_bundle_rejects_tampered_check_detail_hash() -> None:
    from app.services.production_audit_service import verify_production_audit_bundle

    openapi = {"paths": {"/api/v1/health": {}}, "components": {"schemas": {"ApiResponse": {}}}}
    bundle = build_production_audit_bundle(openapi)
    source = ZipFile(BytesIO(bundle))
    audit = json.loads(source.read("production_audit.json").decode("utf-8"))
    assert audit["checks"]

    audit["checks"][0]["detail_hash"] = "0" * 64

    buffer = BytesIO()
    with ZipFile(buffer, "w") as tampered:
        for item in source.infolist():
            if item.filename == "production_audit.json":
                tampered.writestr(item, json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                tampered.writestr(item, source.read(item.filename))
    source.close()

    verification = verify_production_audit_bundle(buffer.getvalue())
    assert verification["semantic_checks"]["check_detail_hashes"] == "fail"
    assert "detail_hash" in " ".join(verification["errors"])


def test_production_audit_bundle_rejects_tampered_promotion_summary() -> None:
    from app.services.production_audit_service import verify_production_audit_bundle

    openapi = {"paths": {"/api/v1/health": {}}, "components": {"schemas": {"ApiResponse": {}}}}
    bundle = build_production_audit_bundle(openapi)
    source = ZipFile(BytesIO(bundle))
    audit = json.loads(source.read("production_audit.json").decode("utf-8"))
    promotion = json.loads(source.read("evidence/promotion_summary.json").decode("utf-8"))

    promotion["production_ready"] = True
    promotion["required_actions"] = []
    audit["promotion_summary"] = promotion
    audit["evidence"]["promotion_summary"] = promotion

    buffer = BytesIO()
    with ZipFile(buffer, "w") as tampered:
        for item in source.infolist():
            if item.filename == "production_audit.json":
                tampered.writestr(item, json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
            elif item.filename == "evidence/promotion_summary.json":
                tampered.writestr(item, json.dumps(promotion, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                tampered.writestr(item, source.read(item.filename))
    source.close()

    verification = verify_production_audit_bundle(buffer.getvalue())
    assert verification["semantic_checks"]["promotion_summary_evidence"] == "pass"
    assert verification["semantic_checks"]["promotion_summary_recomputed"] == "fail"
    assert "promotion_summary" in " ".join(verification["errors"])


def test_production_audit_bundle_rejects_tampered_gap_summary() -> None:
    from app.services.production_audit_service import verify_production_audit_bundle

    openapi = {"paths": {"/api/v1/health": {}}, "components": {"schemas": {"ApiResponse": {}}}}
    bundle = build_production_audit_bundle(openapi)
    source = ZipFile(BytesIO(bundle))
    audit = json.loads(source.read("production_audit.json").decode("utf-8"))
    gap_summary = json.loads(source.read("evidence/production_gap_summary.json").decode("utf-8"))

    gap_summary["gap_count"] = 0
    gap_summary["gaps"] = []
    audit["production_gap_summary"] = gap_summary
    audit["evidence"]["production_gap_summary"] = gap_summary

    buffer = BytesIO()
    with ZipFile(buffer, "w") as tampered:
        for item in source.infolist():
            if item.filename == "production_audit.json":
                tampered.writestr(item, json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
            elif item.filename == "evidence/production_gap_summary.json":
                tampered.writestr(item, json.dumps(gap_summary, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                tampered.writestr(item, source.read(item.filename))
    source.close()

    verification = verify_production_audit_bundle(buffer.getvalue())
    assert verification["semantic_checks"]["production_gap_summary_evidence"] == "pass"
    assert verification["semantic_checks"]["production_gap_summary_recomputed"] == "fail"
    assert verification["semantic_checks"]["production_gap_summary_hash"] == "fail"
    assert "production_gap_summary" in " ".join(verification["errors"])


def test_production_audit_bundle_rejects_missing_vector_archive_evidence() -> None:
    from app.services.production_audit_service import verify_production_audit_bundle

    openapi = {"paths": {"/api/v1/health": {}}, "components": {"schemas": {"ApiResponse": {}}}}
    bundle = build_production_audit_bundle(openapi)
    source = ZipFile(BytesIO(bundle))
    audit = json.loads(source.read("production_audit.json").decode("utf-8"))
    vector_archive = json.loads(source.read("evidence/rag_vector_index_archive_semantics.json").decode("utf-8"))
    assert int(vector_archive.get("checked_count") or 0) > 0
    assert vector_archive["latest_artifacts"]

    latest = vector_archive["latest_artifacts"][0]
    latest.pop("recommended_backend", None)
    latest.pop("migration_target_backend", None)
    latest["parity_status"] = "fail"
    latest.pop("vector_row_hash", None)
    audit["evidence"]["rag_vector_index_archive_semantics"] = vector_archive

    buffer = BytesIO()
    with ZipFile(buffer, "w") as tampered:
        for item in source.infolist():
            if item.filename == "production_audit.json":
                tampered.writestr(item, json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
            elif item.filename == "evidence/rag_vector_index_archive_semantics.json":
                tampered.writestr(item, json.dumps(vector_archive, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                tampered.writestr(item, source.read(item.filename))
    source.close()

    verification = verify_production_audit_bundle(buffer.getvalue())
    assert verification["semantic_checks"]["rag_vector_index_archive_evidence"] == "pass"
    assert verification["semantic_checks"]["rag_vector_index_archive_migration_backend"] == "fail"
    assert verification["semantic_checks"]["rag_vector_index_archive_parity"] == "fail"
    assert verification["semantic_checks"]["rag_vector_index_archive_row_hash"] == "fail"
    assert "RAG vector index archive" in " ".join(verification["errors"])


def test_production_audit_bundle_rejects_missing_qc_archive_candidate_evidence() -> None:
    from app.services.production_audit_service import verify_production_audit_bundle

    openapi = {"paths": {"/api/v1/health": {}}, "components": {"schemas": {"ApiResponse": {}}}}
    bundle = build_production_audit_bundle(openapi)
    source = ZipFile(BytesIO(bundle))
    audit = json.loads(source.read("production_audit.json").decode("utf-8"))
    qc_archive = json.loads(source.read("evidence/qc_bundle_archive_semantics.json").decode("utf-8"))
    assert int(qc_archive.get("checked_count") or 0) > 0
    assert qc_archive["latest_artifacts"]

    latest = qc_archive["latest_artifacts"][0]
    latest.pop("recommendation_readiness_candidate_id", None)
    latest.pop("recommended_folding_candidate_id", None)
    audit["evidence"]["qc_bundle_archive_semantics"] = qc_archive

    buffer = BytesIO()
    with ZipFile(buffer, "w") as tampered:
        for item in source.infolist():
            if item.filename == "production_audit.json":
                tampered.writestr(item, json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
            elif item.filename == "evidence/qc_bundle_archive_semantics.json":
                tampered.writestr(item, json.dumps(qc_archive, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                tampered.writestr(item, source.read(item.filename))
    source.close()

    verification = verify_production_audit_bundle(buffer.getvalue())
    assert verification["semantic_checks"]["qc_bundle_archive_evidence"] == "pass"
    assert verification["semantic_checks"]["qc_bundle_archive_recommendation_candidate"] == "fail"
    assert verification["semantic_checks"]["qc_bundle_archive_folding_candidate"] == "fail"
    assert "QC archive evidence" in " ".join(verification["errors"])


def test_production_audit_bundle_rejects_tampered_summary() -> None:
    from app.services.production_audit_service import verify_production_audit_bundle

    openapi = {"paths": {"/api/v1/health": {}}, "components": {"schemas": {"ApiResponse": {}}}}
    bundle = build_production_audit_bundle(openapi)
    source = ZipFile(BytesIO(bundle))
    audit = json.loads(source.read("production_audit.json").decode("utf-8"))

    audit["summary"] = {
        **audit["summary"],
        "status": "pass",
        "deployment_ready": True,
        "production_ready": True,
        "counts": {"pass": len(audit["checks"]), "warning": 0, "fail": 0},
        "blocking_checks": [],
        "warning_checks": [],
    }

    buffer = BytesIO()
    with ZipFile(buffer, "w") as tampered:
        for item in source.infolist():
            if item.filename == "production_audit.json":
                tampered.writestr(item, json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                tampered.writestr(item, source.read(item.filename))
    source.close()

    verification = verify_production_audit_bundle(buffer.getvalue())
    assert verification["semantic_checks"]["summary_recomputed"] == "fail"
    assert "summary" in " ".join(verification["errors"])


def test_deployment_readiness_surfaces_optimizer_result_hash() -> None:
    openapi = {"paths": {"/api/v1/health": {}}, "components": {"schemas": {"ApiResponse": {}}}}
    readiness = deployment_readiness(openapi)
    assert set(readiness["attention_gates"]) == {gate["name"] for gate in readiness["gates"] if gate["status"] != "pass"}
    assert all(action["gate"] in readiness["attention_gates"] for action in readiness["required_actions"])
    assert all(action["priority"] in {"blocking", "promotion"} for action in readiness["required_actions"])
    assert all(len(action["detail_hash"]) == 64 for action in readiness["required_actions"])
    assert len(readiness["attention_gates_hash"]) == 64
    assert len(readiness["required_actions_hash"]) == 64
    if readiness["required_actions"]:
        assert readiness["required_actions"][0]["action"]
    rag_gate = next(gate for gate in readiness["gates"] if gate["name"] == "rag_regression")
    assert len(rag_gate["details"]["cases_hash"]) == 64
    assert len(rag_gate["details"]["results_hash"]) == 64
    embedding_gate = next(gate for gate in readiness["gates"] if gate["name"] == "rag_embedding_backend")
    embedding_requirements = embedding_gate["details"]["production_requirements"]
    assert embedding_requirements["accepted_backends"] == ["sentence_transformers", "openai"]
    assert embedding_requirements["sentence_transformers"]["model_load_policy"] == "local_files_only"
    assert embedding_requirements["openai"]["api_key_configured"] is True
    assert embedding_requirements["openai"]["price_configured"] is True
    assert embedding_requirements["openai"]["budget_configured"] is True
    object_store_gate = next(gate for gate in readiness["gates"] if gate["name"] == "artifact_object_store")
    assert object_store_gate["details"]["mirror_plan_status"] in {"ready", "disabled", "misconfigured"}
    assert object_store_gate["details"]["mirror_plan_candidate_count"] >= 0
    assert object_store_gate["details"]["mirror_plan_candidate_bytes"] >= 0
    assert object_store_gate["details"]["mirror_plan_limit"] >= 1
    assert len(object_store_gate["details"]["lifecycle_policy_hash"]) == 64
    qc_archive_gate = next(gate for gate in readiness["gates"] if gate["name"] == "qc_bundle_archive_semantics")
    assert qc_archive_gate["details"]["status"] in {"pass", "warning"}
    for field in [
        "latest_optimizer_manifest_hash",
        "latest_request_hash",
        "latest_qc_report_hash",
        "latest_candidate_ranking_hash",
        "latest_recommendation_audit_hash",
        "latest_recommendation_readiness_hash",
        "latest_candidate_folding_audit_hash",
    ]:
        assert qc_archive_gate["details"][field] is None or len(qc_archive_gate["details"][field]) == 64
    assert qc_archive_gate["details"]["latest_recommendation_readiness_status"] in {None, "pass", "warning"}
    assert qc_archive_gate["details"]["latest_recommendation_release_ready"] in {None, True, False}
    assert qc_archive_gate["details"]["latest_recommendation_readiness_candidate_id"] is None or qc_archive_gate["details"]["latest_recommendation_readiness_candidate_id"]
    assert qc_archive_gate["details"]["latest_candidate_folding_audit_selection_signal"] in {None, "secondary_structure_proxy_score", "thermodynamic_risk_score"}
    assert qc_archive_gate["details"]["latest_candidate_folding_audit_validated_backend_count"] is None or qc_archive_gate["details"]["latest_candidate_folding_audit_validated_backend_count"] >= 0
    assert qc_archive_gate["details"]["latest_recommended_folding_candidate_id"] is None or qc_archive_gate["details"]["latest_recommended_folding_candidate_id"]
    assert qc_archive_gate["details"]["latest_retrieval_quality_status"] in {None, "pass", "warning"}
    assert qc_archive_gate["details"]["latest_retrieval_quality_rank_evidence_count"] is None or qc_archive_gate["details"]["latest_retrieval_quality_rank_evidence_count"] >= 1
    assert qc_archive_gate["details"]["latest_retrieval_quality_rank_evidence_hash"] is None or len(qc_archive_gate["details"]["latest_retrieval_quality_rank_evidence_hash"]) == 64
    assert qc_archive_gate["details"]["latest_data_quality_status"] in {None, "pass", "warning"}
    assert qc_archive_gate["details"]["latest_optimizer_stress_status"] in {None, "pass", "warning"}
    assert qc_archive_gate["details"]["latest_objective_count"] is None or qc_archive_gate["details"]["latest_objective_count"] >= 1
    optimizer_gate = next(gate for gate in readiness["gates"] if gate["name"] == "optimizer_benchmark")
    assert len(optimizer_gate["details"]["cases_hash"]) == 64
    assert len(optimizer_gate["details"]["results_hash"]) == 64
    folding_gate = next(gate for gate in readiness["gates"] if gate["name"] == "rna_folding_backend")
    assert folding_gate["details"]["evidence_capabilities"]["raw_output_sha256"] is True
    assert folding_gate["details"]["evidence_capabilities"]["raw_output_storage_policy"]
    data_release_gate = next(gate for gate in readiness["gates"] if gate["name"] == "data_release_archive_semantics")
    assert data_release_gate["details"]["status"] in {"pass", "warning"}
    assert data_release_gate["details"]["latest_records_hash"] is None or len(data_release_gate["details"]["latest_records_hash"]) == 64
    assert data_release_gate["details"]["latest_records_csv_hash"] is None or len(data_release_gate["details"]["latest_records_csv_hash"]) == 64
    assert data_release_gate["details"]["latest_release_handoff_hash"] is None or len(data_release_gate["details"]["latest_release_handoff_hash"]) == 64
    assert data_release_gate["details"]["latest_rag_structured_manifest_hash"] is None or data_release_gate["details"]["latest_rag_structured_manifest_hash"]
    assert data_release_gate["details"]["latest_rag_index_hash"] is None or len(data_release_gate["details"]["latest_rag_index_hash"]) == 64
    assert data_release_gate["details"]["freshness_status"] in {"fresh", "stale", "unknown", "empty"}
    assert data_release_gate["details"]["freshness_warning_hours"] > 0
    data_snapshot_gate = next(gate for gate in readiness["gates"] if gate["name"] == "data_snapshot_archive_semantics")
    assert data_snapshot_gate["details"]["status"] in {"pass", "warning"}
    assert data_snapshot_gate["details"]["latest_snapshot_manifest_hash"] is None or len(data_snapshot_gate["details"]["latest_snapshot_manifest_hash"]) == 64
    assert data_snapshot_gate["details"]["latest_structured_manifest_hash"] is None or data_snapshot_gate["details"]["latest_structured_manifest_hash"]
    assert data_snapshot_gate["details"]["latest_rag_index_hash"] is None or len(data_snapshot_gate["details"]["latest_rag_index_hash"]) == 64
    assert data_snapshot_gate["details"]["latest_external_snapshot_file_count"] is None or data_snapshot_gate["details"]["latest_external_snapshot_file_count"] >= 1
    assert data_snapshot_gate["details"]["freshness_status"] in {"fresh", "stale", "unknown", "empty"}
    refresh_plan_gate = next(gate for gate in readiness["gates"] if gate["name"] == "data_refresh_plan_archive_semantics")
    assert refresh_plan_gate["details"]["status"] in {"pass", "warning"}
    assert refresh_plan_gate["details"]["latest_operation_count"] is None or refresh_plan_gate["details"]["latest_operation_count"] >= 1
    assert refresh_plan_gate["details"]["latest_request_hash"] is None or len(refresh_plan_gate["details"]["latest_request_hash"]) == 64
    assert refresh_plan_gate["details"]["latest_operations_hash"] is None or len(refresh_plan_gate["details"]["latest_operations_hash"]) == 64
    assert refresh_plan_gate["details"]["latest_validation_status"] in {None, "pass", "warning", "fail"}
    assert refresh_plan_gate["details"]["latest_dataset_id"] is None or refresh_plan_gate["details"]["latest_dataset_id"]
    assert refresh_plan_gate["details"]["freshness_status"] in {"fresh", "stale", "unknown", "empty"}
    structured_import_gate = next(gate for gate in readiness["gates"] if gate["name"] == "structured_import_archive_semantics")
    assert structured_import_gate["details"]["status"] in {"pass", "warning"}
    assert structured_import_gate["details"]["latest_structured_manifest_hash"] is None or structured_import_gate["details"]["latest_structured_manifest_hash"]
    assert structured_import_gate["details"]["latest_checked_files"] is None or structured_import_gate["details"]["latest_checked_files"] >= 1
    assert structured_import_gate["details"]["latest_file_count"] is None or structured_import_gate["details"]["latest_file_count"] >= 1
    rag_evaluation_archive_gate = next(gate for gate in readiness["gates"] if gate["name"] == "rag_evaluation_archive_semantics")
    assert rag_evaluation_archive_gate["details"]["status"] in {"pass", "warning"}
    assert rag_evaluation_archive_gate["details"]["latest_source_provenance_hash"] is None or len(rag_evaluation_archive_gate["details"]["latest_source_provenance_hash"]) == 64
    assert rag_evaluation_archive_gate["details"]["latest_source_provenance_count"] is None or rag_evaluation_archive_gate["details"]["latest_source_provenance_count"] >= 1
    workflow_trace_archive_gate = next(gate for gate in readiness["gates"] if gate["name"] == "workflow_trace_archive_semantics")
    assert workflow_trace_archive_gate["details"]["status"] in {"pass", "warning"}
    assert workflow_trace_archive_gate["details"]["latest_trace_step_count"] is None or workflow_trace_archive_gate["details"]["latest_trace_step_count"] >= 1
    assert workflow_trace_archive_gate["details"]["latest_trace_hash"] is None or len(workflow_trace_archive_gate["details"]["latest_trace_hash"]) == 64
    assert workflow_trace_archive_gate["details"]["latest_task_type"] is None or workflow_trace_archive_gate["details"]["latest_task_type"]
    vector_archive_gate = next(gate for gate in readiness["gates"] if gate["name"] == "rag_vector_index_archive_semantics")
    assert vector_archive_gate["details"]["status"] in {"pass", "warning"}
    assert vector_archive_gate["details"]["latest_chunk_count"] is None or vector_archive_gate["details"]["latest_chunk_count"] >= 1
    assert vector_archive_gate["details"]["freshness_status"] in {"fresh", "stale", "unknown", "empty"}
    assert vector_archive_gate["details"]["latest_migration_target_backend"] in {None, "local_json", "pgvector", "qdrant"}
    assert vector_archive_gate["details"]["latest_parity_status"] in {None, "pass", "warning"}
    assert vector_archive_gate["details"]["latest_vector_row_hash"] is None or len(vector_archive_gate["details"]["latest_vector_row_hash"]) == 64
    rag_regression_archive_gate = next(gate for gate in readiness["gates"] if gate["name"] == "rag_regression_archive_semantics")
    assert rag_regression_archive_gate["details"]["status"] in {"pass", "warning"}
    assert rag_regression_archive_gate["details"]["latest_case_metrics_hash"] is None or len(rag_regression_archive_gate["details"]["latest_case_metrics_hash"]) == 64
    assert rag_regression_archive_gate["details"]["latest_quality_status"] in {None, "pass", "warning"}
    optimizer_archive_gate = next(gate for gate in readiness["gates"] if gate["name"] == "optimizer_benchmark_archive_semantics")
    assert optimizer_archive_gate["details"]["status"] in {"pass", "warning"}
    assert optimizer_archive_gate["details"]["latest_stress_status"] in {None, "pass", "warning"}
    for field in [
        "latest_results_hash",
        "latest_benchmark_hash",
        "latest_diagnostics_hash",
        "latest_case_metrics_hash",
        "latest_candidate_diagnostics_hash",
        "latest_recommendation_summary_hash",
        "latest_case_provenance_hash",
        "latest_recommended_folding_evidence_hash",
    ]:
        assert optimizer_archive_gate["details"][field] is None or len(optimizer_archive_gate["details"][field]) == 64
    assert (
        optimizer_archive_gate["details"]["latest_recommendation_summary_status"] is None
        or optimizer_archive_gate["details"]["latest_recommendation_summary_status"] in {"pass", "warning"}
    )
    assert (
        optimizer_archive_gate["details"]["latest_recommended_on_pareto_front_count"] is None
        or optimizer_archive_gate["details"]["latest_recommended_on_pareto_front_count"] >= 1
    )
    assert (
        optimizer_archive_gate["details"]["latest_recommendation_max_regret"] is None
        or optimizer_archive_gate["details"]["latest_recommendation_max_regret"] >= 0
    )
    assert (
        optimizer_archive_gate["details"]["latest_case_fingerprint_count"] is None
        or optimizer_archive_gate["details"]["latest_case_fingerprint_count"] >= 1
    )
    assert (
        optimizer_archive_gate["details"]["latest_recommended_folding_evidence_count"] is None
        or optimizer_archive_gate["details"]["latest_recommended_folding_evidence_count"] >= 1
    )
    assert (
        optimizer_archive_gate["details"]["latest_recommended_folding_candidate_match_count"] is None
        or optimizer_archive_gate["details"]["latest_recommended_folding_candidate_match_count"] >= 1
    )
    assert optimizer_archive_gate["details"]["freshness_status"] in {"fresh", "stale", "unknown", "empty"}


def test_deployment_readiness_archive_actions_are_specific() -> None:
    gates = [
        {"name": "qc_bundle_archive_semantics", "status": "warning", "message": "QC archive stale.", "details": {}},
        {"name": "data_snapshot_archive_semantics", "status": "warning", "message": "Snapshot archive stale.", "details": {}},
        {"name": "rag_evaluation_archive_semantics", "status": "warning", "message": "RAG evaluation archive stale.", "details": {}},
        {"name": "rag_regression_archive_semantics", "status": "warning", "message": "RAG regression archive stale.", "details": {}},
        {"name": "rag_vector_index_archive_semantics", "status": "warning", "message": "Vector archive stale.", "details": {}},
        {"name": "workflow_trace_archive_semantics", "status": "warning", "message": "Workflow trace archive stale.", "details": {}},
        {"name": "optimizer_benchmark_archive_semantics", "status": "warning", "message": "Optimizer archive stale.", "details": {}},
    ]
    actions = {item["gate"]: item["action"] for item in deployment_readiness_service._required_actions(gates)}
    action_hashes = {item["gate"]: item["detail_hash"] for item in deployment_readiness_service._required_actions(gates)}
    assert "QC report bundle" in actions["qc_bundle_archive_semantics"]
    assert "data snapshot bundle" in actions["data_snapshot_archive_semantics"]
    assert "RAG evaluation bundle" in actions["rag_evaluation_archive_semantics"]
    assert "source-hash evidence" in actions["rag_evaluation_archive_semantics"]
    assert "RAG regression bundle" in actions["rag_regression_archive_semantics"]
    assert "quality-summary hashes" in actions["rag_regression_archive_semantics"]
    assert "vector index bundle" in actions["rag_vector_index_archive_semantics"]
    assert "workflow trace evidence" in actions["workflow_trace_archive_semantics"]
    assert "optimizer benchmark bundle" in actions["optimizer_benchmark_archive_semantics"]
    assert "recommendation-summary" in actions["optimizer_benchmark_archive_semantics"]
    assert len(set(actions.values())) == len(actions)
    assert all(len(value) == 64 for value in action_hashes.values())


def test_cli_production_audit_hashes_preflight_evidence_report() -> None:
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "production_audit.py"
    spec = importlib.util.spec_from_file_location("cli_production_audit", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    evidence_path = root / "backend" / "app" / "data" / "runtime" / f"preflight_unit_{uuid.uuid4().hex}.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence = {
        "preflight_schema": module.PREFLIGHT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "root": str(root),
        "output_json": str(evidence_path),
        "required_checks": module.REQUIRED_PREFLIGHT_CHECKS,
        "required_check_count": len(module.REQUIRED_PREFLIGHT_CHECKS),
        "checks": [
            _preflight_check(name, _preflight_detail(name), root)
            for name in module.REQUIRED_PREFLIGHT_CHECKS
        ],
        "mode": {"skip_frontend": True, "skip_smoke": False, "skip_signing_smoke": False, "skip_ui_smoke": False},
        "failed": [],
        "skipped": ["frontend_build"],
    }
    evidence["check_count"] = len(evidence["checks"])
    evidence["passed_count"] = len(evidence["checks"])
    evidence["failed_count"] = len(evidence["failed"])
    evidence["skipped_count"] = len(evidence["skipped"])
    evidence["mode_hash"] = module.hash_payload(evidence["mode"])
    evidence["skipped_hash"] = module.hash_payload(evidence["skipped"])
    evidence["checks_hash"] = module.hash_payload(evidence["checks"])
    evidence["preflight_hash"] = module.hash_payload({key: value for key, value in evidence.items() if key != "preflight_hash"})
    try:
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        preflight_check = module.validate_preflight_evidence(evidence_path, max_age_hours=24.0)
        report = {
            "audit_schema": "agentic-rag-cli-production-audit-v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "purpose": "deployment promotion evidence for the Agentic RAG codon optimization platform",
            "duration_seconds": 0.1,
            "mode": {"env": "template", "skip_api": True},
            "summary": {"status": "pass", "checks": 2, "failures": [], "warnings": ["preflight_evidence"]},
            "checks": [
                preflight_check,
                {
                    "name": "deployment_readiness",
                    "kind": "api",
                    "status": "pass",
                    "http_status": 200,
                    "failures": [],
                    "warnings": [],
                    "details": {"status": "pass"},
                },
            ],
        }
        report["audit_hash"] = module.audit_hash(report)
        markdown = module.render_markdown(report)
        audit_json_path = evidence_path.with_suffix(".audit.json")
        audit_md_path = evidence_path.with_suffix(".audit.md")
        audit_json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        audit_md_path.write_text(markdown, encoding="utf-8")
        write_result = module.write_result(report, json_path=audit_json_path, markdown_path=audit_md_path)

        assert preflight_check["status"] == "pass"
        assert preflight_check["details"]["preflight_schema"] == module.PREFLIGHT_SCHEMA
        assert preflight_check["details"]["root"] == str(root)
        assert preflight_check["details"]["output_json"] == str(evidence_path)
        assert preflight_check["details"]["check_count"] == len(module.REQUIRED_PREFLIGHT_CHECKS)
        assert preflight_check["details"]["skipped_count"] == 1
        assert preflight_check["details"]["missing_required_checks"] == []
        assert preflight_check["details"]["missing_command_checks"] == []
        assert preflight_check["details"]["missing_cwd_checks"] == []
        assert preflight_check["details"]["missing_duration_checks"] == []
        assert preflight_check["details"]["missing_details_checks"] == []
        assert preflight_check["details"]["missing_output_checks"] == []
        assert preflight_check["details"]["mode_hash"] == evidence["mode_hash"]
        assert preflight_check["details"]["skipped_hash"] == evidence["skipped_hash"]
        assert preflight_check["details"]["checks_hash"] == evidence["checks_hash"]
        assert preflight_check["details"]["preflight_hash"] == evidence["preflight_hash"]
        assert len(preflight_check["details"]["required_checks"]) == len(module.REQUIRED_PREFLIGHT_CHECKS)
        assert module.audit_hash(report) == report["audit_hash"]
        assert write_result["result_schema"] == "agentic-rag-cli-production-audit-write-result-v1"
        assert write_result["audit_hash"] == report["audit_hash"]
        assert write_result["json_sha256"] == module.file_sha256(audit_json_path)
        assert write_result["markdown_sha256"] == module.file_sha256(audit_md_path)
        assert write_result["preflight_status"] == "pass"
        assert write_result["preflight_failure_count"] == 0
        assert write_result["preflight_warning_count"] == 1
        assert write_result["preflight_hash"] == evidence["preflight_hash"]
        assert write_result["preflight_checks_hash"] == evidence["checks_hash"]
        assert report["audit_hash"] in markdown
        assert "## API Evidence" in markdown
        assert "API checks: `1`" in markdown
        assert "| deployment_readiness | pass | 200 | pass |" in markdown
        assert "## Preflight Evidence" in markdown
        assert f"Evidence root: `{root}`" in markdown
        assert f"Output JSON: `{evidence_path}`" in markdown
        assert f"Mode hash: `{evidence['mode_hash']}`" in markdown
        assert f"Skipped hash: `{evidence['skipped_hash']}`" in markdown
        assert "Missing required checks: `0`" in markdown
        assert "Actual failed checks: `0`" in markdown
        assert "Status mismatch checks: `0`" in markdown
        assert "Skipped checks: `1`" in markdown
        assert "Missing command evidence: `0`" in markdown
        assert "Missing cwd evidence: `0`" in markdown
        assert "Missing duration evidence: `0`" in markdown
        assert "Missing details evidence: `0`" in markdown
        assert "Missing output evidence: `0`" in markdown
        tampered = {**evidence, "preflight_hash": "0" * 64}
        evidence_path.write_text(json.dumps(tampered), encoding="utf-8")
        tampered_check = module.validate_preflight_evidence(evidence_path, max_age_hours=24.0)
        assert tampered_check["status"] == "fail"
        assert "preflight_hash" in " ".join(tampered_check["failures"])
        tampered = dict(evidence)
        tampered["skipped"] = []
        tampered["skipped_count"] = 0
        tampered["preflight_hash"] = module.hash_payload({key: value for key, value in tampered.items() if key != "preflight_hash"})
        evidence_path.write_text(json.dumps(tampered), encoding="utf-8")
        tampered_check = module.validate_preflight_evidence(evidence_path, max_age_hours=24.0)
        assert tampered_check["status"] == "fail"
        assert "skipped_hash" in " ".join(tampered_check["failures"])
        tampered = dict(evidence)
        tampered["mode"] = {"skip_frontend": False, "skip_smoke": False, "skip_signing_smoke": False, "skip_ui_smoke": False}
        tampered["mode_hash"] = module.hash_payload(tampered["mode"])
        tampered["preflight_hash"] = module.hash_payload({key: value for key, value in tampered.items() if key != "preflight_hash"})
        evidence_path.write_text(json.dumps(tampered), encoding="utf-8")
        tampered_check = module.validate_preflight_evidence(evidence_path, max_age_hours=24.0)
        assert tampered_check["status"] == "fail"
        assert "skipped[] does not match mode skip flags" in " ".join(tampered_check["failures"])
        tampered = json.loads(json.dumps(evidence))
        tampered["checks"][0]["returncode"] = 1
        tampered["checks"][0]["status"] = "fail"
        tampered["passed_count"] = len(tampered["checks"]) - 1
        tampered["failed"] = []
        tampered["failed_count"] = 0
        tampered["checks_hash"] = module.hash_payload(tampered["checks"])
        tampered["preflight_hash"] = module.hash_payload({key: value for key, value in tampered.items() if key != "preflight_hash"})
        evidence_path.write_text(json.dumps(tampered), encoding="utf-8")
        tampered_check = module.validate_preflight_evidence(evidence_path, max_age_hours=24.0)
        assert tampered_check["status"] == "fail"
        assert "failed[] does not match checks with nonzero returncode" in " ".join(tampered_check["failures"])
        tampered = json.loads(json.dumps(evidence))
        tampered["checks"][0]["status"] = "fail"
        tampered["checks_hash"] = module.hash_payload(tampered["checks"])
        tampered["preflight_hash"] = module.hash_payload({key: value for key, value in tampered.items() if key != "preflight_hash"})
        evidence_path.write_text(json.dumps(tampered), encoding="utf-8")
        tampered_check = module.validate_preflight_evidence(evidence_path, max_age_hours=24.0)
        assert tampered_check["status"] == "fail"
        assert "check status does not match returncode" in " ".join(tampered_check["failures"])
        tampered = json.loads(json.dumps(evidence))
        tampered["checks"][0].pop("command")
        tampered["checks"][1]["cwd"] = ""
        tampered["checks"][2]["duration_seconds"] = None
        tampered["checks_hash"] = module.hash_payload(tampered["checks"])
        tampered["preflight_hash"] = module.hash_payload({key: value for key, value in tampered.items() if key != "preflight_hash"})
        evidence_path.write_text(json.dumps(tampered), encoding="utf-8")
        tampered_check = module.validate_preflight_evidence(evidence_path, max_age_hours=24.0)
        failures = " ".join(tampered_check["failures"])
        assert tampered_check["status"] == "fail"
        assert "missing command evidence" in failures
        assert "missing cwd evidence" in failures
        assert "missing duration evidence" in failures
        tampered = dict(evidence)
        tampered["root"] = str(root / "other")
        tampered["output_json"] = str(evidence_path.with_name("different_preflight.json"))
        tampered["preflight_hash"] = module.hash_payload({key: value for key, value in tampered.items() if key != "preflight_hash"})
        evidence_path.write_text(json.dumps(tampered), encoding="utf-8")
        tampered_check = module.validate_preflight_evidence(evidence_path, max_age_hours=24.0)
        failures = " ".join(tampered_check["failures"])
        assert tampered_check["status"] == "fail"
        assert "root does not match" in failures
        assert "output_json does not match" in failures
        tampered = json.loads(json.dumps(evidence))
        tampered["checks"][0].pop("details")
        tampered["checks"][1].pop("stdout")
        tampered["checks"][2]["stderr"] = None
        tampered["checks_hash"] = module.hash_payload(tampered["checks"])
        tampered["preflight_hash"] = module.hash_payload({key: value for key, value in tampered.items() if key != "preflight_hash"})
        evidence_path.write_text(json.dumps(tampered), encoding="utf-8")
        tampered_check = module.validate_preflight_evidence(evidence_path, max_age_hours=24.0)
        failures = " ".join(tampered_check["failures"])
        assert tampered_check["status"] == "fail"
        assert "missing details evidence" in failures
        assert "missing stdout/stderr evidence" in failures
    finally:
        evidence_path.unlink(missing_ok=True)
        evidence_path.with_suffix(".audit.json").unlink(missing_ok=True)
        evidence_path.with_suffix(".audit.md").unlink(missing_ok=True)


def test_deployment_docs_describe_archive_evidence_contracts() -> None:
    root = Path(__file__).resolve().parents[2]
    deployment_doc = (root / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")

    for token in [
        "detail_hash",
        "data-snapshots/semantic-summary",
        "archived data snapshot semantic verification",
        "retrieval-quality source count/status",
        "optimizer objective count",
    ]:
        assert token in deployment_doc
    assert "hash-pinned required action evidence" in readme
    assert "archived semantic verification" in readme


def _preflight_detail(name: str) -> dict[str, Any]:
    if name == "structured_import_cli_preview":
        return {
            "status": "pass",
            "preview": {
                "status": "pass",
                "source": {"sha256": "a" * 64},
                "records": {"import_count": 1},
                "validation": {"projected": {"error_count": 0}},
            },
        }
    if name == "data_refresh_cli_plan":
        return {
            "status": "pass",
            "plan": {
                "dry_run": True,
                "manifest_hash": "b" * 64,
                "summary": {"planned": 1, "succeeded": 0, "failed": 0},
            },
        }
    if name == "data_refresh_cli_validate":
        return {
            "status": "warning",
            "validation": {
                "validation_schema": "agentic-rag-data-refresh-plan-validation-v1",
                "status": "warning",
                "normalized_request": {"genes": ["SNCA"], "brain_regions": ["substantia nigra"]},
                "plan": {"operation_count": 1},
            },
        }
    return {"status": "pass"}


def _preflight_check(name: str, details: dict[str, Any], root: Path) -> dict[str, Any]:
    return {
        "name": name,
        "returncode": 0,
        "status": "pass",
        "duration_seconds": 0.01,
        "command": ["python", name],
        "cwd": str(root / "backend"),
        "stdout": "{}",
        "stderr": "",
        "details": details,
    }


def test_cli_production_audit_requires_preflight_data_evidence_details() -> None:
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "production_audit.py"
    spec = importlib.util.spec_from_file_location("cli_production_audit_preflight_data", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    evidence_path = root / "backend" / "app" / "data" / "runtime" / f"preflight_unit_bad_{uuid.uuid4().hex}.json"
    evidence = {
        "preflight_schema": module.PREFLIGHT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "root": str(root),
        "output_json": str(evidence_path),
        "required_checks": module.REQUIRED_PREFLIGHT_CHECKS,
        "required_check_count": len(module.REQUIRED_PREFLIGHT_CHECKS),
        "checks": [
            _preflight_check(name, {"status": "pass"}, root)
            for name in module.REQUIRED_PREFLIGHT_CHECKS
        ],
        "mode": {"skip_frontend": False, "skip_smoke": False, "skip_signing_smoke": False, "skip_ui_smoke": False},
        "failed": [],
        "skipped": [],
    }
    evidence["check_count"] = len(evidence["checks"])
    evidence["passed_count"] = len(evidence["checks"])
    evidence["failed_count"] = len(evidence["failed"])
    evidence["skipped_count"] = len(evidence["skipped"])
    evidence["mode_hash"] = module.hash_payload(evidence["mode"])
    evidence["skipped_hash"] = module.hash_payload(evidence["skipped"])
    evidence["checks_hash"] = module.hash_payload(evidence["checks"])
    evidence["preflight_hash"] = module.hash_payload({key: value for key, value in evidence.items() if key != "preflight_hash"})
    try:
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        preflight_check = module.validate_preflight_evidence(evidence_path, max_age_hours=24.0)
        failures = " ".join(preflight_check["failures"])
        assert preflight_check["status"] == "fail"
        assert "structured_import_cli_preview did not record source sha256" in failures
        assert "data_refresh_cli_plan did not run as a dry run" in failures
        assert "data_refresh_cli_validate did not include the expected validation schema" in failures
        assert "preflight_hash" not in failures
    finally:
        evidence_path.unlink(missing_ok=True)


def test_cli_production_audit_write_result_verifier_recomputes_artifact_hashes() -> None:
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "verify_production_audit_write_result.py"
    spec = importlib.util.spec_from_file_location("production_audit_write_result_verifier", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    runtime_dir = root / "backend" / "app" / "data" / "runtime"
    audit_json_path = runtime_dir / f"production_audit_verifier_{uuid.uuid4().hex}.json"
    audit_md_path = runtime_dir / f"production_audit_verifier_{uuid.uuid4().hex}.md"
    preflight_path = runtime_dir / f"preflight_verifier_{uuid.uuid4().hex}.json"
    try:
        audit_json_path.write_text('{"summary":{"status":"pass"}}', encoding="utf-8")
        audit_md_path.write_text("# Production Audit\n", encoding="utf-8")
        preflight_path.write_text('{"status":"pass"}', encoding="utf-8")
        payload = {
            "result_schema": module.WRITE_RESULT_SCHEMA,
            "summary": {"status": "pass"},
            "audit_hash": "a" * 64,
            "json_path": str(audit_json_path),
            "json_sha256": module.file_sha256(audit_json_path),
            "markdown_path": str(audit_md_path),
            "markdown_sha256": module.file_sha256(audit_md_path),
            "preflight_evidence": str(preflight_path),
            "preflight_status": "pass",
            "preflight_failure_count": 0,
            "preflight_warning_count": 0,
            "preflight_hash": "b" * 64,
            "preflight_checks_hash": "c" * 64,
        }

        assert module.validate_write_result(payload, base_dir=root) == []
        tampered = {**payload, "json_sha256": "0" * 64}
        assert "json_sha256 mismatch" in " ".join(module.validate_write_result(tampered, base_dir=root))
        tampered = {**payload, "preflight_status": "warning"}
        assert "preflight_status must be pass" in " ".join(module.validate_write_result(tampered, base_dir=root))
        tampered = {**payload, "preflight_failure_count": 1}
        assert "preflight_failure_count must be 0" in " ".join(module.validate_write_result(tampered, base_dir=root))
        write_result_path = runtime_dir / f"production_audit_write_result_{uuid.uuid4().hex}.json"
        write_result_path.write_text(json.dumps(payload), encoding="utf-16")
        assert module.load_payload(write_result_path)["result_schema"] == module.WRITE_RESULT_SCHEMA
    finally:
        if "write_result_path" in locals():
            write_result_path.unlink(missing_ok=True)
        audit_json_path.unlink(missing_ok=True)
        audit_md_path.unlink(missing_ok=True)
        preflight_path.unlink(missing_ok=True)


def test_portfolio_readiness_matrix_covers_pdf_requirement_areas() -> None:
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "portfolio_readiness_matrix.py"
    spec = importlib.util.spec_from_file_location("portfolio_readiness_matrix", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    matrix = module.build_matrix(root)
    requirement_ids = {item["id"] for item in matrix["requirements"]}
    assert matrix["schema"] == module.MATRIX_SCHEMA
    assert matrix["summary"]["status"] == "pass"
    assert matrix["summary"]["requirement_count"] == len(module.REQUIREMENTS)
    assert len(matrix["matrix_hash"]) == 64
    assert {
        "real_data_ingestion",
        "agentic_rag_search",
        "multi_objective_optimizer",
        "qc_export_artifacts",
        "operator_ui",
        "operations_and_deployment",
        "verification_and_audit",
    }.issubset(requirement_ids)
    for requirement in matrix["requirements"]:
        assert requirement["status"] == "pass"
        assert requirement["evidence_count"] == requirement["passing_evidence_count"]
        assert len(requirement["evidence_hash"]) == 64


def test_compose_preflight_includes_required_external_service_env() -> None:
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "compose_preflight.py"
    spec = importlib.util.spec_from_file_location("compose_preflight_unit", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    failures: list[str] = []
    module.static_checks(failures)
    assert failures == []
    synthetic = module.SYNTHETIC_ENV
    required_object_store_keys = {
        "ARTIFACT_OBJECT_STORE_ENABLED",
        "ARTIFACT_OBJECT_STORE_ENDPOINT",
        "ARTIFACT_OBJECT_STORE_BUCKET",
        "ARTIFACT_OBJECT_STORE_PREFIX",
        "ARTIFACT_OBJECT_STORE_REGION",
        "ARTIFACT_OBJECT_STORE_ACCESS_KEY_ID",
        "ARTIFACT_OBJECT_STORE_SECRET_ACCESS_KEY",
        "ARTIFACT_EXTERNAL_TIMESTAMP_REQUIRED",
        "ARTIFACT_EXTERNAL_TIMESTAMP_URL",
        "ARTIFACT_EXTERNAL_TIMESTAMP_KEY_ID",
    }
    assert required_object_store_keys.issubset(synthetic)
    assert synthetic["ARTIFACT_OBJECT_STORE_ENABLED"] == "true"
    assert synthetic["ARTIFACT_OBJECT_STORE_ENDPOINT"].startswith("https://")
    assert synthetic["ARTIFACT_OBJECT_STORE_PREFIX"]
    assert synthetic["ARTIFACT_OBJECT_STORE_REGION"]
    assert synthetic["ARTIFACT_OBJECT_STORE_ACCESS_KEY_ID"]
    assert synthetic["ARTIFACT_OBJECT_STORE_SECRET_ACCESS_KEY"]
    assert synthetic["ARTIFACT_EXTERNAL_TIMESTAMP_REQUIRED"] == "true"
    assert synthetic["ARTIFACT_EXTERNAL_TIMESTAMP_URL"].startswith("https://")
    assert synthetic["ARTIFACT_EXTERNAL_TIMESTAMP_KEY_ID"]

    required_openai_embedding_keys = {
        "RAG_EMBEDDING_BACKEND",
        "RAG_EMBEDDING_MODEL",
        "RAG_EMBEDDING_DIMENSIONS",
        "OPENAI_API_KEY",
        "OPENAI_EMBEDDING_BASE_URL",
        "OPENAI_EMBEDDING_PRICE_PER_1K_TOKENS",
        "OPENAI_EMBEDDING_BUDGET_USD",
    }
    assert required_openai_embedding_keys.issubset(synthetic)
    assert synthetic["RAG_EMBEDDING_BACKEND"] == "openai"
    assert synthetic["OPENAI_API_KEY"]
    assert synthetic["OPENAI_EMBEDDING_BASE_URL"].startswith("https://")
    assert float(synthetic["OPENAI_EMBEDDING_PRICE_PER_1K_TOKENS"]) > 0
    assert float(synthetic["OPENAI_EMBEDDING_BUDGET_USD"]) > 0


def test_production_env_validator_tracks_openai_embedding_env() -> None:
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_production_env.py"
    spec = importlib.util.spec_from_file_location("validate_production_env_unit", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    required_openai_embedding_keys = {
        "OPENAI_API_KEY",
        "OPENAI_EMBEDDING_BASE_URL",
        "OPENAI_EMBEDDING_PRICE_PER_1K_TOKENS",
        "OPENAI_EMBEDDING_BUDGET_USD",
    }
    assert required_openai_embedding_keys.issubset(module.REQUIRED_KEYS)


def test_production_env_template_requires_object_store_credentials() -> None:
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_production_env.py"
    spec = importlib.util.spec_from_file_location("validate_production_env_template_unit", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    values = module.parse_env(root / ".env.production.example")
    for key in [
        "ARTIFACT_OBJECT_STORE_PREFIX",
        "ARTIFACT_OBJECT_STORE_ACCESS_KEY_ID",
        "ARTIFACT_OBJECT_STORE_SECRET_ACCESS_KEY",
        "ARTIFACT_EXTERNAL_TIMESTAMP_URL",
        "ARTIFACT_EXTERNAL_TIMESTAMP_KEY_ID",
    ]:
        broken = dict(values)
        broken[key] = ""
        failures: list[str] = []
        warnings: list[str] = []
        module.validate_template(broken, failures, warnings)
        assert key.lower().replace("artifact_object_store_", "").replace("artifact_external_timestamp_", "").replace("_", " ") in " ".join(failures).lower()


def test_production_env_strict_requires_hmac_signing_key_id() -> None:
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_production_env.py"
    spec = importlib.util.spec_from_file_location("validate_production_env_signing_unit", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    values = module.parse_env(root / ".env.production.example")
    values.update(
        {
            "NEXT_PUBLIC_API_BASE_URL": "https://dashboard.example.org/api/v1",
            "NEXT_PUBLIC_API_KEY": "viewer-key-000000000000000000",
            "CORS_ORIGINS": "https://dashboard.example.org",
            "DATABASE_URL": "postgresql://agentic:strong-secret@postgres:5432/agentic",
            "POSTGRES_PASSWORD": "strong-postgres-secret",
            "OPENAI_API_KEY": "",
            "OPENAI_EMBEDDING_PRICE_PER_1K_TOKENS": "0",
            "API_KEYS": "admin-key-000000000000000000,viewer-key-000000000000000000",
            "API_KEY_ROLES": "admin-key-000000000000000000=admin;viewer-key-000000000000000000=viewer",
            "ARTIFACT_SIGNING_KEY": "unit-test-hmac-signing-secret-with-32-bytes",
            "ARTIFACT_SIGNING_KEY_ID": "",
            "ARTIFACT_OBJECT_STORE_ENDPOINT": "https://s3.example.org",
            "ARTIFACT_OBJECT_STORE_BUCKET": "agentic-rag-prod",
            "ARTIFACT_OBJECT_STORE_ACCESS_KEY_ID": "object-access-key",
            "ARTIFACT_OBJECT_STORE_SECRET_ACCESS_KEY": "object-secret-key",
        }
    )
    failures: list[str] = []
    warnings: list[str] = []
    module.validate_strict(values, failures, warnings)
    assert "ARTIFACT_SIGNING_KEY_ID" in " ".join(failures)


def test_artifact_object_store_status_includes_lifecycle_policy_hash() -> None:
    with patch.dict(
        os.environ,
        {
            "ARTIFACT_RETENTION_DAYS": "365",
            "ARTIFACT_RETENTION_KEEP_MIN": "1000",
            "ARTIFACT_OBJECT_STORE_ENABLED": "true",
            "ARTIFACT_OBJECT_STORE_ENDPOINT": "https://s3.example.com",
            "ARTIFACT_OBJECT_STORE_BUCKET": "agentic-rag-prod-artifacts",
            "ARTIFACT_OBJECT_STORE_PREFIX": "agentic-rag/artifacts",
            "ARTIFACT_OBJECT_STORE_REGION": "us-east-1",
            "ARTIFACT_OBJECT_STORE_ACCESS_KEY_ID": "unit-test-access-key",
            "ARTIFACT_OBJECT_STORE_SECRET_ACCESS_KEY": "unit-test-secret-key",
            "ARTIFACT_EXTERNAL_TIMESTAMP_REQUIRED": "true",
            "ARTIFACT_EXTERNAL_TIMESTAMP_URL": "https://timestamp.example.com/rfc3161",
            "ARTIFACT_EXTERNAL_TIMESTAMP_KEY_ID": "unit-test-rfc3161",
        },
    ):
        status = artifact_object_store_status()
        summary_object_store = archive_summary()["object_store"]

    lifecycle = status["lifecycle_policy"]
    assert status["status"] == "ready"
    assert lifecycle["status"] == "pass"
    assert lifecycle["retention_days"] == 365
    assert lifecycle["keep_min"] == 1000
    assert lifecycle["object_store_mirror_ready"] is True
    assert status["external_timestamp"]["status"] == "pass"
    assert status["external_timestamp"]["required"] is True
    assert status["external_timestamp"]["endpoint"] == "https://timestamp.example.com"
    assert status["external_timestamp"]["key_id"] == "unit-test-rfc3161"
    assert len(status["external_timestamp_hash"]) == 64
    assert len(status["lifecycle_policy_hash"]) == 64
    assert summary_object_store["lifecycle_policy"]["status"] == "pass"
    assert summary_object_store["lifecycle_policy_hash"] == status["lifecycle_policy_hash"]
    assert summary_object_store["external_timestamp"]["status"] == "pass"
    assert summary_object_store["external_timestamp_hash"] == status["external_timestamp_hash"]


def test_production_gap_summary_classifies_resolution_scope() -> None:
    from app.services import production_audit_service as module

    detail_hash = module._hash_payload({"active_backend": "hash-bow-v1"})
    checks = [
        {
            "name": "deployment_readiness",
            "status": "warning",
            "blocking": False,
            "message": "Deployment readiness has promotion warnings.",
            "detail_hash": module._hash_payload({"warning": 1}),
        },
        {
            "name": "rag_embedding_backend",
            "status": "warning",
            "blocking": False,
            "message": "Embedding backend is not production configured.",
            "detail_hash": detail_hash,
        },
        {
            "name": "data_release_archive_semantics",
            "status": "warning",
            "blocking": False,
            "message": "Data release archive is stale.",
            "detail_hash": module._hash_payload({"status": "warning"}),
        },
        {
            "name": "optimizer_diagnostics",
            "status": "warning",
            "blocking": False,
            "message": "Optimizer diagnostics have promotion warnings.",
            "detail_hash": module._hash_payload({"status": "warning", "area": "optimizer"}),
        },
    ]
    readiness = {
        "required_actions": [
            {
                "gate": "rag_embedding_backend",
                "action": "Configure a production embedding backend.",
                "detail_hash": detail_hash,
            }
        ],
        "required_actions_hash": module._hash_payload(["rag_embedding_backend"]),
    }
    promotion = {
        "items": [
            {
                "area": "data_release_archive_semantics",
                "action": "Archive a fresh data release bundle.",
            }
        ],
        "required_actions": ["Configure a production embedding backend."],
    }

    summary = module._production_gap_summary(checks, promotion, readiness)
    gaps = {gap["area"]: gap for gap in summary["gaps"]}

    assert gaps["deployment_readiness"]["resolution_scope"] == "aggregate_gate"
    assert gaps["deployment_readiness"]["resolution_mode"] == "readiness_rollup"
    assert "required_actions" in gaps["deployment_readiness"]["proof_hint"]
    assert gaps["rag_embedding_backend"]["resolution_scope"] == "operator_environment"
    assert gaps["rag_embedding_backend"]["resolution_mode"] == "managed_runtime"
    assert "rebuild the RAG index" in gaps["rag_embedding_backend"]["proof_hint"]
    assert gaps["data_release_archive_semantics"]["resolution_scope"] == "reproducible_artifact"
    assert gaps["data_release_archive_semantics"]["resolution_mode"] == "artifact_refresh"
    assert "fresh data release bundle" in gaps["data_release_archive_semantics"]["proof_hint"]
    assert gaps["optimizer_diagnostics"]["resolution_scope"] == "optimizer_validation"
    assert gaps["optimizer_diagnostics"]["resolution_mode"] == "benchmark_calibration"
    assert "optimizer diagnostics" in gaps["optimizer_diagnostics"]["proof_hint"]
    assert summary["resolution_scope_counts"] == {
        "aggregate_gate": 1,
        "operator_environment": 1,
        "optimizer_validation": 1,
        "reproducible_artifact": 1,
    }
    assert summary["resolution_mode_counts"] == {
        "artifact_refresh": 1,
        "benchmark_calibration": 1,
        "managed_runtime": 1,
        "readiness_rollup": 1,
    }
    assert module._gap_summary_hash_matches(summary)


def test_production_promotion_runbook_groups_gap_evidence(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "production_promotion_runbook.py"
    spec = importlib.util.spec_from_file_location("production_promotion_runbook", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    valid_hash = "a" * 64
    gap = {
        "area": "rag_embedding_backend",
        "status": "warning",
        "priority": "promotion",
        "resolution_scope": "operator_environment",
        "resolution_mode": "managed_runtime",
        "proof_hint": "Configure OpenAI or sentence-transformers and rebuild the index.",
        "evidence_key": "rag_embedding",
        "check_detail_hash": valid_hash,
        "readiness_detail_hash": valid_hash,
        "action": "Configure a production embedding backend.",
    }
    gap["gap_hash"] = module.hash_payload(gap)
    gap_summary = {
        "gap_schema": "agentic-rag-production-gap-summary-v1",
        "status": "warning",
        "gap_count": 1,
        "blocking_count": 0,
        "promotion_count": 1,
        "resolution_scope_counts": {"operator_environment": 1},
        "resolution_mode_counts": {"managed_runtime": 1},
        "evidence_keys": ["rag_embedding"],
        "readiness_action_hash": valid_hash,
        "promotion_required_action_count": 1,
        "gaps": [gap],
    }
    gap_summary["gap_summary_hash"] = module.hash_payload(gap_summary)
    audit = {
        "audit_hash": valid_hash,
        "generated_at": "2026-06-09T00:00:00Z",
        "summary": {"production_ready": False},
        "production_gap_summary": gap_summary,
    }
    audit_path = tmp_path / "production_audit.json"
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    write_result_path = tmp_path / "write_result.json"
    write_result_path.write_text(json.dumps({"json_path": str(audit_path)}), encoding="utf-16")

    loaded = module.load_audit(type("Args", (), {"audit_json": None, "write_result": write_result_path})())
    runbook = module.build_runbook(loaded)
    markdown = module.render_markdown(runbook)

    assert runbook["runbook_schema"] == "agentic-rag-production-promotion-runbook-v1"
    assert runbook["verification"]["status"] == "pass"
    assert runbook["resolution_scope_counts"] == {"operator_environment": 1}
    assert runbook["groups"][0]["resolution_scope"] == "operator_environment"
    assert "Production Promotion Runbook" in markdown
    assert "rag_embedding_backend" in markdown
    assert len(runbook["runbook_hash"]) == 64

    tampered = dict(gap_summary)
    tampered["gap_count"] = 2
    assert module.verify_gap_summary(tampered)["status"] == "fail"
    static_runbook = module.build_runbook({"audit_hash": valid_hash, "summary": {"production_ready": False}})
    assert static_runbook["verification"]["status"] == "warning"
    assert "live production audit" in static_runbook["verification"]["warnings"][0]


def test_cli_production_audit_requires_bundle_hash_evidence() -> None:
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "production_audit.py"
    spec = importlib.util.spec_from_file_location("cli_production_audit_hash_checks", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    valid_hash = "a" * 64
    api_check_names = {check["name"] for check in module.API_CHECKS}
    assert "structured_import_archive_semantics" in api_check_names
    assert "data_snapshot_archive_semantics" in api_check_names
    assert "data_refresh_plan_archive_semantics" in api_check_names
    assert "data_release_archive_semantics" in api_check_names
    assert "rag_regression_archive_semantics" in api_check_names
    assert "rag_vector_index_archive_semantics" in api_check_names
    assert "workflow_trace_archive_semantics" in api_check_names
    assert "rag_embedding_status" in api_check_names
    assert "rna_folding_status" in api_check_names
    assert "artifact_object_store_mirror_plan" in api_check_names
    assert "production_audit_status" in api_check_names
    assert "production_audit_bundle_verify" in api_check_names
    readiness_gates = [
        {"name": "structured_data", "status": "pass", "message": "Gate passed.", "details": {"records": 12}},
        {"name": "rag_embedding_backend", "status": "warning", "message": "Pin embedding backend.", "details": {"active_backend": "hash_bow"}},
    ]
    readiness_attention = ["rag_embedding_backend"]
    readiness_actions = [
        {
            "gate": "rag_embedding_backend",
            "status": "warning",
            "priority": "promotion",
            "message": "Pin embedding backend.",
            "action": "Configure a production embedding backend.",
            "detail_hash": module.compact_hash_payload({"active_backend": "hash_bow"}),
        }
    ]
    assert module.api_failures(
        "deployment_readiness",
        {
            "data": {
                "deployment_ready": True,
                "summary": {"pass": 1, "warning": 1, "fail": 0},
                "gates": readiness_gates,
                "attention_gates": readiness_attention,
                "attention_gates_hash": module.compact_hash_payload(readiness_attention),
                "required_actions": readiness_actions,
                "required_actions_hash": module.compact_hash_payload(readiness_actions),
            }
        },
    ) == []
    readiness_failures = module.api_failures(
        "deployment_readiness",
        {
            "data": {
                "deployment_ready": True,
                "summary": {"pass": 2, "warning": 0, "fail": 0},
                "gates": readiness_gates,
                "attention_gates": [],
                "attention_gates_hash": "",
                "required_actions": [{**readiness_actions[0], "detail_hash": "0" * 64}],
                "required_actions_hash": "",
            }
        },
    )
    assert "summary counts" in " ".join(readiness_failures)
    assert "attention_gates" in " ".join(readiness_failures)
    assert "attention_gates_hash" in " ".join(readiness_failures)
    assert "required_actions_hash" in " ".join(readiness_failures)
    assert "detail_hash" in " ".join(readiness_failures)
    production_gap = {
        "area": "rag_embedding_backend",
        "status": "warning",
        "priority": "promotion",
        "resolution_scope": "operator_environment",
        "resolution_mode": "managed_runtime",
        "proof_hint": "Configure OpenAI or sentence-transformers and rebuild the index.",
        "evidence_key": "rag_embedding",
        "check_detail_hash": valid_hash,
        "readiness_detail_hash": valid_hash,
        "action": "Configure a production embedding backend.",
    }
    production_gap["gap_hash"] = module.compact_hash_payload(production_gap)
    production_gap_summary = {
        "gap_schema": "agentic-rag-production-gap-summary-v1",
        "status": "warning",
        "gap_count": 1,
        "blocking_count": 0,
        "promotion_count": 1,
        "resolution_scope_counts": {"operator_environment": 1},
        "resolution_mode_counts": {"managed_runtime": 1},
        "evidence_keys": ["rag_embedding"],
        "readiness_action_hash": valid_hash,
        "promotion_required_action_count": 1,
        "gaps": [production_gap],
    }
    production_gap_summary["gap_summary_hash"] = module.compact_hash_payload(production_gap_summary)
    assert module.api_failures(
        "production_audit_status",
        {
            "data": {
                "audit_hash": valid_hash,
                "summary": {"status": "warning"},
                "production_gap_summary": production_gap_summary,
                "evidence": {"production_gap_summary": production_gap_summary},
            }
        },
    ) == []
    tampered_gap_summary = {
        **production_gap_summary,
        "resolution_scope_counts": {"operator_environment": 2},
    }
    gap_failures = module.api_failures(
        "production_audit_status",
        {
            "data": {
                "audit_hash": valid_hash,
                "summary": {"status": "warning"},
                "production_gap_summary": tampered_gap_summary,
                "evidence": {"production_gap_summary": production_gap_summary},
            }
        },
    )
    assert "production_gap_summary does not match embedded evidence" in " ".join(gap_failures)
    assert "resolution_scope_counts" in " ".join(gap_failures)
    audit_semantic_checks = {
        "audit_hash": "pass",
        "evidence_hashes_manifest": "pass",
        "evidence_hashes_combined": "pass",
        "check_detail_hashes": "pass",
        "required_action_coverage": "pass",
        "required_action_detail_hashes": "pass",
        "markdown_recomputed": "pass",
        "summary_recomputed": "pass",
    }
    assert module.api_failures(
        "production_audit_bundle_verify",
        {
            "data": {
                "status": "pass",
                "audit_hash": valid_hash,
                "artifact_verification": {"status": "pass"},
                "semantic_checks": audit_semantic_checks,
                "semantic_summary": {
                    "summary_schema": "agentic-rag-production-audit-semantic-summary-v1",
                    "check_count": len(audit_semantic_checks),
                    "pass_count": len(audit_semantic_checks),
                    "fail_count": 0,
                    "warning_count": 0,
                    "status": "pass",
                    "summary_hash": valid_hash,
                },
            }
        },
    ) == []
    audit_verify_failures = module.api_failures(
        "production_audit_bundle_verify",
        {
            "data": {
                "status": "warning",
                "audit_hash": "",
                "artifact_verification": {"status": "warning"},
                "semantic_checks": {**audit_semantic_checks, "required_action_coverage": "fail"},
                "semantic_summary": {
                    "summary_schema": "unexpected",
                    "check_count": len(audit_semantic_checks),
                    "pass_count": len(audit_semantic_checks) - 1,
                    "fail_count": 1,
                    "warning_count": 0,
                    "status": "fail",
                    "summary_hash": "",
                },
            }
        },
    )
    assert "verification status" in " ".join(audit_verify_failures)
    assert "artifact verification" in " ".join(audit_verify_failures)
    assert "audit_hash" in " ".join(audit_verify_failures)
    assert "semantic_summary" in " ".join(audit_verify_failures)
    assert "summary_hash" in " ".join(audit_verify_failures)
    assert "required_action_coverage" in " ".join(audit_verify_failures)
    assert module.api_failures(
        "rag_embedding_status",
        {
            "data": {
                "status": "pass",
                "production_ready": True,
                "active_backend": "openai",
                "fallback_active": False,
                "model_fingerprint_hash": valid_hash,
                "openai": {"budget": {"price_configured": True, "budget_configured": True, "within_budget": True}},
            }
        },
    ) == []
    embedding_failures = module.api_failures(
        "rag_embedding_status",
        {
            "data": {
                "status": "warning",
                "production_ready": False,
                "active_backend": "hash_bow",
                "fallback_active": True,
                "model_fingerprint_hash": "",
                "openai": {"budget": {"price_configured": False, "budget_configured": False, "within_budget": False}},
            }
        },
    )
    assert "production_ready" in " ".join(embedding_failures)
    assert "production backend" in " ".join(embedding_failures)
    assert "fallback_active" in " ".join(embedding_failures)
    assert "model_fingerprint_hash" in " ".join(embedding_failures)
    assert module.api_failures(
        "rna_folding_status",
        {
            "data": {
                "status": "ready",
                "production_ready": True,
                "active_backend": "rnafold",
                "fallback_active": False,
                "validated_backend": True,
                "executable_path": "C:\\Tools\\RNAfold.exe",
            }
        },
    ) == []
    folding_failures = module.api_failures(
        "rna_folding_status",
        {
            "data": {
                "status": "proxy",
                "production_ready": False,
                "active_backend": "deterministic_proxy",
                "fallback_active": True,
                "validated_backend": False,
                "executable_path": None,
            }
        },
    )
    assert "production_ready" in " ".join(folding_failures)
    assert "rnafold" in " ".join(folding_failures)
    assert "fallback_active" in " ".join(folding_failures)
    assert "validated_backend" in " ".join(folding_failures)
    assert "executable_path" in " ".join(folding_failures)
    assert module.api_failures(
        "governance_attestation_verify",
        {
            "data": {
                "status": "pass",
                "attestation_hash": valid_hash,
                "artifact_verification": {"status": "pass", "checked_files": 7, "file_count": 7},
                "signature": {"status": "verified"},
            }
        },
    ) == []
    governance_failures = module.api_failures(
        "governance_attestation_verify",
        {
            "data": {
                "status": "warning",
                "attestation_hash": "",
                "artifact_verification": {"status": "warning", "checked_files": 0, "file_count": 7},
                "signature": {"status": "unsigned"},
            }
        },
    )
    assert "verification did not pass" in " ".join(governance_failures)
    assert "attestation_hash" in " ".join(governance_failures)
    assert "artifact verification" in " ".join(governance_failures)
    assert "checked no files" in " ".join(governance_failures)
    assert "signature" in " ".join(governance_failures)
    assert module.api_failures(
        "artifact_ledger_verify",
        {"data": {"status": "pass", "entry_count": 3, "latest_hash": valid_hash, "missing_from_ledger_count": 0}},
    ) == []
    ledger_failures = module.api_failures(
        "artifact_ledger_verify",
        {"data": {"status": "warning", "entry_count": 0, "latest_hash": "GENESIS", "missing_from_ledger_count": 2}},
    )
    assert "verification did not pass" in " ".join(ledger_failures)
    assert "no entries" in " ".join(ledger_failures)
    assert "GENESIS" in " ".join(ledger_failures)
    assert "missing from ledger" in " ".join(ledger_failures)
    assert module.api_failures(
        "security_status",
        {
            "data": {
                "auth_enabled": True,
                "rbac_enabled": True,
                "configured_keys": 1,
                "configured_role_bindings": 1,
                "rate_limit_per_minute": 60,
                "artifact_signing_enabled": True,
                "artifact_signing_key_id": "prod-hmac-key",
                "signing": {"hmac": {"signing_enabled": True, "key_id": "prod-hmac-key"}},
            }
        },
    ) == []
    assert module.api_failures(
        "storage_status",
        {
            "data": {
                "status": "pass",
                "target_backend": "postgres",
                "active_runtime_adapter": "postgres",
                "database_url_configured": True,
                "postgres": {
                    "schema_hash": valid_hash,
                    "driver_available": True,
                    "required_extensions": ["vector"],
                },
            }
        },
    ) == []
    storage_failures = module.api_failures(
        "storage_status",
        {
            "data": {
                "status": "pass",
                "target_backend": "sqlite",
                "active_runtime_adapter": "sqlite",
                "database_url_configured": False,
                "postgres": {"schema_hash": "", "driver_available": False, "required_extensions": []},
            }
        },
    )
    assert "target_backend" in " ".join(storage_failures)
    assert "active_runtime_adapter" in " ".join(storage_failures)
    assert "DATABASE_URL" in " ".join(storage_failures)
    assert "schema_hash" in " ".join(storage_failures)
    assert "driver" in " ".join(storage_failures)
    assert "vector extension" in " ".join(storage_failures)
    security_failures = module.api_failures(
        "security_status",
        {
            "data": {
                "auth_enabled": False,
                "rbac_enabled": False,
                "configured_keys": 0,
                "configured_role_bindings": 0,
                "rate_limit_per_minute": 0,
                "artifact_signing_enabled": True,
                "artifact_signing_key_id": "",
                "signing": {"hmac": {"signing_enabled": True, "key_id": ""}},
            }
        },
    )
    assert "authentication" in " ".join(security_failures)
    assert "RBAC" in " ".join(security_failures)
    assert "configured API keys" in " ".join(security_failures)
    assert "rate_limit_per_minute" in " ".join(security_failures)
    assert "key_id" in " ".join(security_failures)
    assert module.api_failures(
        "artifact_object_store_mirror_plan",
        {"data": {"status": "ready", "candidate_count": 0, "candidate_bytes": 0, "object_store": {"status": "ready", "enabled": True}}},
    ) == []
    assert "pending candidates" in " ".join(
        module.api_failures(
            "artifact_object_store_mirror_plan",
            {"data": {"status": "ready", "candidate_count": 2, "candidate_bytes": 1234, "object_store": {"status": "ready", "enabled": True}}},
        )
    )
    assert "misconfigured" in " ".join(
        module.api_failures(
            "artifact_object_store_mirror_plan",
            {"data": {"status": "misconfigured", "candidate_count": 0, "candidate_bytes": 0, "object_store": {"status": "misconfigured", "enabled": True}}},
        )
    )
    assert "disabled" in " ".join(
        module.api_warnings(
            "artifact_object_store_mirror_plan",
            {"data": {"status": "disabled", "candidate_count": 0, "candidate_bytes": 0, "object_store": {"status": "disabled", "enabled": False}}},
        )
    )
    structured_import_failures = module.api_failures(
        "structured_import_archive_semantics",
        {"data": {"status": "pass", "checked_count": 1, "latest_artifacts": [{"semantic_status": "pass"}]}},
    )
    assert "structured_manifest_hash" in " ".join(structured_import_failures)
    assert "checked_files" in " ".join(structured_import_failures)
    assert "file_count" in " ".join(structured_import_failures)
    assert module.api_failures(
        "data_release_bundle_verify",
        {
            "data": {
                "status": "pass",
                "semantic_status": "pass",
                "semantic_checks": {
                    "records_hash": "pass",
                    "records_csv_hash": "pass",
                    "record_source_summary_hash": "pass",
                    "record_source_summary_schema": "pass",
                    "record_source_summary_consistency": "pass",
                    "record_source_summary_counts": "pass",
                    "release_handoff_hash": "pass",
                    "rag_structured_manifest_hash": "pass",
                    "rag_index_hash": "pass",
                    "trna_caveat_count": "pass",
                    "trna_blocking_production_use": "pass",
                    "external_snapshot_reference_coverage": "pass",
                },
                "records_hash": valid_hash,
                "records_csv_hash": valid_hash,
                "record_source_summary_hash": valid_hash,
                "release_handoff_hash": valid_hash,
                "rag_structured_manifest_hash": "manifest-hash",
                "rag_index_hash": valid_hash,
                "trna_caveat_count": 0,
                "trna_blocking_production_use": False,
            }
        },
    ) == []
    assert module.api_failures(
        "rag_evaluation_bundle_verify",
        {
            "data": {
                "status": "pass",
                "semantic_status": "pass",
                "semantic_checks": {
                    "retrieval_trace_schema": "pass",
                    "evidence_sufficiency_schema": "pass",
                    "facet_gap_analysis": "pass",
                    "query_term_coverage": "pass",
                    "source_provenance_schema": "pass",
                    "source_provenance_consistency": "pass",
                    "source_provenance_count": "pass",
                    "retrieval_trace_hash": "pass",
                    "evidence_sufficiency_hash": "pass",
                    "facet_gap_analysis_hash": "pass",
                    "query_term_coverage_hash": "pass",
                    "top_sources_consistency": "pass",
                    "evaluation_hash": "pass",
                    "top_sources_hash": "pass",
                    "source_provenance_hash": "pass",
                    "score_breakdown_hash": "pass",
                    "chunks_hash": "pass",
                },
                "evaluation_hash": valid_hash,
                "retrieval_trace_hash": valid_hash,
                "evidence_sufficiency_hash": valid_hash,
                "facet_gap_analysis_hash": valid_hash,
                "query_term_coverage_hash": valid_hash,
                "top_sources_hash": valid_hash,
                "source_provenance_hash": valid_hash,
                "source_provenance_count": 3,
                "score_breakdown_hash": valid_hash,
                "chunks_hash": valid_hash,
            }
        },
    ) == []
    assert module.api_failures(
        "rag_regression_bundle_verify",
        {
            "data": {
                "status": "pass",
                "semantic_status": "pass",
                "semantic_checks": {
                    "results_hash": "pass",
                    "quality_summary_hash": "pass",
                    "quality_summary_schema": "pass",
                    "quality_summary_case_count": "pass",
                    "quality_summary_status": "pass",
                    "case_metric_columns": "pass",
                    "case_metrics_hash": "pass",
                    "source_provenance_summary_schema": "pass",
                    "source_provenance_summary_consistency": "pass",
                    "source_provenance_summary_hash": "pass",
                    "source_provenance_case_count": "pass",
                },
                "results_hash": valid_hash,
                "quality_summary_hash": valid_hash,
                "case_metrics_hash": valid_hash,
                "source_provenance_summary_hash": valid_hash,
            }
        },
    ) == []
    assert module.api_failures(
        "optimizer_benchmark_bundle_verify",
        {
            "data": {
                "status": "pass",
                "semantic_status": "pass",
                "semantic_checks": {
                    "search_strategy_schema": "pass",
                    "optimizer_seed_strategy": "pass",
                    "candidate_diagnostics": "pass",
                    "recommendation_audit": "pass",
                    "pareto_quality_schema": "pass",
                    "pareto_quality_hash": "pass",
                    "recommendation_summary_schema": "pass",
                    "recommendation_summary_consistency": "pass",
                    "recommendation_summary_case_count": "pass",
                    "recommendation_summary_hash": "pass",
                    "case_metric_columns": "pass",
                    "results_hash": "pass",
                    "benchmark_hash": "pass",
                    "diagnostics_hash": "pass",
                    "case_metrics_hash": "pass",
                    "candidate_diagnostics_hash": "pass",
                    "recommended_folding_evidence_schema": "pass",
                    "recommended_folding_evidence_hashes": "pass",
                    "recommended_folding_evidence_payload_hash": "pass",
                    "recommended_folding_evidence_hash": "pass",
                },
                "results_hash": valid_hash,
                "benchmark_hash": valid_hash,
                "diagnostics_hash": valid_hash,
                "case_metrics_hash": valid_hash,
                "candidate_diagnostics_hash": valid_hash,
                "recommendation_summary_hash": valid_hash,
                "recommended_folding_evidence_hash": valid_hash,
            }
        },
    ) == []
    assert module.api_failures(
        "qc_report_bundle_verify",
        {
            "data": {
                "status": "pass",
                "semantic_status": "pass",
                "semantic_checks": {
                    "request_payload": "pass",
                    "request_hash": "pass",
                    "qc_report_hash": "pass",
                    "report_formats_summary_hash": "pass",
                    "candidate_ranking_hash": "pass",
                    "recommendation_audit_hash": "pass",
                    "recommendation_readiness_hash": "pass",
                    "recommended_folding_evidence_hash": "pass",
                    "optimizer_hash_report": "pass",
                    "report_formats_summary_schema": "pass",
                    "report_formats_summary_consistency": "pass",
                    "candidate_csv_explainability_columns": "pass",
                    "candidate_ranking_report_count": "pass",
                    "candidate_ranking_report_ids": "pass",
                    "candidate_ranking_report_scores": "pass",
                    "candidate_diagnostics_candidate_count": "pass",
                    "recommended_constraint_risk_csv": "pass",
                    "recommended_candidate_score_csv": "pass",
                    "objective_inventory": "pass",
                    "evidence_retrieval_quality_schema": "pass",
                    "evidence_retrieval_quality_sources": "pass",
                    "evidence_retrieval_quality_rank_hashes": "pass",
                    "recommendation_audit": "pass",
                    "recommendation_audit_file_schema": "pass",
                    "recommendation_audit_file_report": "pass",
                    "recommendation_audit_candidate": "pass",
                    "recommendation_readiness_schema": "pass",
                    "recommendation_readiness_report": "pass",
                    "recommendation_readiness_payload_hash": "pass",
                    "recommendation_readiness_candidate": "pass",
                    "recommendation_readiness_status": "pass",
                    "recommended_folding_evidence_schema": "pass",
                    "recommended_folding_evidence_report": "pass",
                    "recommended_folding_evidence_payload_hash": "pass",
                },
                "request_hash": valid_hash,
                "qc_report_hash": valid_hash,
                "report_formats_summary_hash": valid_hash,
                "candidate_ranking_hash": valid_hash,
                "recommendation_audit_hash": valid_hash,
                "recommendation_readiness_hash": valid_hash,
                "recommended_folding_evidence_hash": valid_hash,
                "retrieval_quality_rank_evidence_count": 3,
                "retrieval_quality_rank_evidence_hash": valid_hash,
            }
        },
    ) == []

    assert "results_hash" in " ".join(
        module.api_failures("rag_regression_bundle_verify", {"data": {"status": "pass", "semantic_status": "pass", "semantic_checks": {}}})
    )
    rag_regression_failures = module.api_failures(
        "rag_regression_bundle_verify",
        {"data": {"status": "pass", "semantic_status": "pass", "semantic_checks": {"results_hash": "pass"}, "results_hash": valid_hash}},
    )
    assert "quality_summary_hash" in " ".join(rag_regression_failures)
    assert "quality_summary_schema" in " ".join(rag_regression_failures)
    assert "case_metric_columns" in " ".join(rag_regression_failures)
    assert "case_metrics_hash" in " ".join(rag_regression_failures)
    rag_eval_failures = module.api_failures(
        "rag_evaluation_bundle_verify",
        {"data": {"status": "pass", "semantic_status": "pass", "semantic_checks": {"evidence_sufficiency_schema": "pass"}}},
    )
    assert "retrieval_trace_schema" in " ".join(rag_eval_failures)
    assert "facet_gap_analysis" in " ".join(rag_eval_failures)
    assert "query_term_coverage" in " ".join(rag_eval_failures)
    assert "retrieval_trace_hash" in " ".join(rag_eval_failures)
    assert "evidence_sufficiency_hash" in " ".join(rag_eval_failures)
    assert "facet_gap_analysis_hash" in " ".join(rag_eval_failures)
    assert "query_term_coverage_hash" in " ".join(rag_eval_failures)
    assert "top_sources_hash" in " ".join(rag_eval_failures)
    data_release_failures = module.api_failures(
        "data_release_bundle_verify",
        {"data": {"status": "pass", "semantic_status": "pass", "semantic_checks": {}}},
    )
    assert "records_hash" in " ".join(data_release_failures)
    assert "records_csv_hash" in " ".join(data_release_failures)
    assert "record_source_summary_hash" in " ".join(data_release_failures)
    assert "release_handoff_hash" in " ".join(data_release_failures)
    assert "trna_caveat_count" in " ".join(data_release_failures)
    assert "trna_blocking_production_use" in " ".join(data_release_failures)
    assert "external_snapshot_reference_coverage" in " ".join(data_release_failures)
    assert "results_hash" in " ".join(
        module.api_failures(
            "optimizer_benchmark_bundle_verify",
            {"data": {"status": "pass", "semantic_status": "pass", "semantic_checks": {"search_strategy_schema": "pass"}}},
        )
    )
    optimizer_failures = module.api_failures(
        "optimizer_benchmark_bundle_verify",
        {
            "data": {
                "status": "pass",
                "semantic_status": "pass",
                "semantic_checks": {"search_strategy_schema": "pass", "results_hash": "pass"},
                "results_hash": valid_hash,
            }
        },
    )
    assert "optimizer_seed_strategy" in " ".join(optimizer_failures)
    assert "candidate_diagnostics" in " ".join(optimizer_failures)
    assert "recommendation_audit" in " ".join(optimizer_failures)
    assert "pareto_quality_schema" in " ".join(optimizer_failures)
    assert "pareto_quality_hash" in " ".join(optimizer_failures)
    qc_failures = module.api_failures(
        "qc_report_bundle_verify",
        {"data": {"status": "pass", "semantic_status": "pass", "semantic_checks": {"request_payload": "pass"}}},
    )
    assert "request_hash" in " ".join(qc_failures)
    assert "qc_report_hash" in " ".join(qc_failures)
    assert "report_formats_summary_hash" in " ".join(qc_failures)
    assert "candidate_ranking_hash" in " ".join(qc_failures)
    assert "recommendation_audit_hash" in " ".join(qc_failures)
    assert "recommendation_readiness_hash" in " ".join(qc_failures)
    assert "recommended_folding_evidence_hash" in " ".join(qc_failures)
    assert "optimizer_hash_report" in " ".join(qc_failures)
    assert "candidate_csv_explainability_columns" in " ".join(qc_failures)
    assert "recommended_constraint_risk_csv" in " ".join(qc_failures)
    assert "evidence_retrieval_quality_schema" in " ".join(qc_failures)
    assert "evidence_retrieval_quality_sources" in " ".join(qc_failures)
    assert "evidence_retrieval_quality_rank_hashes" in " ".join(qc_failures)
    assert "archive semantic summary is fail" in " ".join(
        module.api_failures("data_release_archive_semantics", {"data": {"status": "fail", "checked_count": 1, "latest_artifacts": []}})
    )
    qc_archive_failures = module.api_failures(
        "qc_bundle_archive_semantics",
        {"data": {"status": "pass", "checked_count": 1, "latest_artifacts": [{"request_payload_status": "pass"}]}},
    )
    assert "request_hash" in " ".join(qc_archive_failures)
    assert "report_formats_summary_hash" in " ".join(qc_archive_failures)
    assert "recommendation_readiness_hash" in " ".join(qc_archive_failures)
    assert "recommendation_readiness_status" in " ".join(qc_archive_failures)
    assert "recommendation_readiness_candidate_id" in " ".join(qc_archive_failures)
    assert "recommended_folding_evidence_hash" in " ".join(qc_archive_failures)
    assert "recommended_folding_status" in " ".join(qc_archive_failures)
    assert "recommended_folding_candidate_id" in " ".join(qc_archive_failures)
    assert "retrieval_quality_status" in " ".join(qc_archive_failures)
    assert "retrieval_quality_rank_evidence_count" in " ".join(qc_archive_failures)
    assert "retrieval_quality_rank_evidence_hash" in " ".join(qc_archive_failures)
    assert "objective_count" in " ".join(qc_archive_failures)
    archive_hash_failures = module.api_failures(
        "data_release_archive_semantics",
        {"data": {"status": "pass", "checked_count": 1, "latest_artifacts": [{"records_hash": valid_hash}]}},
    )
    assert "records_csv_hash" in " ".join(archive_hash_failures)
    assert "record_source_summary_hash" in " ".join(archive_hash_failures)
    assert "release_handoff_hash" in " ".join(archive_hash_failures)
    release_snapshot_failures = module.api_failures(
        "data_release_archive_semantics",
        {
            "data": {
                "status": "pass",
                "checked_count": 1,
                "latest_artifacts": [
                    {
                        "records_hash": valid_hash,
                        "records_csv_hash": valid_hash,
                        "record_source_summary_hash": valid_hash,
                        "dataset_count": 2,
                        "source_file_count": 2,
                        "release_handoff_hash": valid_hash,
                        "trna_caveat_count": 0,
                        "trna_blocking_production_use": False,
                        "external_snapshot_referenced_count": 2,
                        "external_snapshot_contained_count": 1,
                        "external_snapshot_missing_count": 1,
                    }
                ],
            }
        },
    )
    assert "missing external source snapshots" in " ".join(release_snapshot_failures)
    snapshot_archive_failures = module.api_failures(
        "data_snapshot_archive_semantics",
        {"data": {"status": "pass", "checked_count": 1, "latest_artifacts": [{"snapshot_manifest_hash": valid_hash}]}},
    )
    assert "structured_manifest_hash" in " ".join(snapshot_archive_failures)
    assert "rag_index_hash" in " ".join(snapshot_archive_failures)
    assert "external_snapshot_file_count" in " ".join(snapshot_archive_failures)
    assert module.api_failures(
        "data_refresh_plan_archive_semantics",
        {
            "data": {
                "status": "pass",
                "checked_count": 1,
                "latest_artifacts": [
                    {
                        "operation_count": 2,
                        "request_hash": valid_hash,
                        "operations_hash": valid_hash,
                        "data_catalog_hash": valid_hash,
                        "external_sources_hash": valid_hash,
                        "structured_quality_hash": valid_hash,
                        "data_provenance_hash": valid_hash,
                        "rag_status_hash": valid_hash,
                        "validation_status": "pass",
                        "dataset_id": "gtex_v8",
                        "structured_manifest_hash": valid_hash,
                    }
                ],
            }
        },
    ) == []
    assert module.api_failures(
        "workflow_trace_archive_semantics",
        {
            "data": {
                "status": "pass",
                "checked_count": 1,
                "latest_artifacts": [
                    {
                        "trace_step_count": 3,
                        "trace_hash": valid_hash,
                        "task_type": "cds_optimize",
                        "structured_manifest_hash": valid_hash,
                    }
                ],
            }
        },
    ) == []
    refresh_plan_failures = module.api_failures(
        "data_refresh_plan_archive_semantics",
        {"data": {"status": "pass", "checked_count": 1, "latest_artifacts": [{"validation_status": "pass"}]}},
    )
    assert "operation_count" in " ".join(refresh_plan_failures)
    assert "request_hash" in " ".join(refresh_plan_failures)
    assert "operations_hash" in " ".join(refresh_plan_failures)
    assert "data_catalog_hash" in " ".join(refresh_plan_failures)
    assert "external_sources_hash" in " ".join(refresh_plan_failures)
    assert "structured_quality_hash" in " ".join(refresh_plan_failures)
    assert "data_provenance_hash" in " ".join(refresh_plan_failures)
    assert "rag_status_hash" in " ".join(refresh_plan_failures)
    assert "dataset_id" in " ".join(refresh_plan_failures)
    assert "structured_manifest_hash" in " ".join(refresh_plan_failures)
    workflow_trace_failures = module.api_failures(
        "workflow_trace_archive_semantics",
        {"data": {"status": "pass", "checked_count": 1, "latest_artifacts": [{"task_type": "cds_optimize"}]}},
    )
    assert "trace_step_count" in " ".join(workflow_trace_failures)
    assert "trace_hash" in " ".join(workflow_trace_failures)
    assert "structured_manifest_hash" in " ".join(workflow_trace_failures)
    assert module.api_failures(
        "rag_vector_index_archive_semantics",
        {
            "data": {
                "status": "pass",
                "checked_count": 1,
                "latest_artifacts": [
                    {
                        "chunk_count": 8,
                        "embedding_dimensions": 64,
                        "embedding_model": "hash-bow-v1",
                        "retrieval_model": "hybrid-hash-bm25-facet-rerank-v3",
                        "recommended_backend": "local_json",
                        "migration_target_backend": "local_json",
                        "parity_status": "pass",
                        "vector_row_hash": valid_hash,
                        "structured_manifest_hash": valid_hash,
                    }
                ],
            }
        },
    ) == []
    assert module.api_failures(
        "rag_regression_archive_semantics",
        {
            "data": {
                "status": "pass",
                "checked_count": 1,
                "latest_artifacts": [
                    {
                        "case_count": 5,
                        "cases_hash": valid_hash,
                        "results_hash": valid_hash,
                        "quality_summary_hash": valid_hash,
                        "case_metrics_hash": valid_hash,
                        "source_provenance_summary_hash": valid_hash,
                        "source_provenance_case_count": 5,
                        "source_provenance_source_count": 4,
                        "source_snapshot_case_count": 1,
                        "quality_status": "pass",
                        "top_source_count": 4,
                        "missing_term_case_count": 0,
                        "structured_manifest_hash": valid_hash,
                    }
                ],
            }
        },
    ) == []
    rag_regression_archive_failures = module.api_failures(
        "rag_regression_archive_semantics",
        {"data": {"status": "pass", "checked_count": 1, "latest_artifacts": [{"case_count": 5}]}},
    )
    assert "cases_hash" in " ".join(rag_regression_archive_failures)
    assert "results_hash" in " ".join(rag_regression_archive_failures)
    assert "quality_summary_hash" in " ".join(rag_regression_archive_failures)
    assert "case_metrics_hash" in " ".join(rag_regression_archive_failures)
    assert "source_provenance_summary_hash" in " ".join(rag_regression_archive_failures)
    assert "top_source_count" in " ".join(rag_regression_archive_failures)
    vector_index_failures = module.api_failures(
        "rag_vector_index_archive_semantics",
        {"data": {"status": "pass", "checked_count": 1, "latest_artifacts": [{"chunk_count": 8}]}},
    )
    assert "embedding_dimensions" in " ".join(vector_index_failures)
    assert "embedding_model" in " ".join(vector_index_failures)
    assert "retrieval_model" in " ".join(vector_index_failures)
    assert "recommended_backend" in " ".join(vector_index_failures)
    assert "migration_target_backend" in " ".join(vector_index_failures)
    assert "parity_status" in " ".join(vector_index_failures)
    assert "vector_row_hash" in " ".join(vector_index_failures)
    assert "structured_manifest_hash" in " ".join(vector_index_failures)
    assert module.api_failures(
        "optimizer_benchmark_archive_semantics",
        {
            "data": {
                "status": "pass",
                "checked_count": 1,
                "latest_artifacts": [
                    {
                        "benchmark_status": "pass",
                        "diagnostics_status": "pass",
                        "stress_status": "warning",
                        "case_count": 3,
                        "cases_hash": valid_hash,
                        "results_hash": valid_hash,
                        "benchmark_hash": valid_hash,
                        "diagnostics_hash": valid_hash,
                        "case_metrics_hash": valid_hash,
                        "candidate_diagnostics_hash": valid_hash,
                        "recommendation_summary_hash": valid_hash,
                        "recommendation_summary_status": "pass",
                        "recommended_on_pareto_front_count": 3,
                        "recommendation_max_regret": 0.0,
                        "case_provenance_hash": valid_hash,
                        "case_fingerprint_count": 3,
                        "recommended_folding_evidence_hash": valid_hash,
                        "recommended_folding_evidence_count": 3,
                        "recommended_folding_candidate_match_count": 3,
                    }
                ],
            }
        },
    ) == []
    optimizer_archive_failures = module.api_failures(
        "optimizer_benchmark_archive_semantics",
        {"data": {"status": "pass", "checked_count": 1, "latest_artifacts": [{"benchmark_status": "pass"}]}},
    )
    assert "diagnostics_status" in " ".join(optimizer_archive_failures)
    assert "stress_status" in " ".join(optimizer_archive_failures)
    assert "case_count" in " ".join(optimizer_archive_failures)
    assert "cases_hash" in " ".join(optimizer_archive_failures)
    assert "results_hash" in " ".join(optimizer_archive_failures)
    assert "benchmark_hash" in " ".join(optimizer_archive_failures)
    assert "diagnostics_hash" in " ".join(optimizer_archive_failures)
    assert "case_metrics_hash" in " ".join(optimizer_archive_failures)
    assert "candidate_diagnostics_hash" in " ".join(optimizer_archive_failures)
    assert "recommendation_summary_hash" in " ".join(optimizer_archive_failures)
    assert "recommendation_summary_status" in " ".join(optimizer_archive_failures)
    assert "recommended_on_pareto_front_count" in " ".join(optimizer_archive_failures)
    assert "recommendation_max_regret" in " ".join(optimizer_archive_failures)
    assert "case_provenance_hash" in " ".join(optimizer_archive_failures)
    assert "case_fingerprint_count" in " ".join(optimizer_archive_failures)
    assert "recommended_folding_evidence_hash" in " ".join(optimizer_archive_failures)
    assert "recommended_folding_evidence_count" in " ".join(optimizer_archive_failures)
    assert "recommended_folding_candidate_match_count" in " ".join(optimizer_archive_failures)


def test_audit_log_records_filters_and_summarizes_events() -> None:
    marker = f"test_audit_{random.randint(1, 10_000_000)}"
    event = record_audit_event(
        marker,
        "unit_test",
        actor="test",
        request_id="req_test",
        resource_type="run",
        resource_id="run_test",
        detail={"ok": True},
    )
    events = list_audit_events(event_type=marker, resource_type="run", resource_id="run_test", limit=5)["events"]
    summary = audit_summary()
    assert event["event_id"] in {item["event_id"] for item in events}
    assert events[0]["detail"]["ok"] is True
    assert summary["total_events"] >= 1
    assert summary["by_event_type"][marker] >= 1


def test_artifact_manifest_can_be_hmac_signed_and_verified() -> None:
    previous_key = os.environ.get("ARTIFACT_SIGNING_KEY")
    previous_key_id = os.environ.get("ARTIFACT_SIGNING_KEY_ID")
    os.environ["ARTIFACT_SIGNING_KEY"] = "test-signing-secret"
    os.environ["ARTIFACT_SIGNING_KEY_ID"] = "test-key"
    try:
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            bundle = ManifestedZip(archive, "test_bundle", {"run_id": "run_test"})
            bundle.writestr("payload.json", '{"ok": true}')
            bundle.write_artifact_manifest()
        verified = verify_artifact_bundle(buffer.getvalue())
        assert verified["status"] == "pass"
        assert verified["signature"]["status"] == "verified"
        assert verified["signature"]["signatures"][0]["algorithm"] == "HMAC-SHA256"

        os.environ["ARTIFACT_SIGNING_KEY"] = "wrong-secret"
        failed = verify_artifact_bundle(buffer.getvalue())
        assert failed["status"] == "fail"
        assert any("signature" in error for error in failed["errors"])
    finally:
        if previous_key is None:
            os.environ.pop("ARTIFACT_SIGNING_KEY", None)
        else:
            os.environ["ARTIFACT_SIGNING_KEY"] = previous_key
        if previous_key_id is None:
            os.environ.pop("ARTIFACT_SIGNING_KEY_ID", None)
        else:
            os.environ["ARTIFACT_SIGNING_KEY_ID"] = previous_key_id


def test_artifact_manifest_can_be_ed25519_signed_and_verified() -> None:
    if importlib.util.find_spec("cryptography") is None:
        return
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    previous_values = {
        name: os.environ.get(name)
        for name in (
            "ARTIFACT_SIGNING_KEY",
            "ARTIFACT_ED25519_PRIVATE_KEY",
            "ARTIFACT_ED25519_PUBLIC_KEY",
            "ARTIFACT_ED25519_KEY_ID",
        )
    }
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    os.environ.pop("ARTIFACT_SIGNING_KEY", None)
    os.environ["ARTIFACT_ED25519_PRIVATE_KEY"] = base64.b64encode(
        private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
    ).decode("ascii")
    os.environ["ARTIFACT_ED25519_PUBLIC_KEY"] = base64.b64encode(
        public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    os.environ["ARTIFACT_ED25519_KEY_ID"] = "test-ed25519"
    try:
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            bundle = ManifestedZip(archive, "test_ed25519_bundle", {"run_id": "run_ed25519_test"})
            bundle.writestr("payload.json", '{"ok": true}')
            bundle.write_artifact_manifest()
        verified = verify_artifact_bundle(buffer.getvalue())
        assert verified["status"] == "pass"
        assert verified["signature"]["status"] == "verified"
        assert verified["signature"]["signatures"][0]["algorithm"] == "Ed25519"
    finally:
        for name, value in previous_values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_artifact_archive_stores_dedupes_and_verifies_bundle() -> None:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        bundle = ManifestedZip(archive, "test_archive_bundle", {"resource_id": "run_archive_test"})
        bundle.writestr("payload.json", '{"ok": true}')
        bundle.write_artifact_manifest()

    first = archive_artifact_bundle(
        buffer.getvalue(),
        action="unit_test_archive",
        resource_type="run",
        resource_id="run_archive_test",
        filename="run_archive_test.zip",
        metadata={"test": True},
    )
    second = archive_artifact_bundle(
        buffer.getvalue(),
        action="unit_test_archive",
        resource_type="run",
        resource_id="run_archive_test",
        filename="run_archive_test.zip",
    )
    listed = list_archived_artifacts(resource_type="run", resource_id="run_archive_test", limit=5)["artifacts"]
    verified = verify_archived_artifact(first["artifact_id"])
    ledger = artifact_ledger(limit=20)
    ledger_verification = verify_artifact_ledger()
    summary = archive_summary()
    assert first["archive_status"] in {"stored", "existing"}
    assert second["archive_status"] == "existing"
    assert first["artifact_id"] == second["artifact_id"]
    assert verified["status"] == "pass"
    assert any(item["artifact_id"] == first["artifact_id"] for item in listed)
    assert any(item["artifact_id"] == first["artifact_id"] for item in ledger["entries"])
    assert ledger_verification["status"] in {"pass", "warning"}
    assert ledger_verification["entry_count"] >= 1
    assert summary["ledger"]["status"] in {"pass", "warning"}
    assert summary["total_artifacts"] >= 1


def test_artifact_retention_dry_run_and_apply_preserve_ledger() -> None:
    marker = f"retention_{uuid.uuid4().hex[:10]}"
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        bundle = ManifestedZip(archive, "test_retention_bundle", {"resource_id": marker})
        bundle.writestr("payload.json", json.dumps({"marker": marker}))
        bundle.write_artifact_manifest()

    artifact = archive_artifact_bundle(
        buffer.getvalue(),
        action="unit_test_retention",
        resource_type="retention_test",
        resource_id=marker,
        filename=f"{marker}.zip",
        metadata={"test": True},
    )
    old_created_at = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    with sqlite3.connect(ARCHIVE_DB_PATH) as conn:
        conn.execute(
            "update archived_artifacts set created_at = ? where artifact_id = ?",
            (old_created_at, artifact["artifact_id"]),
        )

    plan = plan_artifact_retention(retention_days=7, keep_min=0, resource_type="retention_test")
    dry_run = apply_artifact_retention(retention_days=7, keep_min=0, resource_type="retention_test", dry_run=True)
    applied = apply_artifact_retention(retention_days=7, keep_min=0, resource_type="retention_test", dry_run=False)
    listed = list_archived_artifacts(resource_type="retention_test", resource_id=marker, limit=5)["artifacts"]
    ledger = verify_artifact_ledger()

    assert any(item["artifact_id"] == artifact["artifact_id"] for item in plan["candidates"])
    assert dry_run["deleted_count"] == 0
    assert applied["deleted_count"] == 1
    assert not listed
    assert ledger["status"] in {"pass", "warning"}


def test_score_sequence_returns_basic_metrics() -> None:
    scores = score_sequence("ATGGCTGCTTAA")
    assert scores.length_nt == 12
    assert 0 <= scores.cai <= 1
    assert scores.cpg_count >= 0
    assert scores.gc_window_max_deviation >= 0
    assert scores.five_prime_gc_deviation >= 0
    assert 0 <= scores.hairpin_proxy_score <= 1
    assert 0 <= scores.sequence_complexity <= 1
    assert scores.codon_pair_risk >= 0
    assert scores.restriction_site_count == 0
    assert scores.tissue_codon_adaptation == 0.0


def test_score_sequence_flags_structure_and_complexity_proxies() -> None:
    scores = score_sequence("ATGCGACGACGACGACGACGATAA")
    assert scores.codon_pair_risk >= 1
    assert scores.hairpin_proxy_score >= 0
    assert scores.low_complexity_penalty > 0


def test_score_sequence_flags_manufacturing_proxy_motifs() -> None:
    scores = score_sequence("ATGGAATTCCAGGGCTAA")
    assert scores.restriction_site_count >= 1
    assert scores.cryptic_splice_motif_count >= 1
    assert scores.sequence_policy_violation_score > 0


def test_sequence_policy_audit_reports_motif_positions() -> None:
    audit = audit_sequence_policy("ATGAATAAAGAATTCGTAAGTTAA")
    assert audit["status"] == "fail"
    assert audit["summary"]["polyadenylation_signal"] == 1
    assert audit["summary"]["restriction_site"] == 1
    assert any(finding["category"] == "polyadenylation_signal" for finding in audit["findings"])
    assert any(finding["positions_1based"] for finding in audit["findings"])


def test_validate_cds_reports_orf_gate_status() -> None:
    valid = validate_cds("ATGGCTGCTTAA")
    invalid = validate_cds("GCTTAA")
    assert valid["status"] in {"pass", "warning"}
    assert valid["protein_length_aa"] == 3
    assert valid["sequence_policy"]["status"] in {"pass", "warning", "fail"}
    assert invalid["status"] == "warning"
    assert any(check["id"] == "start_codon" and check["result"] == "fail" for check in invalid["checks"])


def test_score_sequence_uses_trna_availability_weights() -> None:
    scores = score_sequence(
        "ATGGCCGCCTAA",
        ScoreConfig(codon_availability_weights=(("GCC", 1.2),)),
    )
    assert scores.tissue_codon_adaptation > 1.0
    assert scores.rare_codon_clusters == 0


def test_optimizer_preserves_protein_sequence() -> None:
    native = "ATGGCTGCTGCTGCTTAA"
    config = OptimizationConfig(population_size=16, generations=4, max_candidates=4, seed=7)
    candidates = optimize_cds(native, config)
    assert candidates
    assert all(candidate.protein == translate(native) for candidate in candidates)
    assert all(translate(candidate.cds) == translate(native) for candidate in candidates)


def test_optimizer_seed_population_surfaces_tradeoff_extremes() -> None:
    native = "ATGGCTGCTGCTGCTTAA"
    config = OptimizationConfig(
        population_size=16,
        generations=2,
        max_candidates=6,
        seed=7,
        score_config=ScoreConfig(codon_availability_weights=(("GCC", 1.25), ("GCT", 0.80))),
    )
    candidates = optimize_cds(native, config)
    gc_values = [candidate.scores.gc_fraction for candidate in candidates]
    assert len({candidate.cds for candidate in candidates}) >= 4
    assert max(gc_values) - min(gc_values) >= 0.10
    assert any(candidate.scores.tissue_codon_adaptation > 1.0 for candidate in candidates)
    assert all(translate(candidate.cds) == translate(native) for candidate in candidates)


def test_optimizer_benchmark_suite_tracks_quality_and_constraints() -> None:
    cases = optimizer_benchmark_cases()
    result = evaluate_optimizer_benchmark()
    assert cases["case_count"] >= 3
    assert result["status"] in {"pass", "warning"}
    assert result["fail_count"] == 0
    assert len(result["results_hash"]) == 64
    assert result["macro"]["candidate_count"] >= 3
    assert result["macro"]["unique_cds_count"] >= 2
    assert result["macro"]["approx_hypervolume_2d"] > 0
    assert all(item["metrics"]["protein_preservation_failures"] == 0 for item in result["results"])
    assert all(item["case_provenance"]["provenance_schema"] == "agentic-rag-optimizer-benchmark-case-provenance-v1" for item in result["results"])
    assert all(len(item["case_provenance"]["case_fingerprint"]) == 64 for item in result["results"])
    assert all(len(item["case_provenance"]["target_hash"]) == 64 for item in result["results"])


def test_optimizer_diagnostics_reports_operational_quality_bands() -> None:
    diagnostics = optimizer_diagnostics()
    assert diagnostics["diagnostics_schema"] == "agentic-rag-optimizer-diagnostics-v1"
    assert diagnostics["status"] in {"pass", "warning"}
    assert diagnostics["benchmark"]["fail_count"] == 0
    assert diagnostics["quality_bands"]["candidate_diversity"] in {"pass", "warning", "fail"}
    assert diagnostics["quality_bands"]["constraint_control"] in {"pass", "warning", "fail"}
    assert "maximize_cai" in diagnostics["optimizer"]["objectives"]
    assert diagnostics["optimizer"]["algorithm"] == "seeded_nsga2"
    assert diagnostics["optimizer"]["seed_strategy"] == "deterministic-tradeoff-seeds-v1"
    assert diagnostics["optimizer"]["search_strategy"]["strategy_schema"] == "agentic-rag-optimizer-search-strategy-v1"
    assert "min_cpg" in diagnostics["optimizer"]["search_strategy"]["deterministic_seed_variants"]
    assert diagnostics["recommendations"]


def test_optimizer_benchmark_bundle_includes_search_strategy_evidence() -> None:
    bundle = build_optimizer_benchmark_bundle()
    verification = verify_optimizer_benchmark_bundle(bundle)
    assert verification["status"] in {"pass", "warning"}
    assert verification["semantic_checks"]["search_strategy_schema"] == "pass"
    assert verification["semantic_checks"]["optimizer_algorithm"] == "pass"
    assert verification["semantic_checks"]["optimizer_seed_strategy"] == "pass"
    assert verification["semantic_checks"]["results_hash"] == "pass"
    assert verification["semantic_checks"]["benchmark_hash"] == "pass"
    assert verification["semantic_checks"]["diagnostics_hash"] == "pass"
    assert verification["semantic_checks"]["case_metrics_hash"] == "pass"
    assert verification["semantic_checks"]["candidate_diagnostics_hash"] == "pass"
    assert verification["semantic_checks"]["recommendation_summary_hash"] == "pass"
    assert verification["semantic_checks"]["recommendation_summary_schema"] == "pass"
    assert verification["semantic_checks"]["recommendation_summary_consistency"] == "pass"
    assert verification["semantic_checks"]["recommendation_summary_case_count"] == "pass"
    assert verification["semantic_checks"]["pareto_quality_schema"] == "pass"
    assert verification["semantic_checks"]["pareto_quality_hash"] == "pass"
    assert verification["semantic_checks"]["case_provenance_schema"] == "pass"
    assert verification["semantic_checks"]["case_provenance_fingerprints"] == "pass"
    assert verification["semantic_checks"]["recommended_folding_evidence_schema"] == "pass"
    assert verification["semantic_checks"]["recommended_folding_evidence_hashes"] == "pass"
    assert verification["semantic_checks"]["recommended_folding_evidence_payload_hash"] == "pass"
    assert verification["semantic_checks"]["recommended_folding_evidence_hash"] == "pass"
    assert verification["semantic_checks"]["case_metric_columns"] == "pass"
    assert verification["semantic_checks"]["stress_schema"] == "pass"
    assert verification["stress_status"] in {"pass", "warning"}
    assert len(verification["results_hash"]) == 64
    assert len(verification["benchmark_hash"]) == 64
    assert len(verification["diagnostics_hash"]) == 64
    assert len(verification["case_metrics_hash"]) == 64
    assert len(verification["candidate_diagnostics_hash"]) == 64
    assert len(verification["recommendation_summary_hash"]) == 64
    assert verification["recommendation_summary_status"] in {"pass", "warning"}
    assert verification["recommended_on_pareto_front_count"] >= 1
    assert verification["recommendation_max_regret"] >= 0
    assert len(verification["case_provenance_hash"]) == 64
    assert verification["case_fingerprint_count"] == verification["case_count"]
    assert len(verification["recommended_folding_evidence_hash"]) == 64
    assert verification["recommended_folding_evidence_count"] == verification["case_count"]
    assert verification["recommended_folding_candidate_match_count"] == verification["case_count"]
    with ZipFile(BytesIO(bundle)) as archive:
        names = set(archive.namelist())
        assert "search_strategy.json" in names
        assert "stress_gate.json" in names
        search_strategy = json.loads(archive.read("search_strategy.json"))
        stress = json.loads(archive.read("stress_gate.json"))
        manifest = json.loads(archive.read("bundle_manifest.json"))
        benchmark = json.loads(archive.read("benchmark.json"))
        candidate_diagnostics = json.loads(archive.read("candidate_diagnostics.json"))
        recommendation_summary = json.loads(archive.read("recommendation_summary.json"))
        case_metrics = archive.read("case_metrics.csv").decode("utf-8")
        assert search_strategy["strategy_schema"] == "agentic-rag-optimizer-search-strategy-v1"
        assert search_strategy["algorithm"] == manifest["optimizer_algorithm"]
        assert search_strategy["seed_strategy"] == manifest["optimizer_seed_strategy"]
        assert stress["status"] == manifest["stress_status"] == verification["stress_status"]
        assert benchmark["results_hash"] == manifest["results_hash"] == verification["results_hash"]
        assert manifest["benchmark_hash"] == verification["benchmark_hash"]
        assert manifest["diagnostics_hash"] == verification["diagnostics_hash"]
        assert manifest["case_metrics_hash"] == verification["case_metrics_hash"]
        assert manifest["candidate_diagnostics_hash"] == verification["candidate_diagnostics_hash"]
        assert manifest["recommendation_summary_hash"] == verification["recommendation_summary_hash"]
        assert manifest["recommendation_summary_status"] == verification["recommendation_summary_status"]
        assert manifest["recommended_on_pareto_front_count"] == verification["recommended_on_pareto_front_count"]
        assert manifest["recommended_folding_evidence_hash"] == verification["recommended_folding_evidence_hash"]
        assert manifest["recommended_folding_evidence_count"] == verification["recommended_folding_evidence_count"]
        assert recommendation_summary["summary_schema"] == "agentic-rag-optimizer-benchmark-recommendation-summary-v1"
        assert recommendation_summary["case_count"] == verification["case_count"]
        assert recommendation_summary["recommended_folding_evidence_count"] == verification["recommended_folding_evidence_count"]
        assert recommendation_summary["recommended_on_pareto_front_count"] == verification["recommended_on_pareto_front_count"]
        assert len(recommendation_summary["cases_hash"]) == 64
        assert recommendation_summary["cases"][0]["recommended_folding_candidate_id"] == recommendation_summary["cases"][0]["recommended_candidate_id"]
        first_case = candidate_diagnostics["cases"][0]
        assert first_case["pareto_quality"]["quality_schema"] == "agentic-rag-pareto-quality-v1"
        assert first_case["case_provenance"]["provenance_schema"] == "agentic-rag-optimizer-benchmark-case-provenance-v1"
        assert len(first_case["case_provenance"]["case_fingerprint"]) == 64
        assert first_case["recommended_folding_evidence"]["folding_schema"] == "agentic-rag-rna-folding-v1"
        assert first_case["recommended_folding_evidence"]["candidate_id"] == first_case["recommended_candidate_id"]
        assert len(first_case["recommended_folding_evidence"]["folding_evidence_hash"]) == 64
        assert first_case["recommendation_audit"]["pareto_quality_hash"] == first_case["pareto_quality"]["quality_hash"]
        assert "case_fingerprint" in case_metrics
        assert "target_hash" in case_metrics
        assert "cds_sha256" in case_metrics
        assert "protein_sha256" in case_metrics
        assert "recommendation_max_regret" in case_metrics
        assert "recommendation_tradeoff_count" in case_metrics
        assert "pareto_quality_hash" in case_metrics
        assert "recommended_on_pareto_front" in case_metrics
        assert "recommended_folding_evidence_hash" in case_metrics
        assert "recommended_folding_candidate_id" in case_metrics
        assert "recommended_folding_status" in case_metrics
    assert verification["semantic_checks"]["recommended_folding_candidate_id"] == "pass"
    assert verification["semantic_checks"]["case_metric_folding_candidate_id"] == "pass"
    archived = archive_artifact_bundle(
        bundle,
        action="unit_test_optimizer_benchmark_bundle",
        resource_type="optimizer_benchmark",
        resource_id=str(verification.get("cases_hash") or "optimizer_benchmark"),
        filename="unit_test_optimizer_benchmark_bundle.zip",
        metadata={"verification_status": verification["status"]},
    )
    semantic = archived["metadata"]["optimizer_benchmark_semantic_verification"]
    assert semantic["stress_status"] == verification["stress_status"]
    assert semantic["case_metrics_hash"] == verification["case_metrics_hash"]
    assert semantic["recommendation_summary_hash"] == verification["recommendation_summary_hash"]
    assert semantic["recommendation_summary_status"] == verification["recommendation_summary_status"]
    assert semantic["recommended_on_pareto_front_count"] == verification["recommended_on_pareto_front_count"]
    assert semantic["recommendation_max_regret"] == verification["recommendation_max_regret"]
    assert semantic["case_provenance_hash"] == verification["case_provenance_hash"]
    assert semantic["case_fingerprint_count"] == verification["case_fingerprint_count"]
    assert semantic["recommended_folding_evidence_hash"] == verification["recommended_folding_evidence_hash"]
    assert semantic["recommended_folding_evidence_count"] == verification["recommended_folding_evidence_count"]
    assert semantic["recommended_folding_candidate_match_count"] == verification["recommended_folding_candidate_match_count"]
    summary = optimizer_benchmark_archive_summary(limit=5, verify_files=False)
    assert any(
        item["artifact_id"] == archived["artifact_id"]
        and item["stress_status"] == verification["stress_status"]
        and item["case_count"] == verification["case_count"]
        and item["case_metrics_hash"] == verification["case_metrics_hash"]
        and item["recommendation_summary_hash"] == verification["recommendation_summary_hash"]
        and item["recommendation_summary_status"] == verification["recommendation_summary_status"]
        and item["recommended_on_pareto_front_count"] == verification["recommended_on_pareto_front_count"]
        and item["case_provenance_hash"] == verification["case_provenance_hash"]
        and item["case_fingerprint_count"] == verification["case_fingerprint_count"]
        and item["recommended_folding_evidence_hash"] == verification["recommended_folding_evidence_hash"]
        and item["recommended_folding_evidence_count"] == verification["recommended_folding_evidence_count"]
        and item["recommended_folding_candidate_match_count"] == verification["recommended_folding_candidate_match_count"]
        for item in summary["latest_artifacts"]
    )


def test_repair_removes_synonymous_forbidden_motif() -> None:
    native = "ATGAAAAAATAA"
    protein = translate(native)
    config = ScoreConfig(forbidden_motifs=("AAAAAA",))
    repaired = repair_cds(native, protein, config, random.Random(3), "TAA", max_passes=3)
    assert translate(repaired) == protein
    assert motif_violations(repaired, config.forbidden_motifs) == 0


def test_design_service_returns_report_shape() -> None:
    design = optimize_design(
        "ATGGCTGCTGCTGCTTAA",
        OptimizationConfig(population_size=16, generations=4, max_candidates=3, seed=11),
        {"gene": "DEMO", "modality": "AAV"},
    )
    assert design["run_id"].startswith("run_")
    assert design["native"]["protein"] == "MAAAA"
    assert len(design["candidates"]) <= 3
    assert design["recommended_candidate"] is not None
    assert design["recommended_candidate"]["selection_trace"]
    assert design["recommended_candidate"]["constraint_risk"]["status"] in {"pass", "warning", "fail"}
    assert design["candidate_folding_audit"]["audit_schema"] == "agentic-rag-candidate-folding-audit-v1"
    assert design["candidate_folding_audit"]["candidate_count"] == len(design["candidates"])
    assert design["candidate_folding_audit"]["evaluated_count"] == len(design["candidates"])
    assert design["candidate_folding_audit"]["selection_signal"] in {"secondary_structure_proxy_score", "thermodynamic_risk_score"}
    assert len(design["candidate_folding_audit"]["audit_hash"]) == 64
    assert design["candidate_diagnostics"]["candidate_count"] == len(design["candidates"])
    assert "constraint_risk_summary" in design["candidate_diagnostics"]
    assert design["candidate_diagnostics"]["pareto_front"]["size"] >= 1
    assert design["candidate_diagnostics"]["pareto_quality"]["quality_schema"] == "agentic-rag-pareto-quality-v1"
    assert len(design["candidate_diagnostics"]["pareto_quality"]["quality_hash"]) == 64
    assert design["candidate_diagnostics"]["recommendation_audit"]["pareto_quality_hash"] == design["candidate_diagnostics"]["pareto_quality"]["quality_hash"]
    assert "mean_pairwise_codon_distance" in design["candidate_diagnostics"]["diversity"]
    sequence_policy = design["candidate_diagnostics"]["sequence_policy_audit"]
    assert sequence_policy["audit_schema"] == "agentic-rag-candidate-sequence-policy-audit-v1"
    assert len(sequence_policy["audit_hash"]) == 64
    assert sequence_policy["candidate_count"] == len(design["candidates"])
    assert sequence_policy["candidate_summaries"][0]["candidate_id"]
    assert design["recommendation_audit"]["sequence_policy_audit_hash"] == sequence_policy["audit_hash"]
    assert design["validation"]["native"]["status"] in {"pass", "warning"}
    assert design["qc_gate"]["status"] in {"pass", "warning", "fail"}
    report = generate_qc_report(design)
    assert report["candidate_folding_audit"]["audit_hash"] == design["candidate_folding_audit"]["audit_hash"]
    assert report["recommendation_readiness"]["readiness_schema"] == "agentic-rag-recommendation-readiness-v1"
    assert len(report["recommendation_readiness"]["readiness_hash"]) == 64
    assert report["recommendation_readiness"]["recommended_candidate_id"] == design["recommended_candidate"]["candidate_id"]
    assert "provenance" in design


def test_design_recommendation_uses_validated_rnafold_risk_when_available() -> None:
    candidates = [
        {
            "candidate_id": "cand_high_composite",
            "rank": 1,
            "scores": {
                "composite_quality": 0.90,
                "aav_budget_pass": True,
                "motif_violations": 0,
                "polyadenylation_signal_count": 0,
                "restriction_site_count": 0,
                "splice_donor_motif_count": 0,
                "splice_acceptor_motif_count": 0,
                "hairpin_proxy_score": 0.10,
                "secondary_structure_proxy_score": 0.10,
                "low_complexity_penalty": 0.10,
            },
        },
        {
            "candidate_id": "cand_low_thermo_risk",
            "rank": 2,
            "scores": {
                "composite_quality": 0.86,
                "aav_budget_pass": True,
                "motif_violations": 0,
                "polyadenylation_signal_count": 0,
                "restriction_site_count": 0,
                "splice_donor_motif_count": 0,
                "splice_acceptor_motif_count": 0,
                "hairpin_proxy_score": 0.10,
                "secondary_structure_proxy_score": 0.10,
                "low_complexity_penalty": 0.10,
            },
        },
    ]
    folding_audit = {
        "candidate_evaluations": [
            {"candidate_id": "cand_high_composite", "validated_backend": True, "thermodynamic_risk_score": 0.90},
            {"candidate_id": "cand_low_thermo_risk", "validated_backend": True, "thermodynamic_risk_score": 0.05},
        ]
    }
    recommended = design_service._recommend_candidate(candidates, folding_audit)
    assert recommended["candidate_id"] == "cand_low_thermo_risk"


def test_design_service_applies_structured_custom_prior() -> None:
    design = optimize_design(
        "ATGGCTGCTGCTGCTTAA",
        OptimizationConfig(population_size=16, generations=2, max_candidates=2, seed=11),
        {"gene": "DEMO", "species": "human", "brain_region": "substantia nigra", "cell_type": "dopaminergic neuron"},
    )
    assert "CUSTOM_structured_seed" in design["provenance"]["codon_weight_source"]
    assert "tRNA_availability_seed" in design["provenance"]["codon_weight_source"]
    assert design["optimization_config"]["score_config"]["codon_weight_multipliers"]
    assert design["optimization_config"]["score_config"]["codon_availability_weights"]


def test_resolve_gene_summarizes_canonical_transcript() -> None:
    resolved = resolve_gene("demo", client=FakeEnsemblClient())
    assert resolved["gene"]["canonical_transcript"] == "ENST_CANON.2"
    assert resolved["transcripts"][0]["id"] == "ENST_CANON"
    assert resolved["transcripts"][0]["is_protein_coding"] is True
    assert resolved["transcripts"][0]["mane_select"]["refseq_match"] == "NM_DEMO.1"


def test_fetch_canonical_cds_uses_selected_transcript() -> None:
    fetched = fetch_canonical_cds("demo", client=FakeEnsemblClient())
    assert fetched["selected_transcript"]["id"] == "ENST_CANON"
    assert fetched["selected_transcript"]["selection_reason"] == "mane_select"
    assert fetched["protein"] == "MAA"


def test_workflow_plans_and_runs_gene_design() -> None:
    payload = {
        "gene": "DEMO",
        "species": "human",
        "target": {"brain_region": "striatum", "cell_type": "medium spiny neuron", "modality": "AAV"},
    }
    plan = plan_gene_design_task(payload)
    design = run_gene_design_workflow(
        payload,
        OptimizationConfig(population_size=16, generations=2, max_candidates=2, seed=5),
        client=FakeEnsemblClient(),
    )
    assert "hybrid_rag_retriever" in plan["required_tools"]
    assert design["workflow"]["plan"]
    assert any(step["name"] == "qc_writer_completed" for step in design["trace"])
    assert design["qc_report"]["project_metadata"]["run_id"] == design["run_id"]


def test_workflow_runs_cds_design() -> None:
    design = run_cds_design_workflow(
        "ATGGCTGCTTAA",
        {"gene": "DEMO", "species": "human", "modality": "AAV"},
        OptimizationConfig(population_size=16, generations=2, max_candidates=2, seed=5),
    )
    assert design["workflow"]["task"]["task_type"] == "cds_optimize"
    assert design["qc_report"]
    assert any(step["name"] == "synonymous_optimizer" for step in design["trace"])


def test_workflow_trace_bundle_archive_semantics() -> None:
    design = run_cds_design_workflow(
        "ATGGCTGCTTAA",
        {"gene": "DEMO", "species": "human", "modality": "AAV"},
        OptimizationConfig(population_size=16, generations=2, max_candidates=2, seed=7),
    )
    run = {
        "run_id": design["run_id"],
        "run_type": "cds_optimize",
        "design": design,
        "trace": design["trace"],
    }
    bundle = build_workflow_trace_bundle(run)
    verification = verify_workflow_trace_bundle(bundle)
    assert verification["status"] == "pass"
    assert verification["semantic_checks"]["trace_hash"] == "pass"
    assert verification["semantic_checks"]["plan_hash"] == "pass"
    assert verification["trace_step_count"] >= 3
    assert len(verification["trace_hash"]) == 64
    archived = archive_artifact_bundle(
        bundle,
        action="unit_test_workflow_trace_bundle",
        resource_type="workflow_trace",
        resource_id=design["run_id"],
        filename="unit_test_workflow_trace_bundle.zip",
    )
    semantic = archived["metadata"]["workflow_trace_semantic_verification"]
    assert semantic["trace_hash"] == verification["trace_hash"]
    assert semantic["trace_step_count"] == verification["trace_step_count"]
    summary = workflow_trace_archive_summary(limit=5, verify_files=False)
    assert summary["verification_mode"] == "indexed"
    assert summary["freshness_status"] == "fresh"
    assert any(
        item["artifact_id"] == archived["artifact_id"]
        and item["trace_hash"] == verification["trace_hash"]
        and item["trace_step_count"] == verification["trace_step_count"]
        for item in summary["latest_artifacts"]
    )


def test_batch_gene_design_persists_per_gene_results() -> None:
    payload = {
        "genes": ["demo", "DEMO"],
        "species": "human",
        "target": {"brain_region": "striatum", "cell_type": "medium spiny neuron", "modality": "AAV"},
        "optimization_settings": {},
        "continue_on_error": True,
    }
    result = run_batch_gene_design(
        payload,
        OptimizationConfig(population_size=16, generations=2, max_candidates=2, seed=31),
        client=FakeEnsemblClient(),
        persist=False,
    )
    assert result["batch_status"] == "succeeded"
    assert result["summary"]["requested"] == 1
    assert result["summary"]["succeeded"] == 1
    assert result["runs"][0]["gene"] == "DEMO"
    assert result["runs"][0]["qc_gate_status"] in {"pass", "warning", "fail"}


def test_evidence_search_returns_coverage() -> None:
    evidence = search_evidence(
        "SNCA substantia nigra dopaminergic neuron AAV mane_select",
        {
            "species": "human",
            "brain_region": "substantia nigra",
            "cell_type": "dopaminergic neuron",
            "modality": "AAV",
            "transcript_selection": "mane_select",
        },
        5,
    )
    assert evidence["records"]
    assert evidence["retrieval"]["embedding_model"] == "hash-bow-v1"
    assert evidence["coverage"]["cell_type"] == "Allen cell-type prior"


def test_build_design_evidence_marks_mane_select() -> None:
    source_cds = fetch_canonical_cds("demo", client=FakeEnsemblClient())
    evidence = build_design_evidence(
        {"gene": "DEMO", "species": "human", "brain_region": "striatum", "cell_type": "medium spiny neuron", "modality": "AAV"},
        source_cds,
    )
    assert evidence["coverage"]["transcript"] == "MANE Select"
    assert evidence["coverage"]["transcript_confidence"] == "high"
    assert "trna_structured" in evidence["coverage"]
    synthesis = synthesize_evidence(evidence)
    assert synthesis["supported_rules"]
    assert synthesis["uncertain_rules"]


def test_rag_index_status_and_search() -> None:
    rebuilt = rebuild_rag_index(persist=False)
    assert rebuilt["chunks"] >= 6
    status = rag_status()
    assert status["embedding_model"] == "hash-bow-v1"
    results = rag_search(
        "MANE Select transcript RefSeq Ensembl CDS",
        {"species": "human", "transcript_selection": "mane_select"},
        8,
    )
    assert results["chunks"]
    assert any(chunk["metadata"]["collection"] == "canonical_transcript" for chunk in results["chunks"])
    assert all(chunk["rank_evidence"]["rank_evidence_schema"] == "agentic-rag-result-rank-evidence-v1" for chunk in results["chunks"])
    assert all(len(chunk["rank_evidence_hash"]) == 64 for chunk in results["chunks"])


def test_rag_evaluation_explains_coverage() -> None:
    evaluation = evaluate_rag_query(
        "SNCA substantia nigra dopaminergic neuron AAV",
        {"species": "human", "brain_region": "substantia nigra", "cell_type": "dopaminergic neuron", "modality": "AAV"},
        8,
    )
    assert evaluation["result_count"] > 0
    assert "score_breakdown" in evaluation
    assert "recommended_query_terms" in evaluation
    assert evaluation["query_fingerprint"]
    assert evaluation["ranking_policy"]["weights"]["vector_score"] == 0.36
    assert evaluation["retrieval_trace"]["trace_schema"] == "agentic-rag-retrieval-trace-v1"
    assert evaluation["retrieval_trace"]["score_weights"]["rerank_score"] == 0.40
    assert evaluation["retrieval_trace"]["retrieval_query_hash"] == evaluation["query_analysis"]["retrieval_query_hash"]
    assert "dopaminergic" in evaluation["retrieval_trace"]["retrieval_query_terms"]
    assert all(len(value) == 64 for value in evaluation["retrieval_trace"]["top_rank_evidence_hashes"])
    assert evaluation["index"]["retrieval_model"] == "hybrid-hash-bm25-facet-rerank-v3"
    assert evaluation["query_analysis"]["aliases_added"]
    assert "brain" in evaluation["query_analysis"]["retrieval_query_terms"]
    assert len(evaluation["query_analysis"]["retrieval_query_hash"]) == 64
    assert evaluation["evidence_sufficiency"]["sufficiency_schema"] == "agentic-rag-evidence-sufficiency-v1"
    assert evaluation["evidence_sufficiency"]["status"] in {"pass", "warning", "fail"}
    assert evaluation["evidence_sufficiency"]["checks"]
    assert all("facet_score" in item for item in evaluation["score_breakdown"])
    assert all("intent_match_score" in item for item in evaluation["score_breakdown"])
    assert any(item["intent_match_score"] > 0 for item in evaluation["score_breakdown"])
    assert all(len(item["rank_evidence_hash"]) == 64 for item in evaluation["score_breakdown"])
    assert all(item["rationale"] for item in evaluation["score_breakdown"])
    assert any(item["matched_facets"] for item in evaluation["score_breakdown"])


def test_rag_evaluation_bundle_contains_evidence_sufficiency_gate() -> None:
    request = {
        "query": "SNCA substantia nigra dopaminergic neuron AAV",
        "filters": {"species": "human", "brain_region": "substantia nigra", "cell_type": "dopaminergic neuron", "modality": "AAV"},
        "limit": 8,
    }
    bundle = build_rag_evaluation_bundle(request)
    verification = verify_rag_evaluation_bundle(bundle)
    assert verification["status"] == "pass"
    assert verification["semantic_checks"]["evidence_sufficiency_schema"] == "pass"
    assert verification["semantic_checks"]["evaluation_hash"] == "pass"
    assert verification["semantic_checks"]["retrieval_trace_hash"] == "pass"
    assert verification["semantic_checks"]["evidence_sufficiency_hash"] == "pass"
    assert verification["semantic_checks"]["facet_gap_analysis_hash"] == "pass"
    assert verification["semantic_checks"]["query_term_coverage_hash"] == "pass"
    assert verification["semantic_checks"]["top_sources_hash"] == "pass"
    assert verification["semantic_checks"]["top_sources_consistency"] == "pass"
    assert verification["semantic_checks"]["source_provenance_hash"] == "pass"
    assert verification["semantic_checks"]["source_provenance_schema"] == "pass"
    assert verification["semantic_checks"]["source_provenance_consistency"] == "pass"
    assert verification["semantic_checks"]["source_provenance_count"] == "pass"
    assert verification["semantic_checks"]["score_breakdown_hash"] == "pass"
    assert verification["semantic_checks"]["chunks_hash"] == "pass"
    assert len(verification["evaluation_hash"]) == 64
    assert len(verification["retrieval_trace_hash"]) == 64
    assert len(verification["evidence_sufficiency_hash"]) == 64
    assert len(verification["facet_gap_analysis_hash"]) == 64
    assert len(verification["query_term_coverage_hash"]) == 64
    assert len(verification["top_sources_hash"]) == 64
    assert len(verification["source_provenance_hash"]) == 64
    assert verification["source_provenance_count"] >= 1
    assert verification["source_payload_hash_count"] >= 0
    assert verification["source_snapshot_count"] >= 0
    assert len(verification["score_breakdown_hash"]) == 64
    assert len(verification["chunks_hash"]) == 64
    with ZipFile(BytesIO(bundle)) as archive:
        assert "evidence_sufficiency.json" in set(archive.namelist())
        assert "facet_gap_analysis.json" in set(archive.namelist())
        assert "query_term_coverage.json" in set(archive.namelist())
        assert "top_sources.json" in set(archive.namelist())
        assert "source_provenance.json" in set(archive.namelist())
        manifest = json.loads(archive.read("bundle_manifest.json"))
        trace = json.loads(archive.read("retrieval_trace.json"))
        sufficiency = json.loads(archive.read("evidence_sufficiency.json"))
        facet_gap = json.loads(archive.read("facet_gap_analysis.json"))
        term_coverage = json.loads(archive.read("query_term_coverage.json"))
        top_sources = json.loads(archive.read("top_sources.json"))
        source_provenance = json.loads(archive.read("source_provenance.json"))
        evaluation = json.loads(archive.read("evaluation.json"))
        assert manifest["evaluation_hash"] == verification["evaluation_hash"]
        assert manifest["retrieval_trace_hash"] == verification["retrieval_trace_hash"]
        assert manifest["evidence_sufficiency_hash"] == verification["evidence_sufficiency_hash"]
        assert manifest["facet_gap_analysis_hash"] == verification["facet_gap_analysis_hash"]
        assert manifest["query_term_coverage_hash"] == verification["query_term_coverage_hash"]
        assert manifest["top_sources_hash"] == verification["top_sources_hash"]
        assert manifest["source_provenance_hash"] == verification["source_provenance_hash"]
        assert manifest["source_provenance_count"] == verification["source_provenance_count"]
        assert manifest["score_breakdown_hash"] == verification["score_breakdown_hash"]
        assert manifest["chunks_hash"] == verification["chunks_hash"]
        assert sufficiency["sufficiency_schema"] == "agentic-rag-evidence-sufficiency-v1"
        assert sufficiency["status"] == evaluation["evidence_sufficiency"]["status"]
        assert sufficiency["source_count"] >= 1
        assert trace == evaluation["retrieval_trace"]
        assert facet_gap == evaluation["facet_gap_analysis"]
        assert term_coverage == evaluation["query_term_coverage"]
        assert top_sources == evaluation["top_sources"]
        assert source_provenance["provenance_schema"] == "agentic-rag-evaluation-source-provenance-v1"
        assert source_provenance["source_count"] == verification["source_provenance_count"]
        assert source_provenance["sources"]


def test_rag_facet_reranker_prioritizes_cell_type_context() -> None:
    evaluation = evaluate_rag_query(
        "dopamine neuron substantia nigra translation prior",
        {"species": "human", "brain_region": "substantia nigra", "cell_type": "dopaminergic neuron"},
        5,
    )
    assert evaluation["result_count"] > 0
    assert evaluation["query_analysis"]["filters_requested"] == ["brain_region", "cell_type", "species"]
    assert "Allen Brain Cell Atlas" in {item["source"] for item in evaluation["top_sources"]}
    assert max(item["facet_score"] for item in evaluation["score_breakdown"]) > 0


def test_rag_regression_suite_passes_required_evidence() -> None:
    cases = rag_regression_cases()
    result = evaluate_rag_regression()
    assert cases["case_count"] >= 5
    assert result["status"] == "pass"
    assert len(result["results_hash"]) == 64
    assert result["macro"]["recall_at_k"] >= 0.9
    assert result["macro"]["source_coverage"] >= 0.9
    assert all(
        len(item["rank_evidence_hash"]) == 64
        for case in result["results"]
        for item in case["top_results"]
    )
    assert {case["case_id"] for case in result["results"]} >= {
        "canonical_transcript_mane",
        "allen_dopaminergic_substantia_nigra",
        "gtex_snca_substantia_nigra_expression",
        "custom_translation_prior",
        "aav_design_constraints",
    }


def test_rag_diagnostics_reports_index_and_regression_health() -> None:
    diagnostics = rag_diagnostics()
    assert diagnostics["status"] in {"pass", "warning"}
    assert diagnostics["index"]["chunk_count"] > 0
    assert diagnostics["distributions"]["sources"]
    assert diagnostics["token_stats"]["mean"] > 0
    assert diagnostics["embedding_stats"]["dimension_mismatch_count"] == 0
    assert diagnostics["regression"]["macro"]["recall_at_k"] >= 0.9
    assert diagnostics["regression"]["status"] == "pass"
    assert len(diagnostics["regression"]["results_hash"]) == 64
    assert diagnostics["recommendations"]


def test_rag_regression_bundle_verifies_result_hash() -> None:
    bundle = build_rag_regression_bundle()
    verification = verify_rag_regression_bundle(bundle)
    assert verification["status"] in {"pass", "warning"}
    assert verification["semantic_checks"]["results_hash"] == "pass"
    assert verification["semantic_checks"]["quality_summary_hash"] == "pass"
    assert verification["semantic_checks"]["quality_summary_schema"] == "pass"
    assert verification["semantic_checks"]["quality_summary_case_count"] == "pass"
    assert verification["semantic_checks"]["quality_summary_status"] == "pass"
    assert verification["semantic_checks"]["case_metric_columns"] == "pass"
    assert verification["semantic_checks"]["case_metrics_hash"] == "pass"
    assert verification["semantic_checks"]["source_provenance_summary_schema"] == "pass"
    assert verification["semantic_checks"]["source_provenance_summary_consistency"] == "pass"
    assert verification["semantic_checks"]["source_provenance_summary_hash"] == "pass"
    assert verification["semantic_checks"]["source_provenance_case_count"] == "pass"
    assert len(verification["results_hash"]) == 64
    assert len(verification["quality_summary_hash"]) == 64
    assert len(verification["case_metrics_hash"]) == 64
    assert len(verification["source_provenance_summary_hash"]) == 64
    assert verification["quality_status"] == "pass"
    assert verification["source_provenance_case_count"] == verification["case_count"]
    assert verification["source_provenance_source_count"] >= 1
    assert verification["source_snapshot_case_count"] >= 0
    assert verification["top_source_count"] >= 1
    assert verification["missing_term_case_count"] == 0
    with ZipFile(BytesIO(bundle)) as archive:
        manifest = json.loads(archive.read("bundle_manifest.json"))
        regression = json.loads(archive.read("regression.json"))
        quality = json.loads(archive.read("quality_summary.json"))
        source_provenance = json.loads(archive.read("source_provenance_summary.json"))
        case_metrics = archive.read("case_metrics.csv").decode("utf-8")
        assert manifest["results_hash"] == regression["results_hash"] == verification["results_hash"]
        assert manifest["quality_summary_hash"] == regression["quality_summary_hash"] == verification["quality_summary_hash"]
        assert manifest["case_metrics_hash"] == verification["case_metrics_hash"]
        assert manifest["source_provenance_summary_hash"] == verification["source_provenance_summary_hash"]
        assert manifest["source_provenance_case_count"] == verification["source_provenance_case_count"]
        assert quality["quality_summary_schema"] == "agentic-rag-regression-quality-summary-v1"
        assert quality == regression["quality_summary"]
        assert source_provenance["provenance_schema"] == "agentic-rag-regression-source-provenance-summary-v1"
        assert source_provenance["case_count"] == verification["case_count"]
        assert source_provenance["cases"]
        assert "missing_terms" in case_metrics
        assert "top_document_id" in case_metrics
    archived = archive_artifact_bundle(
        bundle,
        action="unit_test_rag_regression_bundle",
        resource_type="rag_regression",
        resource_id=str(verification.get("results_hash") or "rag_regression"),
        filename="unit_test_rag_regression_bundle.zip",
        metadata={"verification_status": verification["status"]},
    )
    semantic = archived["metadata"]["rag_regression_semantic_verification"]
    assert semantic["case_metrics_hash"] == verification["case_metrics_hash"]
    summary = rag_regression_archive_summary(limit=5, verify_files=False)
    assert any(
        item["artifact_id"] == archived["artifact_id"]
        and item["case_metrics_hash"] == verification["case_metrics_hash"]
        for item in summary["latest_artifacts"]
    )


def test_rag_vector_index_archive_semantics_track_migration_evidence() -> None:
    bundle = build_rag_vector_index_bundle()
    verification = verify_rag_vector_index_bundle(bundle)
    assert verification["status"] in {"pass", "warning"}
    assert verification["semantic_checks"]["chunk_count"] == "pass"
    assert verification["semantic_checks"]["embedding_dimensions"] == "pass"
    assert verification["semantic_checks"]["vector_readiness_schema"] == "pass"
    assert verification["semantic_checks"]["migration_plan_schema"] == "pass"
    assert verification["semantic_checks"]["migration_parity_schema"] == "pass"
    assert verification["semantic_checks"]["migration_source_count"] == "pass"
    assert verification["semantic_checks"]["migration_row_hash"] == "pass"
    assert verification["chunk_count"] >= 1
    assert verification["embedding_dimensions"] >= 1
    assert verification["migration_target_backend"] in {"local_json", "pgvector", "qdrant"}
    assert verification["parity_status"] in {"pass", "warning"}
    assert len(verification["vector_row_hash"]) == 64
    with ZipFile(BytesIO(bundle)) as archive:
        names = set(archive.namelist())
        assert "vector_store_import_plan.json" in names
        assert "vector_store_parity.json" in names
        manifest = json.loads(archive.read("bundle_manifest.json"))
        import_plan = json.loads(archive.read("vector_store_import_plan.json"))
        parity = json.loads(archive.read("vector_store_parity.json"))
        assert manifest["vector_row_hash"] == import_plan["source"]["row_fingerprint"]["combined_row_hash"]
        assert parity["comparison"]["source_row_hash"] == verification["vector_row_hash"]
    archived = archive_artifact_bundle(
        bundle,
        action="unit_test_rag_vector_index_bundle",
        resource_type="rag_vector_index",
        resource_id=str(verification.get("manifest_hash") or "rag_vector_index"),
        filename="unit_test_rag_vector_index_bundle.zip",
        metadata={"verification_status": verification["status"]},
    )
    semantic = archived["metadata"]["rag_vector_index_semantic_verification"]
    assert semantic["chunk_count"] == verification["chunk_count"]
    assert semantic["embedding_dimensions"] == verification["embedding_dimensions"]
    assert semantic["embedding_model"] == verification["embedding_model"]
    assert semantic["migration_target_backend"] == verification["migration_target_backend"]
    assert semantic["parity_status"] == verification["parity_status"]
    assert semantic["vector_row_hash"] == verification["vector_row_hash"]
    summary = rag_vector_index_archive_summary(limit=5, verify_files=False)
    assert summary["verification_mode"] == "indexed"
    assert summary["status"] in {"pass", "warning"}
    assert any(
        item["artifact_id"] == archived["artifact_id"]
        and item["chunk_count"] == verification["chunk_count"]
        and item["embedding_dimensions"] == verification["embedding_dimensions"]
        and item["vector_row_hash"] == verification["vector_row_hash"]
        for item in summary["latest_artifacts"]
    )


def test_structured_manifest_validates_sources() -> None:
    manifest = structured_manifest()
    validation = validate_structured_records()
    assert manifest["manifest_hash"]
    assert manifest["files"]
    assert "provenance" in manifest
    assert manifest["provenance"]["tracked_records"] >= 1
    assert validation["error_count"] == 0
    preview = preview_structured_import(manifest["files"][0]["path"])
    assert preview["status"] in {"pass", "warning"}
    assert preview["source"]["sha256"] == manifest["files"][0]["sha256"]
    assert preview["records"]["import_count"] == manifest["files"][0]["records"]
    assert preview["validation"]["projected"]["error_count"] == 0
    assert preview["manifest"]["projected_hash"]
    import_result = {"imported_path": manifest["files"][0]["path"], "status": {"manifest_hash": manifest["manifest_hash"]}}
    bundle = build_structured_import_audit_bundle(import_result, {"source_path": manifest["files"][0]["path"], "rebuild_index": False})
    with ZipFile(BytesIO(bundle)) as archive:
        names = set(archive.namelist())
        assert "artifact_manifest.json" in names
        assert "import_manifest.json" in names
        assert "structured_manifest.json" in names
        assert "structured_validation.json" in names
        assert any(name.startswith("imported/") for name in names)
        artifact_manifest = json.loads(archive.read("artifact_manifest.json"))
        assert artifact_manifest["artifact_type"] == "structured_import_audit_bundle"
    verification = verify_structured_import_audit_bundle(bundle)
    assert verify_artifact_bundle(bundle)["status"] == "pass"
    assert verification["status"] in {"pass", "warning"}
    assert verification["semantic_status"] in {"pass", "warning"}
    assert verification["structured_manifest_hash"] == manifest["manifest_hash"]
    archived = archive_artifact_bundle(
        bundle,
        action="unit_test_structured_import_audit",
        resource_type="data",
        resource_id=Path(manifest["files"][0]["path"]).name,
        filename="structured_import_audit_test.zip",
    )
    assert archived["metadata"]["structured_import_semantic_verification"]["structured_manifest_hash"] == manifest["manifest_hash"]
    archived_verification = verify_archived_artifact(archived["artifact_id"])
    assert archived_verification["bundle_verification"]["structured_manifest_hash"] == manifest["manifest_hash"]
    summary = structured_import_archive_summary(limit=5, verify_files=False)
    assert summary["verification_mode"] == "indexed"
    assert summary["checked_count"] >= 1
    assert summary["freshness_status"] == "fresh"
    assert summary["latest_age_hours"] is not None
    assert summary["freshness_policy"]["warning_hours"] > 0
    assert any(
        item["artifact_id"] == archived["artifact_id"] and item["metadata_indexed"] is True
        for item in summary["latest_artifacts"]
    )


def test_data_catalog_and_refresh_plan() -> None:
    catalog = data_catalog()
    plan = refresh_reference_data(genes=["SNCA"], include_allen=False, dry_run=True)
    assert catalog["sources"]
    assert "external_sources" in catalog
    assert catalog["status"]["records"] >= 1
    assert plan["refresh_status"] == "planned"
    assert plan["operations"][0]["gene"] == "SNCA"
    bundle = build_data_refresh_plan_bundle({"genes": ["SNCA"], "include_allen": False, "dataset_id": "gtex_v8"})
    verification = verify_data_refresh_plan_bundle(bundle)
    assert verification["status"] in {"pass", "warning"}
    assert verification["semantic_checks"]["dry_run"] == "pass"
    assert verification["semantic_checks"]["operation_count_csv"] == "pass"
    assert verification["semantic_checks"]["request_hash"] == "pass"
    assert verification["semantic_checks"]["operations_hash"] == "pass"
    assert verification["semantic_checks"]["data_catalog_hash"] == "pass"
    assert verification["semantic_checks"]["external_sources_hash"] == "pass"
    assert verification["semantic_checks"]["structured_quality_hash"] == "pass"
    assert verification["semantic_checks"]["data_provenance_hash"] == "pass"
    assert verification["semantic_checks"]["rag_status_hash"] == "pass"
    assert len(verification["request_hash"]) == 64
    assert len(verification["operations_hash"]) == 64
    assert len(verification["data_catalog_hash"]) == 64
    assert len(verification["external_sources_hash"]) == 64
    assert len(verification["structured_quality_hash"]) == 64
    assert len(verification["data_provenance_hash"]) == 64
    assert len(verification["rag_status_hash"]) == 64
    archived = archive_artifact_bundle(
        bundle,
        action="unit_test_data_refresh_plan_bundle",
        resource_type="data_refresh",
        resource_id="gtex_v8",
        filename="unit_test_data_refresh_plan_bundle.zip",
    )
    semantic = archived["metadata"]["data_refresh_plan_semantic_verification"]
    assert semantic["operation_count"] >= 1
    assert semantic["request_hash"] == verification["request_hash"]
    assert semantic["operations_hash"] == verification["operations_hash"]
    assert semantic["data_catalog_hash"] == verification["data_catalog_hash"]
    assert semantic["external_sources_hash"] == verification["external_sources_hash"]
    assert semantic["structured_quality_hash"] == verification["structured_quality_hash"]
    assert semantic["data_provenance_hash"] == verification["data_provenance_hash"]
    assert semantic["rag_status_hash"] == verification["rag_status_hash"]
    assert semantic["dataset_id"] == "gtex_v8"
    summary = data_refresh_plan_archive_summary(limit=5, verify_files=False)
    assert summary["verification_mode"] == "indexed"
    assert summary["freshness_status"] == "fresh"
    assert summary["latest_age_hours"] is not None
    assert any(
        item["artifact_id"] == archived["artifact_id"]
        and item["operation_count"] >= 1
        and item["request_hash"] == verification["request_hash"]
        and item["operations_hash"] == verification["operations_hash"]
        and item["data_catalog_hash"] == verification["data_catalog_hash"]
        and item["external_sources_hash"] == verification["external_sources_hash"]
        and item["structured_quality_hash"] == verification["structured_quality_hash"]
        and item["data_provenance_hash"] == verification["data_provenance_hash"]
        and item["rag_status_hash"] == verification["rag_status_hash"]
        and item["dataset_id"] == "gtex_v8"
        for item in summary["latest_artifacts"]
    )


def test_external_source_snapshot_backfill_reports_coverage() -> None:
    status = external_source_status()
    coverage = external_source_coverage()
    plan = backfill_external_source_snapshots(dry_run=True)
    assert "coverage" in status
    assert coverage["tracked_records"] >= 1
    assert 0.0 <= coverage["source_snapshot_path_fraction"] <= 1.0
    assert plan["dry_run"] is True
    assert plan["candidate_record_count"] >= 0


def test_data_baseline_event_records_current_state() -> None:
    event = record_data_baseline_event("test_baseline")
    assert event["refresh_status"] == "baseline_recorded"
    assert event["summary"]["structured_records"] >= 1
    assert event["manifest_hash"]
    assert event["log"]["entries"]


def test_data_snapshot_bundle_contains_core_manifests() -> None:
    bundle = build_data_snapshot_bundle()
    with ZipFile(BytesIO(bundle)) as archive:
        names = set(archive.namelist())
        assert "snapshot_manifest.json" in names
        assert "structured_manifest.json" in names
        assert "data_provenance_audit.json" in names
        assert "data_lock_status.json" in names
        assert "data_release_lock_status.json" in names
        assert "rag_status.json" in names
        assert "documents_manifest.json" in names
        assert "artifact_manifest.json" in names
        snapshot_manifest = json.loads(archive.read("snapshot_manifest.json"))
        if external_source_status()["file_count"]:
            assert any("external_sources" in item["path"] for item in snapshot_manifest["files"])
        artifact_manifest = json.loads(archive.read("artifact_manifest.json"))
        assert artifact_manifest["artifact_type"] == "data_snapshot_bundle"
        assert artifact_manifest["manifest_hash"]
        assert any(item["path"] == "snapshot_manifest.json" for item in artifact_manifest["files"])
    verification = verify_data_snapshot_bundle(bundle)
    assert verification["status"] == "pass"
    assert verification["semantic_status"] == "pass"
    assert verification["checked_files"] == verification["file_count"]
    assert verification["semantic_checks"]["snapshot_manifest_hash"] == "pass"
    assert verification["semantic_checks"]["snapshot_file_listing"] == "pass"
    assert verification["semantic_checks"]["structured_manifest_hash"] == "pass"
    assert verification["snapshot_manifest_hash"]
    assert verification["structured_manifest_hash"]
    assert verification["external_snapshot_file_count"] >= 1
    assert verification["rag_index_hash"]
    archived = archive_artifact_bundle(
        bundle,
        action="unit_test_data_snapshot_bundle",
        resource_type="data_snapshot",
        resource_id=verification["snapshot_manifest_hash"],
        filename="unit_test_data_snapshot_bundle.zip",
    )
    semantic = archived["metadata"]["data_snapshot_semantic_verification"]
    assert semantic["snapshot_manifest_hash"] == verification["snapshot_manifest_hash"]
    assert semantic["structured_manifest_hash"] == verification["structured_manifest_hash"]
    assert semantic["rag_index_hash"] == verification["rag_index_hash"]
    assert semantic["external_snapshot_file_count"] == verification["external_snapshot_file_count"]
    archive_semantics = data_snapshot_archive_summary(limit=5)
    assert archive_semantics["status"] in {"pass", "warning"}
    assert archive_semantics["checked_count"] >= 1
    latest = archive_semantics["latest_artifacts"][0]
    assert latest["snapshot_manifest_hash"]
    assert latest["structured_manifest_hash"]
    assert latest["rag_index_hash"]


def test_data_lockfile_can_be_written_and_verified() -> None:
    lockfile = build_data_lockfile()
    assert lockfile["lock_hash"]
    written = write_data_lockfile()
    verified = verify_data_lockfile()
    assert written["status"] == "current"
    assert verified["status"] == "current"
    assert verified["locked_hash"] == verified["current_hash"]


def test_data_release_lockfile_can_be_written_and_verified() -> None:
    lockfile = build_data_release_lock()
    assert lockfile["release_lock_schema"] == "agentic-rag-data-release-lock-v1"
    assert lockfile["release_hash"]
    assert lockfile["state"]["summary"]["records"] >= 1
    written = write_data_release_lock()
    verified = verify_data_release_lock()
    assert written["status"] == "current"
    assert verified["status"] == "current"
    assert verified["locked_hash"] == verified["current_hash"]


def test_data_release_bundle_verifies_record_hashes() -> None:
    bundle = build_data_release_bundle()
    verification = verify_data_release_bundle(bundle)
    assert verification["status"] in {"pass", "warning"}
    assert verification["semantic_checks"]["records_hash"] == "pass"
    assert verification["semantic_checks"]["records_csv_hash"] == "pass"
    assert verification["semantic_checks"]["record_source_summary_hash"] == "pass"
    assert verification["semantic_checks"]["record_source_summary_schema"] == "pass"
    assert verification["semantic_checks"]["record_source_summary_consistency"] == "pass"
    assert verification["semantic_checks"]["record_source_summary_counts"] == "pass"
    assert verification["semantic_checks"]["release_handoff_hash"] == "pass"
    assert verification["semantic_checks"]["rag_structured_manifest_hash"] == "pass"
    assert verification["semantic_checks"]["rag_index_hash"] in {"pass", "warning"}
    assert verification["semantic_checks"]["trna_caveat_count"] == "pass"
    assert verification["semantic_checks"]["trna_blocking_production_use"] == "pass"
    assert verification["semantic_checks"]["external_snapshot_reference_coverage"] in {"pass", "warning"}
    assert len(verification["records_hash"]) == 64
    assert len(verification["records_csv_hash"]) == 64
    assert len(verification["record_source_summary_hash"]) == 64
    assert verification["dataset_count"] >= 1
    assert verification["source_file_count"] >= 1
    assert len(verification["release_handoff_hash"]) == 64
    assert verification["rag_structured_manifest_hash"]
    if verification["rag_index_hash"] is not None:
        assert len(verification["rag_index_hash"]) == 64
    assert isinstance(verification["trna_caveat_count"], int)
    assert isinstance(verification["trna_blocking_production_use"], bool)
    assert verification["external_snapshot_contained_count"] <= verification["external_snapshot_referenced_count"]
    assert verification["external_snapshot_missing_count"] == 0
    with ZipFile(BytesIO(bundle)) as archive:
        release_manifest = json.loads(archive.read("release_manifest.json"))
        source_summary = json.loads(archive.read("record_source_summary.json"))
        assert release_manifest["records_hash"] == verification["records_hash"]
        assert release_manifest["records_csv_hash"] == verification["records_csv_hash"]
        assert release_manifest["record_source_summary_hash"] == verification["record_source_summary_hash"]
        assert source_summary["summary_schema"] == "agentic-rag-data-release-record-source-summary-v1"
        assert source_summary["record_count"] == verification["record_count"]
        assert source_summary["dataset_count"] == verification["dataset_count"]
        assert release_manifest["release_handoff_hash"] == verification["release_handoff_hash"]
        assert release_manifest["rag_structured_manifest_hash"] == verification["rag_structured_manifest_hash"]
        assert release_manifest["rag_index_hash"] == verification["rag_index_hash"]
        assert release_manifest["trna_caveat_count"] == verification["trna_caveat_count"]
        assert release_manifest["trna_blocking_production_use"] == verification["trna_blocking_production_use"]
    archived = archive_artifact_bundle(
        bundle,
        action="unit_test_data_release_bundle",
        resource_type="data_release",
        resource_id=verification["structured_manifest_hash"] or "data_release",
        filename="unit_test_data_release_bundle.zip",
    )
    semantic = archived["metadata"]["data_release_semantic_verification"]
    assert semantic["records_hash"] == verification["records_hash"]
    assert semantic["records_csv_hash"] == verification["records_csv_hash"]
    assert semantic["record_source_summary_hash"] == verification["record_source_summary_hash"]
    assert semantic["dataset_count"] == verification["dataset_count"]
    assert semantic["source_file_count"] == verification["source_file_count"]
    assert semantic["release_handoff_hash"] == verification["release_handoff_hash"]
    assert semantic["rag_structured_manifest_hash"] == verification["rag_structured_manifest_hash"]
    assert semantic["rag_index_hash"] == verification["rag_index_hash"]
    assert semantic["trna_caveat_count"] == verification["trna_caveat_count"]
    assert semantic["trna_blocking_production_use"] == verification["trna_blocking_production_use"]
    assert semantic["external_snapshot_contained_count"] == verification["external_snapshot_contained_count"]
    assert semantic["external_snapshot_missing_count"] == 0
    summary = data_release_archive_summary(limit=5, verify_files=False)
    latest = next(item for item in summary["latest_artifacts"] if item["artifact_id"] == archived["artifact_id"])
    assert latest["records_hash"] == verification["records_hash"]
    assert latest["records_csv_hash"] == verification["records_csv_hash"]
    assert latest["record_source_summary_hash"] == verification["record_source_summary_hash"]
    assert latest["dataset_count"] == verification["dataset_count"]
    assert latest["source_file_count"] == verification["source_file_count"]
    assert latest["release_handoff_hash"] == verification["release_handoff_hash"]
    assert latest["rag_structured_manifest_hash"] == verification["rag_structured_manifest_hash"]
    assert latest["rag_index_hash"] == verification["rag_index_hash"]
    assert latest["trna_caveat_count"] == verification["trna_caveat_count"]
    assert latest["trna_blocking_production_use"] == verification["trna_blocking_production_use"]
    assert latest["external_snapshot_contained_count"] == verification["external_snapshot_contained_count"]
    assert latest["external_snapshot_missing_count"] == 0


def test_data_provenance_audit_reports_checks() -> None:
    audit = data_provenance_audit()
    assert audit["status"] in {"pass", "warning", "fail"}
    assert audit["manifest_hash"]
    assert audit["lockfile"]["status"] in {"current", "missing", "drift"}
    assert audit["release_lock"]["status"] in {"current", "missing", "drift"}
    assert any(check["id"] == "structured_validation" for check in audit["checks"])
    assert any(check["id"] == "source_hashes_present" for check in audit["checks"])
    assert any(check["id"] == "external_payload_hashes_present" for check in audit["checks"])


def test_external_source_status_reports_snapshot_store() -> None:
    status = external_source_status()
    assert "external_source_path" in status
    assert isinstance(status["files"], list)


def test_qc_report_contains_rationale() -> None:
    design = optimize_design(
        "ATGGCTGCTGCTGCTTAA",
        OptimizationConfig(population_size=16, generations=4, max_candidates=3, seed=11),
        {"gene": "DEMO", "species": "human", "modality": "AAV"},
        evidence_used=True,
    )
    source_cds = fetch_canonical_cds("demo", client=FakeEnsemblClient())
    design["source_cds"] = {
        "gene": source_cds["gene"],
        "selected_transcript": source_cds["selected_transcript"],
        "cds_length_nt": source_cds["cds_length_nt"],
        "protein_length_aa": source_cds["protein_length_aa"],
        "provenance": source_cds["provenance"],
    }
    design["evidence"] = build_design_evidence(design["target"], design["source_cds"])
    report = generate_qc_report(design)
    assert report["evidence_summary"]["supported_rules"]
    assert report["evidence_summary"]["retrieval_quality"]["quality_schema"] == "agentic-rag-qc-retrieval-quality-v1"
    assert report["evidence_summary"]["retrieval_quality"]["source_count"] >= 1
    assert report["evidence_summary"]["retrieval_quality"]["top_sources"]
    assert report["evidence_summary"]["retrieval_quality"]["rank_evidence_count"] >= 1
    assert len(report["evidence_summary"]["retrieval_quality"]["rank_evidence_hash"]) == 64
    assert report["recommended_candidate"]["rationale"]
    assert report["recommended_candidate"]["selection_trace"]
    assert report["recommended_candidate"]["constraint_risk"]["status"] in {"pass", "warning", "fail"}
    assert report["project_metadata"]["config_hash"]
    assert "hairpin_proxy_score" in report["score_summary"]["recommended"]
    assert "codon_pair_risk" in report["recommended_candidate"]["constraint_status"]
    assert "sequence_policy" in report
    assert report["candidate_diagnostics"]["candidate_count"] == len(design["candidates"])
    assert "constraint_risk_summary" in report["candidate_diagnostics"]
    assert report["candidate_diagnostics"]["best_by_metric"]["composite_quality"]["candidate_id"]
    assert report["candidate_diagnostics"]["sequence_policy_audit"]["audit_schema"] == "agentic-rag-candidate-sequence-policy-audit-v1"
    assert report["recommendation_readiness"]["readiness_schema"] == "agentic-rag-recommendation-readiness-v1"
    assert report["recommendation_readiness"]["recommended_candidate_id"] == design["recommended_candidate"]["candidate_id"]
    assert report["recommendation_readiness"]["readiness_status"] in {"pass", "warning"}
    assert len(report["recommendation_readiness"]["readiness_hash"]) == 64
    assert "polyadenylation_signal_count" in report["recommended_candidate"]["constraint_status"]
    assert "qc_gate" in report
    assert report["optimizer_reproducibility"]["manifest_schema"] == "agentic-rag-optimizer-reproducibility-v1"
    assert report["project_metadata"]["optimizer_manifest_hash"] == report["optimizer_reproducibility"]["manifest_hash"]
    assert report["optimizer_reproducibility"]["repair_policy"]["enabled"] is True
    assert report["optimizer_reproducibility"]["seed_strategy"]["version"] == "deterministic-tradeoff-seeds-v1"
    assert "maximize_cai" in report["optimizer_reproducibility"]["objective_inventory"]
    assert report["target_structured_evidence"]["matched_record_count"] >= 1
    assert "top_records" in report["target_structured_evidence"]
    markdown = export_qc_report(report, "markdown")
    html = export_qc_report(report, "html")
    json_report = export_qc_report(report, "json")
    pdf_report = export_qc_report(report, "pdf")
    assert "# Gene Therapy Design QC Report" in markdown
    assert "## QC Gate" in markdown
    assert "## Candidate Diagnostics" in markdown
    assert "## Recommendation Readiness" in markdown
    assert "Readiness hash" in markdown
    assert "Sequence-policy audit" in markdown
    assert "## Target Structured Evidence" in markdown
    assert "## Retrieval Evidence Quality" in markdown
    assert "## Optimizer Reproducibility" in markdown
    assert "<html" in html
    assert '"project_metadata"' in json_report
    assert isinstance(pdf_report, bytes)
    assert pdf_report.startswith(b"%PDF-")
    assert len(PdfReader(BytesIO(pdf_report)).pages) >= 1


def test_qc_report_bundle_contains_manifested_multiformat_exports() -> None:
    design = optimize_design(
        "ATGGCTGCTGCTGCTTAA",
        OptimizationConfig(population_size=16, generations=3, max_candidates=3, seed=71),
        {"gene": "DEMO", "species": "human", "brain_region": "cortex", "cell_type": "neuron", "modality": "AAV"},
        evidence_used=True,
    )
    source_cds = fetch_canonical_cds("demo", client=FakeEnsemblClient())
    design["source_cds"] = {
        "gene": source_cds["gene"],
        "selected_transcript": source_cds["selected_transcript"],
        "cds_length_nt": source_cds["cds_length_nt"],
        "protein_length_aa": source_cds["protein_length_aa"],
        "provenance": source_cds["provenance"],
    }
    design["evidence"] = build_design_evidence(design["target"], design["source_cds"])
    design["qc_report"] = generate_qc_report(design)
    request_payload = {
        "cds": "ATGGCTGCTGCTGCTTAA",
        "species": "human",
        "brain_region": "cortex",
        "cell_type": "neuron",
        "modality": "AAV",
    }
    bundle = build_qc_report_bundle(design, request_payload, bundle_type="cds_report")
    with ZipFile(BytesIO(bundle)) as archive:
        names = set(archive.namelist())
        assert "artifact_manifest.json" in names
        assert "bundle_manifest.json" in names
        assert "request.json" in names
        assert "qc_report.json" in names
        assert "qc_report.md" in names
        assert "qc_report.html" in names
        assert "qc_report.pdf" in names
        assert "report_formats_summary.json" in names
        assert "optimizer_reproducibility.json" in names
        assert "recommendation_audit.json" in names
        assert "recommendation_readiness.json" in names
        assert "candidate_folding_audit.json" in names
        assert "recommended_folding_evidence.json" in names
        assert "candidate_ranking.csv" in names
        optimizer_manifest = json.loads(archive.read("optimizer_reproducibility.json"))
        recommendation_audit = json.loads(archive.read("recommendation_audit.json"))
        recommendation_readiness = json.loads(archive.read("recommendation_readiness.json"))
        candidate_folding_audit = json.loads(archive.read("candidate_folding_audit.json"))
        recommended_folding_evidence = json.loads(archive.read("recommended_folding_evidence.json"))
        report_formats_summary = json.loads(archive.read("report_formats_summary.json"))
        request = json.loads(archive.read("request.json"))
        assert request["brain_region"] == "cortex"
        assert optimizer_manifest["manifest_schema"] == "agentic-rag-optimizer-reproducibility-v1"
        assert optimizer_manifest["manifest_hash"]
        assert recommendation_audit["audit_schema"] == "agentic-rag-recommendation-audit-v1"
        assert recommendation_audit["recommended_candidate_id"] == design["recommended_candidate"]["candidate_id"]
        assert recommendation_readiness["readiness_schema"] == "agentic-rag-recommendation-readiness-v1"
        assert recommendation_readiness["recommended_candidate_id"] == design["recommended_candidate"]["candidate_id"]
        assert recommendation_readiness["readiness_status"] in {"pass", "warning"}
        assert len(recommendation_readiness["readiness_hash"]) == 64
        assert candidate_folding_audit["audit_schema"] == "agentic-rag-candidate-folding-audit-v1"
        assert candidate_folding_audit["audit_hash"] == design["candidate_folding_audit"]["audit_hash"]
        assert recommended_folding_evidence["folding_schema"] == "agentic-rag-rna-folding-v1"
        assert recommended_folding_evidence["candidate_id"] == design["recommended_candidate"]["candidate_id"]
        assert recommended_folding_evidence["folding_evidence_hash"] == design["recommended_folding_evidence"]["folding_evidence_hash"]
        artifact_manifest = json.loads(archive.read("artifact_manifest.json"))
        bundle_manifest = json.loads(archive.read("bundle_manifest.json"))
        assert artifact_manifest["artifact_type"] == "qc_report_bundle"
        assert any(item["path"] == "qc_report.pdf" and item["sha256"] for item in artifact_manifest["files"])
        assert len(bundle_manifest["request_hash"]) == 64
        assert len(bundle_manifest["qc_report_hash"]) == 64
        assert len(bundle_manifest["report_formats_summary_hash"]) == 64
        assert report_formats_summary["summary_schema"] == "agentic-rag-qc-report-formats-summary-v1"
        assert report_formats_summary["format_count"] == 4
        assert any(item["path"] == "qc_report.pdf" and item["pdf_header_valid"] for item in report_formats_summary["formats"])
        assert len(bundle_manifest["candidate_ranking_hash"]) == 64
        assert len(bundle_manifest["recommendation_audit_hash"]) == 64
        assert len(bundle_manifest["recommendation_readiness_hash"]) == 64
        assert len(bundle_manifest["candidate_folding_audit_hash"]) == 64
        assert len(bundle_manifest["recommended_folding_evidence_hash"]) == 64
        candidate_csv = archive.read("candidate_ranking.csv").decode("utf-8")
        assert "constraint_risk_status" in candidate_csv
        assert "selection_trace" in candidate_csv
    verification = verify_qc_report_bundle(bundle)
    assert verification["status"] == "pass"
    assert verification["semantic_status"] == "pass"
    assert verification["optimizer_manifest_hash"] == optimizer_manifest["manifest_hash"]
    assert verification["request_hash"] == bundle_manifest["request_hash"]
    assert verification["qc_report_hash"] == bundle_manifest["qc_report_hash"]
    assert verification["report_formats_summary_hash"] == bundle_manifest["report_formats_summary_hash"]
    assert verification["candidate_ranking_hash"] == bundle_manifest["candidate_ranking_hash"]
    assert verification["recommendation_audit_hash"] == bundle_manifest["recommendation_audit_hash"]
    assert verification["recommendation_readiness_hash"] == bundle_manifest["recommendation_readiness_hash"]
    assert verification["recommendation_readiness_status"] in {"pass", "warning"}
    assert verification["recommendation_readiness_candidate_id"] == design["recommended_candidate"]["candidate_id"]
    assert verification["candidate_folding_audit_hash"] == bundle_manifest["candidate_folding_audit_hash"]
    assert verification["candidate_folding_audit_selection_signal"] in {"secondary_structure_proxy_score", "thermodynamic_risk_score"}
    assert verification["recommended_folding_evidence_hash"] == bundle_manifest["recommended_folding_evidence_hash"]
    assert verification["recommended_folding_status"] in {"pass", "warning"}
    assert verification["recommended_folding_backend"] in {"rnafold", "deterministic_proxy"}
    assert verification["recommended_folding_candidate_id"] == design["recommended_candidate"]["candidate_id"]
    assert verification["retrieval_quality_rank_evidence_count"] >= 1
    assert len(verification["retrieval_quality_rank_evidence_hash"]) == 64
    assert verification["semantic_checks"]["request_hash"] == "pass"
    assert verification["semantic_checks"]["qc_report_hash"] == "pass"
    assert verification["semantic_checks"]["report_formats_summary_hash"] == "pass"
    assert verification["semantic_checks"]["report_formats_summary_schema"] == "pass"
    assert verification["semantic_checks"]["report_formats_summary_consistency"] == "pass"
    assert verification["semantic_checks"]["candidate_ranking_hash"] == "pass"
    assert verification["semantic_checks"]["recommendation_audit_hash"] == "pass"
    assert verification["semantic_checks"]["recommendation_readiness_hash"] == "pass"
    assert verification["semantic_checks"]["evidence_retrieval_quality_schema"] == "pass"
    assert verification["semantic_checks"]["evidence_retrieval_quality_sources"] == "pass"
    assert verification["semantic_checks"]["evidence_retrieval_quality_rank_hashes"] == "pass"
    assert verification["semantic_checks"]["recommendation_audit_file_schema"] == "pass"
    assert verification["semantic_checks"]["recommendation_audit_file_report"] == "pass"
    assert verification["semantic_checks"]["recommendation_audit_candidate"] == "pass"
    assert verification["semantic_checks"]["recommendation_readiness_schema"] == "pass"
    assert verification["semantic_checks"]["recommendation_readiness_report"] == "pass"
    assert verification["semantic_checks"]["recommendation_readiness_payload_hash"] == "pass"
    assert verification["semantic_checks"]["recommendation_readiness_candidate"] == "pass"
    assert verification["semantic_checks"]["recommendation_readiness_folding_candidate"] == "pass"
    assert verification["semantic_checks"]["recommendation_readiness_status"] == "pass"
    assert verification["semantic_checks"]["candidate_folding_audit_schema"] == "pass"
    assert verification["semantic_checks"]["candidate_folding_audit_report"] == "pass"
    assert verification["semantic_checks"]["candidate_folding_audit_payload_hash"] == "pass"
    assert verification["semantic_checks"]["recommended_folding_evidence_schema"] == "pass"
    assert verification["semantic_checks"]["recommended_folding_evidence_report"] == "pass"
    assert verification["semantic_checks"]["recommended_folding_evidence_payload_hash"] == "pass"
    assert verification["semantic_checks"]["recommended_folding_evidence_candidate"] == "pass"
    assert verification["semantic_checks"]["optimizer_hash_report"] == "pass"
    assert verification["semantic_checks"]["request_payload"] == "pass"
    assert verification["semantic_checks"]["request_target_brain_region"] == "pass"
    assert verification["semantic_checks"]["request_target_cell_type"] == "pass"
    assert verification["semantic_checks"]["request_target_modality"] == "pass"
    assert verification["semantic_checks"]["recommended_candidate_in_csv"] == "pass"
    assert verification["semantic_checks"]["candidate_csv_explainability_columns"] == "pass"
    assert verification["semantic_checks"]["candidate_ranking_report_count"] == "pass"
    assert verification["semantic_checks"]["candidate_ranking_report_ids"] == "pass"
    assert verification["semantic_checks"]["candidate_ranking_report_scores"] == "pass"
    assert verification["semantic_checks"]["candidate_diagnostics_candidate_count"] == "pass"
    assert verification["semantic_checks"]["recommended_constraint_risk_csv"] == "pass"
    assert verification["semantic_checks"]["recommended_candidate_score_csv"] == "pass"
    assert verification["checked_files"] == verification["file_count"]
    archived = archive_artifact_bundle(
        bundle,
        action="unit_test_qc_report_bundle",
        resource_type="run",
        resource_id=design["run_id"],
        filename=f"{design['run_id']}_qc_report_bundle.zip",
    )
    assert archived["metadata"]["qc_bundle_semantic_verification"]["semantic_status"] == "pass"
    assert archived["metadata"]["qc_bundle_semantic_verification"]["optimizer_manifest_hash"] == optimizer_manifest["manifest_hash"]
    assert archived["metadata"]["qc_bundle_semantic_verification"]["qc_report_hash"] == verification["qc_report_hash"]
    assert archived["metadata"]["qc_bundle_semantic_verification"]["report_formats_summary_hash"] == verification["report_formats_summary_hash"]
    assert archived["metadata"]["qc_bundle_semantic_verification"]["candidate_ranking_hash"] == verification["candidate_ranking_hash"]
    assert archived["metadata"]["qc_bundle_semantic_verification"]["recommendation_audit_hash"] == verification["recommendation_audit_hash"]
    assert archived["metadata"]["qc_bundle_semantic_verification"]["recommendation_readiness_hash"] == verification["recommendation_readiness_hash"]
    assert archived["metadata"]["qc_bundle_semantic_verification"]["recommendation_readiness_status"] in {"pass", "warning"}
    assert archived["metadata"]["qc_bundle_semantic_verification"]["recommendation_readiness_candidate_id"] == design["recommended_candidate"]["candidate_id"]
    assert archived["metadata"]["qc_bundle_semantic_verification"]["candidate_folding_audit_hash"] == verification["candidate_folding_audit_hash"]
    assert archived["metadata"]["qc_bundle_semantic_verification"]["candidate_folding_audit_selection_signal"] in {"secondary_structure_proxy_score", "thermodynamic_risk_score"}
    assert archived["metadata"]["qc_bundle_semantic_verification"]["candidate_folding_audit_validated_backend_count"] >= 0
    assert archived["metadata"]["qc_bundle_semantic_verification"]["recommended_folding_evidence_hash"] == verification["recommended_folding_evidence_hash"]
    assert archived["metadata"]["qc_bundle_semantic_verification"]["recommended_folding_status"] in {"pass", "warning"}
    assert archived["metadata"]["qc_bundle_semantic_verification"]["recommended_folding_candidate_id"] == design["recommended_candidate"]["candidate_id"]
    assert archived["metadata"]["qc_bundle_semantic_verification"]["retrieval_quality_status"] in {"pass", "warning"}
    assert archived["metadata"]["qc_bundle_semantic_verification"]["retrieval_quality_source_count"] >= 1
    assert archived["metadata"]["qc_bundle_semantic_verification"]["retrieval_quality_rank_evidence_count"] >= 1
    assert len(archived["metadata"]["qc_bundle_semantic_verification"]["retrieval_quality_rank_evidence_hash"]) == 64
    assert archived["metadata"]["qc_bundle_semantic_verification"]["objective_count"] >= 1
    assert archived["metadata"]["qc_bundle_semantic_verification"]["data_quality_status"] in {"pass", "warning"}
    assert archived["metadata"]["qc_bundle_semantic_verification"]["optimizer_stress_status"] in {"pass", "warning"}
    assert archived["metadata"]["qc_bundle_semantic_verification"]["request_payload_status"] == "pass"
    assert archived["metadata"]["qc_bundle_semantic_verification"]["request_target_checks"]["brain_region"] == "pass"
    archived_verification = verify_archived_artifact(archived["artifact_id"])
    assert archived_verification["status"] == "pass"
    assert archived_verification["bundle_verification"]["semantic_status"] == "pass"
    assert archived_verification["bundle_verification"]["optimizer_manifest_hash"] == optimizer_manifest["manifest_hash"]
    archive_semantics = qc_bundle_archive_semantic_summary(limit=5)
    assert archive_semantics["status"] in {"pass", "warning"}
    assert archive_semantics["checked_count"] >= 1
    assert archive_semantics["semantic_pass_count"] >= 1
    assert archive_semantics["freshness_status"] == "fresh"
    assert archive_semantics["latest_age_hours"] is not None
    assert any(item["artifact_id"] == archived["artifact_id"] for item in archive_semantics["latest_artifacts"])
    indexed_semantics = qc_bundle_archive_semantic_summary(limit=5, verify_files=False)
    assert indexed_semantics["verification_mode"] == "indexed"
    assert any(
        item["artifact_id"] == archived["artifact_id"]
        and item["metadata_indexed"] is True
        and item["recommendation_audit_hash"] == verification["recommendation_audit_hash"]
        and item["recommendation_readiness_hash"] == verification["recommendation_readiness_hash"]
        and item["recommendation_readiness_status"] in {"pass", "warning"}
        and item["recommendation_readiness_candidate_id"] == design["recommended_candidate"]["candidate_id"]
        and item["request_hash"] == verification["request_hash"]
        and item["qc_report_hash"] == verification["qc_report_hash"]
        and item["report_formats_summary_hash"] == verification["report_formats_summary_hash"]
        and item["candidate_ranking_hash"] == verification["candidate_ranking_hash"]
        and item["candidate_folding_audit_hash"] == verification["candidate_folding_audit_hash"]
        and item["candidate_folding_audit_selection_signal"] in {"secondary_structure_proxy_score", "thermodynamic_risk_score"}
        and item["candidate_folding_audit_validated_backend_count"] >= 0
        and item["recommended_folding_evidence_hash"] == verification["recommended_folding_evidence_hash"]
        and item["recommended_folding_status"] in {"pass", "warning"}
        and item["recommended_folding_candidate_id"] == design["recommended_candidate"]["candidate_id"]
        and item["retrieval_quality_source_count"] >= 1
        and item["retrieval_quality_rank_evidence_count"] >= 1
        and len(item["retrieval_quality_rank_evidence_hash"]) == 64
        and item["objective_count"] >= 1
        and item["data_quality_status"] in {"pass", "warning"}
        and item["optimizer_stress_status"] in {"pass", "warning"}
        and item["request_payload_status"] == "pass"
        and item["request_target_checks"]["modality"] == "pass"
        for item in indexed_semantics["latest_artifacts"]
    )


def test_qc_gate_for_design_checks_candidate_constraints() -> None:
    design = optimize_design(
        "ATGGCTGCTGCTTAA",
        OptimizationConfig(population_size=16, generations=2, max_candidates=2, seed=29),
        {"gene": "DEMO", "species": "human", "modality": "AAV"},
        evidence_used=True,
    )
    gate = qc_gate_for_design(design)
    assert gate["checks"]
    assert gate["status"] in {"pass", "warning", "fail"}


def test_run_store_persists_design() -> None:
    design = optimize_design(
        "ATGGCTGCTGCTGCTTAA",
        OptimizationConfig(population_size=16, generations=2, max_candidates=2, seed=13),
        {"gene": "DEMO", "species": "human", "modality": "AAV"},
        evidence_used=True,
    )
    design["qc_report"] = generate_qc_report(design)
    design["trace"] = [{"name": "test", "status": "ok", "timestamp": design["timestamp"], "detail": {}}]
    summary = save_run(design, run_type="test", request_payload={"gene": "DEMO"})
    stored = get_run(design["run_id"])
    listed = list_runs(limit=5)
    assert summary["run_id"] == design["run_id"]
    assert stored is not None
    assert stored["design"]["run_id"] == design["run_id"]
    assert any(run["run_id"] == design["run_id"] for run in listed["runs"])


def test_job_store_tracks_lifecycle() -> None:
    job = create_job("test_job", {"gene": "DEMO"})
    assert job["status"] == "queued"
    running = mark_job_running(job["job_id"])
    assert running is not None
    assert running["status"] == "running"
    completed = complete_job(job["job_id"], {"run_id": "run_demo"})
    stored = get_job(job["job_id"])
    listed = list_jobs(limit=10)
    assert completed is not None
    assert completed["status"] == "succeeded"
    assert stored is not None
    assert stored["result"]["run_id"] == "run_demo"
    assert any(item["job_id"] == job["job_id"] for item in listed["jobs"])


def test_job_export_bundle_contains_batch_audit_files() -> None:
    design = optimize_design(
        "ATGGCTGCTTAA",
        OptimizationConfig(population_size=16, generations=2, max_candidates=2, seed=37),
        {"gene": "DEMO", "species": "human", "modality": "AAV"},
        evidence_used=True,
    )
    design["qc_report"] = generate_qc_report(design)
    save_run(design, run_type="batch_gene_design_job", request_payload={"gene": "DEMO"})
    job = create_job("batch_gene_design", {"genes": ["DEMO"]})
    completed = complete_job(
        job["job_id"],
        {
            "batch_status": "succeeded",
            "summary": {"requested": 1, "attempted": 1, "succeeded": 1, "failed": 0},
            "runs": [
                {
                    "gene": "DEMO",
                    "run_id": design["run_id"],
                    "recommended_candidate_id": "cand_001",
                    "qc_gate_status": "warning",
                    "warnings": [],
                }
            ],
            "failures": [],
        },
    )
    assert completed is not None
    bundle = build_job_export_bundle(completed)
    with ZipFile(BytesIO(bundle)) as archive:
        names = set(archive.namelist())
        assert "job_manifest.json" in names
        assert "request.json" in names
        assert "result.json" in names
        assert "batch_results.csv" in names
        assert "artifact_manifest.json" in names
        assert f"runs/{design['run_id']}/qc_report.md" in names
        assert f"runs/{design['run_id']}/qc_report.pdf" in names
        assert f"runs/{design['run_id']}/candidate_diagnostics.json" in names
        artifact_manifest = json.loads(archive.read("artifact_manifest.json"))
        assert artifact_manifest["artifact_type"] == "job_audit_bundle"
        assert any(item["path"] == "job_manifest.json" for item in artifact_manifest["files"])
    assert verify_artifact_bundle(bundle)["status"] == "pass"


def test_run_export_bundle_contains_audit_files() -> None:
    design = optimize_design(
        "ATGGCTGCTGCTTAA",
        OptimizationConfig(population_size=16, generations=2, max_candidates=2, seed=23),
        {"gene": "DEMO", "species": "human", "modality": "AAV"},
        evidence_used=True,
    )
    design["qc_report"] = generate_qc_report(design)
    design["trace"] = [{"name": "test", "status": "ok", "timestamp": design["timestamp"], "detail": {}}]
    save_run(design, run_type="test_export", request_payload={"gene": "DEMO"})
    stored = get_run(design["run_id"])
    assert stored is not None
    bundle = build_run_export_bundle(stored)
    with ZipFile(BytesIO(bundle)) as archive:
        names = set(archive.namelist())
        assert "bundle_manifest.json" in names
        assert "artifact_manifest.json" in names
        assert "design.json" in names
        assert "qc_report.md" in names
        assert "qc_report.pdf" in names
        assert "candidate_diagnostics.json" in names
        assert "candidate_ranking.csv" in names
        assert "provenance/structured_manifest.json" in names
        artifact_manifest = json.loads(archive.read("artifact_manifest.json"))
        assert artifact_manifest["artifact_type"] == "run_audit_bundle"
        assert artifact_manifest["manifest_hash"]
        assert any(item["path"] == "qc_report.pdf" and item["sha256"] for item in artifact_manifest["files"])
    assert verify_artifact_bundle(bundle)["status"] == "pass"
    tampered_buffer = BytesIO()
    with ZipFile(BytesIO(bundle)) as source, ZipFile(tampered_buffer, "w") as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == "request.json":
                content = b"{}"
            target.writestr(info.filename, content)
    tampered = verify_artifact_bundle(tampered_buffer.getvalue())
    assert tampered["status"] == "fail"
    assert any("request.json" in error for error in tampered["errors"])
