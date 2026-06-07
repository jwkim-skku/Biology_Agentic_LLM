from __future__ import annotations

import csv
import hashlib
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.rag_service import rebuild_rag_index
from app.services.structured_data_service import STRUCTURED_DIR, load_structured_records, structured_status


GTEX_API_BASE = "https://gtexportal.org/api/v2"
ALLEN_WHB_TAXONOMY_BASE = "https://allen-brain-cell-atlas.s3.us-west-2.amazonaws.com/metadata/WHB-taxonomy/20240330"
EXTERNAL_SOURCE_DIR = STRUCTURED_DIR.parent / "runtime" / "external_sources"

GTEX_BRAIN_TISSUES = {
    "substantia nigra": "Brain_Substantia_nigra",
    "striatum": "Brain_Caudate_basal_ganglia",
    "caudate": "Brain_Caudate_basal_ganglia",
    "putamen": "Brain_Putamen_basal_ganglia",
    "nucleus accumbens": "Brain_Nucleus_accumbens_basal_ganglia",
    "cortex": "Brain_Cortex",
    "frontal cortex": "Brain_Frontal_Cortex_BA9",
    "hippocampus": "Brain_Hippocampus",
    "amygdala": "Brain_Amygdala",
    "hypothalamus": "Brain_Hypothalamus",
    "cerebellum": "Brain_Cerebellum",
}


def external_source_status() -> dict[str, Any]:
    files = []
    if EXTERNAL_SOURCE_DIR.exists():
        for path in sorted(EXTERNAL_SOURCE_DIR.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
            files.append(
                {
                    "file": path.name,
                    "path": str(path),
                    "bytes": path.stat().st_size,
                    "sha256": _file_sha256(path),
                    "source": payload.get("source"),
                    "schema": payload.get("snapshot_schema") or payload.get("source_manifest_schema"),
                    "captured_at": payload.get("captured_at") or payload.get("created_at"),
                    "url": payload.get("url"),
                    "snapshot_count": payload.get("snapshot_count"),
                }
            )
    coverage = external_source_coverage()
    return {
        "external_source_path": str(EXTERNAL_SOURCE_DIR),
        "file_count": len(files),
        "files": files,
        "coverage": coverage,
    }


def external_source_coverage() -> dict[str, Any]:
    records = load_structured_records()
    tracked = [record for record in records if record.get("dataset") in {"GTEx", "Allen Brain Cell Atlas", "CUSTOM", "Kapur brain tRNA"}]
    if not tracked:
        return {
            "tracked_records": 0,
            "source_snapshot_path_fraction": 0.0,
            "source_payload_hash_fraction": 0.0,
            "records_missing_snapshot": [],
            "records_missing_payload_hash": [],
        }
    missing_snapshot = [record for record in tracked if not record.get("source_snapshot_path")]
    missing_hash = [record for record in tracked if not record.get("source_payload_sha256")]
    return {
        "tracked_records": len(tracked),
        "source_snapshot_path_fraction": round((len(tracked) - len(missing_snapshot)) / len(tracked), 4),
        "source_payload_hash_fraction": round((len(tracked) - len(missing_hash)) / len(tracked), 4),
        "records_missing_snapshot": _record_refs(missing_snapshot),
        "records_missing_payload_hash": _record_refs(missing_hash),
    }


def backfill_external_source_snapshots(dry_run: bool = True) -> dict[str, Any]:
    STRUCTURED_DIR.mkdir(parents=True, exist_ok=True)
    EXTERNAL_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    planned: list[dict[str, Any]] = []
    written: list[dict[str, Any]] = []

    for path in sorted(STRUCTURED_DIR.glob("*.json")):
        records = _load_structured_json_file(path)
        if not records:
            continue
        missing = [
            record
            for record in records
            if isinstance(record, dict)
            and record.get("dataset") in {"GTEx", "Allen Brain Cell Atlas", "CUSTOM", "Kapur brain tRNA"}
            and (not record.get("source_snapshot_path") or not record.get("source_payload_sha256"))
        ]
        if not missing:
            continue
        source_hash = _file_sha256(path)
        snapshot_path = EXTERNAL_SOURCE_DIR / f"backfill_{path.stem}_{source_hash[:16]}.json"
        snapshot = _local_seed_snapshot(path, records, source_hash, snapshot_path)
        planned_item = {
            "source_file": path.name,
            "snapshot_file": snapshot_path.name,
            "snapshot_path": str(snapshot_path),
            "source_file_sha256": source_hash,
            "records_to_update": len(missing),
            "datasets": sorted({str(record.get("dataset")) for record in missing if record.get("dataset")}),
        }
        planned.append(planned_item)
        if dry_run:
            continue
        if not snapshot_path.exists():
            snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        updated_records = []
        for record in records:
            if isinstance(record, dict) and record in missing:
                updated = {**record}
                updated.setdefault("source_payload_sha256", source_hash)
                updated.setdefault("source_snapshot_path", str(snapshot_path))
                if updated.get("source_url") and not updated.get("source_request_url"):
                    updated["source_request_url"] = updated["source_url"]
                updated_records.append(updated)
            else:
                updated_records.append(record)
        path.write_text(json.dumps(updated_records, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(planned_item)

    if not dry_run and written:
        load_structured_records.cache_clear()

    return {
        "status": "planned" if dry_run else "completed",
        "dry_run": dry_run,
        "candidate_file_count": len(planned),
        "candidate_record_count": sum(item["records_to_update"] for item in planned),
        "backfilled_file_count": len(written),
        "backfilled_record_count": sum(item["records_to_update"] for item in written),
        "planned": planned,
        "coverage": external_source_coverage(),
    }


def import_gtex_gene_expression(
    gene: str,
    brain_regions: list[str] | None = None,
    dataset_id: str = "gtex_v8",
    rebuild_index_after: bool = True,
) -> dict[str, Any]:
    gene_record, gene_snapshot = _resolve_gtex_gene(gene)
    regions = brain_regions or ["substantia nigra", "striatum", "cortex", "hippocampus"]
    records = []
    snapshots = [gene_snapshot]
    for region in regions:
        tissue_id = _gtex_tissue_id(region)
        if not tissue_id:
            continue
        expression_payload, expression_snapshot = _gtex_get(
            "/expression/medianGeneExpression",
            {
                "gencodeId": gene_record["gencodeId"],
                "tissueSiteDetailId": tissue_id,
                "datasetId": dataset_id,
                "itemsPerPage": 25,
            },
        )
        snapshots.append(expression_snapshot)
        expression_rows = expression_payload.get("data", [])
        for row in expression_rows:
            records.append(_gtex_expression_record(gene_record, row, region, dataset_id, expression_snapshot))

    if not records:
        raise ValueError(f"No GTEx expression rows found for {gene} in requested regions.")

    suffix = _payload_hash({"gene": gene.upper(), "regions": regions, "dataset_id": dataset_id})
    target = _write_structured_json(f"gtex_expression_{gene.upper()}_{dataset_id}_{suffix}.json", records)
    source_manifest = _write_source_manifest("gtex", suffix, snapshots)
    load_structured_records.cache_clear()
    result = {"imported_path": str(target), "records": len(records), "source_manifest": str(source_manifest), "source_snapshots": snapshots, "status": structured_status()}
    if rebuild_index_after:
        result["index"] = rebuild_rag_index(persist=True)
    return result


def import_allen_whb_taxonomy(
    query_terms: list[str] | None = None,
    max_records: int = 250,
    rebuild_index_after: bool = True,
) -> dict[str, Any]:
    rows, source_snapshot = _download_csv(f"{ALLEN_WHB_TAXONOMY_BASE}/cluster_annotation_term.csv")
    terms = [_normalize(term) for term in (query_terms or []) if term.strip()]
    records = []
    for row in rows:
        name = row.get("name", "")
        description = row.get("description", "")
        term_level = row.get("cluster_annotation_term_set_name", "")
        if terms and not any(term in _normalize(f"{name} {description} {term_level}") for term in terms):
            continue
        records.append(_allen_taxonomy_record(row, source_snapshot))
        if len(records) >= max_records:
            break

    if not records:
        raise ValueError("No Allen taxonomy records matched the requested query terms.")

    suffix = _payload_hash({"terms": terms or ["sample"], "max_records": max_records})
    target = _write_structured_json(f"allen_whb_taxonomy_{suffix}.json", records)
    source_manifest = _write_source_manifest("allen_whb_taxonomy", suffix, [source_snapshot])
    load_structured_records.cache_clear()
    result = {"imported_path": str(target), "records": len(records), "source_manifest": str(source_manifest), "source_snapshots": [source_snapshot], "status": structured_status()}
    if rebuild_index_after:
        result["index"] = rebuild_rag_index(persist=True)
    return result


def _resolve_gtex_gene(gene: str) -> tuple[dict[str, Any], dict[str, Any]]:
    payload, snapshot = _gtex_get("/reference/geneSearch", {"geneId": gene, "itemsPerPage": 20})
    rows = payload.get("data", [])
    exact = [row for row in rows if row.get("geneSymbol", "").upper() == gene.upper()]
    protein_coding = [row for row in exact if row.get("geneType") == "protein coding"]
    selected = (protein_coding or exact or rows or [None])[0]
    if not selected:
        raise ValueError(f"GTEx gene search returned no records for {gene}.")
    return selected, snapshot


def _gtex_expression_record(
    gene_record: dict[str, Any],
    row: dict[str, Any],
    region: str,
    dataset_id: str,
    source_snapshot: dict[str, Any],
) -> dict[str, Any]:
    median = row.get("median")
    unit = row.get("unit", "TPM")
    return {
        "id": f"gtex_{gene_record['geneSymbol'].lower()}_{row.get('tissueSiteDetailId', region).lower()}",
        "dataset": "GTEx",
        "release": dataset_id,
        "species": "human",
        "gene": gene_record.get("geneSymbol"),
        "gencode_id": gene_record.get("gencodeId"),
        "brain_region": region,
        "tissue": row.get("tissueSiteDetailId"),
        "coverage_level": "gene_tissue_expression",
        "confidence": "high",
        "median_expression": median,
        "unit": unit,
        "summary": f"{gene_record.get('geneSymbol')} median expression in {row.get('tissueSiteDetailId')} is {median} {unit} in {dataset_id}.",
        "source_url": "https://gtexportal.org/home/",
        "source_request_url": source_snapshot["url"],
        "source_payload_sha256": source_snapshot["sha256"],
        "source_snapshot_path": source_snapshot["path"],
        "source_record_id": row.get("tissueSiteDetailId"),
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }


def _allen_taxonomy_record(row: dict[str, Any], source_snapshot: dict[str, Any]) -> dict[str, Any]:
    name = row.get("name", "")
    level = row.get("cluster_annotation_term_set_name", "")
    return {
        "id": f"allen_whb_{row.get('label', name).lower()}",
        "dataset": "Allen Brain Cell Atlas",
        "release": "WHB-taxonomy/20240330",
        "species": ["human"],
        "brain_region": "whole human brain",
        "cell_type": name,
        "coverage_level": level or "taxonomy_term",
        "confidence": "high",
        "number_of_cells": _to_int(row.get("number_of_cells")),
        "summary": row.get("description") or f"Allen WHB taxonomy term: {name} ({level}).",
        "source_url": f"{ALLEN_WHB_TAXONOMY_BASE}/cluster_annotation_term.csv",
        "source_request_url": source_snapshot["url"],
        "source_payload_sha256": source_snapshot["sha256"],
        "source_snapshot_path": source_snapshot["path"],
        "source_record_id": row.get("label") or name,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }


def _gtex_tissue_id(region: str) -> str | None:
    normalized = _normalize(region)
    if normalized in GTEX_BRAIN_TISSUES:
        return GTEX_BRAIN_TISSUES[normalized]
    for key, value in GTEX_BRAIN_TISSUES.items():
        if key in normalized or normalized in key:
            return value
    return None


def _gtex_get(path: str, params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    query = urllib.parse.urlencode(params, doseq=True)
    url = f"{GTEX_API_BASE}{path}?{query}"
    with urllib.request.urlopen(url, timeout=30) as response:
        body = response.read()
    payload = json.loads(body.decode("utf-8"))
    return payload, _write_source_snapshot(
        "gtex",
        {
            "url": url,
            "api_path": path,
            "params": params,
            "payload": payload,
        },
    )


def _download_csv(url: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
    with urllib.request.urlopen(url, timeout=45) as response:
        body = response.read()
    text = body.decode("utf-8-sig")
    rows = list(csv.DictReader(text.splitlines()))
    snapshot = _write_source_snapshot(
        "allen_whb_taxonomy",
        {
            "url": url,
            "format": "csv",
            "rows": len(rows),
            "payload_sha256": hashlib.sha256(body).hexdigest(),
            "header": list(rows[0].keys()) if rows else [],
        },
    )
    return rows, snapshot


def _write_structured_json(filename: str, records: list[dict[str, Any]]) -> Path:
    STRUCTURED_DIR.mkdir(parents=True, exist_ok=True)
    target = STRUCTURED_DIR / filename
    target.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def _write_source_snapshot(source: str, payload: dict[str, Any]) -> dict[str, Any]:
    EXTERNAL_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "snapshot_schema": "agentic-rag-external-source-snapshot-v1",
        "source": source,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    digest = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    target = EXTERNAL_SOURCE_DIR / f"{source}_{digest[:16]}.json"
    snapshot["sha256"] = digest
    snapshot["path"] = str(target)
    target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "source": source,
        "url": str(payload.get("url") or ""),
        "sha256": digest,
        "path": str(target),
        "captured_at": snapshot["captured_at"],
    }


def _write_source_manifest(source: str, suffix: str, snapshots: list[dict[str, Any]]) -> Path:
    EXTERNAL_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "source_manifest_schema": "agentic-rag-external-source-manifest-v1",
        "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_count": len(snapshots),
        "snapshots": sorted(snapshots, key=lambda item: (item.get("source", ""), item.get("url", ""))),
    }
    manifest["manifest_hash"] = hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    target = EXTERNAL_SOURCE_DIR / f"{source}_manifest_{suffix}.json"
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return target


def _load_structured_json_file(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(payload, dict):
        payload = payload.get("records", [payload])
    if not isinstance(payload, list):
        return []
    return [record for record in payload if isinstance(record, dict)]


def _local_seed_snapshot(path: Path, records: list[dict[str, Any]], source_hash: str, snapshot_path: Path) -> dict[str, Any]:
    return {
        "snapshot_schema": "agentic-rag-external-source-backfill-v1",
        "source": "local_structured_seed",
        "captured_at": "backfilled-from-local-structured-file",
        "url": _first_source_url(records),
        "source_file": path.name,
        "source_file_sha256": source_hash,
        "snapshot_path": str(snapshot_path),
        "record_count": len(records),
        "datasets": sorted({str(record.get("dataset")) for record in records if record.get("dataset")}),
        "record_ids": sorted(str(record.get("id")) for record in records if record.get("id")),
        "payload": records,
        "sha256": source_hash,
    }


def _first_source_url(records: list[dict[str, Any]]) -> str:
    for record in records:
        if isinstance(record, dict) and record.get("source_url"):
            return str(record["source_url"])
    return ""


def _record_refs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": record.get("id"),
            "dataset": record.get("dataset"),
            "source_file": record.get("_source_file"),
        }
        for record in records[:100]
    ]


def _normalize(value: str) -> str:
    return value.lower().replace("_", " ").replace("-", " ").strip()


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:10]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
