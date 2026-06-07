from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from app.config import get_settings
from app.services.data_lock_service import DATA_LOCK_PATH, verify_data_lockfile
from app.services.data_provenance_service import data_provenance_audit
from app.services.data_refresh_service import refresh_log
from app.services.data_release_lock_service import DATA_RELEASE_LOCK_PATH, verify_data_release_lock
from app.services.external_data_service import EXTERNAL_SOURCE_DIR
from app.services.export_manifest_service import ManifestedZip
from app.services.agent_memory_service import agent_memory_summary
from app.services.ingestion_service import DOCUMENT_DIR, list_ingested_documents
from app.services.rag_service import EVIDENCE_PATH, RAG_INDEX_PATH, rag_status
from app.services.structured_data_service import STRUCTURED_DIR, structured_manifest, structured_status


def build_data_snapshot_bundle() -> bytes:
    settings = get_settings()
    files = _snapshot_files()
    manifest = _snapshot_manifest(settings.data_dir, files)
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        bundle = ManifestedZip(archive, "data_snapshot_bundle", manifest)
        bundle.writestr("snapshot_manifest.json", _json(manifest))
        bundle.writestr("structured_manifest.json", _json(structured_manifest()))
        bundle.writestr("structured_status.json", _json(structured_status()))
        bundle.writestr("data_provenance_audit.json", _json(data_provenance_audit()))
        bundle.writestr("data_lock_status.json", _json(verify_data_lockfile()))
        bundle.writestr("data_release_lock_status.json", _json(verify_data_release_lock()))
        bundle.writestr("rag_status.json", _json(rag_status()))
        bundle.writestr("documents_manifest.json", _json(list_ingested_documents()))
        bundle.writestr("refresh_log.json", _json(refresh_log(limit=200)))
        bundle.writestr("agent_memory_summary.json", _json(agent_memory_summary()))

        for path in files:
            bundle.write_file(path, _archive_name(settings.data_dir, path))
        bundle.write_artifact_manifest()
    return buffer.getvalue()


def _snapshot_files() -> list[Path]:
    files: list[Path] = []
    for base in [STRUCTURED_DIR, DOCUMENT_DIR, EXTERNAL_SOURCE_DIR]:
        if base.exists():
            files.extend(path for path in sorted(base.rglob("*")) if path.is_file())
    for path in [EVIDENCE_PATH, RAG_INDEX_PATH, get_settings().data_dir / "runtime" / "data_refresh_log.jsonl", DATA_LOCK_PATH, DATA_RELEASE_LOCK_PATH]:
        if path.exists() and path.is_file():
            files.append(path)
    return _dedupe(files)


def _snapshot_manifest(data_dir: Path, files: list[Path]) -> dict[str, Any]:
    return {
        "snapshot_schema": "agentic-rag-data-snapshot-v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "data_dir": str(data_dir),
        "file_count": len(files),
        "artifact_manifest": "artifact_manifest.json",
        "files": [
            {
                "path": _archive_name(data_dir, path),
                "source_path": str(path),
                "bytes": path.stat().st_size,
                "sha256": _file_sha256(path),
            }
            for path in files
        ],
    }


def _archive_name(data_dir: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(data_dir.resolve())
    except ValueError:
        relative = Path(path.name)
    return str(Path("data") / relative).replace("\\", "/")


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dedupe(paths: list[Path]) -> list[Path]:
    output = []
    seen = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        output.append(path)
    return output


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
