from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.config import get_settings
from app.services.external_data_service import external_source_status, import_allen_whb_taxonomy, import_gtex_gene_expression
from app.services.ingestion_service import list_ingested_documents
from app.services.rag_service import rag_status, rebuild_rag_index
from app.services.structured_data_service import structured_manifest, structured_status
from app.services.data_release_lock_service import verify_data_release_lock


DEFAULT_GENES = ["SNCA", "HTT", "GBA1", "MECP2"]
DEFAULT_BRAIN_REGIONS = ["substantia nigra", "striatum", "cortex", "hippocampus"]
DEFAULT_ALLEN_TERMS = ["dopaminergic", "medium spiny", "astrocyte", "microglia", "neuron"]


def data_catalog() -> dict[str, Any]:
    return {
        "sources": [
            {
                "id": "gtex_gene_expression",
                "name": "GTEx median gene expression",
                "release_default": "gtex_v8",
                "source_url": "https://gtexportal.org/home/",
                "record_type": "gene_tissue_expression",
                "refreshable": True,
            },
            {
                "id": "allen_whb_taxonomy",
                "name": "Allen Brain Cell Atlas whole human brain taxonomy",
                "release_default": "WHB-taxonomy/20240330",
                "source_url": "https://allen-brain-cell-atlas.s3.us-west-2.amazonaws.com/metadata/WHB-taxonomy/20240330/cluster_annotation_term.csv",
                "record_type": "cell_taxonomy",
                "refreshable": True,
            },
            {
                "id": "custom_codon_priors",
                "name": "CUSTOM-style tissue codon priors",
                "release_default": "local_seed",
                "record_type": "codon_weight_multipliers",
                "refreshable": False,
            },
            {
                "id": "kapur_brain_trna",
                "name": "Kapur-style brain tRNA availability priors",
                "release_default": "local_seed",
                "record_type": "codon_availability_weights",
                "refreshable": False,
            },
        ],
        "default_refresh": {
            "genes": DEFAULT_GENES,
            "brain_regions": DEFAULT_BRAIN_REGIONS,
            "allen_query_terms": DEFAULT_ALLEN_TERMS,
        },
        "status": structured_status(),
        "manifest": structured_manifest(),
        "external_sources": external_source_status(),
        "last_refresh": _last_refresh_entry(),
    }


def refresh_reference_data(
    genes: list[str] | None = None,
    brain_regions: list[str] | None = None,
    include_gtex: bool = True,
    include_allen: bool = True,
    allen_query_terms: list[str] | None = None,
    max_allen_records: int = 250,
    dataset_id: str = "gtex_v8",
    rebuild_index_after: bool = True,
    dry_run: bool = False,
    gtex_importer: Callable[..., dict[str, Any]] = import_gtex_gene_expression,
    allen_importer: Callable[..., dict[str, Any]] = import_allen_whb_taxonomy,
) -> dict[str, Any]:
    normalized_genes = _clean_list(genes) or DEFAULT_GENES
    normalized_regions = _clean_list(brain_regions) or DEFAULT_BRAIN_REGIONS
    normalized_terms = _clean_list(allen_query_terms) or DEFAULT_ALLEN_TERMS
    operations = _planned_operations(normalized_genes, normalized_regions, include_gtex, include_allen, normalized_terms, max_allen_records, dataset_id)
    started_at = _utc_now()

    if dry_run:
        return {
            "refresh_status": "planned",
            "started_at": started_at,
            "completed_at": started_at,
            "operations": operations,
            "summary": {"planned": len(operations), "succeeded": 0, "failed": 0},
        }

    results = []
    for operation in operations:
        try:
            if operation["source"] == "GTEx":
                payload = gtex_importer(
                    operation["gene"],
                    brain_regions=normalized_regions,
                    dataset_id=dataset_id,
                    rebuild_index_after=False,
                )
            elif operation["source"] == "Allen Brain Cell Atlas":
                payload = allen_importer(
                    query_terms=normalized_terms,
                    max_records=max_allen_records,
                    rebuild_index_after=False,
                )
            else:
                continue
            results.append({"operation": operation, "status": "succeeded", "payload": _compact_payload(payload)})
        except Exception as exc:  # noqa: BLE001 - refresh should continue and report partial failures.
            results.append({"operation": operation, "status": "failed", "error": str(exc)})

    succeeded = sum(1 for result in results if result["status"] == "succeeded")
    failed = sum(1 for result in results if result["status"] == "failed")
    completed_at = _utc_now()
    response: dict[str, Any] = {
        "refresh_status": "completed" if failed == 0 else "partial_failure",
        "started_at": started_at,
        "completed_at": completed_at,
        "operations": operations,
        "results": results,
        "summary": {"planned": len(operations), "succeeded": succeeded, "failed": failed},
        "status_before_rebuild": structured_status(),
    }
    if rebuild_index_after and succeeded:
        response["index"] = rebuild_rag_index(persist=True)
    response["status"] = structured_status()
    response["manifest_hash"] = response["status"]["manifest_hash"]
    _append_refresh_log(response)
    return response


def validate_refresh_plan(
    genes: list[str] | None = None,
    brain_regions: list[str] | None = None,
    include_gtex: bool = True,
    include_allen: bool = True,
    allen_query_terms: list[str] | None = None,
    max_allen_records: int = 250,
    dataset_id: str = "gtex_v8",
) -> dict[str, Any]:
    normalized_genes = _clean_list(genes) or DEFAULT_GENES
    normalized_regions = _clean_list(brain_regions) or DEFAULT_BRAIN_REGIONS
    normalized_terms = _clean_list(allen_query_terms) or DEFAULT_ALLEN_TERMS
    operations = _planned_operations(normalized_genes, normalized_regions, include_gtex, include_allen, normalized_terms, max_allen_records, dataset_id)
    manifest = structured_manifest()
    status = structured_status()
    external_sources = external_source_status()
    release_lock = verify_data_release_lock()
    errors: list[str] = []
    warnings: list[str] = []

    if not include_gtex and not include_allen:
        errors.append("At least one live source must be enabled: GTEx or Allen Brain Cell Atlas.")
    if include_gtex and not normalized_genes:
        errors.append("GTEx refresh requires at least one gene symbol.")
    if include_gtex and not normalized_regions:
        errors.append("GTEx refresh requires at least one brain region.")
    if include_allen and not normalized_terms:
        errors.append("Allen taxonomy refresh requires at least one query term.")
    if include_allen and max_allen_records < 1:
        errors.append("Allen max record count must be positive.")
    if not dataset_id.strip():
        errors.append("GTEx dataset id must be populated.")

    if not genes:
        warnings.append(f"No genes were supplied; default panel will be used: {', '.join(DEFAULT_GENES)}.")
    if not brain_regions:
        warnings.append(f"No brain regions were supplied; default panel will be used: {', '.join(DEFAULT_BRAIN_REGIONS)}.")
    if include_allen and not allen_query_terms:
        warnings.append(f"No Allen query terms were supplied; default panel will be used: {', '.join(DEFAULT_ALLEN_TERMS)}.")
    if len(operations) > 25:
        warnings.append("Refresh plan contains more than 25 operations; consider splitting it for easier audit and retry.")
    if release_lock.get("status") not in {"current", "missing"}:
        warnings.append(f"Data release lock is {release_lock.get('status')}; write a new lock after a successful refresh.")

    coverage = external_sources.get("coverage") or {}
    source_snapshot_fraction = float(coverage.get("source_snapshot_path_fraction") or 0)
    payload_hash_fraction = float(coverage.get("source_payload_hash_fraction") or 0)
    if status.get("records", 0) and source_snapshot_fraction < 1.0:
        warnings.append("Some structured records are missing source snapshot paths; run external source backfill after refresh.")
    if status.get("records", 0) and payload_hash_fraction < 1.0:
        warnings.append("Some structured records are missing source payload hashes; run external source backfill after refresh.")

    validation = {
        "validation_schema": "agentic-rag-data-refresh-plan-validation-v1",
        "status": "fail" if errors else "warning" if warnings else "pass",
        "errors": errors,
        "warnings": warnings,
        "normalized_request": {
            "genes": normalized_genes,
            "brain_regions": normalized_regions,
            "include_gtex": include_gtex,
            "include_allen": include_allen,
            "allen_query_terms": normalized_terms,
            "max_allen_records": max_allen_records,
            "dataset_id": dataset_id,
        },
        "plan": {
            "operation_count": len(operations),
            "operations": operations,
            "sources": sorted({operation["source"] for operation in operations}),
        },
        "current_data": {
            "records": status.get("records"),
            "datasets": status.get("datasets"),
            "manifest_hash": status.get("manifest_hash"),
            "structured_files": len(manifest.get("files") or []),
        },
        "release_lock": {
            "status": release_lock.get("status"),
            "current_hash": release_lock.get("current_hash"),
            "locked_hash": release_lock.get("locked_hash"),
        },
        "external_source_coverage": coverage,
    }
    return validation


def refresh_log(limit: int = 20) -> dict[str, Any]:
    path = _refresh_log_path()
    if not path.exists():
        return {"log_path": str(path), "entries": []}
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"log_path": str(path), "entries": entries[-max(1, min(limit, 200)) :]}


def record_data_baseline_event(note: str = "manual_baseline") -> dict[str, Any]:
    completed_at = _utc_now()
    status = structured_status()
    manifest = structured_manifest()
    rag = rag_status()
    documents = list_ingested_documents()
    entry = {
        "refresh_status": "baseline_recorded",
        "started_at": completed_at,
        "completed_at": completed_at,
        "operations": [
            {
                "source": "local_data_store",
                "action": "record_baseline",
                "note": note,
            }
        ],
        "summary": {
            "planned": 1,
            "succeeded": 1,
            "failed": 0,
            "structured_records": status["records"],
            "structured_files": len(manifest["files"]),
            "rag_chunks": rag.get("chunks", 0),
            "documents": len(documents.get("documents", [])),
        },
        "manifest_hash": status.get("manifest_hash"),
        "status": status,
        "rag": {
            "chunks": rag.get("chunks"),
            "index_version": rag.get("index_version"),
            "embedding_model": rag.get("embedding_model"),
        },
    }
    _append_refresh_log(entry)
    return {**entry, "log": refresh_log(limit=1)}


def _planned_operations(
    genes: list[str],
    brain_regions: list[str],
    include_gtex: bool,
    include_allen: bool,
    allen_query_terms: list[str],
    max_allen_records: int,
    dataset_id: str,
) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    if include_gtex:
        operations.extend(
            {
                "source": "GTEx",
                "action": "import_gene_expression",
                "gene": gene.upper(),
                "brain_regions": brain_regions,
                "dataset_id": dataset_id,
            }
            for gene in genes
        )
    if include_allen:
        operations.append(
            {
                "source": "Allen Brain Cell Atlas",
                "action": "import_whb_taxonomy",
                "query_terms": allen_query_terms,
                "max_records": max_allen_records,
            }
        )
    return operations


def _append_refresh_log(entry: dict[str, Any]) -> None:
    path = _refresh_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    audit_entry = {
        "completed_at": entry["completed_at"],
        "refresh_status": entry["refresh_status"],
        "summary": entry["summary"],
        "manifest_hash": entry.get("manifest_hash"),
        "operations": entry["operations"],
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(audit_entry, ensure_ascii=False, sort_keys=True) + "\n")


def _last_refresh_entry() -> dict[str, Any] | None:
    entries = refresh_log(limit=1)["entries"]
    return entries[-1] if entries else None


def _refresh_log_path() -> Path:
    return get_settings().data_dir / "runtime" / "data_refresh_log.jsonl"


def _compact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "imported_path": payload.get("imported_path"),
        "records": payload.get("records"),
        "manifest_hash": (payload.get("status") or {}).get("manifest_hash"),
    }


def _clean_list(values: list[str] | None) -> list[str]:
    return [value.strip() for value in values or [] if value and value.strip()]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
