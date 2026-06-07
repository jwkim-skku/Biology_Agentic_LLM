from __future__ import annotations

import base64
import importlib.util
import random
import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from pypdf import PdfReader

from app.optimizer.codon_table import translate
from app.config import get_settings
from app.optimizer.nsga2 import OptimizationConfig, optimize_cds
from app.optimizer.repair import repair_cds
from app.optimizer.scoring import ScoreConfig, motif_violations, score_sequence
from app.services.data_refresh_service import data_catalog, record_data_baseline_event, refresh_reference_data
from app.services.data_snapshot_service import build_data_snapshot_bundle
from app.services.data_provenance_service import data_provenance_audit
from app.services.data_lock_service import build_data_lockfile, verify_data_lockfile, write_data_lockfile
from app.services.data_release_lock_service import build_data_release_lock, verify_data_release_lock, write_data_release_lock
from app.services.external_data_service import backfill_external_source_snapshots, external_source_coverage, external_source_status
from app.services.audit_log_service import audit_summary, list_audit_events, record_audit_event
from app.services.artifact_archive_service import (
    ARCHIVE_DB_PATH,
    apply_artifact_retention,
    artifact_ledger,
    archive_artifact_bundle,
    archive_summary,
    list_archived_artifacts,
    plan_artifact_retention,
    qc_bundle_archive_semantic_summary,
    structured_import_archive_summary,
    verify_archived_artifact,
    verify_artifact_ledger,
)
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
from app.services.rag_regression_service import evaluate_rag_regression, rag_regression_cases
from app.services.report_service import export_qc_report, generate_qc_report, synthesize_evidence
from app.services.run_export_service import build_run_export_bundle
from app.services.sequence_policy_service import audit_sequence_policy
from app.services.job_store import complete_job, create_job, get_job, list_jobs, mark_job_running
from app.services.metrics_service import metrics_prometheus, metrics_snapshot, record_request
from app.services.optimizer_benchmark_service import evaluate_optimizer_benchmark, optimizer_benchmark_cases
from app.services.optimizer_diagnostics_service import optimizer_diagnostics
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
    assert "Total seconds" in markdown
    assert "structured_import_archive_semantics" in {item["name"] for item in audit["checks"]}
    assert "structured_import_archive_semantics" in audit["evidence"]
    assert verification["status"] in {"pass", "warning"}
    with ZipFile(BytesIO(bundle)) as archive:
        names = set(archive.namelist())
        assert "production_audit.md" in names
        assert "evidence/timings.json" in names
        assert "evidence/structured_import_archive_semantics.json" in names
        bundled_markdown = archive.read("production_audit.md").decode("utf-8")
        assert "## Timing" in bundled_markdown


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


def test_optimizer_benchmark_suite_tracks_quality_and_constraints() -> None:
    cases = optimizer_benchmark_cases()
    result = evaluate_optimizer_benchmark()
    assert cases["case_count"] >= 3
    assert result["status"] in {"pass", "warning"}
    assert result["fail_count"] == 0
    assert result["macro"]["candidate_count"] >= 3
    assert result["macro"]["unique_cds_count"] >= 2
    assert result["macro"]["approx_hypervolume_2d"] > 0
    assert all(item["metrics"]["protein_preservation_failures"] == 0 for item in result["results"])


def test_optimizer_diagnostics_reports_operational_quality_bands() -> None:
    diagnostics = optimizer_diagnostics()
    assert diagnostics["diagnostics_schema"] == "agentic-rag-optimizer-diagnostics-v1"
    assert diagnostics["status"] in {"pass", "warning"}
    assert diagnostics["benchmark"]["fail_count"] == 0
    assert diagnostics["quality_bands"]["candidate_diversity"] in {"pass", "warning", "fail"}
    assert diagnostics["quality_bands"]["constraint_control"] in {"pass", "warning", "fail"}
    assert "maximize_cai" in diagnostics["optimizer"]["objectives"]
    assert diagnostics["recommendations"]


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
    assert design["candidate_diagnostics"]["candidate_count"] == len(design["candidates"])
    assert "constraint_risk_summary" in design["candidate_diagnostics"]
    assert design["candidate_diagnostics"]["pareto_front"]["size"] >= 1
    assert "mean_pairwise_codon_distance" in design["candidate_diagnostics"]["diversity"]
    assert design["validation"]["native"]["status"] in {"pass", "warning"}
    assert design["qc_gate"]["status"] in {"pass", "warning", "fail"}
    assert "provenance" in design


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
    assert evaluation["index"]["retrieval_model"] == "hybrid-hash-bm25-facet-rerank-v2"
    assert evaluation["query_analysis"]["aliases_added"]
    assert all("facet_score" in item for item in evaluation["score_breakdown"])
    assert all(item["rationale"] for item in evaluation["score_breakdown"])
    assert any(item["matched_facets"] for item in evaluation["score_breakdown"])


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
    assert result["macro"]["recall_at_k"] >= 0.9
    assert result["macro"]["source_coverage"] >= 0.9
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
    assert diagnostics["recommendations"]


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
    verification = verify_artifact_bundle(bundle)
    assert verification["status"] == "pass"
    assert verification["checked_files"] == verification["file_count"]


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
    assert "polyadenylation_signal_count" in report["recommended_candidate"]["constraint_status"]
    assert "qc_gate" in report
    assert report["optimizer_reproducibility"]["manifest_schema"] == "agentic-rag-optimizer-reproducibility-v1"
    assert report["project_metadata"]["optimizer_manifest_hash"] == report["optimizer_reproducibility"]["manifest_hash"]
    assert report["optimizer_reproducibility"]["repair_policy"]["enabled"] is True
    assert "maximize_cai" in report["optimizer_reproducibility"]["objective_inventory"]
    markdown = export_qc_report(report, "markdown")
    html = export_qc_report(report, "html")
    json_report = export_qc_report(report, "json")
    pdf_report = export_qc_report(report, "pdf")
    assert "# Gene Therapy Design QC Report" in markdown
    assert "## QC Gate" in markdown
    assert "## Candidate Diagnostics" in markdown
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
    design["qc_report"] = generate_qc_report(design)
    bundle = build_qc_report_bundle(design, {"cds": "ATGGCTGCTGCTGCTTAA"}, bundle_type="cds_report")
    with ZipFile(BytesIO(bundle)) as archive:
        names = set(archive.namelist())
        assert "artifact_manifest.json" in names
        assert "bundle_manifest.json" in names
        assert "qc_report.json" in names
        assert "qc_report.md" in names
        assert "qc_report.html" in names
        assert "qc_report.pdf" in names
        assert "optimizer_reproducibility.json" in names
        assert "candidate_ranking.csv" in names
        optimizer_manifest = json.loads(archive.read("optimizer_reproducibility.json"))
        assert optimizer_manifest["manifest_schema"] == "agentic-rag-optimizer-reproducibility-v1"
        assert optimizer_manifest["manifest_hash"]
        artifact_manifest = json.loads(archive.read("artifact_manifest.json"))
        assert artifact_manifest["artifact_type"] == "qc_report_bundle"
        assert any(item["path"] == "qc_report.pdf" and item["sha256"] for item in artifact_manifest["files"])
        candidate_csv = archive.read("candidate_ranking.csv").decode("utf-8")
        assert "constraint_risk_status" in candidate_csv
        assert "selection_trace" in candidate_csv
    verification = verify_qc_report_bundle(bundle)
    assert verification["status"] == "pass"
    assert verification["semantic_status"] == "pass"
    assert verification["optimizer_manifest_hash"] == optimizer_manifest["manifest_hash"]
    assert verification["semantic_checks"]["optimizer_hash_report"] == "pass"
    assert verification["semantic_checks"]["recommended_candidate_in_csv"] == "pass"
    assert verification["semantic_checks"]["candidate_csv_explainability_columns"] == "pass"
    assert verification["semantic_checks"]["recommended_constraint_risk_csv"] == "pass"
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
    archived_verification = verify_archived_artifact(archived["artifact_id"])
    assert archived_verification["status"] == "pass"
    assert archived_verification["bundle_verification"]["semantic_status"] == "pass"
    assert archived_verification["bundle_verification"]["optimizer_manifest_hash"] == optimizer_manifest["manifest_hash"]
    archive_semantics = qc_bundle_archive_semantic_summary(limit=5)
    assert archive_semantics["status"] in {"pass", "warning"}
    assert archive_semantics["checked_count"] >= 1
    assert archive_semantics["semantic_pass_count"] >= 1
    assert any(item["artifact_id"] == archived["artifact_id"] for item in archive_semantics["latest_artifacts"])
    indexed_semantics = qc_bundle_archive_semantic_summary(limit=5, verify_files=False)
    assert indexed_semantics["verification_mode"] == "indexed"
    assert any(
        item["artifact_id"] == archived["artifact_id"] and item["metadata_indexed"] is True
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
