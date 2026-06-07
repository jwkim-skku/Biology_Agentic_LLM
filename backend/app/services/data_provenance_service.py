from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.data_lock_service import verify_data_lockfile
from app.services.data_release_lock_service import verify_data_release_lock
from app.services.data_refresh_service import data_catalog, refresh_log
from app.services.external_data_service import external_source_coverage
from app.services.ingestion_service import list_ingested_documents
from app.services.rag_service import rag_status
from app.services.structured_data_service import load_structured_records, structured_manifest, structured_status


def data_provenance_audit() -> dict[str, Any]:
    manifest = structured_manifest()
    status = structured_status()
    rag = rag_status()
    documents = list_ingested_documents()
    refresh = refresh_log(limit=10)
    records = load_structured_records()
    external_records = [record for record in records if record.get("dataset") in {"GTEx", "Allen Brain Cell Atlas"}]
    lockfile = verify_data_lockfile()
    release_lock = verify_data_release_lock()
    source_coverage = external_source_coverage()
    trna_caveats = _trna_prior_caveats(records)
    checks = [
        _check("structured_validation", manifest["validation"]["error_count"] == 0, "error", f"{manifest['validation']['error_count']} validation errors"),
        _check("structured_records_present", status["records"] > 0, "error", f"{status['records']} structured records loaded"),
        _check("structured_files_hashed", all(file.get("sha256") for file in manifest["files"]), "error", f"{len(manifest['files'])} structured files hashed"),
        _check("rag_index_present", rag.get("chunks", 0) > 0, "warning", f"{rag.get('chunks', 0)} RAG chunks indexed"),
        _check("documents_registered", len(documents.get("documents", [])) > 0, "warning", f"{len(documents.get('documents', []))} documents registered"),
        _check("refresh_log_present", len(refresh.get("entries", [])) > 0, "warning", f"{len(refresh.get('entries', []))} refresh log entries"),
        _check("release_metadata_present", _metadata_fraction(records, "release") >= 0.70, "warning", _metadata_message(records, "release")),
        _check("source_urls_present", _metadata_fraction(records, "source_url") >= 0.70, "warning", _metadata_message(records, "source_url")),
        _check("source_hashes_present", all(record.get("_source_sha256") for record in records), "error", "all loaded structured records carry source file hashes"),
        _check(
            "external_payload_hashes_present",
            not external_records or _metadata_fraction(external_records, "source_payload_sha256") >= 0.70,
            "warning",
            _metadata_message(external_records, "source_payload_sha256"),
        ),
        _check(
            "external_source_snapshots_present",
            source_coverage["tracked_records"] == 0 or source_coverage["source_snapshot_path_fraction"] >= 0.70,
            "warning",
            f"{source_coverage['source_snapshot_path_fraction']:.0%} tracked structured records include source_snapshot_path",
        ),
        _check("data_lockfile_present", lockfile["status"] != "missing", "warning", f"data lockfile status is {lockfile['status']}"),
        _check("data_lockfile_current", lockfile["status"] in {"current", "missing"}, "warning", f"data lockfile status is {lockfile['status']}"),
        _check("data_release_lock_present", release_lock["status"] != "missing", "warning", f"data release lock status is {release_lock['status']}"),
        _check("data_release_lock_current", release_lock["status"] in {"current", "missing"}, "warning", f"data release lock status is {release_lock['status']}"),
        _check(
            "trna_prior_release_pinned",
            not trna_caveats["blocking_production_use"],
            "warning",
            f"{trna_caveats['caveat_count']} tRNA/codon-availability records require production review",
        ),
    ]
    catalog = data_catalog()
    return {
        "status": _aggregate_status(checks),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "manifest_hash": status.get("manifest_hash"),
        "structured": {
            "records": status["records"],
            "datasets": status["datasets"],
            "files": len(manifest["files"]),
            "validation": status["validation"],
            "provenance": status.get("provenance"),
        },
        "rag": {
            "chunks": rag.get("chunks"),
            "index_version": rag.get("index_version"),
            "embedding_model": rag.get("embedding_model"),
        },
        "documents": {
            "count": len(documents.get("documents", [])),
            "documents_path": documents.get("documents_path"),
        },
        "refresh": {
            "log_path": refresh.get("log_path"),
            "latest": (refresh.get("entries") or [None])[-1],
        },
        "external_source_coverage": source_coverage,
        "trna_prior_caveats": trna_caveats,
        "lockfile": lockfile,
        "release_lock": release_lock,
        "sources": catalog["sources"],
        "checks": checks,
    }


def _check(check_id: str, passed: bool, severity: str, message: str) -> dict[str, Any]:
    return {
        "id": check_id,
        "result": "pass" if passed else "fail",
        "severity": severity,
        "message": message,
    }


def _aggregate_status(checks: list[dict[str, Any]]) -> str:
    if any(check["result"] == "fail" and check["severity"] == "error" for check in checks):
        return "fail"
    if any(check["result"] == "fail" for check in checks):
        return "warning"
    return "pass"


def _metadata_fraction(records: list[dict[str, Any]], field: str) -> float:
    if not records:
        return 0.0
    present = sum(1 for record in records if record.get(field))
    return present / len(records)


def _metadata_message(records: list[dict[str, Any]], field: str) -> str:
    present = sum(1 for record in records if record.get(field))
    return f"{present}/{len(records)} structured records include {field}"


def _trna_prior_caveats(records: list[dict[str, Any]]) -> dict[str, Any]:
    reviewed: list[dict[str, Any]] = []
    for record in records:
        if not record.get("codon_availability_weights"):
            continue
        release = str(record.get("release") or "").lower()
        summary = str(record.get("summary") or "").lower()
        confidence = str(record.get("confidence") or "").lower()
        caveats: list[str] = []
        if "seed" in release or "placeholder" in summary:
            caveats.append("seed_or_placeholder_prior")
        if confidence == "low":
            caveats.append("low_confidence_prior")
        if "quantitative" in summary and "before quantitative use" in summary:
            caveats.append("not_quantitative_use_ready")
        if caveats:
            reviewed.append(
                {
                    "id": record.get("id"),
                    "dataset": record.get("dataset"),
                    "release": record.get("release"),
                    "confidence": record.get("confidence"),
                    "source_file": record.get("_source_file"),
                    "source_url": record.get("source_url"),
                    "caveats": caveats,
                }
            )
    return {
        "status": "warning" if reviewed else "pass",
        "caveat_count": len(reviewed),
        "blocking_production_use": bool(reviewed),
        "records": reviewed[:100],
        "operator_action": (
            "Replace caveated codon-availability priors with release-pinned quantitative matrices before production interpretation."
            if reviewed
            else "No caveated codon-availability priors detected."
        ),
    }
