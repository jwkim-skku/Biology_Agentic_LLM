from __future__ import annotations

import hmac
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from app.config import get_settings
from app.services.artifact_archive_service import (
    get_archived_artifact,
    list_archived_artifacts,
    read_archived_artifact,
    update_archived_artifact_metadata,
)


OBJECT_STORE_SCHEMA = "agentic-rag-artifact-object-store-v1"


def artifact_object_store_status() -> dict[str, Any]:
    settings = get_settings()
    configured = _configured(settings)
    missing = _missing_settings(settings)
    return {
        "status_schema": OBJECT_STORE_SCHEMA,
        "status": "disabled" if not settings.artifact_object_store_enabled else "ready" if configured else "misconfigured",
        "enabled": settings.artifact_object_store_enabled,
        "configured": configured,
        "endpoint": _redacted_endpoint(settings.artifact_object_store_endpoint),
        "bucket": settings.artifact_object_store_bucket or None,
        "prefix": settings.artifact_object_store_prefix,
        "region": settings.artifact_object_store_region,
        "missing_settings": missing,
        "capabilities": {
            "dry_run": True,
            "put_object": configured,
            "head_verify": configured,
            "path_style": True,
        },
        "recommendation": _recommendation(settings, missing),
    }


def plan_artifact_object_store_mirror(
    *,
    limit: int = 100,
    artifact_type: str | None = None,
    resource_type: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    status = artifact_object_store_status()
    artifacts = list_archived_artifacts(limit=limit, artifact_type=artifact_type, resource_type=resource_type)["artifacts"]
    candidates = [_mirror_candidate(settings, artifact) for artifact in artifacts if _needs_mirror(artifact)]
    return {
        "mirror_schema": OBJECT_STORE_SCHEMA,
        "status": "ready" if status["configured"] else status["status"],
        "object_store": status,
        "filters": {"artifact_type": artifact_type, "resource_type": resource_type, "limit": max(1, min(limit, 500))},
        "candidate_count": len(candidates),
        "candidate_bytes": sum(int(item.get("bytes") or 0) for item in candidates),
        "candidates": candidates,
    }


def mirror_artifact_archive(
    *,
    dry_run: bool = True,
    limit: int = 100,
    artifact_type: str | None = None,
    resource_type: str | None = None,
) -> dict[str, Any]:
    plan = plan_artifact_object_store_mirror(limit=limit, artifact_type=artifact_type, resource_type=resource_type)
    status = plan["object_store"]
    if dry_run or not status["configured"]:
        return {
            **plan,
            "dry_run": True,
            "mirrored_count": 0,
            "mirrored_bytes": 0,
            "mirrored": [],
            "errors": [] if status["configured"] else [f"Object store is {status['status']}."],
        }

    mirrored: list[dict[str, Any]] = []
    errors: list[str] = []
    settings = get_settings()
    for candidate in plan["candidates"]:
        artifact_id = str(candidate["artifact_id"])
        try:
            artifact = get_archived_artifact(artifact_id)
            if not artifact:
                raise FileNotFoundError(artifact_id)
            content = read_archived_artifact(artifact_id)
            object_key = str(candidate["object_key"])
            put_result = _put_object(settings, object_key, content, artifact)
            metadata = artifact.get("metadata") or {}
            metadata["object_store_mirror"] = {
                "mirror_schema": OBJECT_STORE_SCHEMA,
                "mirrored_at": datetime.now(timezone.utc).isoformat(),
                "bucket": settings.artifact_object_store_bucket,
                "object_key": object_key,
                "endpoint": _redacted_endpoint(settings.artifact_object_store_endpoint),
                "sha256": artifact.get("sha256"),
                "etag": put_result.get("etag"),
                "status": "pass",
            }
            update_archived_artifact_metadata(artifact_id, metadata)
            mirrored.append({**candidate, "status": "pass", "etag": put_result.get("etag")})
        except (OSError, HTTPError, URLError, ValueError) as exc:
            errors.append(f"{artifact_id}: {exc}")

    return {
        **plan,
        "dry_run": False,
        "status": "fail" if errors else "pass",
        "mirrored_count": len(mirrored),
        "mirrored_bytes": sum(int(item.get("bytes") or 0) for item in mirrored),
        "mirrored": mirrored,
        "errors": errors,
    }


def _needs_mirror(artifact: dict[str, Any]) -> bool:
    mirror = (artifact.get("metadata") or {}).get("object_store_mirror") or {}
    return not (mirror.get("status") == "pass" and mirror.get("sha256") == artifact.get("sha256"))


def _mirror_candidate(settings: Any, artifact: dict[str, Any]) -> dict[str, Any]:
    object_key = _object_key(settings, artifact)
    return {
        "artifact_id": artifact.get("artifact_id"),
        "artifact_type": artifact.get("artifact_type"),
        "resource_type": artifact.get("resource_type"),
        "resource_id": artifact.get("resource_id"),
        "created_at": artifact.get("created_at"),
        "bytes": artifact.get("bytes"),
        "sha256": artifact.get("sha256"),
        "manifest_hash": artifact.get("manifest_hash"),
        "filename": artifact.get("filename"),
        "object_key": object_key,
        "object_uri": f"s3://{settings.artifact_object_store_bucket}/{object_key}" if settings.artifact_object_store_bucket else None,
    }


def _object_key(settings: Any, artifact: dict[str, Any]) -> str:
    filename = Path(str(artifact.get("filename") or f"{artifact.get('artifact_id')}.zip")).name
    parts = [
        settings.artifact_object_store_prefix,
        str(artifact.get("artifact_type") or "unknown"),
        str(artifact.get("artifact_id")),
        filename,
    ]
    return "/".join(part.strip("/") for part in parts if part)


def _put_object(settings: Any, object_key: str, content: bytes, artifact: dict[str, Any]) -> dict[str, Any]:
    url = _object_url(settings, object_key)
    headers = {
        "content-type": artifact.get("media_type") or "application/zip",
        "x-amz-content-sha256": sha256(content).hexdigest(),
        "x-amz-meta-artifact-id": str(artifact.get("artifact_id") or ""),
        "x-amz-meta-manifest-hash": str(artifact.get("manifest_hash") or ""),
        "x-amz-meta-sha256": str(artifact.get("sha256") or ""),
    }
    signed_headers = _signed_headers(settings, "PUT", url, headers)
    request = Request(url, data=content, headers=signed_headers, method="PUT")
    with urlopen(request, timeout=30) as response:
        etag = response.headers.get("ETag", "").strip('"') or None
    _head_object(settings, object_key)
    return {"etag": etag}


def _head_object(settings: Any, object_key: str) -> None:
    url = _object_url(settings, object_key)
    headers = _signed_headers(settings, "HEAD", url, {"x-amz-content-sha256": "UNSIGNED-PAYLOAD"})
    request = Request(url, headers=headers, method="HEAD")
    with urlopen(request, timeout=15):
        return


def _object_url(settings: Any, object_key: str) -> str:
    endpoint = settings.artifact_object_store_endpoint.rstrip("/")
    bucket = quote(settings.artifact_object_store_bucket.strip("/"), safe="")
    key = quote(object_key.strip("/"), safe="/")
    return f"{endpoint}/{bucket}/{key}"


def _signed_headers(settings: Any, method: str, url: str, headers: dict[str, str]) -> dict[str, str]:
    parsed = urlparse(url)
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_scope = now.strftime("%Y%m%d")
    host = parsed.netloc
    canonical_uri = parsed.path or "/"
    canonical_query = parsed.query
    request_headers = {key.lower(): value.strip() for key, value in headers.items()}
    request_headers["host"] = host
    request_headers["x-amz-date"] = amz_date
    signed_header_names = sorted(request_headers)
    canonical_headers = "".join(f"{key}:{request_headers[key]}\n" for key in signed_header_names)
    signed_headers = ";".join(signed_header_names)
    payload_hash = request_headers.get("x-amz-content-sha256") or "UNSIGNED-PAYLOAD"
    canonical_request = "\n".join([method, canonical_uri, canonical_query, canonical_headers, signed_headers, payload_hash])
    credential_scope = f"{date_scope}/{settings.artifact_object_store_region}/s3/aws4_request"
    string_to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, credential_scope, sha256(canonical_request.encode("utf-8")).hexdigest()])
    signing_key = _signing_key(settings.artifact_object_store_secret_access_key, date_scope, settings.artifact_object_store_region)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), sha256).hexdigest()
    request_headers["authorization"] = (
        "AWS4-HMAC-SHA256 "
        f"Credential={settings.artifact_object_store_access_key_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return {key: value for key, value in request_headers.items()}


def _signing_key(secret_key: str, date_scope: str, region: str) -> bytes:
    date_key = hmac.new(("AWS4" + secret_key).encode("utf-8"), date_scope.encode("utf-8"), sha256).digest()
    region_key = hmac.new(date_key, region.encode("utf-8"), sha256).digest()
    service_key = hmac.new(region_key, b"s3", sha256).digest()
    return hmac.new(service_key, b"aws4_request", sha256).digest()


def _configured(settings: Any) -> bool:
    return settings.artifact_object_store_enabled and not _missing_settings(settings)


def _missing_settings(settings: Any) -> list[str]:
    if not settings.artifact_object_store_enabled:
        return []
    required = {
        "ARTIFACT_OBJECT_STORE_ENDPOINT": settings.artifact_object_store_endpoint,
        "ARTIFACT_OBJECT_STORE_BUCKET": settings.artifact_object_store_bucket,
        "ARTIFACT_OBJECT_STORE_REGION": settings.artifact_object_store_region,
        "ARTIFACT_OBJECT_STORE_ACCESS_KEY_ID": settings.artifact_object_store_access_key_id,
        "ARTIFACT_OBJECT_STORE_SECRET_ACCESS_KEY": settings.artifact_object_store_secret_access_key,
    }
    return [key for key, value in required.items() if not value]


def _redacted_endpoint(endpoint: str) -> str | None:
    if not endpoint:
        return None
    parsed = urlparse(endpoint)
    if not parsed.netloc:
        return endpoint
    return f"{parsed.scheme}://{parsed.netloc}"


def _recommendation(settings: Any, missing: list[str]) -> str:
    if not settings.artifact_object_store_enabled:
        return "Set ARTIFACT_OBJECT_STORE_ENABLED=true with S3-compatible settings to mirror immutable archives off-host."
    if missing:
        return f"Configure missing object-store settings: {', '.join(missing)}."
    return "Object-store mirror is configured; run the dry-run mirror plan before applying uploads."
