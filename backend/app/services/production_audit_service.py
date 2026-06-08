from __future__ import annotations

import json
from copy import deepcopy
from time import perf_counter
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from threading import Lock
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

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
from app.services.artifact_object_store_service import artifact_object_store_status
from app.services.audit_log_service import audit_summary
from app.services.data_provenance_service import data_provenance_audit
from app.services.data_release_bundle_service import build_data_release_bundle, verify_data_release_bundle
from app.services.deployment_readiness_service import deployment_readiness
from app.services.export_manifest_service import ManifestedZip, verify_artifact_bundle
from app.services.external_data_service import external_source_status
from app.services.governance_service import build_governance_attestation_bundle, verify_governance_attestation_bundle
from app.services.optimizer_diagnostics_service import optimizer_diagnostics
from app.services.rag_diagnostics_service import rag_diagnostics
from app.services.rag_embedding_service import rag_embedding_status
from app.services.rna_folding_service import rna_folding_status
from app.services.signature_service import signatures_for_hash, signing_status, verify_payload_signatures
from app.services.storage_service import storage_status
from app.services.structured_quality_service import structured_quality_gate
from app.services.workflow_trace_bundle_service import workflow_runtime_status


PRODUCTION_AUDIT_CACHE_TTL_SECONDS = 300
_AUDIT_CACHE_LOCK = Lock()
_AUDIT_BUILD_LOCK = Lock()
_AUDIT_CACHE: dict[str, Any] | None = None


def build_production_audit(openapi_spec: dict[str, Any] | None = None, *, refresh: bool = False) -> dict[str, Any]:
    cache_key = _cache_key(openapi_spec or {})
    if not refresh:
        cached = _cached_audit(cache_key)
        if cached is not None:
            return cached
    with _AUDIT_BUILD_LOCK:
        if not refresh:
            cached = _cached_audit(cache_key)
            if cached is not None:
                return cached
        audit = _build_production_audit_uncached(openapi_spec or {}, cache_key=cache_key)
        _store_cached_audit(cache_key, audit)
        return deepcopy(audit)


def production_audit_cache_status() -> dict[str, Any]:
    with _AUDIT_CACHE_LOCK:
        if not _AUDIT_CACHE:
            return {
                "cache_schema": "agentic-rag-production-audit-cache-v1",
                "status": "empty",
                "ttl_seconds": PRODUCTION_AUDIT_CACHE_TTL_SECONDS,
            }
        age_seconds = round(perf_counter() - float(_AUDIT_CACHE["stored_monotonic"]), 3)
        audit = _AUDIT_CACHE["audit"]
        return {
            "cache_schema": "agentic-rag-production-audit-cache-v1",
            "status": "hit" if age_seconds <= PRODUCTION_AUDIT_CACHE_TTL_SECONDS else "expired",
            "ttl_seconds": PRODUCTION_AUDIT_CACHE_TTL_SECONDS,
            "age_seconds": age_seconds,
            "audit_hash": audit.get("audit_hash"),
            "generated_at": audit.get("generated_at"),
            "summary_status": (audit.get("summary") or {}).get("status"),
        }


def _build_production_audit_uncached(openapi_spec: dict[str, Any], *, cache_key: str) -> dict[str, Any]:
    settings = get_settings()
    timings: dict[str, Any] = {"items": []}
    start = perf_counter()
    readiness = _timed("deployment_readiness", timings, lambda: deployment_readiness(openapi_spec))
    storage = _timed("storage", timings, storage_status)
    provenance = _timed("data_provenance", timings, data_provenance_audit)
    structured_quality = _timed("structured_quality", timings, structured_quality_gate)
    data_release_bundle = _timed("data_release_bundle", timings, lambda: verify_data_release_bundle(build_data_release_bundle()))
    rag = _timed("rag_diagnostics", timings, rag_diagnostics)
    embedding = _timed("rag_embedding", timings, rag_embedding_status)
    optimizer = _timed("optimizer_diagnostics", timings, optimizer_diagnostics)
    folding = _timed("rna_folding", timings, rna_folding_status)
    workflow_runtime = _timed("workflow_runtime", timings, workflow_runtime_status)
    memory = _timed("agent_memory", timings, agent_memory_summary)
    governance = _timed(
        "governance_attestation",
        timings,
        lambda: verify_governance_attestation_bundle(build_governance_attestation_bundle(openapi_spec or {})),
    )
    ledger = _timed("artifact_ledger", timings, verify_artifact_ledger)
    archive = _timed("artifact_archive", timings, archive_summary)
    object_store = _timed("artifact_object_store", timings, artifact_object_store_status)
    qc_archive = _timed("qc_bundle_archive_semantics", timings, lambda: qc_bundle_archive_semantic_summary(limit=3, verify_files=False))
    data_refresh_plan_archive = _timed(
        "data_refresh_plan_archive_semantics",
        timings,
        lambda: data_refresh_plan_archive_summary(limit=3, verify_files=False),
    )
    data_release_archive = _timed(
        "data_release_archive_semantics",
        timings,
        lambda: data_release_archive_summary(limit=3, verify_files=False),
    )
    data_snapshot_archive = _timed(
        "data_snapshot_archive_semantics",
        timings,
        lambda: data_snapshot_archive_summary(limit=3, verify_files=False),
    )
    import_archive = _timed(
        "structured_import_archive_semantics",
        timings,
        lambda: structured_import_archive_summary(limit=3, verify_files=False),
    )
    rag_archive = _timed("rag_evaluation_archive_semantics", timings, lambda: rag_evaluation_archive_summary(limit=3, verify_files=False))
    rag_regression_archive = _timed(
        "rag_regression_archive_semantics",
        timings,
        lambda: rag_regression_archive_summary(limit=3, verify_files=False),
    )
    rag_vector_index_archive = _timed(
        "rag_vector_index_archive_semantics",
        timings,
        lambda: rag_vector_index_archive_summary(limit=3, verify_files=False),
    )
    optimizer_archive = _timed(
        "optimizer_benchmark_archive_semantics",
        timings,
        lambda: optimizer_benchmark_archive_summary(limit=3, verify_files=False),
    )
    workflow_trace_archive = _timed(
        "workflow_trace_archive_semantics",
        timings,
        lambda: workflow_trace_archive_summary(limit=3, verify_files=False),
    )
    security = _timed("security", timings, _security_summary)
    external_sources = _timed("external_sources", timings, external_source_status)
    audit_log = _timed("audit_log", timings, audit_summary)
    timings["total_seconds"] = round(perf_counter() - start, 3)
    timings["slowest"] = sorted(timings["items"], key=lambda item: item["duration_seconds"], reverse=True)[:5]

    checks = [
        _check("deployment_readiness", readiness.get("deployment_ready") is True, readiness.get("production_ready") is True, readiness),
        _check(
            "security",
            security["rate_limit_per_minute"] >= 0,
            bool(security["auth_enabled"] and security["rbac_enabled"] and security["rate_limit_per_minute"] > 0),
            security,
        ),
        _check("storage", storage.get("status") in {"pass", "warning", "ready"}, storage.get("active_runtime_adapter") == "postgres", storage),
        _check("data_provenance", provenance.get("status") in {"pass", "warning"}, provenance.get("status") == "pass", provenance),
        _check(
            "structured_quality",
            structured_quality.get("status") in {"pass", "warning"},
            structured_quality.get("status") == "pass",
            structured_quality,
        ),
        _check(
            "data_release_bundle",
            data_release_bundle.get("status") in {"pass", "warning"}
            and data_release_bundle.get("semantic_status") in {"pass", "warning"},
            data_release_bundle.get("status") == "pass"
            and data_release_bundle.get("semantic_status") == "pass"
            and data_release_bundle.get("promotion_status") == "pass",
            data_release_bundle,
        ),
        _check("external_source_coverage", _external_coverage_ok(external_sources), True, external_sources),
        _check("rag_diagnostics", rag.get("status") in {"pass", "warning"}, rag.get("status") == "pass", rag),
        _check("rag_embedding_backend", embedding.get("status") in {"pass", "warning"}, embedding.get("production_ready") is True, embedding),
        _check("optimizer_diagnostics", optimizer.get("status") in {"pass", "warning"}, optimizer.get("status") == "pass", optimizer),
        _check("rna_folding_backend", folding.get("status") in {"ready", "proxy", "fallback"}, folding.get("production_ready") is True, folding),
        _check("workflow_runtime", workflow_runtime.get("status") == "pass", workflow_runtime.get("status") == "pass", workflow_runtime),
        _check("agent_memory", memory.get("status") in {"pass", "warning"}, memory.get("memory_count", 0) > 0, memory),
        _check("governance_attestation", governance.get("status") in {"pass", "warning"}, governance.get("status") == "pass", governance),
        _check("artifact_ledger", ledger.get("status") in {"pass", "warning"}, ledger.get("status") == "pass", ledger),
        _check("artifact_archive", archive.get("total_artifacts", 0) >= 0, ledger.get("status") == "pass", archive),
        _check(
            "artifact_object_store",
            object_store.get("status") in {"disabled", "ready"},
            object_store.get("status") == "ready",
            object_store,
        ),
        _check("qc_bundle_archive_semantics", qc_archive.get("status") in {"pass", "warning"}, qc_archive.get("status") == "pass", qc_archive),
        _check(
            "data_refresh_plan_archive_semantics",
            data_refresh_plan_archive.get("status") in {"pass", "warning"},
            data_refresh_plan_archive.get("status") == "pass",
            data_refresh_plan_archive,
        ),
        _check(
            "data_release_archive_semantics",
            data_release_archive.get("status") in {"pass", "warning"},
            data_release_archive.get("status") == "pass",
            data_release_archive,
        ),
        _check(
            "data_snapshot_archive_semantics",
            data_snapshot_archive.get("status") in {"pass", "warning"},
            data_snapshot_archive.get("status") == "pass",
            data_snapshot_archive,
        ),
        _check(
            "structured_import_archive_semantics",
            import_archive.get("status") in {"pass", "warning"},
            import_archive.get("status") == "pass",
            import_archive,
        ),
        _check(
            "rag_evaluation_archive_semantics",
            rag_archive.get("status") in {"pass", "warning"},
            rag_archive.get("status") == "pass",
            rag_archive,
        ),
        _check(
            "rag_regression_archive_semantics",
            rag_regression_archive.get("status") in {"pass", "warning"},
            rag_regression_archive.get("status") == "pass",
            rag_regression_archive,
        ),
        _check(
            "rag_vector_index_archive_semantics",
            rag_vector_index_archive.get("status") in {"pass", "warning"},
            rag_vector_index_archive.get("status") == "pass",
            rag_vector_index_archive,
        ),
        _check(
            "optimizer_benchmark_archive_semantics",
            optimizer_archive.get("status") in {"pass", "warning"},
            optimizer_archive.get("status") == "pass",
            optimizer_archive,
        ),
        _check(
            "workflow_trace_archive_semantics",
            workflow_trace_archive.get("status") in {"pass", "warning"},
            workflow_trace_archive.get("status") == "pass",
            workflow_trace_archive,
        ),
    ]
    summary = _summary(checks)
    promotion_summary = _promotion_summary(
        summary=summary,
        readiness=readiness,
        structured_quality=structured_quality,
        data_release_bundle=data_release_bundle,
        rag_embedding=embedding,
        optimizer=optimizer,
        folding=folding,
        storage=storage,
        security=security,
        signing=signing_status(),
        object_store=object_store,
    )
    production_gap_summary = _production_gap_summary(checks, promotion_summary, readiness)
    payload = {
        "audit_schema": "agentic-rag-production-audit-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "production promotion evidence for the Agentic RAG codon optimization platform",
        "cache_policy": {
            "cache_schema": "agentic-rag-production-audit-cache-v1",
            "cache_key": cache_key,
            "ttl_seconds": PRODUCTION_AUDIT_CACHE_TTL_SECONDS,
            "force_refresh_query": "refresh=true",
        },
        "runtime": {
            "data_dir": str(settings.data_dir),
            "storage_backend": settings.storage_backend,
            "database_url_configured": bool(settings.database_url),
            "auth_enabled": settings.auth_enabled,
            "rbac_enabled": bool(settings.api_key_roles),
            "rate_limit_per_minute": settings.rate_limit_per_minute,
            "signing": signing_status(),
        },
        "summary": summary,
        "promotion_summary": promotion_summary,
        "production_gap_summary": production_gap_summary,
        "checks": checks,
        "evidence": {
            "deployment_readiness": readiness,
            "promotion_summary": promotion_summary,
            "production_gap_summary": production_gap_summary,
            "security": security,
            "storage": storage,
            "data_provenance": provenance,
            "structured_quality": structured_quality,
            "data_release_bundle": data_release_bundle,
            "external_sources": external_sources,
            "rag_diagnostics": rag,
            "rag_embedding": embedding,
            "optimizer_diagnostics": optimizer,
            "rna_folding": folding,
            "workflow_runtime": workflow_runtime,
            "agent_memory": memory,
            "governance_attestation_verification": governance,
            "artifact_ledger": ledger,
            "artifact_archive": archive,
            "artifact_object_store": object_store,
            "qc_bundle_archive_semantics": qc_archive,
            "data_refresh_plan_archive_semantics": data_refresh_plan_archive,
            "data_release_archive_semantics": data_release_archive,
            "data_snapshot_archive_semantics": data_snapshot_archive,
            "structured_import_archive_semantics": import_archive,
            "rag_evaluation_archive_semantics": rag_archive,
            "rag_regression_archive_semantics": rag_regression_archive,
            "rag_vector_index_archive_semantics": rag_vector_index_archive,
            "optimizer_benchmark_archive_semantics": optimizer_archive,
            "workflow_trace_archive_semantics": workflow_trace_archive,
            "audit_log": audit_log,
            "timings": timings,
        },
        "operator_notes": _operator_notes(summary),
    }
    payload["evidence_hashes"] = _evidence_hashes(payload["evidence"])
    payload["audit_hash"] = _hash_without_signatures(payload)
    signatures = signatures_for_hash(payload["audit_hash"], signed_field="audit_hash")
    if signatures:
        payload["audit_signatures"] = signatures
        hmac_signature = next((item for item in signatures if item.get("algorithm") == "HMAC-SHA256"), None)
        if hmac_signature:
            payload["audit_signature"] = hmac_signature
    return payload


def build_production_audit_bundle(openapi_spec: dict[str, Any] | None = None, *, refresh: bool = False) -> bytes:
    audit = build_production_audit(openapi_spec, refresh=refresh)
    buffer = BytesIO()
    metadata = {
        "audit_hash": audit["audit_hash"],
        "generated_at": audit["generated_at"],
        "status": audit["summary"]["status"],
    }
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        bundle = ManifestedZip(archive, "production_audit_bundle", metadata)
        bundle.writestr("production_audit.json", _json(audit))
        bundle.writestr("production_audit.md", render_production_audit_markdown(audit))
        bundle.writestr("evidence_hashes.json", _json(audit["evidence_hashes"]))
        bundle.writestr("evidence/deployment_readiness.json", _json(audit["evidence"]["deployment_readiness"]))
        bundle.writestr("evidence/promotion_summary.json", _json(audit["evidence"]["promotion_summary"]))
        bundle.writestr("evidence/production_gap_summary.json", _json(audit["evidence"]["production_gap_summary"]))
        bundle.writestr("evidence/security.json", _json(audit["evidence"]["security"]))
        bundle.writestr("evidence/storage.json", _json(audit["evidence"]["storage"]))
        bundle.writestr("evidence/data_provenance.json", _json(audit["evidence"]["data_provenance"]))
        bundle.writestr("evidence/structured_quality.json", _json(audit["evidence"]["structured_quality"]))
        bundle.writestr("evidence/data_release_bundle.json", _json(audit["evidence"]["data_release_bundle"]))
        bundle.writestr("evidence/external_sources.json", _json(audit["evidence"]["external_sources"]))
        bundle.writestr("evidence/rag_diagnostics.json", _json(audit["evidence"]["rag_diagnostics"]))
        bundle.writestr("evidence/rag_embedding.json", _json(audit["evidence"]["rag_embedding"]))
        bundle.writestr("evidence/optimizer_diagnostics.json", _json(audit["evidence"]["optimizer_diagnostics"]))
        bundle.writestr("evidence/rna_folding.json", _json(audit["evidence"]["rna_folding"]))
        bundle.writestr("evidence/workflow_runtime.json", _json(audit["evidence"]["workflow_runtime"]))
        bundle.writestr("evidence/agent_memory.json", _json(audit["evidence"]["agent_memory"]))
        bundle.writestr("evidence/governance_attestation_verification.json", _json(audit["evidence"]["governance_attestation_verification"]))
        bundle.writestr("evidence/artifact_ledger.json", _json(audit["evidence"]["artifact_ledger"]))
        bundle.writestr("evidence/artifact_archive.json", _json(audit["evidence"]["artifact_archive"]))
        bundle.writestr("evidence/artifact_object_store.json", _json(audit["evidence"]["artifact_object_store"]))
        bundle.writestr("evidence/qc_bundle_archive_semantics.json", _json(audit["evidence"]["qc_bundle_archive_semantics"]))
        bundle.writestr("evidence/data_refresh_plan_archive_semantics.json", _json(audit["evidence"]["data_refresh_plan_archive_semantics"]))
        bundle.writestr("evidence/data_release_archive_semantics.json", _json(audit["evidence"]["data_release_archive_semantics"]))
        bundle.writestr("evidence/data_snapshot_archive_semantics.json", _json(audit["evidence"]["data_snapshot_archive_semantics"]))
        bundle.writestr(
            "evidence/structured_import_archive_semantics.json",
            _json(audit["evidence"]["structured_import_archive_semantics"]),
        )
        bundle.writestr("evidence/rag_evaluation_archive_semantics.json", _json(audit["evidence"]["rag_evaluation_archive_semantics"]))
        bundle.writestr("evidence/rag_regression_archive_semantics.json", _json(audit["evidence"]["rag_regression_archive_semantics"]))
        bundle.writestr("evidence/rag_vector_index_archive_semantics.json", _json(audit["evidence"]["rag_vector_index_archive_semantics"]))
        bundle.writestr(
            "evidence/optimizer_benchmark_archive_semantics.json",
            _json(audit["evidence"]["optimizer_benchmark_archive_semantics"]),
        )
        bundle.writestr("evidence/workflow_trace_archive_semantics.json", _json(audit["evidence"]["workflow_trace_archive_semantics"]))
        bundle.writestr("evidence/audit_log.json", _json(audit["evidence"]["audit_log"]))
        bundle.writestr("evidence/timings.json", _json(audit["evidence"]["timings"]))
        bundle.write_artifact_manifest()
    return buffer.getvalue()


def verify_production_audit_bundle(bundle: bytes) -> dict[str, Any]:
    verification = verify_artifact_bundle(bundle)
    errors = list(verification.get("errors") or [])
    warnings = list(verification.get("warnings") or [])
    audit_hash = None
    summary: dict[str, Any] = {}
    semantic_checks: dict[str, str] = {}
    try:
        with ZipFile(BytesIO(bundle), "r") as archive:
            audit = json.loads(archive.read("production_audit.json").decode("utf-8"))
            deployment_readiness = json.loads(archive.read("evidence/deployment_readiness.json").decode("utf-8"))
            workflow_trace_archive = json.loads(archive.read("evidence/workflow_trace_archive_semantics.json").decode("utf-8"))
            evidence_hashes = json.loads(archive.read("evidence_hashes.json").decode("utf-8"))
            audit_hash = audit.get("audit_hash")
            summary = audit.get("summary") or {}
            evidence = audit.get("evidence") or {}
            actual_hash = _hash_without_signatures(audit)
            if audit_hash != actual_hash:
                errors.append("production_audit.json audit_hash does not match contents.")
                semantic_checks["audit_hash"] = "fail"
            else:
                semantic_checks["audit_hash"] = "pass"
            _record_semantic_check(
                semantic_checks,
                errors,
                "summary_recomputed",
                summary == _summary(audit.get("checks") or []),
                "production_audit.json summary does not match recomputed check summary.",
            )
            check_detail_mismatches = _check_detail_hash_mismatches(audit.get("checks") or [], evidence)
            _record_semantic_check(
                semantic_checks,
                errors,
                "check_detail_hashes",
                not check_detail_mismatches,
                "production audit check detail_hash values do not match embedded evidence: "
                + ", ".join(check_detail_mismatches[:8]),
            )
            _record_semantic_check(
                semantic_checks,
                errors,
                "deployment_readiness_evidence",
                deployment_readiness == (evidence.get("deployment_readiness") or {}),
                "evidence/deployment_readiness.json does not match production_audit.json evidence.",
            )
            _record_semantic_check(
                semantic_checks,
                errors,
                "promotion_summary_evidence",
                audit.get("promotion_summary") == (evidence.get("promotion_summary") or {}),
                "production_audit.json promotion_summary does not match embedded promotion_summary evidence.",
            )
            recomputed_promotion = _promotion_summary_from_audit(audit)
            _record_semantic_check(
                semantic_checks,
                errors,
                "promotion_summary_recomputed",
                audit.get("promotion_summary") == recomputed_promotion,
                "production_audit.json promotion_summary does not match recomputed promotion evidence.",
            )
            _record_semantic_check(
                semantic_checks,
                errors,
                "workflow_trace_archive_evidence",
                workflow_trace_archive == (evidence.get("workflow_trace_archive_semantics") or {}),
                "evidence/workflow_trace_archive_semantics.json does not match production_audit.json evidence.",
            )
            _record_semantic_check(
                semantic_checks,
                errors,
                "evidence_hashes_payload",
                evidence_hashes == (audit.get("evidence_hashes") or {}),
                "evidence_hashes.json does not match production_audit.json evidence_hashes.",
            )
            _verify_evidence_hashes(archive, evidence, evidence_hashes, semantic_checks, errors)
            latest_trace = (workflow_trace_archive.get("latest_artifacts") or [{}])[0]
            checked_traces = int(workflow_trace_archive.get("checked_count") or 0)
            _record_semantic_check(
                semantic_checks,
                errors,
                "workflow_trace_archive_hash",
                checked_traces == 0 or bool(latest_trace.get("trace_hash")),
                "Latest workflow trace archive evidence is missing trace_hash.",
            )
            _record_semantic_check(
                semantic_checks,
                errors,
                "workflow_trace_archive_steps",
                checked_traces == 0 or int(latest_trace.get("trace_step_count") or 0) > 0,
                "Latest workflow trace archive evidence is missing trace_step_count.",
            )
            _record_semantic_check(
                semantic_checks,
                errors,
                "attention_gates_hash",
                deployment_readiness.get("attention_gates_hash") == _hash_payload(deployment_readiness.get("attention_gates") or []),
                "deployment readiness attention_gates_hash does not match attention_gates.",
            )
            _record_semantic_check(
                semantic_checks,
                errors,
                "required_actions_hash",
                deployment_readiness.get("required_actions_hash") == _hash_payload(deployment_readiness.get("required_actions") or []),
                "deployment readiness required_actions_hash does not match required_actions.",
            )
            action_detail_mismatches = _required_action_detail_hash_mismatches(deployment_readiness)
            _record_semantic_check(
                semantic_checks,
                errors,
                "required_action_detail_hashes",
                not action_detail_mismatches,
                "deployment readiness required action detail_hash values do not match gate details: "
                + ", ".join(action_detail_mismatches[:8]),
            )
            signature_result = _verify_audit_signature(audit)
            if signature_result["status"] == "fail":
                errors.extend(signature_result["messages"])
            elif signature_result["status"] == "warning":
                warnings.extend(signature_result["messages"])
    except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        errors.append(f"Invalid production audit payload: {exc}")
        signature_result = {"status": "not_checked", "messages": []}
    return {
        "status": "fail" if errors else "warning" if warnings else verification.get("status", "pass"),
        "errors": errors,
        "warnings": warnings,
        "audit_hash": audit_hash,
        "summary": summary,
        "semantic_checks": semantic_checks,
        "artifact_verification": verification,
        "signature": signature_result,
    }


def render_production_audit_markdown(audit: dict[str, Any]) -> str:
    promotion = audit.get("promotion_summary") or {}
    lines = [
        "# Production Audit Report",
        "",
        f"- Generated at: `{audit['generated_at']}`",
        f"- Audit hash: `{audit['audit_hash']}`",
        f"- Status: `{audit['summary']['status']}`",
        f"- Deployment ready: `{audit['summary']['deployment_ready']}`",
        f"- Production ready: `{audit['summary']['production_ready']}`",
        f"- Promotion summary: `{promotion.get('status', 'n/a')}`",
        f"- Remaining production actions: `{len(promotion.get('required_actions') or [])}`",
        f"- Production gaps: `{((audit.get('production_gap_summary') or {}).get('gap_count', 'n/a'))}`",
        f"- Readiness action hash: `{((audit.get('evidence') or {}).get('deployment_readiness') or {}).get('required_actions_hash', 'n/a')}`",
        "",
        "## Promotion Summary",
        "",
        "| Area | Status | Detail | Action |",
        "| --- | --- | --- | --- |",
    ]
    for item in promotion.get("items") or []:
        lines.append(
            "| {area} | {status} | {detail} | {action} |".format(
                area=_escape_cell(str(item.get("area") or "unknown")),
                status=_escape_cell(str(item.get("status") or "unknown")),
                detail=_escape_cell(str(item.get("detail") or "")),
                action=_escape_cell(str(item.get("action") or "")),
            )
        )
    if promotion.get("required_actions"):
        lines.extend(["", "Required actions:"])
        for action in promotion.get("required_actions") or []:
            lines.append(f"- {action}")
    gap_summary = audit.get("production_gap_summary") or {}
    lines.extend(
        [
            "",
            "## Production Gaps",
            "",
            "| Area | Priority | Status | Action |",
            "| --- | --- | --- | --- |",
        ]
    )
    for item in gap_summary.get("gaps") or []:
        lines.append(
            "| {area} | {priority} | {status} | {action} |".format(
                area=_escape_cell(str(item.get("area") or "unknown")),
                priority=_escape_cell(str(item.get("priority") or "unknown")),
                status=_escape_cell(str(item.get("status") or "unknown")),
                action=_escape_cell(str(item.get("action") or "")),
            )
        )
    lines.extend(
        [
            "",
        "## Checks",
        "",
        "| Check | Status | Blocking | Message |",
        "| --- | --- | --- | --- |",
        ]
    )
    for check in audit["checks"]:
        lines.append(
            "| {name} | {status} | {blocking} | {message} |".format(
                name=check["name"],
                status=check["status"],
                blocking=str(check["blocking"]).lower(),
                message=_escape_cell(check["message"]),
            )
        )
    timings = (audit.get("evidence") or {}).get("timings") or {}
    lines.extend(
        [
            "",
            "## Timing",
            "",
            f"- Total seconds: `{timings.get('total_seconds', 'n/a')}`",
            "",
            "| Evidence | Seconds |",
            "| --- | --- |",
        ]
    )
    for item in timings.get("slowest") or []:
        lines.append(
            "| {name} | {seconds} |".format(
                name=_escape_cell(str(item.get("name") or "unknown")),
                seconds=item.get("duration_seconds", "n/a"),
            )
        )
    lines.extend(["", "## Operator Notes", ""])
    for note in audit["operator_notes"]:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def _security_summary() -> dict[str, Any]:
    settings = get_settings()
    return {
        "auth_enabled": settings.auth_enabled,
        "rbac_enabled": bool(settings.api_key_roles),
        "configured_keys": len(settings.api_keys),
        "configured_role_bindings": len(settings.api_key_roles),
        "rate_limit_per_minute": settings.rate_limit_per_minute,
        "artifact_signing_enabled": settings.artifact_signing_enabled,
        "artifact_asymmetric_signing_enabled": settings.artifact_asymmetric_signing_enabled,
        "artifact_asymmetric_verification_enabled": settings.artifact_asymmetric_verification_enabled,
        "signing": signing_status(),
    }


def _promotion_summary(
    *,
    summary: dict[str, Any],
    readiness: dict[str, Any],
    structured_quality: dict[str, Any],
    data_release_bundle: dict[str, Any],
    rag_embedding: dict[str, Any],
    optimizer: dict[str, Any],
    folding: dict[str, Any],
    storage: dict[str, Any],
    security: dict[str, Any],
    signing: dict[str, Any],
    object_store: dict[str, Any],
) -> dict[str, Any]:
    optimizer_seed_strategy = (
        (optimizer.get("optimizer") or {})
        .get("search_strategy", {})
        .get("seed_strategy")
    )
    items = [
        _promotion_item(
            "structured_data",
            structured_quality.get("status"),
            f"{structured_quality.get('record_count')} records; live fraction {(structured_quality.get('coverage') or {}).get('live_record_fraction')}",
            "; ".join((structured_quality.get("operator_actions") or [])[:2]),
        ),
        _promotion_item(
            "data_release_bundle",
            data_release_bundle.get("promotion_status"),
            f"semantic {data_release_bundle.get('semantic_status')}; snapshots {data_release_bundle.get('external_snapshot_file_count')}/{data_release_bundle.get('external_snapshot_reference_count')}",
            "Resolve seed/local prior warnings before final production interpretation."
            if data_release_bundle.get("promotion_status") != "pass"
            else "Release evidence bundle is promotion-clean.",
        ),
        _promotion_item(
            "rag_embedding",
            "pass" if rag_embedding.get("production_ready") else rag_embedding.get("status"),
            f"{rag_embedding.get('active_backend')} / {rag_embedding.get('embedding_model')}",
            "Pin and load a local biomedical sentence-transformers model."
            if not rag_embedding.get("production_ready")
            else "Embedding backend is production configured.",
        ),
        _promotion_item(
            "optimizer",
            optimizer.get("status"),
            f"benchmark {((optimizer.get('benchmark') or {}).get('status'))}; seed strategy {optimizer_seed_strategy or 'deterministic-tradeoff-seeds-v1'}",
            "; ".join((optimizer.get("recommendations") or [])[:2]) or "Optimizer diagnostics are clean.",
        ),
        _promotion_item(
            "rna_folding",
            "pass" if folding.get("production_ready") else folding.get("status"),
            f"{folding.get('active_backend')} / requested {folding.get('requested_backend')}",
            "Install ViennaRNA RNAfold and set RNA_FOLDING_BACKEND=rnafold."
            if not folding.get("production_ready")
            else "RNAfold backend is production configured.",
        ),
        _promotion_item(
            "storage",
            "pass" if storage.get("active_runtime_adapter") == "postgres" and storage.get("database_url_configured") else storage.get("status"),
            f"{storage.get('active_runtime_adapter')} / target {storage.get('target_backend')}",
            "Use STORAGE_BACKEND=postgres with DATABASE_URL for multi-user production."
            if storage.get("active_runtime_adapter") != "postgres"
            else "Runtime storage is on Postgres.",
        ),
        _promotion_item(
            "security",
            "pass" if security.get("auth_enabled") and security.get("rbac_enabled") and security.get("rate_limit_per_minute", 0) > 0 else "warning",
            f"auth {security.get('auth_enabled')}; roles {security.get('configured_role_bindings')}; rate {security.get('rate_limit_per_minute')}",
            "Enable API_KEYS, explicit API_KEY_ROLES, and positive RATE_LIMIT_PER_MINUTE."
            if not (security.get("auth_enabled") and security.get("rbac_enabled") and security.get("rate_limit_per_minute", 0) > 0)
            else "Auth, RBAC, and rate limiting are configured.",
        ),
        _promotion_item(
            "artifact_signing",
            "pass" if (signing.get("hmac") or {}).get("signing_enabled") or (signing.get("ed25519") or {}).get("signing_enabled") else "warning",
            f"hmac {(signing.get('hmac') or {}).get('signing_enabled')}; ed25519 {(signing.get('ed25519') or {}).get('signing_enabled')}",
            "Configure HMAC or Ed25519 artifact signing."
            if not ((signing.get("hmac") or {}).get("signing_enabled") or (signing.get("ed25519") or {}).get("signing_enabled"))
            else "Artifact signing is enabled.",
        ),
        _promotion_item(
            "artifact_object_store",
            "pass" if object_store.get("status") == "ready" else object_store.get("status"),
            f"enabled {object_store.get('enabled')}; bucket {object_store.get('bucket') or 'n/a'}",
            object_store.get("recommendation") or "Configure managed object-store mirroring for immutable retention.",
        ),
    ]
    required_actions = [item["action"] for item in items if item["status"] != "pass" and item.get("action")]
    return {
        "summary_schema": "agentic-rag-production-promotion-summary-v1",
        "status": summary.get("status"),
        "deployment_ready": readiness.get("deployment_ready"),
        "production_ready": readiness.get("production_ready"),
        "warning_checks": summary.get("warning_checks", []),
        "blocking_checks": summary.get("blocking_checks", []),
        "items": items,
        "required_actions": required_actions,
    }


def _promotion_item(area: str, status: Any, detail: str, action: str) -> dict[str, Any]:
    normalized = str(status or "warning")
    if normalized in {"ready", "current", "verified"}:
        normalized = "pass"
    if normalized in {"disabled", "proxy", "fallback", "missing", "unsigned"}:
        normalized = "warning"
    return {"area": area, "status": normalized, "detail": detail, "action": action}


def _production_gap_summary(checks: list[dict[str, Any]], promotion_summary: dict[str, Any], readiness: dict[str, Any]) -> dict[str, Any]:
    readiness_actions = {
        str(action.get("gate") or ""): action
        for action in readiness.get("required_actions") or []
        if isinstance(action, dict)
    }
    promotion_items = {
        str(item.get("area") or ""): item
        for item in promotion_summary.get("items") or []
        if isinstance(item, dict)
    }
    gaps: list[dict[str, Any]] = []
    for check in checks:
        if check.get("status") == "pass":
            continue
        name = str(check.get("name") or "unknown")
        readiness_action = readiness_actions.get(name)
        promotion_item = promotion_items.get(name)
        action = (
            (readiness_action or {}).get("action")
            or (promotion_item or {}).get("action")
            or check.get("message")
            or "Review this production audit check before promotion."
        )
        evidence_key = _evidence_key_for_check(name)
        gap = {
            "area": name,
            "status": check.get("status"),
            "priority": "blocking" if check.get("blocking") else "promotion",
            "evidence_key": evidence_key,
            "check_detail_hash": check.get("detail_hash"),
            "readiness_detail_hash": (readiness_action or {}).get("detail_hash"),
            "action": action,
        }
        gap["gap_hash"] = _hash_payload(gap)
        gaps.append(gap)
    summary = {
        "gap_schema": "agentic-rag-production-gap-summary-v1",
        "status": "fail" if any(gap["priority"] == "blocking" for gap in gaps) else "warning" if gaps else "pass",
        "gap_count": len(gaps),
        "blocking_count": sum(1 for gap in gaps if gap["priority"] == "blocking"),
        "promotion_count": sum(1 for gap in gaps if gap["priority"] == "promotion"),
        "evidence_keys": sorted({str(gap["evidence_key"]) for gap in gaps if gap.get("evidence_key")}),
        "readiness_action_hash": readiness.get("required_actions_hash"),
        "promotion_required_action_count": len(promotion_summary.get("required_actions") or []),
        "gaps": gaps,
    }
    summary["gap_summary_hash"] = _hash_payload({key: value for key, value in summary.items() if key != "gap_summary_hash"})
    return summary


def _evidence_key_for_check(name: str) -> str:
    return {
        "external_source_coverage": "external_sources",
        "rag_embedding_backend": "rag_embedding",
        "rna_folding_backend": "rna_folding",
        "governance_attestation": "governance_attestation_verification",
    }.get(name, name)


def _timed(name: str, timings: dict[str, Any], factory: Any) -> Any:
    start = perf_counter()
    result = factory()
    timings["items"].append({"name": name, "duration_seconds": round(perf_counter() - start, 3)})
    return result


def _cached_audit(cache_key: str) -> dict[str, Any] | None:
    with _AUDIT_CACHE_LOCK:
        if not _AUDIT_CACHE:
            return None
        if _AUDIT_CACHE.get("cache_key") != cache_key:
            return None
        age_seconds = perf_counter() - float(_AUDIT_CACHE["stored_monotonic"])
        if age_seconds > PRODUCTION_AUDIT_CACHE_TTL_SECONDS:
            return None
        return deepcopy(_AUDIT_CACHE["audit"])


def _store_cached_audit(cache_key: str, audit: dict[str, Any]) -> None:
    with _AUDIT_CACHE_LOCK:
        global _AUDIT_CACHE
        _AUDIT_CACHE = {
            "cache_key": cache_key,
            "stored_monotonic": perf_counter(),
            "audit": deepcopy(audit),
        }


def _cache_key(openapi_spec: dict[str, Any]) -> str:
    settings = get_settings()
    return _hash_payload(
        {
            "openapi_hash": _hash_payload(openapi_spec),
            "data_dir": str(settings.data_dir),
            "storage_backend": settings.storage_backend,
            "database_url_configured": bool(settings.database_url),
            "auth_enabled": settings.auth_enabled,
            "rbac_enabled": bool(settings.api_key_roles),
            "rate_limit_per_minute": settings.rate_limit_per_minute,
            "signing": signing_status(),
        }
    )


def _check(name: str, pass_condition: bool, production_condition: bool, details: dict[str, Any]) -> dict[str, Any]:
    blocking = not pass_condition
    status = "fail" if blocking else "pass" if production_condition else "warning"
    message = "Gate passed." if status == "pass" else "Blocking production audit failure." if status == "fail" else "Usable, but not fully production configured."
    return {
        "name": name,
        "status": status,
        "blocking": blocking,
        "message": message,
        "detail_hash": _hash_payload(details),
    }


def _record_semantic_check(checks: dict[str, str], errors: list[str], name: str, passed: bool, message: str) -> None:
    checks[name] = "pass" if passed else "fail"
    if not passed:
        errors.append(message)


def _required_action_detail_hash_mismatches(deployment_readiness: dict[str, Any]) -> list[str]:
    gates_by_name = {
        str(gate.get("name")): gate
        for gate in deployment_readiness.get("gates") or []
        if isinstance(gate, dict) and gate.get("name")
    }
    mismatches: list[str] = []
    for action in deployment_readiness.get("required_actions") or []:
        if not isinstance(action, dict):
            mismatches.append("<malformed>")
            continue
        gate_name = str(action.get("gate") or "")
        gate = gates_by_name.get(gate_name)
        if not gate:
            mismatches.append(gate_name or "<missing-gate>")
            continue
        expected_hash = _hash_payload(gate.get("details") or {})
        if action.get("detail_hash") != expected_hash:
            mismatches.append(gate_name)
    return mismatches


def _check_detail_hash_mismatches(checks: list[dict[str, Any]], evidence: dict[str, Any]) -> list[str]:
    evidence_key_by_check = {
        "external_source_coverage": "external_sources",
        "rag_embedding_backend": "rag_embedding",
        "rna_folding_backend": "rna_folding",
        "governance_attestation": "governance_attestation_verification",
    }
    mismatches: list[str] = []
    for check in checks:
        if not isinstance(check, dict):
            mismatches.append("<malformed>")
            continue
        check_name = str(check.get("name") or "")
        evidence_key = evidence_key_by_check.get(check_name, check_name)
        if evidence_key not in evidence:
            mismatches.append(check_name or "<missing-name>")
            continue
        if check.get("detail_hash") != _hash_payload(evidence[evidence_key]):
            mismatches.append(check_name)
    return mismatches


def _promotion_summary_from_audit(audit: dict[str, Any]) -> dict[str, Any]:
    evidence = audit.get("evidence") or {}
    runtime = audit.get("runtime") or {}
    security = evidence.get("security") or {}
    return _promotion_summary(
        summary=audit.get("summary") or {},
        readiness=evidence.get("deployment_readiness") or {},
        structured_quality=evidence.get("structured_quality") or {},
        data_release_bundle=evidence.get("data_release_bundle") or {},
        rag_embedding=evidence.get("rag_embedding") or {},
        optimizer=evidence.get("optimizer_diagnostics") or {},
        folding=evidence.get("rna_folding") or {},
        storage=evidence.get("storage") or {},
        security=security,
        signing=runtime.get("signing") or security.get("signing") or {},
        object_store=evidence.get("artifact_object_store") or {},
    )


def _summary(checks: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"pass": 0, "warning": 0, "fail": 0}
    for check in checks:
        counts[str(check["status"])] += 1
    return {
        "status": "fail" if counts["fail"] else "warning" if counts["warning"] else "pass",
        "deployment_ready": counts["fail"] == 0,
        "production_ready": counts["fail"] == 0 and counts["warning"] == 0,
        "counts": counts,
        "blocking_checks": [check["name"] for check in checks if check["status"] == "fail"],
        "warning_checks": [check["name"] for check in checks if check["status"] == "warning"],
    }


def _external_coverage_ok(status: dict[str, Any]) -> bool:
    coverage = status.get("coverage") or {}
    return (
        float(coverage.get("source_snapshot_path_fraction") or 0) >= 1.0
        and float(coverage.get("source_payload_hash_fraction") or 0) >= 1.0
    )


def _operator_notes(summary: dict[str, Any]) -> list[str]:
    if summary["blocking_checks"]:
        return ["Resolve all blocking checks before deployment promotion."]
    if summary["warning_checks"]:
        return ["No blocking failures were detected. Review warnings before production promotion."]
    return ["No blocking failures or warnings were detected by this audit profile."]


def _hash_without_signatures(payload: dict[str, Any]) -> str:
    return _hash_payload({key: value for key, value in payload.items() if key not in {"audit_hash", "audit_signature", "audit_signatures"}})


def _evidence_hashes(evidence: dict[str, Any]) -> dict[str, Any]:
    items = {
        f"evidence/{name}.json": _hash_payload(payload)
        for name, payload in sorted(evidence.items())
    }
    return {
        "hash_schema": "agentic-rag-production-audit-evidence-hashes-v1",
        "algorithm": "sha256-json-canonical",
        "evidence_count": len(items),
        "items": items,
        "combined_hash": _hash_payload(items),
    }


def _verify_evidence_hashes(
    archive: ZipFile,
    evidence: dict[str, Any],
    evidence_hashes: dict[str, Any],
    semantic_checks: dict[str, str],
    errors: list[str],
) -> None:
    items = evidence_hashes.get("items") if isinstance(evidence_hashes, dict) else None
    if not isinstance(items, dict):
        _record_semantic_check(semantic_checks, errors, "evidence_hashes_schema", False, "evidence_hashes.json items must be an object.")
        return
    expected_paths = {f"evidence/{name}.json" for name in evidence}
    actual_paths = set(str(path) for path in items)
    _record_semantic_check(
        semantic_checks,
        errors,
        "evidence_hashes_schema",
        evidence_hashes.get("hash_schema") == "agentic-rag-production-audit-evidence-hashes-v1"
        and evidence_hashes.get("algorithm") == "sha256-json-canonical",
        "evidence_hashes.json schema or algorithm is not recognized.",
    )
    _record_semantic_check(
        semantic_checks,
        errors,
        "evidence_hashes_count",
        int(evidence_hashes.get("evidence_count") or -1) == len(evidence) and actual_paths == expected_paths,
        "evidence_hashes.json does not cover every production audit evidence item.",
    )
    mismatches: list[str] = []
    for path in sorted(expected_paths):
        try:
            payload = json.loads(archive.read(path).decode("utf-8"))
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError):
            mismatches.append(path)
            continue
        if payload != evidence.get(path.removeprefix("evidence/").removesuffix(".json")):
            mismatches.append(path)
            continue
        if items.get(path) != _hash_payload(payload):
            mismatches.append(path)
    _record_semantic_check(
        semantic_checks,
        errors,
        "evidence_file_hashes",
        not mismatches,
        "Production audit evidence file hashes are missing or mismatched: " + ", ".join(mismatches[:8]),
    )
    _record_semantic_check(
        semantic_checks,
        errors,
        "evidence_hashes_combined",
        evidence_hashes.get("combined_hash") == _hash_payload(items),
        "evidence_hashes.json combined_hash does not match items.",
    )


def _verify_audit_signature(audit: dict[str, Any]) -> dict[str, Any]:
    signatures = list(audit.get("audit_signatures") or [])
    legacy_signature = audit.get("audit_signature")
    if legacy_signature and legacy_signature not in signatures:
        signatures.append(legacy_signature)
    return verify_payload_signatures(str(audit.get("audit_hash") or ""), signatures, signed_field="audit_hash")


def _hash_payload(payload: Any) -> str:
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
