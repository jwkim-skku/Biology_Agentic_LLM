from __future__ import annotations

from typing import Any

from app.optimizer.codon_table import normalize_dna, translate
from app.services.ensembl_client import EnsemblClient, normalize_species, strip_ensembl_version, utc_timestamp


def resolve_gene(symbol: str, species: str = "human", client: EnsemblClient | None = None) -> dict[str, Any]:
    client = client or EnsemblClient()
    payload = client.lookup_symbol(species, symbol)
    transcripts = [_summarize_transcript(item) for item in payload.get("Transcript", [])]
    transcripts = sorted(
        transcripts,
        key=lambda item: (
            item["selection_priority"],
            item["id"] != strip_ensembl_version(str(payload.get("canonical_transcript", ""))),
            not item["is_protein_coding"],
            item["id"],
        ),
    )
    return {
        "query": {"symbol": symbol, "species": species},
        "gene": {
            "symbol": payload.get("display_name", symbol),
            "ensembl_gene_id": payload.get("id"),
            "description": payload.get("description"),
            "biotype": payload.get("biotype"),
            "assembly_name": payload.get("assembly_name"),
            "seq_region_name": payload.get("seq_region_name"),
            "start": payload.get("start"),
            "end": payload.get("end"),
            "strand": payload.get("strand"),
            "canonical_transcript": payload.get("canonical_transcript"),
            "source": payload.get("source"),
        },
        "transcripts": transcripts,
        "provenance": {
            "source": "Ensembl REST lookup/symbol",
            "species": normalize_species(species),
            "fetched_at": utc_timestamp(),
            "mane_status": "checked_when_available_for_human",
        },
    }


def fetch_canonical_cds(symbol: str, species: str = "human", client: EnsemblClient | None = None) -> dict[str, Any]:
    client = client or EnsemblClient()
    resolved = resolve_gene(symbol, species, client)
    transcript = _select_transcript(resolved)
    cds = normalize_dna(client.fetch_cds(transcript["id"]))
    protein = translate(cds)
    return {
        "query": {"symbol": symbol, "species": species},
        "gene": resolved["gene"],
        "selected_transcript": transcript,
        "cds": cds,
        "protein": protein,
        "cds_length_nt": len(cds),
        "protein_length_aa": len(protein),
        "provenance": {
            "source": "Ensembl REST sequence/id",
            "lookup_source": resolved["provenance"],
            "transcript_preference": [
                "MANE Select",
                "MANE Plus Clinical",
                "Ensembl canonical",
                "protein_coding longest translation",
            ],
            "fetched_at": utc_timestamp(),
        },
    }


def _summarize_transcript(transcript: dict[str, Any]) -> dict[str, Any]:
    translation = transcript.get("Translation") or {}
    transcript_id = strip_ensembl_version(str(transcript.get("id", "")))
    version = transcript.get("version")
    mane_records = [_summarize_mane(item) for item in transcript.get("MANE", [])]
    mane_select = next((item for item in mane_records if item["type"] == "MANE_Select"), None)
    mane_plus_clinical = next((item for item in mane_records if item["type"] == "MANE_Plus_Clinical"), None)
    return {
        "id": transcript_id,
        "versioned_id": f"{transcript_id}.{version}" if version else transcript.get("id"),
        "display_name": transcript.get("display_name"),
        "biotype": transcript.get("biotype"),
        "is_canonical": bool(transcript.get("is_canonical")),
        "is_protein_coding": bool(translation) and transcript.get("biotype") == "protein_coding",
        "translation_id": translation.get("id"),
        "translation_length_aa": translation.get("length"),
        "length": transcript.get("length"),
        "source": transcript.get("source"),
        "mane": mane_records,
        "mane_select": mane_select,
        "mane_plus_clinical": mane_plus_clinical,
        "selection_priority": _selection_priority(mane_select, mane_plus_clinical),
    }


def _summarize_mane(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": record.get("type"),
        "refseq_match": record.get("refseq_match"),
        "ensembl_transcript_id": strip_ensembl_version(str(record.get("id", ""))),
        "version": record.get("version"),
        "assembly_name": record.get("assembly_name"),
    }


def _selection_priority(mane_select: dict[str, Any] | None, mane_plus_clinical: dict[str, Any] | None) -> int:
    if mane_select:
        return 0
    if mane_plus_clinical:
        return 1
    return 2


def _select_transcript(resolved: dict[str, Any]) -> dict[str, Any]:
    canonical = strip_ensembl_version(str(resolved["gene"].get("canonical_transcript") or ""))
    transcripts = resolved["transcripts"]
    for transcript in transcripts:
        if transcript["is_protein_coding"] and transcript["mane_select"]:
            return {**transcript, "selection_reason": "mane_select"}
    for transcript in transcripts:
        if transcript["is_protein_coding"] and transcript["mane_plus_clinical"]:
            return {**transcript, "selection_reason": "mane_plus_clinical"}
    for transcript in transcripts:
        if transcript["id"] == canonical and transcript["is_protein_coding"]:
            return {**transcript, "selection_reason": "ensembl_canonical_transcript"}
    protein_coding = [item for item in transcripts if item["is_protein_coding"]]
    if protein_coding:
        selected = max(protein_coding, key=lambda item: item.get("translation_length_aa") or 0)
        return {**selected, "selection_reason": "longest_protein_coding_transcript_fallback"}
    raise ValueError("No protein-coding transcript with CDS was found for this gene.")
