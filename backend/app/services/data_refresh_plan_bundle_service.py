from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO, StringIO
from typing import Any
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from app.services.data_provenance_service import data_provenance_audit
from app.services.data_refresh_service import data_catalog, refresh_log, refresh_reference_data, validate_refresh_plan
from app.services.data_release_lock_service import verify_data_release_lock
from app.services.export_manifest_service import ManifestedZip, verify_artifact_bundle
from app.services.external_data_service import external_source_status
from app.services.rag_service import rag_status
from app.services.structured_data_service import structured_manifest, structured_status, validate_structured_records
from app.services.structured_quality_service import structured_quality_gate


REQUIRED_DATA_REFRESH_PLAN_FILES = {
    "artifact_manifest.json",
    "bundle_manifest.json",
    "request.json",
    "refresh_plan.json",
    "validation.json",
    "operations.csv",
    "data_catalog.json",
    "structured_status.json",
    "structured_manifest.json",
    "structured_validation.json",
    "structured_quality.json",
    "data_provenance.json",
    "external_sources.json",
    "data_release_lock.json",
    "rag_status.json",
    "refresh_log.json",
}


def build_data_refresh_plan_bundle(request_payload: dict[str, Any]) -> bytes:
    normalized = _normalized_request(request_payload)
    validation = validate_refresh_plan(**normalized)
    plan = refresh_reference_data(**normalized, dry_run=True, rebuild_index_after=False)
    manifest = structured_manifest()
    status = structured_status()
    release_lock = verify_data_release_lock()
    catalog_json = _json(data_catalog())
    external_sources_json = _json(external_source_status())
    request_json = _json(normalized)
    operations_csv = _operations_csv(plan.get("operations") or [])
    metadata = {
        "bundle_schema": "agentic-rag-data-refresh-plan-bundle-v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "dataset_id": normalized["dataset_id"],
        "operation_count": len(plan.get("operations") or []),
        "request_hash": _hash_text(request_json),
        "operations_hash": _hash_text(operations_csv),
        "data_catalog_hash": _hash_text(catalog_json),
        "external_sources_hash": _hash_text(external_sources_json),
        "validation_status": validation.get("status"),
        "structured_manifest_hash": manifest.get("manifest_hash"),
        "release_lock_status": release_lock.get("status"),
        "dry_run": True,
    }
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        bundle = ManifestedZip(archive, "data_refresh_plan_bundle", metadata)
        bundle.writestr("bundle_manifest.json", _json(metadata))
        bundle.writestr("request.json", request_json)
        bundle.writestr("refresh_plan.json", _json(plan))
        bundle.writestr("validation.json", _json(validation))
        bundle.writestr("operations.csv", operations_csv)
        bundle.writestr("data_catalog.json", catalog_json)
        bundle.writestr("structured_status.json", _json(status))
        bundle.writestr("structured_manifest.json", _json(manifest))
        bundle.writestr("structured_validation.json", _json(validate_structured_records()))
        bundle.writestr("structured_quality.json", _json(structured_quality_gate()))
        bundle.writestr("data_provenance.json", _json(data_provenance_audit()))
        bundle.writestr("external_sources.json", external_sources_json)
        bundle.writestr("data_release_lock.json", _json(release_lock))
        bundle.writestr("rag_status.json", _json(rag_status()))
        bundle.writestr("refresh_log.json", _json(refresh_log(limit=200)))
        bundle.write_artifact_manifest()
    return buffer.getvalue()


def verify_data_refresh_plan_bundle(bundle: bytes) -> dict[str, Any]:
    base = verify_artifact_bundle(bundle)
    semantic_errors: list[str] = []
    semantic_warnings: list[str] = []
    semantic_checks: dict[str, str] = {}
    payloads: dict[str, dict[str, Any]] = {}
    operations_rows: list[dict[str, str]] = []
    request_text = ""
    operations_text = ""
    data_catalog_text = ""
    external_sources_text = ""

    if base.get("artifact_type") != "data_refresh_plan_bundle":
        semantic_errors.append("Artifact manifest artifact_type must be data_refresh_plan_bundle.")
        semantic_checks["artifact_type"] = "fail"
    else:
        semantic_checks["artifact_type"] = "pass"

    try:
        with ZipFile(BytesIO(bundle), "r") as archive:
            names = set(archive.namelist())
            missing = sorted(REQUIRED_DATA_REFRESH_PLAN_FILES - names)
            if missing:
                semantic_errors.append(f"Required data refresh plan bundle files are missing: {', '.join(missing)}.")
                semantic_checks["required_files"] = "fail"
            else:
                semantic_checks["required_files"] = "pass"
                payloads = {name: _read_json(archive, name) for name in REQUIRED_DATA_REFRESH_PLAN_FILES if name.endswith(".json")}
                request_text = archive.read("request.json").decode("utf-8")
                data_catalog_text = archive.read("data_catalog.json").decode("utf-8")
                external_sources_text = archive.read("external_sources.json").decode("utf-8")
                operations_rows, operations_text = _read_operations_csv(archive)
    except (BadZipFile, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        semantic_errors.append(f"Invalid data refresh plan bundle: {exc}")

    bundle_manifest = payloads.get("bundle_manifest.json") or {}
    request = payloads.get("request.json") or {}
    plan = payloads.get("refresh_plan.json") or {}
    validation = payloads.get("validation.json") or {}
    structured = payloads.get("structured_status.json") or {}
    manifest = payloads.get("structured_manifest.json") or {}
    release_lock = payloads.get("data_release_lock.json") or {}

    _expect_equal(
        semantic_checks,
        semantic_errors,
        "bundle_schema",
        bundle_manifest.get("bundle_schema"),
        "agentic-rag-data-refresh-plan-bundle-v1",
        "bundle_manifest.json bundle_schema is not recognized.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "validation_schema",
        validation.get("validation_schema"),
        "agentic-rag-data-refresh-plan-validation-v1",
        "validation.json validation_schema is not recognized.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "dry_run",
        plan.get("refresh_status"),
        "planned",
        "refresh_plan.json must be a dry-run planned refresh.",
    )

    operation_count = len(plan.get("operations") or [])
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "operation_count_manifest",
        bundle_manifest.get("operation_count"),
        operation_count,
        "bundle_manifest.json operation_count does not match refresh_plan.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "operation_count_validation",
        (validation.get("plan") or {}).get("operation_count"),
        operation_count,
        "validation.json operation_count does not match refresh_plan.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "operation_count_csv",
        len(operations_rows),
        operation_count,
        "operations.csv row count does not match refresh_plan.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "request_hash",
        bundle_manifest.get("request_hash"),
        _hash_text(request_text),
        "bundle_manifest.json request_hash does not match request.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "operations_hash",
        bundle_manifest.get("operations_hash"),
        _hash_text(operations_text),
        "bundle_manifest.json operations_hash does not match operations.csv.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "data_catalog_hash",
        bundle_manifest.get("data_catalog_hash"),
        _hash_text(data_catalog_text),
        "bundle_manifest.json data_catalog_hash does not match data_catalog.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "external_sources_hash",
        bundle_manifest.get("external_sources_hash"),
        _hash_text(external_sources_text),
        "bundle_manifest.json external_sources_hash does not match external_sources.json.",
    )

    manifest_hashes = {
        str(value)
        for value in [
            bundle_manifest.get("structured_manifest_hash"),
            structured.get("manifest_hash"),
            manifest.get("manifest_hash"),
            (validation.get("current_data") or {}).get("manifest_hash"),
        ]
        if value
    }
    if len(manifest_hashes) > 1:
        semantic_errors.append("Structured manifest hashes disagree across data refresh plan bundle files.")
        semantic_checks["structured_manifest_hash"] = "fail"
    elif manifest_hashes:
        semantic_checks["structured_manifest_hash"] = "pass"
    else:
        semantic_warnings.append("Structured manifest hash is not recorded in the data refresh plan bundle.")
        semantic_checks["structured_manifest_hash"] = "warning"

    if validation.get("status") == "fail":
        semantic_errors.append("Data refresh validation failed.")
        semantic_checks["validation_status"] = "fail"
    elif validation.get("status") == "warning":
        semantic_warnings.append("Data refresh validation has warnings.")
        semantic_checks["validation_status"] = "warning"
    else:
        semantic_checks["validation_status"] = "pass"

    _expect_equal(
        semantic_checks,
        semantic_errors,
        "release_lock_status",
        bundle_manifest.get("release_lock_status"),
        release_lock.get("status"),
        "bundle_manifest.json release_lock_status does not match data_release_lock.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "dataset_id_request",
        bundle_manifest.get("dataset_id"),
        request.get("dataset_id"),
        "bundle_manifest.json dataset_id does not match request.json.",
    )

    errors = list(base.get("errors") or []) + semantic_errors
    warnings = list(base.get("warnings") or []) + semantic_warnings
    semantic_status = "fail" if semantic_errors else "warning" if semantic_warnings else "pass"
    return {
        **base,
        "status": "fail" if errors else "warning" if warnings else "pass",
        "errors": errors,
        "warnings": warnings,
        "semantic_status": semantic_status,
        "semantic_errors": semantic_errors,
        "semantic_warnings": semantic_warnings,
        "semantic_checks": semantic_checks,
        "operation_count": operation_count,
        "request_hash": bundle_manifest.get("request_hash"),
        "operations_hash": bundle_manifest.get("operations_hash"),
        "data_catalog_hash": bundle_manifest.get("data_catalog_hash"),
        "external_sources_hash": bundle_manifest.get("external_sources_hash"),
        "validation_status": validation.get("status"),
        "structured_manifest_hash": next(iter(manifest_hashes), None),
        "release_lock_status": release_lock.get("status"),
        "dataset_id": request.get("dataset_id"),
    }


def _normalized_request(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "genes": _list(payload.get("genes")),
        "brain_regions": _list(payload.get("brain_regions")),
        "include_gtex": bool(payload.get("include_gtex", True)),
        "include_allen": bool(payload.get("include_allen", True)),
        "allen_query_terms": _list(payload.get("allen_query_terms")),
        "max_allen_records": int(payload.get("max_allen_records") or 250),
        "dataset_id": str(payload.get("dataset_id") or "gtex_v8"),
    }


def _list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _operations_csv(operations: list[dict[str, Any]]) -> str:
    output = StringIO()
    fieldnames = ["source", "action", "gene", "brain_regions", "dataset_id", "query_terms", "max_records"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for operation in operations:
        writer.writerow(
            {
                "source": operation.get("source"),
                "action": operation.get("action"),
                "gene": operation.get("gene"),
                "brain_regions": " | ".join(operation.get("brain_regions") or []),
                "dataset_id": operation.get("dataset_id"),
                "query_terms": " | ".join(operation.get("query_terms") or []),
                "max_records": operation.get("max_records"),
            }
        )
    return output.getvalue()


def _read_operations_csv(archive: ZipFile) -> tuple[list[dict[str, str]], str]:
    text = archive.read("operations.csv").decode("utf-8")
    return list(csv.DictReader(StringIO(text))), text


def _hash_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _expect_equal(
    checks: dict[str, str],
    errors: list[str],
    name: str,
    actual: Any,
    expected: Any,
    message: str,
) -> None:
    if actual == expected and actual is not None:
        checks[name] = "pass"
    else:
        checks[name] = "fail"
        errors.append(message)


def _read_json(archive: ZipFile, name: str) -> dict[str, Any]:
    payload = json.loads(archive.read(name).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must contain a JSON object.")
    return payload


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
