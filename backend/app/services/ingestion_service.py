from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from app.config import get_settings

DATA_DIR = get_settings().data_dir
DOCUMENT_DIR = DATA_DIR / "documents"
SUPPORTED_SUFFIXES = {".pdf", ".txt", ".md", ".json"}


def list_ingested_documents() -> dict[str, Any]:
    DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)
    records = load_ingested_documents()
    return {
        "documents_path": str(DOCUMENT_DIR),
        "documents": [
            {
                "id": record["id"],
                "title": record["title"],
                "collection": record["collection"],
                "source": record["source"],
                "source_path": record.get("source_path"),
                "word_count": len(str(record.get("content", "")).split()),
                "ingested_at": record.get("ingested_at"),
            }
            for record in records
        ],
    }


def load_ingested_documents() -> list[dict[str, Any]]:
    DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for path in sorted(DOCUMENT_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            records.extend(payload)
        elif isinstance(payload, dict):
            records.append(payload)
    return records


def ingest_local_document(
    source_path: str,
    *,
    title: str | None = None,
    collection: str = "literature",
    source: str | None = None,
    source_url: str | None = None,
    evidence_class: str = "project_document",
    confidence: str = "medium",
    species: list[str] | None = None,
    topics: list[str] | None = None,
    regions: list[str] | None = None,
    cell_types: list[str] | None = None,
    modalities: list[str] | None = None,
    copy_source: bool = False,
) -> dict[str, Any]:
    path = Path(source_path).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"Source document does not exist: {source_path}")
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"Unsupported document type: {path.suffix}")

    if path.suffix.lower() == ".json":
        return _ingest_json_document(path)

    content = _extract_text(path)
    if not content.strip():
        raise ValueError("No text could be extracted from this document.")

    DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)
    doc_id = _document_id(path, content)
    target_source_path = str(path)
    if copy_source:
        source_copy = DOCUMENT_DIR / f"{doc_id}{path.suffix.lower()}"
        shutil.copy2(path, source_copy)
        target_source_path = str(source_copy)

    record = {
        "id": doc_id,
        "title": title or path.stem,
        "collection": collection,
        "source": source or path.name,
        "source_url": source_url or "",
        "source_path": target_source_path,
        "evidence_class": evidence_class,
        "confidence": confidence,
        "species": species or [],
        "topics": topics or _infer_topics(content),
        "regions": regions or _infer_regions(content),
        "cell_types": cell_types or _infer_cell_types(content),
        "modalities": modalities or _infer_modalities(content),
        "summary": _summary(content),
        "content": content,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }
    output_path = DOCUMENT_DIR / f"{doc_id}.json"
    output_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "document": {
            "id": doc_id,
            "title": record["title"],
            "collection": collection,
            "source_path": target_source_path,
            "word_count": len(content.split()),
            "output_path": str(output_path),
        }
    }


def _ingest_json_document(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, dict) and "id" in payload:
        output_path = DOCUMENT_DIR / f"{_slug(str(payload['id']))}.json"
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"document": {"id": payload["id"], "title": payload.get("title", payload["id"]), "output_path": str(output_path)}}
    if isinstance(payload, list):
        saved = []
        for record in payload:
            if not isinstance(record, dict) or "id" not in record:
                continue
            output_path = DOCUMENT_DIR / f"{_slug(str(record['id']))}.json"
            output_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            saved.append({"id": record["id"], "output_path": str(output_path)})
        return {"documents": saved}
    raise ValueError("JSON ingestion expects an evidence record object or a list of record objects.")


def _extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        return _clean_text("\n\n".join(pages))
    return _clean_text(path.read_text(encoding="utf-8", errors="replace"))


def _clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _summary(content: str, max_chars: int = 700) -> str:
    compact = re.sub(r"\s+", " ", content).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars].rsplit(" ", 1)[0] + "..."


def _document_id(path: Path, content: str) -> str:
    digest = sha256(f"{path.name}:{content[:2000]}".encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"{_slug(path.stem)}_{digest}"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9가-힣]+", "_", value).strip("_").lower()
    return slug[:80] or "document"


def _infer_topics(content: str) -> list[str]:
    candidates = [
        "Agentic RAG",
        "MANE",
        "Ensembl",
        "GTEx",
        "Allen",
        "CUSTOM",
        "codon optimization",
        "NSGA-II",
        "AAV",
        "CpG",
        "gene therapy",
    ]
    lowered = content.lower()
    return [item for item in candidates if item.lower() in lowered]


def _infer_regions(content: str) -> list[str]:
    candidates = ["brain", "striatum", "substantia nigra", "cortex", "hippocampus", "cerebellum"]
    lowered = content.lower()
    return [item for item in candidates if item.lower() in lowered]


def _infer_cell_types(content: str) -> list[str]:
    candidates = ["dopaminergic neuron", "medium spiny neuron", "neuron", "astrocyte", "microglia"]
    lowered = content.lower()
    return [item for item in candidates if item.lower() in lowered]


def _infer_modalities(content: str) -> list[str]:
    candidates = ["AAV", "mRNA", "plasmid"]
    lowered = content.lower()
    return [item for item in candidates if item.lower() in lowered]
