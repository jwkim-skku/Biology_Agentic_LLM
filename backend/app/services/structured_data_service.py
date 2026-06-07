from __future__ import annotations

import csv
import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.optimizer.codon_table import DNA_CODON_TABLE


DATA_DIR = get_settings().data_dir
STRUCTURED_DIR = DATA_DIR / "structured"
VALID_CODONS = {codon for codon, amino_acid in DNA_CODON_TABLE.items() if amino_acid != "*"}


def structured_status() -> dict[str, Any]:
    records = load_structured_records()
    by_dataset: dict[str, int] = {}
    for record in records:
        by_dataset[record["dataset"]] = by_dataset.get(record["dataset"], 0) + 1
    validation = validate_structured_records()
    provenance = _provenance_summary(records)
    return {
        "records": len(records),
        "datasets": by_dataset,
        "structured_path": str(STRUCTURED_DIR),
        "formats": ["json", "csv"],
        "manifest_hash": structured_manifest_hash(),
        "provenance": provenance,
        "validation": {
            "errors": validation["error_count"],
            "warnings": validation["warning_count"],
        },
    }


def structured_coverage_matrix() -> dict[str, Any]:
    records = load_structured_records()
    datasets = sorted({str(record.get("dataset") or "unknown") for record in records})
    genes = sorted({str(record.get("gene")) for record in records if record.get("gene")})
    regions = sorted({str(record.get("brain_region")) for record in records if record.get("brain_region")})
    cell_types = sorted({str(record.get("cell_type")) for record in records if record.get("cell_type")})
    releases = sorted({str(record.get("release")) for record in records if record.get("release")})
    source_files = sorted({str(record.get("_source_file")) for record in records if record.get("_source_file")})
    by_dataset = [_coverage_group(dataset, [record for record in records if record.get("dataset") == dataset]) for dataset in datasets]
    gene_region = _gene_region_matrix(records)
    cell_type_rows = _cell_type_matrix(records)
    production = _production_readiness(records)
    return {
        "coverage_schema": "agentic-rag-structured-coverage-v1",
        "manifest_hash": structured_manifest_hash(),
        "record_count": len(records),
        "dataset_count": len(datasets),
        "gene_count": len(genes),
        "brain_region_count": len(regions),
        "cell_type_count": len(cell_types),
        "release_count": len(releases),
        "source_file_count": len(source_files),
        "datasets": by_dataset,
        "gene_region_matrix": gene_region,
        "cell_type_matrix": cell_type_rows,
        "production_readiness": production,
        "top_targets": {
            "genes": genes[:25],
            "brain_regions": regions[:25],
            "cell_types": cell_types[:25],
            "releases": releases[:25],
        },
    }


@lru_cache(maxsize=1)
def load_structured_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not STRUCTURED_DIR.exists():
        return records
    for path in sorted(STRUCTURED_DIR.glob("*")):
        if path.suffix.lower() not in {".json", ".csv"}:
            continue
        source_hash = _file_sha256(path)
        if path.suffix.lower() == ".json":
            loaded = _load_json_records(path)
        else:
            loaded = _load_csv_records(path)
        for record in loaded:
            record["_source_file"] = path.name
            record["_source_sha256"] = source_hash
        records.extend(loaded)
    return [_normalize_record(record) for record in records]


def structured_manifest() -> dict[str, Any]:
    records = load_structured_records()
    files = []
    for path in sorted(STRUCTURED_DIR.glob("*")) if STRUCTURED_DIR.exists() else []:
        if path.suffix.lower() not in {".json", ".csv"}:
            continue
        file_records = [record for record in records if record.get("_source_file") == path.name]
        files.append(
            {
                "file": path.name,
                "path": str(path),
                "sha256": _file_sha256(path),
                "bytes": path.stat().st_size,
                "records": len(file_records),
                "datasets": sorted({record.get("dataset") for record in file_records if record.get("dataset")}),
                "releases": sorted({str(record.get("release")) for record in file_records if record.get("release")}),
            }
        )
    validation = validate_structured_records()
    return {
        "structured_path": str(STRUCTURED_DIR),
        "manifest_hash": structured_manifest_hash(),
        "files": files,
        "provenance": _provenance_summary(records),
        "validation": validation,
    }


def structured_manifest_hash() -> str:
    if not STRUCTURED_DIR.exists():
        return "empty"
    payload = []
    for path in sorted(STRUCTURED_DIR.glob("*")):
        if path.suffix.lower() in {".json", ".csv"}:
            payload.append({"file": path.name, "sha256": _file_sha256(path), "bytes": path.stat().st_size})
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def validate_structured_records() -> dict[str, Any]:
    return _validate_records(load_structured_records())


def preview_structured_import(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if not source.exists() or source.suffix.lower() not in {".json", ".csv"}:
        raise ValueError("Structured import preview expects an existing .json or .csv file.")
    source_hash = _file_sha256(source)
    imported_records = _load_records_from_path(source)
    normalized_import = []
    for record in imported_records:
        record["_source_file"] = source.name
        record["_source_sha256"] = source_hash
        normalized_import.append(_normalize_record(record))

    current_records = load_structured_records()
    existing_without_target = [record for record in current_records if record.get("_source_file") != source.name]
    projected_records = existing_without_target + normalized_import
    current_ids = {str(record.get("id") or "") for record in existing_without_target if record.get("id")}
    import_ids = [str(record.get("id") or "") for record in normalized_import if record.get("id")]
    duplicate_import_ids = sorted({record_id for record_id in import_ids if import_ids.count(record_id) > 1})
    colliding_existing_ids = sorted({record_id for record_id in import_ids if record_id in current_ids})
    would_replace = any(record.get("_source_file") == source.name for record in current_records)
    import_validation = _validate_records(normalized_import)
    projected_validation = _validate_records(projected_records)
    projected_manifest_hash = _manifest_hash_for_files(_projected_file_entries(source, source_hash))
    status = "fail" if projected_validation["error_count"] else "warning" if projected_validation["warning_count"] else "pass"

    return {
        "status": status,
        "source": {
            "path": str(source),
            "file": source.name,
            "sha256": source_hash,
            "bytes": source.stat().st_size,
            "suffix": source.suffix.lower(),
        },
        "target": {
            "path": str(STRUCTURED_DIR / source.name),
            "would_replace": would_replace,
            "existing_records_replaced": len(current_records) - len(existing_without_target),
        },
        "records": {
            "import_count": len(normalized_import),
            "current_count": len(current_records),
            "projected_count": len(projected_records),
            "datasets": _dataset_counts(normalized_import),
            "releases": sorted({str(record.get("release")) for record in normalized_import if record.get("release")}),
            "duplicate_import_ids": duplicate_import_ids[:100],
            "colliding_existing_ids": colliding_existing_ids[:100],
        },
        "manifest": {
            "current_hash": structured_manifest_hash(),
            "projected_hash": projected_manifest_hash,
        },
        "validation": {
            "import": import_validation,
            "projected": projected_validation,
        },
        "provenance": {
            "import": _provenance_summary(normalized_import),
            "projected": _provenance_summary(projected_records),
        },
    }


def _validate_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for idx, record in enumerate(records):
        locator = {"index": idx, "id": record.get("id"), "source_file": record.get("_source_file")}
        for key in ["id", "dataset", "summary"]:
            if not record.get(key):
                errors.append({**locator, "field": key, "message": "required field is missing"})
        if record.get("id") in seen_ids:
            errors.append({**locator, "field": "id", "message": "duplicate record id"})
        seen_ids.add(record.get("id"))

        confidence = str(record.get("confidence", "")).lower()
        if confidence not in {"high", "medium", "low"}:
            warnings.append({**locator, "field": "confidence", "message": "confidence should be high, medium, or low"})

        if record.get("dataset") == "GTEx" and record.get("coverage_level") == "gene_tissue_expression":
            if record.get("median_expression") is None:
                errors.append({**locator, "field": "median_expression", "message": "GTEx expression record requires median_expression"})
            if not record.get("gene") or not record.get("gencode_id"):
                errors.append({**locator, "field": "gene", "message": "GTEx expression record requires gene and gencode_id"})
            if not record.get("source_payload_sha256"):
                warnings.append({**locator, "field": "source_payload_sha256", "message": "GTEx expression record should include source payload hash"})

        if record.get("dataset") == "Allen Brain Cell Atlas" and not record.get("source_payload_sha256"):
            warnings.append({**locator, "field": "source_payload_sha256", "message": "Allen record should include source payload hash"})

        if record.get("dataset") in {"GTEx", "Allen Brain Cell Atlas", "CUSTOM", "Kapur brain tRNA"}:
            if not record.get("release"):
                warnings.append({**locator, "field": "release", "message": "release should be populated for production provenance"})
            if not record.get("source_url"):
                warnings.append({**locator, "field": "source_url", "message": "source_url should be populated for production provenance"})

        for field in ["codon_weight_multipliers", "codon_availability_weights"]:
            weights = record.get(field)
            if weights is None:
                continue
            if not isinstance(weights, dict) or not weights:
                errors.append({**locator, "field": field, "message": "codon matrix must be a non-empty object"})
                continue
            for codon, value in weights.items():
                if str(codon).upper() not in VALID_CODONS:
                    errors.append({**locator, "field": field, "message": f"invalid DNA coding codon: {codon}"})
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    errors.append({**locator, "field": field, "message": f"non-numeric weight for {codon}"})
                    continue
                if numeric <= 0:
                    errors.append({**locator, "field": field, "message": f"weight must be positive for {codon}"})
                elif numeric < 0.5 or numeric > 1.5:
                    warnings.append({**locator, "field": field, "message": f"weight for {codon} is outside conservative range"})

    return {
        "records": len(records),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors[:100],
        "warnings": warnings[:100],
    }


def structured_records_as_evidence() -> list[dict[str, Any]]:
    evidence = []
    for record in load_structured_records():
        dataset = record["dataset"]
        collection = {
            "GTEx": "tissue_evidence",
            "Allen Brain Cell Atlas": "brain_cell_atlas",
            "CUSTOM": "structured_translation_prior",
            "Kapur brain tRNA": "trna_availability_prior",
        }.get(dataset, "structured_data")
        evidence.append(
            {
                "id": record["id"],
                "title": _record_title(record),
                "collection": collection,
                "source": dataset,
                "source_url": record.get("source_url", ""),
                "evidence_class": record.get("coverage_level", "structured_prior"),
                "confidence": record.get("confidence", "medium"),
                "species": _as_list(record.get("species")),
                "topics": _topics(record),
                "regions": _as_list(record.get("brain_region")),
                "cell_types": _as_list(record.get("cell_type")),
                "modalities": [],
                "summary": record.get("summary", ""),
                "content": _record_content(record),
                "source_file": record.get("_source_file"),
                "source_sha256": record.get("_source_sha256"),
            }
        )
    return evidence


def search_structured_context(target: dict[str, Any], limit: int = 8) -> dict[str, Any]:
    scored = []
    for record in load_structured_records():
        score = _target_match_score(record, target)
        if score <= 0:
            continue
        scored.append({**record, "match_score": round(score, 4)})
    scored.sort(key=lambda item: (item["match_score"], item.get("confidence") == "high"), reverse=True)
    selected = scored[:limit]
    return {
        "query": {
            "species": target.get("species"),
            "brain_region": target.get("brain_region"),
            "cell_type": target.get("cell_type"),
        },
        "coverage": _structured_coverage(selected),
        "records": selected,
    }


def codon_weight_multipliers(target: dict[str, Any]) -> dict[str, float]:
    multipliers: dict[str, float] = {}
    context = search_structured_context(target, limit=12)
    for record in context["records"]:
        if record.get("dataset") != "CUSTOM":
            continue
        record_weight = 1.0 + min(max(float(record.get("match_score", 0.0)), 0.0), 1.0) * 0.25
        for codon, multiplier in (record.get("codon_weight_multipliers") or {}).items():
            multipliers[codon.upper()] = max(multipliers.get(codon.upper(), 1.0), 1.0 + (float(multiplier) - 1.0) * record_weight)
    return multipliers


def codon_availability_weights(target: dict[str, Any]) -> dict[str, float]:
    weights: dict[str, float] = {}
    context = search_structured_context(target, limit=16)
    for record in context["records"]:
        if "codon_availability_weights" not in record:
            continue
        record_weight = 1.0 + min(max(float(record.get("match_score", 0.0)), 0.0), 1.0) * 0.20
        for codon, value in (record.get("codon_availability_weights") or {}).items():
            adjusted = 1.0 + (float(value) - 1.0) * record_weight
            existing = weights.get(codon.upper())
            if existing is None or abs(adjusted - 1.0) > abs(existing - 1.0):
                weights[codon.upper()] = adjusted
    return weights


def import_structured_records(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if not source.exists() or source.suffix.lower() not in {".json", ".csv"}:
        raise ValueError("Structured import expects an existing .json or .csv file.")
    STRUCTURED_DIR.mkdir(parents=True, exist_ok=True)
    target = STRUCTURED_DIR / source.name
    target.write_bytes(source.read_bytes())
    load_structured_records.cache_clear()
    return {"imported_path": str(target), "status": structured_status()}


def _load_records_from_path(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        return _load_json_records(path)
    if path.suffix.lower() == ".csv":
        return _load_csv_records(path)
    raise ValueError(f"Unsupported structured import file type: {path.suffix}")


def _projected_file_entries(source: Path, source_hash: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if STRUCTURED_DIR.exists():
        for path in sorted(STRUCTURED_DIR.glob("*")):
            if path.suffix.lower() not in {".json", ".csv"} or path.name == source.name:
                continue
            entries.append({"file": path.name, "sha256": _file_sha256(path), "bytes": path.stat().st_size})
    entries.append({"file": source.name, "sha256": source_hash, "bytes": source.stat().st_size})
    return sorted(entries, key=lambda item: item["file"])


def _manifest_hash_for_files(entries: list[dict[str, Any]]) -> str:
    encoded = json.dumps(entries, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _dataset_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        dataset = str(record.get("dataset") or "unknown")
        counts[dataset] = counts.get(dataset, 0) + 1
    return counts


def _load_json_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("records", [payload])
    if not isinstance(payload, list):
        raise ValueError(f"Structured JSON must contain a list of records: {path}")
    return [record for record in payload if isinstance(record, dict)]


def _load_csv_records(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    dataset = str(record.get("dataset") or record.get("source") or "structured").strip()
    normalized = {**record}
    normalized["dataset"] = dataset
    normalized["id"] = str(record.get("id") or f"{dataset}_{len(repr(record))}").strip()
    normalized["species"] = _as_list(record.get("species"))
    normalized["brain_region"] = _clean_optional(record.get("brain_region") or record.get("region") or record.get("tissue"))
    normalized["cell_type"] = _clean_optional(record.get("cell_type"))
    normalized["confidence"] = str(record.get("confidence") or "medium").lower()
    normalized["_source_file"] = record.get("_source_file")
    normalized["_source_sha256"] = record.get("_source_sha256")
    return normalized


def _record_title(record: dict[str, Any]) -> str:
    parts = [record["dataset"], record.get("brain_region"), record.get("cell_type")]
    return " / ".join(str(part) for part in parts if part)


def _record_content(record: dict[str, Any]) -> str:
    return " ".join(
        str(value)
        for value in [
            _record_title(record),
            record.get("summary"),
            record.get("release"),
            record.get("coverage_level"),
            record.get("tissue"),
        ]
        if value
    )


def _topics(record: dict[str, Any]) -> list[str]:
    topics = [
        record["dataset"],
        record.get("coverage_level"),
        record.get("brain_region"),
        record.get("cell_type"),
        record.get("gene"),
        record.get("gencode_id"),
        "tRNA" if record.get("codon_availability_weights") else None,
    ]
    return [str(topic) for topic in topics if topic]


def _target_match_score(record: dict[str, Any], target: dict[str, Any]) -> float:
    score = 0.0
    target_species = _normalize_text(target.get("species"))
    if target_species and target_species in {_normalize_text(item) for item in _as_list(record.get("species"))}:
        score += 0.20
    if _loose_match(target.get("brain_region"), record.get("brain_region")):
        score += 0.36
    elif _loose_match(target.get("brain_region"), record.get("tissue")):
        score += 0.22
    if _loose_match(target.get("cell_type"), record.get("cell_type")):
        score += 0.34
    if _loose_match(target.get("gene"), record.get("gene")):
        score += 0.28
    if record.get("dataset") in {"GTEx", "Allen Brain Cell Atlas", "CUSTOM"}:
        score += 0.10
    if record.get("dataset") == "Kapur brain tRNA":
        score += 0.10
    return min(score, 1.0)


def _structured_coverage(records: list[dict[str, Any]]) -> dict[str, str]:
    datasets = {record.get("dataset") for record in records}
    return {
        "gtex": "available" if "GTEx" in datasets else "missing",
        "gtex_gene_expression": "available"
        if any(record.get("dataset") == "GTEx" and record.get("coverage_level") == "gene_tissue_expression" for record in records)
        else "missing",
        "allen": "available" if "Allen Brain Cell Atlas" in datasets else "missing",
        "custom": "available" if "CUSTOM" in datasets else "missing",
        "trna": "available" if "Kapur brain tRNA" in datasets else "missing",
    }


def _coverage_group(dataset: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    releases = sorted({str(record.get("release")) for record in records if record.get("release")})
    genes = sorted({str(record.get("gene")) for record in records if record.get("gene")})
    regions = sorted({str(record.get("brain_region")) for record in records if record.get("brain_region")})
    cell_types = sorted({str(record.get("cell_type")) for record in records if record.get("cell_type")})
    live_records = [record for record in records if _is_live_record(record)]
    seed_records = [record for record in records if _is_seed_record(record)]
    snapshot_records = [record for record in records if record.get("source_snapshot_path")]
    payload_records = [record for record in records if record.get("source_payload_sha256")]
    return {
        "dataset": dataset,
        "records": len(records),
        "live_records": len(live_records),
        "seed_records": len(seed_records),
        "snapshot_fraction": _fraction(len(snapshot_records), len(records)),
        "payload_hash_fraction": _fraction(len(payload_records), len(records)),
        "genes": genes[:20],
        "brain_regions": regions[:20],
        "cell_types": cell_types[:20],
        "releases": releases[:20],
        "production_status": "warning" if seed_records else "pass",
    }


def _gene_region_matrix(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        gene = record.get("gene")
        region = record.get("brain_region")
        if gene and region:
            grouped.setdefault((str(gene), str(region)), []).append(record)
    rows = []
    for (gene, region), items in sorted(grouped.items()):
        rows.append(
            {
                "gene": gene,
                "brain_region": region,
                "records": len(items),
                "datasets": sorted({str(item.get("dataset")) for item in items if item.get("dataset")}),
                "live_records": sum(1 for item in items if _is_live_record(item)),
                "seed_records": sum(1 for item in items if _is_seed_record(item)),
                "median_expression_max": _max_float(item.get("median_expression") for item in items),
                "releases": sorted({str(item.get("release")) for item in items if item.get("release")})[:10],
            }
        )
    return rows[:100]


def _cell_type_matrix(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        cell_type = record.get("cell_type")
        if not cell_type:
            continue
        region = str(record.get("brain_region") or "unspecified")
        grouped.setdefault((region, str(cell_type)), []).append(record)
    rows = []
    for (region, cell_type), items in grouped.items():
        rows.append(
            {
                "brain_region": region,
                "cell_type": cell_type,
                "records": len(items),
                "datasets": sorted({str(item.get("dataset")) for item in items if item.get("dataset")}),
                "live_records": sum(1 for item in items if _is_live_record(item)),
                "seed_records": sum(1 for item in items if _is_seed_record(item)),
                "cell_count_max": _max_float(item.get("number_of_cells") for item in items),
                "releases": sorted({str(item.get("release")) for item in items if item.get("release")})[:10],
            }
        )
    rows.sort(key=lambda row: (row["live_records"], row["records"]), reverse=True)
    return rows[:100]


def _production_readiness(records: list[dict[str, Any]]) -> dict[str, Any]:
    seed_records = [record for record in records if _is_seed_record(record)]
    live_records = [record for record in records if _is_live_record(record)]
    missing_snapshots = [record for record in records if not record.get("source_snapshot_path")]
    missing_payload_hashes = [record for record in records if not record.get("source_payload_sha256")]
    status = "warning" if seed_records or missing_snapshots or missing_payload_hashes else "pass"
    return {
        "status": status,
        "live_record_fraction": _fraction(len(live_records), len(records)),
        "seed_record_fraction": _fraction(len(seed_records), len(records)),
        "records_with_snapshots_fraction": _fraction(len(records) - len(missing_snapshots), len(records)),
        "records_with_payload_hash_fraction": _fraction(len(records) - len(missing_payload_hashes), len(records)),
        "seed_record_count": len(seed_records),
        "live_record_count": len(live_records),
        "missing_snapshot_count": len(missing_snapshots),
        "missing_payload_hash_count": len(missing_payload_hashes),
        "operator_action": "Replace seed/local priors with release-pinned quantitative artifacts before final production interpretation."
        if seed_records
        else "All structured records are live or release-pinned by current heuristics.",
    }


def _is_live_record(record: dict[str, Any]) -> bool:
    release = _normalize_text(record.get("release"))
    return bool(record.get("source_request_url") and record.get("source_payload_sha256") and "seed" not in release)


def _is_seed_record(record: dict[str, Any]) -> bool:
    release = _normalize_text(record.get("release"))
    source_file = _normalize_text(record.get("_source_file"))
    summary = _normalize_text(record.get("summary"))
    return "seed" in release or "seed" in source_file or "placeholder" in summary


def _fraction(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _max_float(values: Any) -> float | None:
    parsed = []
    for value in values:
        try:
            parsed.append(float(value))
        except (TypeError, ValueError):
            continue
    return round(max(parsed), 4) if parsed else None


def _provenance_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    tracked = [record for record in records if record.get("dataset") in {"GTEx", "Allen Brain Cell Atlas", "CUSTOM", "Kapur brain tRNA"}]
    if not tracked:
        return {
            "tracked_records": 0,
            "release_fraction": 0.0,
            "source_url_fraction": 0.0,
            "source_payload_hash_fraction": 0.0,
            "source_files": [],
            "source_payload_hashes": [],
        }
    return {
        "tracked_records": len(tracked),
        "release_fraction": _field_fraction(tracked, "release"),
        "source_url_fraction": _field_fraction(tracked, "source_url"),
        "source_payload_hash_fraction": _field_fraction(tracked, "source_payload_sha256"),
        "source_files": sorted({str(record.get("_source_file")) for record in tracked if record.get("_source_file")}),
        "source_payload_hashes": sorted({str(record.get("source_payload_sha256")) for record in tracked if record.get("source_payload_sha256")}),
    }


def _field_fraction(records: list[dict[str, Any]], field: str) -> float:
    if not records:
        return 0.0
    present = sum(1 for record in records if record.get(field))
    return round(present / len(records), 4)


def _as_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and ";" in value:
        return [item.strip() for item in value.split(";") if item.strip()]
    return [str(value)]


def _clean_optional(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _normalize_text(value: Any) -> str:
    return str(value or "").lower().replace("_", " ").strip()


def _loose_match(left: Any, right: Any) -> bool:
    left_norm = _normalize_text(left)
    right_norm = _normalize_text(right)
    if not left_norm or not right_norm:
        return False
    left_tokens = {token for token in left_norm.replace("-", " ").split() if len(token) >= 3}
    right_tokens = {token for token in right_norm.replace("-", " ").split() if len(token) >= 3}
    return bool(left_tokens & right_tokens) or left_norm in right_norm or right_norm in left_norm


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
