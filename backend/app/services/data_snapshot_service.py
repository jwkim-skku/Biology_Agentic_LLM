from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from app.config import get_settings
from app.services.data_lock_service import DATA_LOCK_PATH, verify_data_lockfile
from app.services.data_provenance_service import data_provenance_audit
from app.services.data_refresh_service import refresh_log
from app.services.data_release_lock_service import DATA_RELEASE_LOCK_PATH, verify_data_release_lock
from app.services.external_data_service import EXTERNAL_SOURCE_DIR
from app.services.export_manifest_service import ManifestedZip, verify_artifact_bundle
from app.services.agent_memory_service import agent_memory_summary
from app.services.ingestion_service import DOCUMENT_DIR, list_ingested_documents
from app.services.rag_service import EVIDENCE_PATH, RAG_INDEX_PATH, rag_status
from app.services.structured_data_service import STRUCTURED_DIR, structured_manifest, structured_status


SNAPSHOT_BUNDLE_SCHEMA = "agentic-rag-data-snapshot-v1"
REQUIRED_DATA_SNAPSHOT_FILES = {
    "snapshot_manifest.json",
    "structured_manifest.json",
    "structured_status.json",
    "data_provenance_audit.json",
    "data_lock_status.json",
    "data_release_lock_status.json",
    "rag_status.json",
    "documents_manifest.json",
    "refresh_log.json",
    "agent_memory_summary.json",
}


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


def verify_data_snapshot_bundle(bundle: bytes) -> dict[str, Any]:
    base = verify_artifact_bundle(bundle)
    semantic_errors: list[str] = []
    semantic_warnings: list[str] = []
    semantic_checks: dict[str, str] = {}
    payloads: dict[str, Any] = {}
    zip_names: set[str] = set()

    if base.get("artifact_type") != "data_snapshot_bundle":
        semantic_errors.append("Artifact manifest artifact_type must be data_snapshot_bundle.")
        semantic_checks["artifact_type"] = "fail"
    else:
        semantic_checks["artifact_type"] = "pass"

    try:
        with ZipFile(BytesIO(bundle), "r") as archive:
            zip_names = set(archive.namelist())
            missing = sorted(REQUIRED_DATA_SNAPSHOT_FILES - zip_names)
            if missing:
                semantic_errors.append(f"Required data snapshot files are missing: {', '.join(missing)}.")
                semantic_checks["required_files"] = "fail"
            else:
                semantic_checks["required_files"] = "pass"
                payloads = {name: _read_json(archive, name) for name in REQUIRED_DATA_SNAPSHOT_FILES}
    except (BadZipFile, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        semantic_errors.append(f"Invalid data snapshot bundle: {exc}")

    snapshot_manifest = payloads.get("snapshot_manifest.json") or {}
    structured_manifest_payload = payloads.get("structured_manifest.json") or {}
    structured_status_payload = payloads.get("structured_status.json") or {}
    rag = payloads.get("rag_status.json") or {}

    _expect_equal(
        semantic_checks,
        semantic_errors,
        "snapshot_schema",
        snapshot_manifest.get("snapshot_schema"),
        SNAPSHOT_BUNDLE_SCHEMA,
        "snapshot_manifest.json snapshot_schema is not recognized.",
    )

    snapshot_manifest_hash = _hash_payload(snapshot_manifest) if snapshot_manifest else None
    if snapshot_manifest_hash:
        semantic_checks["snapshot_manifest_hash"] = "pass"
    else:
        semantic_errors.append("snapshot_manifest.json could not be hashed.")
        semantic_checks["snapshot_manifest_hash"] = "fail"

    manifest_files = snapshot_manifest.get("files") if isinstance(snapshot_manifest.get("files"), list) else []
    manifest_paths = {str(item.get("path")) for item in manifest_files if item.get("path")}
    data_paths = {name for name in zip_names if name.startswith("data/")}
    if manifest_paths != data_paths:
        missing_paths = sorted(manifest_paths - data_paths)
        unlisted_paths = sorted(data_paths - manifest_paths)
        detail = []
        if missing_paths:
            detail.append(f"missing data files: {', '.join(missing_paths[:10])}")
        if unlisted_paths:
            detail.append(f"unlisted data files: {', '.join(unlisted_paths[:10])}")
        semantic_errors.append("snapshot_manifest.json files do not match ZIP data members" + (f" ({'; '.join(detail)})." if detail else "."))
        semantic_checks["snapshot_file_listing"] = "fail"
    else:
        semantic_checks["snapshot_file_listing"] = "pass"

    if snapshot_manifest.get("file_count") != len(manifest_files):
        semantic_errors.append("snapshot_manifest.json file_count does not match files length.")
        semantic_checks["snapshot_file_count"] = "fail"
    elif snapshot_manifest.get("file_count") == len(data_paths):
        semantic_checks["snapshot_file_count"] = "pass"
    else:
        semantic_errors.append("snapshot_manifest.json file_count does not match ZIP data member count.")
        semantic_checks["snapshot_file_count"] = "fail"

    structured_hashes = {
        str(value)
        for value in [
            structured_manifest_payload.get("manifest_hash"),
            structured_status_payload.get("manifest_hash"),
            rag.get("structured_manifest_hash"),
        ]
        if value
    }
    if len(structured_hashes) > 1:
        semantic_errors.append("Structured manifest hashes disagree across data snapshot files.")
        semantic_checks["structured_manifest_hash"] = "fail"
    elif structured_hashes:
        semantic_checks["structured_manifest_hash"] = "pass"
    else:
        semantic_warnings.append("Structured manifest hash is not recorded in the data snapshot bundle.")
        semantic_checks["structured_manifest_hash"] = "warning"

    external_snapshot_count = sum(1 for path in manifest_paths if "external_sources/" in path)
    if external_snapshot_count > 0:
        semantic_checks["external_snapshot_files"] = "pass"
    else:
        semantic_warnings.append("Data snapshot bundle does not include external source snapshot bytes.")
        semantic_checks["external_snapshot_files"] = "warning"

    rag_index_hash = next((item.get("sha256") for item in manifest_files if str(item.get("path", "")).endswith("rag_index.json")), None)
    if rag_index_hash:
        semantic_checks["rag_index_hash"] = "pass"
    else:
        semantic_warnings.append("Data snapshot bundle does not include a persisted rag_index.json hash.")
        semantic_checks["rag_index_hash"] = "warning"

    semantic_status = "fail" if semantic_errors else "warning" if semantic_warnings else "pass"
    return {
        **base,
        "status": "fail" if base.get("status") == "fail" or semantic_status == "fail" else "warning" if base.get("status") == "warning" or semantic_status == "warning" else "pass",
        "errors": list(base.get("errors") or []) + semantic_errors,
        "warnings": list(base.get("warnings") or []) + semantic_warnings,
        "semantic_status": semantic_status,
        "semantic_checks": semantic_checks,
        "semantic_errors": semantic_errors,
        "semantic_warnings": semantic_warnings,
        "snapshot_manifest_hash": snapshot_manifest_hash,
        "structured_manifest_hash": next(iter(structured_hashes), None),
        "rag_index_hash": rag_index_hash,
        "external_snapshot_file_count": external_snapshot_count,
        "snapshot_file_count": len(manifest_files),
    }


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
        "snapshot_schema": SNAPSHOT_BUNDLE_SCHEMA,
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


def _read_json(archive: ZipFile, name: str) -> Any:
    return json.loads(archive.read(name).decode("utf-8"))


def _hash_payload(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _expect_equal(
    checks: dict[str, str],
    errors: list[str],
    check_name: str,
    actual: Any,
    expected: Any,
    message: str,
) -> None:
    if actual != expected:
        errors.append(message)
        checks[check_name] = "fail"
    else:
        checks[check_name] = "pass"
