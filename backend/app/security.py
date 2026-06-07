from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from threading import Lock
from typing import Mapping

from fastapi import Request

from app.config import AppSettings


PUBLIC_PATHS = frozenset(
    {
        "/",
        "/api/v1/health",
        "/api/v1/metrics",
        "/api/v1/metrics/prometheus",
        "/api/v1/settings",
        "/api/v1/security/status",
        "/docs",
        "/openapi.json",
        "/redoc",
    }
)


def request_id_from_headers(headers: Mapping[str, str]) -> str:
    value = headers.get("x-request-id") or headers.get("X-Request-ID")
    if value and 0 < len(value) <= 128:
        return value
    return uuid.uuid4().hex


def is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return path.startswith("/api/v1/health/") or path.startswith("/docs/") or path.startswith("/redoc/")


def extract_api_key(request: Request) -> str | None:
    api_key = request.headers.get("x-api-key")
    if api_key:
        return api_key.strip()
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return None


ROLE_LEVELS = {"viewer": 1, "operator": 2, "admin": 3}


def roles_for_api_key(api_key: str | None, settings: AppSettings) -> tuple[str, ...]:
    if not api_key or api_key not in settings.api_keys:
        return ()
    if not settings.api_key_roles:
        return ("admin",)
    return _normalized_roles(settings.api_key_roles.get(api_key, ()))


def has_required_role(roles: tuple[str, ...], required_role: str | None) -> bool:
    if required_role is None:
        return True
    required_level = ROLE_LEVELS.get(required_role, ROLE_LEVELS["admin"])
    return max((ROLE_LEVELS.get(role, 0) for role in roles), default=0) >= required_level


def required_role_for_request(method: str, path: str) -> str | None:
    if is_public_path(path):
        return None
    method = method.upper()
    if (
        path.startswith("/api/v1/security/")
        or path.startswith("/api/v1/audit/")
        or path.startswith("/api/v1/artifacts")
        or path.startswith("/api/v1/governance/")
    ):
        return "admin"
    if path.startswith("/api/v1/storage/migration/"):
        return "admin"
    if _is_admin_data_path(method, path):
        return "admin"
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        return "operator"
    if method == "GET" and (path.endswith("/export.zip") or path.endswith("/download") or path == "/api/v1/data/snapshot.zip"):
        return "operator"
    return "viewer"


def _is_admin_data_path(method: str, path: str) -> bool:
    admin_posts = {
        "/api/v1/data/refresh",
        "/api/v1/data/provenance/baseline",
        "/api/v1/data/lockfile/write",
        "/api/v1/data/release-lock/write",
        "/api/v1/rag/ingest-local",
        "/api/v1/rag/rebuild",
        "/api/v1/structured/import/preview",
        "/api/v1/structured/import",
        "/api/v1/external/gtex/import-gene-expression",
        "/api/v1/external/allen/import-whb-taxonomy",
        "/api/v1/storage/postgres/schema.sql/write",
    }
    return method in {"POST", "PUT", "PATCH", "DELETE"} and path in admin_posts


def _normalized_roles(roles: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(sorted({role for role in roles if role in ROLE_LEVELS}))
    return normalized or ("viewer",)


@dataclass
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int


@dataclass
class InMemoryRateLimiter:
    window_seconds: int = 60
    _buckets: dict[str, tuple[int, int]] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def check(self, key: str, limit: int) -> RateLimitDecision:
        if limit <= 0:
            return RateLimitDecision(True, 0, 0, 0)

        now = int(time.time())
        bucket = now // self.window_seconds
        with self._lock:
            self._cleanup(bucket)
            bucket_id, count = self._buckets.get(key, (bucket, 0))
            if bucket_id != bucket:
                bucket_id, count = bucket, 0
            count += 1
            self._buckets[key] = (bucket_id, count)

        remaining = max(0, limit - count)
        reset_seconds = self.window_seconds - (now % self.window_seconds)
        return RateLimitDecision(count <= limit, limit, remaining, reset_seconds)

    def _cleanup(self, current_bucket: int) -> None:
        stale_keys = [key for key, (bucket, _) in self._buckets.items() if bucket < current_bucket]
        for key in stale_keys:
            del self._buckets[key]
