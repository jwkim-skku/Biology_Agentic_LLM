from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from app.config import get_settings
from app.services.artifact_archive_service import archive_summary, verify_artifact_ledger
from app.services.data_provenance_service import data_provenance_audit
from app.services.data_release_lock_service import verify_data_release_lock
from app.services.export_manifest_service import ManifestedZip, verify_artifact_bundle
from app.services.rag_service import rag_status
from app.services.signature_service import signatures_for_hash, signing_status, verify_payload_signatures
from app.services.storage_migration_service import sqlite_postgres_parity_report
from app.services.storage_service import storage_status
from app.services.structured_data_service import structured_manifest


def build_governance_attestation(openapi_spec: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "attestation_schema": "agentic-rag-governance-attestation-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "in-silico design-support deployment evidence",
        "runtime": _runtime_summary(),
        "openapi": _openapi_summary(openapi_spec or {}),
        "data": {
            "structured_manifest": structured_manifest(),
            "data_provenance": data_provenance_audit(),
            "release_lock": verify_data_release_lock(),
            "rag": rag_status(),
        },
        "storage": {
            "status": storage_status(),
            "parity": sqlite_postgres_parity_report(),
        },
        "artifact_archive": {
            "summary": archive_summary(),
            "ledger_verification": verify_artifact_ledger(),
        },
        "controls": {
            "human_interpretation": "Outputs are in silico design support and require experimental validation.",
            "secret_redaction": "API keys, signing keys, and database credentials are not included.",
            "recommended_retention": "Store exported attestation ZIPs in immutable object storage for regulated deployments.",
        },
    }
    payload["attestation_hash"] = _attestation_hash(payload)
    signatures = signatures_for_hash(payload["attestation_hash"], signed_field="attestation_hash")
    if signatures:
        payload["attestation_signatures"] = signatures
        hmac_signature = next((item for item in signatures if item.get("algorithm") == "HMAC-SHA256"), None)
        if hmac_signature:
            payload["attestation_signature"] = hmac_signature
    return payload


def build_governance_attestation_bundle(openapi_spec: dict[str, Any] | None = None) -> bytes:
    attestation = build_governance_attestation(openapi_spec)
    buffer = BytesIO()
    metadata = {
        "attestation_hash": attestation["attestation_hash"],
        "generated_at": attestation["generated_at"],
        "openapi_hash": attestation["openapi"]["hash"],
    }
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        bundle = ManifestedZip(archive, "governance_attestation_bundle", metadata)
        bundle.writestr("governance_attestation.json", _json(attestation))
        bundle.writestr("openapi.json", _json(openapi_spec or {}))
        bundle.writestr("structured_manifest.json", _json(attestation["data"]["structured_manifest"]))
        bundle.writestr("data_provenance.json", _json(attestation["data"]["data_provenance"]))
        bundle.writestr("rag_status.json", _json(attestation["data"]["rag"]))
        bundle.writestr("storage_status.json", _json(attestation["storage"]["status"]))
        bundle.writestr("storage_parity.json", _json(attestation["storage"]["parity"]))
        bundle.writestr("artifact_ledger_verification.json", _json(attestation["artifact_archive"]["ledger_verification"]))
        bundle.write_artifact_manifest()
    return buffer.getvalue()


def verify_governance_attestation_bundle(bundle: bytes) -> dict[str, Any]:
    verification = verify_artifact_bundle(bundle)
    errors = list(verification.get("errors") or [])
    warnings = list(verification.get("warnings") or [])
    attestation_hash = None
    try:
        with ZipFile(BytesIO(bundle), "r") as archive:
            attestation = json.loads(archive.read("governance_attestation.json").decode("utf-8"))
            attestation_hash = attestation.get("attestation_hash")
            actual_hash = _attestation_hash(attestation)
            if attestation_hash != actual_hash:
                errors.append("governance_attestation.json attestation_hash does not match contents.")
            signature_result = _verify_attestation_signature(attestation)
            if signature_result["status"] == "fail":
                errors.extend(signature_result["messages"])
            elif signature_result["status"] == "warning":
                warnings.extend(signature_result["messages"])
    except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        errors.append(f"Invalid governance attestation payload: {exc}")
        signature_result = {"status": "not_checked", "messages": []}

    return {
        "status": "fail" if errors else "warning" if warnings else verification.get("status", "pass"),
        "errors": errors,
        "warnings": warnings,
        "attestation_hash": attestation_hash,
        "artifact_verification": verification,
        "signature": signature_result,
    }


def _runtime_summary() -> dict[str, Any]:
    settings = get_settings()
    return {
        "data_dir": str(settings.data_dir),
        "storage_backend": settings.storage_backend,
        "database_url_configured": bool(settings.database_url),
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
    }


def _openapi_summary(openapi_spec: dict[str, Any]) -> dict[str, Any]:
    paths = openapi_spec.get("paths") or {}
    schemas = ((openapi_spec.get("components") or {}).get("schemas")) or {}
    return {
        "hash": _hash_payload(openapi_spec),
        "path_count": len(paths),
        "schema_count": len(schemas),
        "paths": sorted(paths),
    }


def _attestation_hash(payload: dict[str, Any]) -> str:
    redacted = {
        key: value
        for key, value in payload.items()
        if key not in {"attestation_hash", "attestation_signature", "attestation_signatures"}
    }
    return _hash_payload(redacted)


def _hash_payload(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _verify_attestation_signature(attestation: dict[str, Any]) -> dict[str, Any]:
    signatures = list(attestation.get("attestation_signatures") or [])
    legacy_signature = attestation.get("attestation_signature")
    if legacy_signature and legacy_signature not in signatures:
        signatures.append(legacy_signature)
    return verify_payload_signatures(str(attestation.get("attestation_hash") or ""), signatures, signed_field="attestation_hash")


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
