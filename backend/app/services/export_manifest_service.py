from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

from app.services.signature_service import signatures_for_hash, verify_payload_signatures


class ManifestedZip:
    def __init__(self, archive: ZipFile, artifact_type: str, bundle_metadata: dict[str, Any] | None = None) -> None:
        self.archive = archive
        self.artifact_type = artifact_type
        self.bundle_metadata = bundle_metadata or {}
        self._files: list[dict[str, Any]] = []

    def writestr(self, archive_name: str, content: str | bytes, media_type: str | None = None) -> None:
        payload = _content_bytes(content)
        self.archive.writestr(archive_name, payload)
        self._record(archive_name, payload, media_type)

    def write_file(self, source_path: Path, archive_name: str, media_type: str | None = None) -> None:
        payload = source_path.read_bytes()
        self.archive.writestr(archive_name, payload)
        self._record(archive_name, payload, media_type)

    def write_artifact_manifest(self, archive_name: str = "artifact_manifest.json") -> dict[str, Any]:
        manifest = build_artifact_manifest(self.artifact_type, self._files, self.bundle_metadata)
        self.archive.writestr(archive_name, _json_bytes(manifest))
        return manifest

    def _record(self, archive_name: str, payload: bytes, media_type: str | None) -> None:
        self._files.append(
            {
                "path": archive_name,
                "bytes": len(payload),
                "sha256": sha256(payload).hexdigest(),
                "media_type": media_type or _infer_media_type(archive_name),
            }
        )


def build_artifact_manifest(
    artifact_type: str,
    files: list[dict[str, Any]],
    bundle_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sorted_files = sorted(files, key=lambda item: item["path"])
    manifest = {
        "artifact_manifest_schema": "agentic-rag-artifact-manifest-v1",
        "artifact_type": artifact_type,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "bundle_metadata": bundle_metadata or {},
        "file_count": len(sorted_files),
        "total_bytes": sum(int(item.get("bytes") or 0) for item in sorted_files),
        "files": sorted_files,
    }
    manifest["manifest_hash"] = _hash_manifest(manifest)
    signatures = signatures_for_hash(manifest["manifest_hash"], signed_field="manifest_hash")
    if signatures:
        manifest["artifact_signatures"] = signatures
        hmac_signature = next((item for item in signatures if item.get("algorithm") == "HMAC-SHA256"), None)
        if hmac_signature:
            manifest["artifact_signature"] = hmac_signature
    return manifest


def verify_artifact_bundle(bundle: bytes) -> dict[str, Any]:
    try:
        with ZipFile(BytesIO(bundle), "r") as archive:
            names = archive.namelist()
            duplicate_names = sorted({name for name in names if names.count(name) > 1})
            if "artifact_manifest.json" not in names:
                return {
                    "status": "fail",
                    "errors": ["artifact_manifest.json is missing."],
                    "warnings": [],
                    "artifact_type": None,
                    "manifest_hash": None,
                    "file_count": 0,
                    "checked_files": 0,
                }
            manifest = json.loads(archive.read("artifact_manifest.json").decode("utf-8"))
            return _verify_manifested_zip(archive, manifest, duplicate_names)
    except (BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return {
            "status": "fail",
            "errors": [f"Invalid artifact bundle: {exc}"],
            "warnings": [],
            "artifact_type": None,
            "manifest_hash": None,
            "file_count": 0,
            "checked_files": 0,
        }


def _hash_manifest(manifest: dict[str, Any]) -> str:
    payload = {key: value for key, value in manifest.items() if key not in {"manifest_hash", "artifact_signature", "artifact_signatures"}}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _verify_manifested_zip(archive: ZipFile, manifest: dict[str, Any], duplicate_names: list[str]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    expected_hash = manifest.get("manifest_hash")
    actual_hash = _hash_manifest(manifest)
    if expected_hash != actual_hash:
        errors.append("artifact_manifest.json manifest_hash does not match manifest contents.")
    signature_result = _verify_signature(manifest)
    if signature_result["status"] == "fail":
        errors.extend(signature_result["messages"])
    elif signature_result["status"] == "warning":
        warnings.extend(signature_result["messages"])
    if duplicate_names:
        errors.append(f"Duplicate ZIP member names detected: {', '.join(duplicate_names)}.")

    files = manifest.get("files") or []
    if manifest.get("file_count") != len(files):
        errors.append("Manifest file_count does not match files length.")
    if manifest.get("total_bytes") != sum(int(item.get("bytes") or 0) for item in files):
        errors.append("Manifest total_bytes does not match files entries.")

    expected_paths = {item.get("path") for item in files}
    zip_paths = set(archive.namelist()) - {"artifact_manifest.json"}
    missing_paths = sorted(path for path in expected_paths if path and path not in zip_paths)
    unlisted_paths = sorted(path for path in zip_paths if path not in expected_paths)
    if missing_paths:
        errors.append(f"Manifested files missing from ZIP: {', '.join(missing_paths)}.")
    if unlisted_paths:
        warnings.append(f"ZIP contains unlisted files: {', '.join(unlisted_paths)}.")

    checked_files = 0
    mismatches: list[str] = []
    for item in files:
        path = item.get("path")
        if not path or path not in zip_paths:
            continue
        payload = archive.read(path)
        checked_files += 1
        if len(payload) != item.get("bytes"):
            mismatches.append(f"{path} byte size mismatch.")
        if sha256(payload).hexdigest() != item.get("sha256"):
            mismatches.append(f"{path} sha256 mismatch.")
    if mismatches:
        errors.extend(mismatches)

    return {
        "status": "fail" if errors else "warning" if warnings else "pass",
        "errors": errors,
        "warnings": warnings,
        "artifact_type": manifest.get("artifact_type"),
        "manifest_hash": expected_hash,
        "signature": signature_result,
        "file_count": len(files),
        "checked_files": checked_files,
        "total_bytes": manifest.get("total_bytes"),
        "bundle_metadata": manifest.get("bundle_metadata") or {},
    }


def _content_bytes(content: str | bytes) -> bytes:
    if isinstance(content, bytes):
        return content
    return content.encode("utf-8")


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def _infer_media_type(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".csv": "text/csv; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".jsonl": "application/jsonl; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",
        ".pdf": "application/pdf",
        ".txt": "text/plain; charset=utf-8",
    }.get(suffix, "application/octet-stream")


def _verify_signature(manifest: dict[str, Any]) -> dict[str, Any]:
    signatures = list(manifest.get("artifact_signatures") or [])
    legacy_signature = manifest.get("artifact_signature")
    if legacy_signature and legacy_signature not in signatures:
        signatures.append(legacy_signature)
    return verify_payload_signatures(str(manifest.get("manifest_hash") or ""), signatures, signed_field="manifest_hash")
