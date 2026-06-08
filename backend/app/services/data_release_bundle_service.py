from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from app.services.data_provenance_service import data_provenance_audit
from app.services.data_refresh_service import data_catalog, refresh_log
from app.services.data_release_lock_service import read_data_release_lock, verify_data_release_lock
from app.services.external_data_service import external_source_status
from app.services.export_manifest_service import ManifestedZip, verify_artifact_bundle
from app.services.rag_service import RAG_INDEX_PATH, rag_status
from app.services.structured_data_service import (
    STRUCTURED_DIR,
    load_structured_records,
    structured_coverage_matrix,
    structured_manifest,
    structured_status,
    validate_structured_records,
)
from app.services.structured_quality_service import structured_quality_gate


RELEASE_BUNDLE_SCHEMA = "agentic-rag-data-release-bundle-v1"
REQUIRED_DATA_RELEASE_FILES = {
    "release_manifest.json",
    "structured_status.json",
    "structured_manifest.json",
    "structured_validation.json",
    "structured_quality.json",
    "structured_coverage.json",
    "data_provenance.json",
    "data_catalog.json",
    "external_sources.json",
    "data_release_lock.json",
    "data_release_lock_persisted.json",
    "rag_status.json",
    "refresh_log.json",
    "record_source_summary.json",
    "records.jsonl",
    "records.csv",
}


def build_data_release_bundle() -> bytes:
    records = load_structured_records()
    manifest = structured_manifest()
    quality = structured_quality_gate()
    provenance = data_provenance_audit()
    release_lock = verify_data_release_lock()
    rag = rag_status()
    trna_caveats = provenance.get("trna_prior_caveats") or {}
    records_jsonl = _records_jsonl(records)
    records_csv = _records_csv(records)
    record_source_summary = _record_source_summary(records)
    record_source_summary_json = _json(record_source_summary)
    rag_index_hash = _file_sha256(RAG_INDEX_PATH) if RAG_INDEX_PATH.exists() else None
    metadata = {
        "bundle_schema": RELEASE_BUNDLE_SCHEMA,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "structured_manifest_hash": manifest.get("manifest_hash"),
        "rag_structured_manifest_hash": rag.get("structured_manifest_hash"),
        "rag_index_hash": rag_index_hash,
        "record_count": len(records),
        "records_hash": _hash_text(records_jsonl),
        "records_csv_hash": _hash_text(records_csv),
        "record_source_summary_hash": _hash_payload(record_source_summary),
        "dataset_count": record_source_summary.get("dataset_count"),
        "release_count": record_source_summary.get("release_count"),
        "source_file_count": record_source_summary.get("source_file_count"),
        "structured_file_count": len(manifest.get("files") or []),
        "external_snapshot_reference_count": len(_external_snapshot_paths(records)),
        "quality_status": quality.get("status"),
        "provenance_status": provenance.get("status"),
        "trna_caveat_count": trna_caveats.get("caveat_count", 0),
        "trna_blocking_production_use": bool(trna_caveats.get("blocking_production_use")),
        "release_lock_status": release_lock.get("status"),
        "promotion_status": _promotion_status(quality, provenance, release_lock),
        "contains_source_bytes": True,
        "contains_external_snapshot_bytes": True,
    }
    metadata["release_handoff_hash"] = _release_handoff_hash(metadata)
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        bundle = ManifestedZip(archive, "data_release_bundle", metadata)
        bundle.writestr("release_manifest.json", _json(metadata))
        bundle.writestr("structured_status.json", _json(structured_status()))
        bundle.writestr("structured_manifest.json", _json(manifest))
        bundle.writestr("structured_validation.json", _json(validate_structured_records()))
        bundle.writestr("structured_quality.json", _json(quality))
        bundle.writestr("structured_coverage.json", _json(structured_coverage_matrix()))
        bundle.writestr("data_provenance.json", _json(provenance))
        bundle.writestr("data_catalog.json", _json(data_catalog()))
        bundle.writestr("external_sources.json", _json(external_source_status()))
        bundle.writestr("data_release_lock.json", _json(release_lock))
        bundle.writestr("data_release_lock_persisted.json", _json(read_data_release_lock() or {}))
        bundle.writestr("rag_status.json", _json(rag))
        bundle.writestr("refresh_log.json", _json(refresh_log(limit=500)))
        bundle.writestr("record_source_summary.json", record_source_summary_json)
        bundle.writestr("records.jsonl", records_jsonl)
        bundle.writestr("records.csv", records_csv)
        for source_path in _structured_source_files():
            bundle.write_file(source_path, f"structured_sources/{source_path.name}")
        for snapshot_path in _external_snapshot_paths(records):
            bundle.write_file(snapshot_path, f"external_source_snapshots/{snapshot_path.name}")
        bundle.write_artifact_manifest()
    return buffer.getvalue()


def verify_data_release_bundle(bundle: bytes) -> dict[str, Any]:
    base = verify_artifact_bundle(bundle)
    semantic_errors: list[str] = []
    semantic_warnings: list[str] = []
    semantic_checks: dict[str, str] = {}
    payloads: dict[str, dict[str, Any]] = {}
    zip_names: set[str] = set()
    row_evidence: dict[str, Any] = {}

    if base.get("artifact_type") != "data_release_bundle":
        semantic_errors.append("Artifact manifest artifact_type must be data_release_bundle.")
        semantic_checks["artifact_type"] = "fail"
    else:
        semantic_checks["artifact_type"] = "pass"

    try:
        with ZipFile(BytesIO(bundle), "r") as archive:
            zip_names = set(archive.namelist())
            missing = sorted(REQUIRED_DATA_RELEASE_FILES - zip_names)
            if missing:
                semantic_errors.append(f"Required data release bundle files are missing: {', '.join(missing)}.")
                semantic_checks["required_files"] = "fail"
            else:
                semantic_checks["required_files"] = "pass"
                payloads = {name: _read_json(archive, name) for name in REQUIRED_DATA_RELEASE_FILES if name.endswith(".json")}
                row_evidence = _verify_record_rows(archive, semantic_checks, semantic_errors)
    except (BadZipFile, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        semantic_errors.append(f"Invalid data release bundle: {exc}")

    release_manifest = payloads.get("release_manifest.json") or {}
    structured_status = payloads.get("structured_status.json") or {}
    structured_manifest_payload = payloads.get("structured_manifest.json") or {}
    validation = payloads.get("structured_validation.json") or {}
    quality = payloads.get("structured_quality.json") or {}
    coverage = payloads.get("structured_coverage.json") or {}
    provenance = payloads.get("data_provenance.json") or {}
    trna_caveats = provenance.get("trna_prior_caveats") or {}
    release_lock = payloads.get("data_release_lock.json") or {}
    rag = payloads.get("rag_status.json") or {}
    record_source_summary = payloads.get("record_source_summary.json") or {}

    _expect_equal(
        semantic_checks,
        semantic_errors,
        "bundle_schema",
        release_manifest.get("bundle_schema"),
        RELEASE_BUNDLE_SCHEMA,
        "release_manifest.json bundle_schema is not recognized.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "quality_schema",
        quality.get("quality_schema"),
        "agentic-rag-structured-quality-gate-v1",
        "structured_quality.json quality_schema is not recognized.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "coverage_schema",
        coverage.get("coverage_schema"),
        "agentic-rag-structured-coverage-v1",
        "structured_coverage.json coverage_schema is not recognized.",
    )

    manifest_hashes = {
        str(value)
        for value in [
            release_manifest.get("structured_manifest_hash"),
            structured_status.get("manifest_hash"),
            structured_manifest_payload.get("manifest_hash"),
            quality.get("manifest_hash"),
            coverage.get("manifest_hash"),
            provenance.get("manifest_hash"),
        ]
        if value
    }
    if len(manifest_hashes) > 1:
        semantic_errors.append("Structured manifest hashes disagree across data release bundle files.")
        semantic_checks["structured_manifest_hash"] = "fail"
    elif manifest_hashes:
        semantic_checks["structured_manifest_hash"] = "pass"
    else:
        semantic_warnings.append("Structured manifest hash is not recorded in the data release bundle.")
        semantic_checks["structured_manifest_hash"] = "warning"

    rag_structured_hashes = {
        str(value)
        for value in [
            release_manifest.get("structured_manifest_hash"),
            release_manifest.get("rag_structured_manifest_hash"),
            rag.get("structured_manifest_hash"),
        ]
        if value
    }
    if len(rag_structured_hashes) > 1:
        semantic_errors.append("RAG status structured_manifest_hash does not match release structured manifest hash.")
        semantic_checks["rag_structured_manifest_hash"] = "fail"
    elif rag_structured_hashes:
        semantic_checks["rag_structured_manifest_hash"] = "pass"
    else:
        semantic_warnings.append("RAG status structured_manifest_hash is not recorded in the data release bundle.")
        semantic_checks["rag_structured_manifest_hash"] = "warning"

    if release_manifest.get("rag_index_hash"):
        semantic_checks["rag_index_hash"] = "pass"
    else:
        semantic_warnings.append("Data release bundle does not record a persisted RAG index hash.")
        semantic_checks["rag_index_hash"] = "warning"

    record_counts = {
        value
        for value in [
            _int_or_none(release_manifest.get("record_count")),
            _int_or_none(structured_status.get("records")),
            _int_or_none(validation.get("records")),
            _int_or_none(quality.get("record_count")),
            _int_or_none(coverage.get("record_count")),
        ]
        if value is not None
    }
    if len(record_counts) > 1:
        semantic_errors.append("Record counts disagree across data release bundle files.")
        semantic_checks["record_count"] = "fail"
    elif record_counts and next(iter(record_counts)) > 0:
        semantic_checks["record_count"] = "pass"
    else:
        semantic_errors.append("Data release bundle must contain at least one structured record.")
        semantic_checks["record_count"] = "fail"

    _expect_equal(
        semantic_checks,
        semantic_errors,
        "records_hash",
        release_manifest.get("records_hash"),
        row_evidence.get("records_hash"),
        "release_manifest.json records_hash does not match records.jsonl.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "records_csv_hash",
        release_manifest.get("records_csv_hash"),
        row_evidence.get("records_csv_hash"),
        "release_manifest.json records_csv_hash does not match records.csv.",
    )
    expected_record_source_summary = _record_source_summary(row_evidence.get("records") or [])
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "record_source_summary_hash",
        release_manifest.get("record_source_summary_hash"),
        _hash_payload(record_source_summary),
        "release_manifest.json record_source_summary_hash does not match record_source_summary.json.",
    )
    if record_source_summary.get("summary_schema") != "agentic-rag-data-release-record-source-summary-v1":
        semantic_errors.append("record_source_summary.json summary_schema is invalid.")
        semantic_checks["record_source_summary_schema"] = "fail"
    elif record_source_summary != expected_record_source_summary:
        semantic_errors.append("record_source_summary.json does not match records.jsonl.")
        semantic_checks["record_source_summary_consistency"] = "fail"
    else:
        semantic_checks["record_source_summary_schema"] = "pass"
        semantic_checks["record_source_summary_consistency"] = "pass"
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "record_source_summary_counts",
        {
            "record_count": release_manifest.get("record_count"),
            "dataset_count": release_manifest.get("dataset_count"),
            "release_count": release_manifest.get("release_count"),
            "source_file_count": release_manifest.get("source_file_count"),
        },
        {
            "record_count": record_source_summary.get("record_count"),
            "dataset_count": record_source_summary.get("dataset_count"),
            "release_count": record_source_summary.get("release_count"),
            "source_file_count": record_source_summary.get("source_file_count"),
        },
        "release_manifest.json source summary counts do not match record_source_summary.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "release_handoff_hash",
        release_manifest.get("release_handoff_hash"),
        _release_handoff_hash(release_manifest),
        "release_manifest.json release_handoff_hash does not match the release handoff evidence.",
    )

    validation_errors = _int_or_none(validation.get("error_count"))
    if validation_errors and validation_errors > 0:
        semantic_errors.append("Data release bundle records structured validation errors.")
        semantic_checks["structured_validation"] = "fail"
    elif validation_errors == 0:
        semantic_checks["structured_validation"] = "pass"
    else:
        semantic_warnings.append("Structured validation error count is missing.")
        semantic_checks["structured_validation"] = "warning"

    _expect_equal(
        semantic_checks,
        semantic_errors,
        "quality_status",
        release_manifest.get("quality_status"),
        quality.get("status"),
        "release_manifest.json quality_status does not match structured_quality.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "provenance_status",
        release_manifest.get("provenance_status"),
        provenance.get("status"),
        "release_manifest.json provenance_status does not match data_provenance.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "trna_caveat_count",
        _int_or_none(release_manifest.get("trna_caveat_count")),
        _int_or_none(trna_caveats.get("caveat_count")),
        "release_manifest.json trna_caveat_count does not match data_provenance.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "trna_blocking_production_use",
        _bool_or_none(release_manifest.get("trna_blocking_production_use")),
        _bool_or_none(trna_caveats.get("blocking_production_use")),
        "release_manifest.json trna_blocking_production_use does not match data_provenance.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "release_lock_status",
        release_manifest.get("release_lock_status"),
        release_lock.get("status"),
        "release_manifest.json release_lock_status does not match data_release_lock.json.",
    )

    source_files = [name for name in zip_names if name.startswith("structured_sources/") and not name.endswith("/")]
    expected_source_files = _int_or_none(release_manifest.get("structured_file_count"))
    if expected_source_files is not None and len(source_files) >= expected_source_files:
        semantic_checks["structured_source_bytes"] = "pass"
    else:
        semantic_errors.append("Data release bundle does not include all structured source bytes.")
        semantic_checks["structured_source_bytes"] = "fail"

    snapshot_files = [name for name in zip_names if name.startswith("external_source_snapshots/") and not name.endswith("/")]
    snapshot_basenames = {Path(name).name for name in snapshot_files}
    referenced_snapshot_basenames = set(row_evidence.get("external_snapshot_reference_basenames") or [])
    missing_snapshot_basenames = sorted(referenced_snapshot_basenames - snapshot_basenames)
    contained_snapshot_reference_count = len(referenced_snapshot_basenames) - len(missing_snapshot_basenames)
    expected_snapshot_files = _int_or_none(release_manifest.get("external_snapshot_reference_count"))
    if expected_snapshot_files is None:
        semantic_warnings.append("Data release bundle does not record external snapshot reference count.")
        semantic_checks["external_snapshot_bytes"] = "warning"
    elif expected_snapshot_files == 0:
        semantic_warnings.append("Data release bundle has no external source snapshot references.")
        semantic_checks["external_snapshot_bytes"] = "warning"
    elif len(snapshot_files) >= expected_snapshot_files:
        semantic_checks["external_snapshot_bytes"] = "pass"
    else:
        semantic_errors.append("Data release bundle does not include all referenced external source snapshot bytes.")
        semantic_checks["external_snapshot_bytes"] = "fail"
    if referenced_snapshot_basenames and not missing_snapshot_basenames:
        semantic_checks["external_snapshot_reference_coverage"] = "pass"
    elif referenced_snapshot_basenames:
        semantic_errors.append("Data release bundle is missing one or more snapshot files referenced by records.jsonl.")
        semantic_checks["external_snapshot_reference_coverage"] = "fail"
    else:
        semantic_warnings.append("records.jsonl contains no external source snapshot references.")
        semantic_checks["external_snapshot_reference_coverage"] = "warning"

    promotion_status = str(release_manifest.get("promotion_status") or "unknown")
    if promotion_status == "fail":
        semantic_errors.append("Data release promotion status is fail.")
        semantic_checks["promotion_status"] = "fail"
    elif promotion_status == "warning":
        semantic_warnings.append("Data release promotion status is warning; see structured quality/provenance caveats.")
        semantic_checks["promotion_status"] = "warning"
    elif promotion_status == "pass":
        semantic_checks["promotion_status"] = "pass"
    else:
        semantic_errors.append("Data release promotion status is missing or invalid.")
        semantic_checks["promotion_status"] = "fail"

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
        "structured_manifest_hash": next(iter(manifest_hashes), None),
        "rag_structured_manifest_hash": rag.get("structured_manifest_hash"),
        "rag_index_hash": release_manifest.get("rag_index_hash"),
        "record_count": next(iter(record_counts), None),
        "records_hash": row_evidence.get("records_hash"),
        "records_csv_hash": row_evidence.get("records_csv_hash"),
        "record_source_summary_hash": release_manifest.get("record_source_summary_hash"),
        "dataset_count": record_source_summary.get("dataset_count"),
        "release_count": record_source_summary.get("release_count"),
        "source_file_count": record_source_summary.get("source_file_count"),
        "release_handoff_hash": release_manifest.get("release_handoff_hash"),
        "quality_status": quality.get("status"),
        "provenance_status": provenance.get("status"),
        "trna_caveat_count": _int_or_none(trna_caveats.get("caveat_count")),
        "trna_blocking_production_use": _bool_or_none(trna_caveats.get("blocking_production_use")),
        "release_lock_status": release_lock.get("status"),
        "promotion_status": promotion_status,
        "structured_source_file_count": len(source_files),
        "external_snapshot_file_count": len(snapshot_files),
        "external_snapshot_reference_count": expected_snapshot_files,
        "external_snapshot_referenced_count": len(referenced_snapshot_basenames),
        "external_snapshot_contained_count": contained_snapshot_reference_count,
        "external_snapshot_missing_count": len(missing_snapshot_basenames),
    }


def _promotion_status(quality: dict[str, Any], provenance: dict[str, Any], release_lock: dict[str, Any]) -> str:
    statuses = [quality.get("status"), provenance.get("status")]
    if release_lock.get("status") not in {"current"}:
        statuses.append("warning")
    if any(status == "fail" for status in statuses):
        return "fail"
    if any(status == "warning" for status in statuses):
        return "warning"
    return "pass"


def _structured_source_files() -> list[Path]:
    if not STRUCTURED_DIR.exists():
        return []
    return sorted(path for path in STRUCTURED_DIR.glob("*") if path.is_file() and path.suffix.lower() in {".json", ".csv"})


def _external_snapshot_paths(records: list[dict[str, Any]]) -> list[Path]:
    paths: dict[str, Path] = {}
    for record in records:
        raw_path = record.get("source_snapshot_path")
        if not raw_path:
            continue
        path = Path(str(raw_path))
        if path.exists() and path.is_file():
            paths[str(path.resolve())] = path
    return sorted(paths.values(), key=lambda item: item.name)


def _records_jsonl(records: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(_export_record(record), ensure_ascii=False, sort_keys=True) for record in records) + ("\n" if records else "")


def _records_csv(records: list[dict[str, Any]]) -> str:
    output = StringIO()
    fieldnames = [
        "id",
        "dataset",
        "release",
        "gene",
        "brain_region",
        "cell_type",
        "confidence",
        "source_file",
        "source_sha256",
        "source_payload_sha256",
        "source_snapshot_path",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for record in records:
        writer.writerow(
            {
                "id": record.get("id"),
                "dataset": record.get("dataset"),
                "release": record.get("release"),
                "gene": record.get("gene"),
                "brain_region": record.get("brain_region"),
                "cell_type": record.get("cell_type"),
                "confidence": record.get("confidence"),
                "source_file": record.get("_source_file"),
                "source_sha256": record.get("_source_sha256"),
                "source_payload_sha256": record.get("source_payload_sha256"),
                "source_snapshot_path": record.get("source_snapshot_path"),
            }
        )
    return output.getvalue()


def _record_source_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    datasets: dict[str, dict[str, Any]] = {}
    releases: dict[str, int] = {}
    source_files: dict[str, dict[str, Any]] = {}
    source_payload_hashes: dict[str, int] = {}
    snapshot_paths: dict[str, int] = {}
    for record in records:
        dataset = str(record.get("dataset") or "unknown")
        release = str(record.get("release") or "unreleased")
        source_file = str(record.get("_source_file") or "unknown")
        source_sha256 = str(record.get("_source_sha256") or "")
        payload_hash = str(record.get("source_payload_sha256") or "")
        snapshot_path = str(record.get("source_snapshot_path") or "")
        dataset_row = datasets.setdefault(
            dataset,
            {
                "dataset": dataset,
                "record_count": 0,
                "releases": set(),
                "source_files": set(),
                "source_payload_hash_count": 0,
                "source_snapshot_count": 0,
            },
        )
        dataset_row["record_count"] += 1
        dataset_row["releases"].add(release)
        dataset_row["source_files"].add(source_file)
        if payload_hash:
            dataset_row["source_payload_hash_count"] += 1
            source_payload_hashes[payload_hash] = source_payload_hashes.get(payload_hash, 0) + 1
        if snapshot_path:
            dataset_row["source_snapshot_count"] += 1
            snapshot_paths[snapshot_path] = snapshot_paths.get(snapshot_path, 0) + 1
        releases[release] = releases.get(release, 0) + 1
        source_row = source_files.setdefault(
            source_file,
            {
                "source_file": source_file,
                "record_count": 0,
                "source_sha256": source_sha256,
                "datasets": set(),
                "releases": set(),
            },
        )
        source_row["record_count"] += 1
        source_row["datasets"].add(dataset)
        source_row["releases"].add(release)
        if source_sha256 and not source_row.get("source_sha256"):
            source_row["source_sha256"] = source_sha256
    dataset_rows = [
        {
            "dataset": row["dataset"],
            "record_count": row["record_count"],
            "releases": sorted(row["releases"]),
            "source_files": sorted(row["source_files"]),
            "source_payload_hash_count": row["source_payload_hash_count"],
            "source_snapshot_count": row["source_snapshot_count"],
        }
        for row in sorted(datasets.values(), key=lambda item: item["dataset"].lower())
    ]
    source_file_rows = [
        {
            "source_file": row["source_file"],
            "record_count": row["record_count"],
            "source_sha256": row.get("source_sha256") or "",
            "datasets": sorted(row["datasets"]),
            "releases": sorted(row["releases"]),
        }
        for row in sorted(source_files.values(), key=lambda item: item["source_file"].lower())
    ]
    return {
        "summary_schema": "agentic-rag-data-release-record-source-summary-v1",
        "record_count": len(records),
        "dataset_count": len(dataset_rows),
        "release_count": len(releases),
        "source_file_count": len(source_file_rows),
        "source_payload_hash_count": len(source_payload_hashes),
        "source_snapshot_count": len(snapshot_paths),
        "datasets": dataset_rows,
        "releases": [{"release": release, "record_count": count} for release, count in sorted(releases.items())],
        "source_files": source_file_rows,
    }


def _export_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if not key.startswith("__")}


def _verify_record_rows(archive: ZipFile, checks: dict[str, str], errors: list[str]) -> dict[str, Any]:
    jsonl_text = archive.read("records.jsonl").decode("utf-8")
    csv_text = archive.read("records.csv").decode("utf-8")
    jsonl_rows = [line for line in jsonl_text.splitlines() if line.strip()]
    csv_rows = list(csv.DictReader(StringIO(csv_text)))
    snapshot_references: set[str] = set()
    evidence = {
        "jsonl_rows": len(jsonl_rows),
        "csv_rows": len(csv_rows),
        "records": [],
        "records_hash": _hash_text(jsonl_text),
        "records_csv_hash": _hash_text(csv_text),
        "external_snapshot_reference_basenames": [],
    }
    if len(jsonl_rows) != len(csv_rows):
        errors.append("records.jsonl and records.csv row counts differ.")
        checks["record_rows"] = "fail"
        return evidence
    for line in jsonl_rows:
        payload = json.loads(line)
        if not isinstance(payload, dict) or not payload.get("id") or not payload.get("dataset"):
            errors.append("records.jsonl contains a row without id or dataset.")
            checks["record_rows"] = "fail"
            return evidence
        snapshot_path = payload.get("source_snapshot_path")
        if snapshot_path:
            snapshot_references.add(Path(str(snapshot_path)).name)
        evidence["records"].append(payload)
    checks["record_rows"] = "pass"
    evidence["external_snapshot_reference_basenames"] = sorted(snapshot_references)
    return evidence


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


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _hash_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _hash_payload(payload: Any) -> str:
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _release_handoff_hash(metadata: dict[str, Any]) -> str:
    payload = {
        "bundle_schema": metadata.get("bundle_schema"),
        "structured_manifest_hash": metadata.get("structured_manifest_hash"),
        "rag_structured_manifest_hash": metadata.get("rag_structured_manifest_hash"),
        "rag_index_hash": metadata.get("rag_index_hash"),
        "record_count": metadata.get("record_count"),
        "records_hash": metadata.get("records_hash"),
        "records_csv_hash": metadata.get("records_csv_hash"),
        "record_source_summary_hash": metadata.get("record_source_summary_hash"),
        "dataset_count": metadata.get("dataset_count"),
        "release_count": metadata.get("release_count"),
        "source_file_count": metadata.get("source_file_count"),
        "structured_file_count": metadata.get("structured_file_count"),
        "external_snapshot_reference_count": metadata.get("external_snapshot_reference_count"),
        "quality_status": metadata.get("quality_status"),
        "provenance_status": metadata.get("provenance_status"),
        "trna_caveat_count": metadata.get("trna_caveat_count"),
        "trna_blocking_production_use": metadata.get("trna_blocking_production_use"),
        "release_lock_status": metadata.get("release_lock_status"),
        "promotion_status": metadata.get("promotion_status"),
        "contains_source_bytes": metadata.get("contains_source_bytes"),
        "contains_external_snapshot_bytes": metadata.get("contains_external_snapshot_bytes"),
    }
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
