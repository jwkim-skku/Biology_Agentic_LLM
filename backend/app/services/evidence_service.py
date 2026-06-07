from __future__ import annotations

from typing import Any

from app.services.rag_service import rag_chunks_to_evidence_records, rag_search
from app.services.structured_data_service import search_structured_context


def search_evidence(query: str, filters: dict[str, Any] | None = None, limit: int = 5) -> dict[str, Any]:
    filters = filters or {}
    rag_result = rag_search(query, filters, limit)
    selected = rag_chunks_to_evidence_records(rag_result["chunks"])
    return {
        "query": query,
        "filters": filters,
        "retrieval": rag_result["index"],
        "coverage": _coverage_summary(selected, filters),
        "records": selected,
    }


def build_design_evidence(target: dict[str, Any], source_cds: dict[str, Any], limit: int = 6) -> dict[str, Any]:
    gene_symbol = (source_cds.get("gene") or {}).get("symbol") or target.get("gene") or ""
    transcript = source_cds.get("selected_transcript") or {}
    query = " ".join(
        str(value)
        for value in [
            gene_symbol,
            target.get("brain_region"),
            target.get("cell_type"),
            target.get("modality"),
            transcript.get("selection_reason"),
            transcript.get("id"),
            "codon optimization translation efficiency tissue-aware CUSTOM",
        ]
        if value
    )
    filters = {
        "species": target.get("species") or "human",
        "brain_region": target.get("brain_region"),
        "cell_type": target.get("cell_type"),
        "modality": target.get("modality"),
        "transcript_selection": transcript.get("selection_reason"),
    }
    evidence = search_evidence(query, filters, limit)
    evidence["structured_context"] = search_structured_context(target)
    structured_coverage = evidence["structured_context"]["coverage"]
    evidence["coverage"]["gtex_structured"] = structured_coverage["gtex"]
    evidence["coverage"]["gtex_gene_expression"] = structured_coverage["gtex_gene_expression"]
    evidence["coverage"]["allen_structured"] = structured_coverage["allen"]
    evidence["coverage"]["custom_structured"] = structured_coverage["custom"]
    evidence["coverage"]["trna_structured"] = structured_coverage["trna"]
    if transcript.get("mane_select"):
        evidence["coverage"]["transcript"] = "MANE Select"
        evidence["coverage"]["transcript_confidence"] = "high"
    elif transcript.get("selection_reason") == "ensembl_canonical_transcript":
        evidence["coverage"]["transcript"] = "Ensembl canonical"
        evidence["coverage"]["transcript_confidence"] = "medium"
    else:
        evidence["coverage"]["transcript"] = "fallback transcript"
        evidence["coverage"]["transcript_confidence"] = "low"
    return evidence


def _coverage_summary(records: list[dict[str, Any]], filters: dict[str, Any]) -> dict[str, str]:
    collections = {record.get("collection") for record in records}
    region = str(filters.get("brain_region") or "").strip()
    cell_type = str(filters.get("cell_type") or "").strip()
    return {
        "region": "GTEx tissue prior" if region and "tissue_evidence" in collections else "not assessed",
        "cell_type": "Allen cell-type prior" if cell_type and "brain_cell_atlas" in collections else "not assessed",
        "translation": "tissue-aware proxy" if "literature" in collections else "generic codon proxy",
        "modality": "AAV size rule" if filters.get("modality") == "AAV" and "design_rules" in collections else "generic",
    }
