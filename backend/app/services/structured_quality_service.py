from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.external_data_service import external_source_coverage
from app.services.structured_data_service import load_structured_records, structured_coverage_matrix, structured_manifest_hash, validate_structured_records


QUALITY_SCHEMA = "agentic-rag-structured-quality-gate-v1"
TRACKED_DATASETS = ("GTEx", "Allen Brain Cell Atlas", "CUSTOM", "Kapur brain tRNA")


def structured_quality_gate() -> dict[str, Any]:
    records = load_structured_records()
    validation = validate_structured_records()
    coverage = structured_coverage_matrix()
    external_coverage = external_source_coverage()
    datasets = [_dataset_quality(dataset, [record for record in records if record.get("dataset") == dataset]) for dataset in TRACKED_DATASETS]
    dataset_statuses = [item["status"] for item in datasets]
    blocking = _blocking_items(datasets, validation)
    warnings = _warnings(datasets, validation)
    status = "fail" if blocking else "warning" if warnings else "pass"
    return {
        "quality_schema": QUALITY_SCHEMA,
        "status": status,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "manifest_hash": structured_manifest_hash(),
        "record_count": len(records),
        "dataset_count": len([item for item in datasets if item["records"] > 0]),
        "validation": {
            "records": validation["records"],
            "error_count": validation["error_count"],
            "warning_count": validation["warning_count"],
        },
        "coverage": {
            "gene_count": coverage.get("gene_count"),
            "brain_region_count": coverage.get("brain_region_count"),
            "cell_type_count": coverage.get("cell_type_count"),
            "live_record_fraction": _safe_fraction(sum(item["live_records"] for item in datasets), sum(item["records"] for item in datasets)),
            "release_pinned_fraction": _safe_fraction(sum(item["release_pinned_records"] for item in datasets), sum(item["records"] for item in datasets)),
        },
        "external_source_coverage": external_coverage,
        "datasets": datasets,
        "summary": {
            "pass_count": sum(1 for status_item in dataset_statuses if status_item == "pass"),
            "warning_count": sum(1 for status_item in dataset_statuses if status_item == "warning"),
            "fail_count": sum(1 for status_item in dataset_statuses if status_item == "fail"),
            "blocking_count": len(blocking),
            "warning_count_total": len(warnings),
        },
        "blocking_items": blocking,
        "warnings": warnings,
        "operator_actions": _operator_actions(blocking, warnings),
    }


def _dataset_quality(dataset: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    live_records = [record for record in records if _is_live_record(record)]
    seed_records = [record for record in records if _is_seed_record(record)]
    release_pinned_records = [record for record in records if record.get("release")]
    payload_hash_records = [record for record in records if record.get("source_payload_sha256")]
    snapshot_records = [record for record in records if record.get("source_snapshot_path")]
    source_url_records = [record for record in records if record.get("source_url")]
    caveats = _dataset_caveats(dataset, records, live_records, seed_records, payload_hash_records, snapshot_records)
    status = "fail" if any(item["severity"] == "error" for item in caveats) else "warning" if caveats else "pass"
    return {
        "dataset": dataset,
        "status": status,
        "records": len(records),
        "live_records": len(live_records),
        "seed_records": len(seed_records),
        "release_pinned_records": len(release_pinned_records),
        "source_payload_hash_records": len(payload_hash_records),
        "source_snapshot_records": len(snapshot_records),
        "source_url_records": len(source_url_records),
        "fractions": {
            "live": _safe_fraction(len(live_records), len(records)),
            "seed": _safe_fraction(len(seed_records), len(records)),
            "release": _safe_fraction(len(release_pinned_records), len(records)),
            "source_payload_hash": _safe_fraction(len(payload_hash_records), len(records)),
            "source_snapshot": _safe_fraction(len(snapshot_records), len(records)),
            "source_url": _safe_fraction(len(source_url_records), len(records)),
        },
        "releases": sorted({str(record.get("release")) for record in records if record.get("release")})[:20],
        "source_files": sorted({str(record.get("_source_file")) for record in records if record.get("_source_file")})[:20],
        "caveats": caveats,
        "recommendation": _dataset_recommendation(dataset, records, caveats),
    }


def _dataset_caveats(
    dataset: str,
    records: list[dict[str, Any]],
    live_records: list[dict[str, Any]],
    seed_records: list[dict[str, Any]],
    payload_hash_records: list[dict[str, Any]],
    snapshot_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    caveats: list[dict[str, Any]] = []
    if not records:
        caveats.append({"severity": "warning", "code": "dataset_missing", "message": f"{dataset} has no structured records loaded."})
        return caveats
    if dataset in {"GTEx", "Allen Brain Cell Atlas"} and not live_records:
        caveats.append({"severity": "error", "code": "no_live_records", "message": f"{dataset} has no live API/file snapshot-backed records."})
    if dataset in {"CUSTOM", "Kapur brain tRNA"} and seed_records:
        caveats.append({"severity": "warning", "code": "seed_prior_present", "message": f"{dataset} still includes seed/local prior records."})
    if dataset == "Kapur brain tRNA":
        caveats.append({"severity": "warning", "code": "trna_quantitative_review_required", "message": "Kapur-style tRNA priors require release-pinned quantitative source review before production interpretation."})
    if records and len(payload_hash_records) < len(records):
        caveats.append({"severity": "warning", "code": "payload_hash_gap", "message": f"{len(records) - len(payload_hash_records)} records lack source_payload_sha256."})
    if records and len(snapshot_records) < len(records):
        caveats.append({"severity": "warning", "code": "snapshot_gap", "message": f"{len(records) - len(snapshot_records)} records lack source_snapshot_path."})
    return caveats


def _blocking_items(datasets: list[dict[str, Any]], validation: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    if validation["error_count"]:
        items.append({"scope": "structured_validation", "code": "validation_errors", "message": f"{validation['error_count']} structured validation errors."})
    for dataset in datasets:
        for caveat in dataset["caveats"]:
            if caveat["severity"] == "error":
                items.append({"scope": dataset["dataset"], **caveat})
    return items


def _warnings(datasets: list[dict[str, Any]], validation: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    if validation["warning_count"]:
        items.append({"scope": "structured_validation", "code": "validation_warnings", "message": f"{validation['warning_count']} structured validation warnings."})
    for dataset in datasets:
        for caveat in dataset["caveats"]:
            if caveat["severity"] == "warning":
                items.append({"scope": dataset["dataset"], **caveat})
    return items


def _operator_actions(blocking: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    if any(item["code"] == "no_live_records" for item in blocking):
        actions.append("Run GTEx/Allen live refresh or import release-pinned source snapshots before production use.")
    if any(item["code"] == "seed_prior_present" for item in warnings):
        actions.append("Replace CUSTOM/Kapur seed priors with release-pinned quantitative matrices as they become available.")
    if any(item["code"] in {"payload_hash_gap", "snapshot_gap"} for item in warnings):
        actions.append("Run external source snapshot backfill and verify source_payload_sha256 coverage.")
    if not actions:
        actions.append("Structured data quality gate is clean under current checks.")
    return actions


def _dataset_recommendation(dataset: str, records: list[dict[str, Any]], caveats: list[dict[str, Any]]) -> str:
    if not records:
        return f"Load {dataset} structured records."
    if not caveats:
        return f"{dataset} records meet current structured quality checks."
    return caveats[0]["message"]


def _is_live_record(record: dict[str, Any]) -> bool:
    release = str(record.get("release") or "").lower()
    return bool(record.get("source_request_url") and record.get("source_payload_sha256") and record.get("source_snapshot_path") and "seed" not in release)


def _is_seed_record(record: dict[str, Any]) -> bool:
    release = str(record.get("release") or "").lower()
    source_file = str(record.get("_source_file") or "").lower()
    summary = str(record.get("summary") or "").lower()
    return "seed" in release or "seed" in source_file or "placeholder" in summary


def _safe_fraction(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0
