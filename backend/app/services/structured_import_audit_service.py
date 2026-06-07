from __future__ import annotations

import json
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from app.services.export_manifest_service import ManifestedZip, verify_artifact_bundle
from app.services.structured_data_service import structured_manifest, structured_status, validate_structured_records


REQUIRED_IMPORT_AUDIT_FILES = {
    "import_manifest.json",
    "request.json",
    "import_result.json",
    "structured_status.json",
    "structured_manifest.json",
    "structured_validation.json",
}


def build_structured_import_audit_bundle(import_result: dict[str, Any], request_payload: dict[str, Any]) -> bytes:
    imported_path = Path(str(import_result.get("imported_path") or ""))
    manifest = structured_manifest()
    validation = validate_structured_records()
    metadata = {
        "bundle_schema": "agentic-rag-structured-import-audit-v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "imported_file": imported_path.name if imported_path.name else None,
        "structured_manifest_hash": manifest.get("manifest_hash"),
        "validation_errors": validation.get("error_count"),
        "validation_warnings": validation.get("warning_count"),
    }
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        bundle = ManifestedZip(archive, "structured_import_audit_bundle", metadata)
        bundle.writestr("import_manifest.json", _json(metadata))
        bundle.writestr("request.json", _json(_redacted_request(request_payload)))
        bundle.writestr("import_result.json", _json(import_result))
        bundle.writestr("structured_status.json", _json(structured_status()))
        bundle.writestr("structured_manifest.json", _json(manifest))
        bundle.writestr("structured_validation.json", _json(validation))
        if imported_path.exists() and imported_path.is_file():
            bundle.write_file(imported_path, f"imported/{imported_path.name}")
        bundle.write_artifact_manifest()
    return buffer.getvalue()


def verify_structured_import_audit_bundle(bundle: bytes) -> dict[str, Any]:
    base = verify_artifact_bundle(bundle)
    errors = list(base.get("errors") or [])
    warnings = list(base.get("warnings") or [])
    semantic_errors: list[str] = []
    semantic_warnings: list[str] = []
    semantic_checks: dict[str, str] = {}
    zip_names: set[str] = set()

    if base.get("artifact_type") != "structured_import_audit_bundle":
        semantic_errors.append("Artifact manifest artifact_type must be structured_import_audit_bundle.")
        semantic_checks["artifact_type"] = "fail"
    else:
        semantic_checks["artifact_type"] = "pass"

    try:
        with ZipFile(BytesIO(bundle), "r") as archive:
            zip_names = set(archive.namelist())
            missing = sorted(REQUIRED_IMPORT_AUDIT_FILES - zip_names)
            if missing:
                semantic_errors.append(f"Required structured import audit files are missing: {', '.join(missing)}.")
                semantic_checks["required_files"] = "fail"
                payloads: dict[str, Any] = {}
            else:
                semantic_checks["required_files"] = "pass"
                payloads = {name: _read_json(archive, name) for name in REQUIRED_IMPORT_AUDIT_FILES}
    except (BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as exc:
        semantic_errors.append(f"Invalid structured import audit bundle: {exc}")
        payloads = {}

    import_manifest = payloads.get("import_manifest.json") or {}
    import_result = payloads.get("import_result.json") or {}
    status = payloads.get("structured_status.json") or {}
    manifest = payloads.get("structured_manifest.json") or {}
    validation = payloads.get("structured_validation.json") or {}
    metadata = base.get("bundle_metadata") or {}

    if import_manifest:
        if import_manifest.get("bundle_schema") != "agentic-rag-structured-import-audit-v1":
            semantic_errors.append("import_manifest.json bundle_schema is invalid.")
            semantic_checks["bundle_schema"] = "fail"
        else:
            semantic_checks["bundle_schema"] = "pass"

    expected_hashes = {
        str(value)
        for value in [
            metadata.get("structured_manifest_hash"),
            import_manifest.get("structured_manifest_hash"),
            manifest.get("manifest_hash"),
            status.get("manifest_hash"),
            (import_result.get("status") or {}).get("manifest_hash"),
        ]
        if value
    }
    if len(expected_hashes) > 1:
        semantic_errors.append("Structured manifest hashes disagree across import audit files.")
        semantic_checks["structured_manifest_hash"] = "fail"
    elif expected_hashes:
        semantic_checks["structured_manifest_hash"] = "pass"
    else:
        semantic_warnings.append("Structured manifest hash was not recorded in the import audit bundle.")
        semantic_checks["structured_manifest_hash"] = "warning"

    validation_error_count = _int_or_none(validation.get("error_count"))
    status_error_count = _int_or_none((status.get("validation") or {}).get("errors"))
    import_error_count = _int_or_none(import_manifest.get("validation_errors"))
    error_counts = {value for value in [validation_error_count, status_error_count, import_error_count] if value is not None}
    if len(error_counts) > 1:
        semantic_errors.append("Structured validation error counts disagree across import audit files.")
        semantic_checks["validation_consistency"] = "fail"
    elif error_counts and next(iter(error_counts)) > 0:
        semantic_errors.append("Structured import audit bundle records validation errors.")
        semantic_checks["validation_consistency"] = "fail"
    elif error_counts:
        semantic_checks["validation_consistency"] = "pass"
    else:
        semantic_warnings.append("Structured validation error count was not recorded.")
        semantic_checks["validation_consistency"] = "warning"

    if not any(name.startswith("imported/") for name in zip_names):
        semantic_warnings.append("Structured import audit bundle does not include imported source bytes.")
        semantic_checks["imported_source_bytes"] = "warning"
    else:
        semantic_checks["imported_source_bytes"] = "pass"

    semantic_status = "fail" if semantic_errors else "warning" if semantic_warnings else "pass"
    status_value = "fail" if errors or semantic_errors else "warning" if warnings or semantic_warnings else "pass"
    return {
        **base,
        "status": status_value,
        "errors": errors + semantic_errors,
        "warnings": warnings + semantic_warnings,
        "semantic_status": semantic_status,
        "semantic_errors": semantic_errors,
        "semantic_warnings": semantic_warnings,
        "semantic_checks": semantic_checks,
        "structured_manifest_hash": next(iter(expected_hashes), None),
        "validation_error_count": validation_error_count,
        "validation_warning_count": _int_or_none(validation.get("warning_count")),
    }


def _redacted_request(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(payload)
    source_path = redacted.get("source_path")
    if source_path:
        redacted["source_path"] = str(source_path)
    return redacted


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _read_json(archive: ZipFile, name: str) -> dict[str, Any]:
    payload = json.loads(archive.read(name).decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

