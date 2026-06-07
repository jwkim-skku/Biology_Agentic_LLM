from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppSettings:
    data_dir: Path
    cors_origins: tuple[str, ...]
    storage_backend: str
    database_url: str
    api_keys: tuple[str, ...]
    api_key_roles: dict[str, tuple[str, ...]]
    rate_limit_per_minute: int
    artifact_signing_key: str
    artifact_signing_key_id: str
    artifact_ed25519_private_key: str
    artifact_ed25519_public_key: str
    artifact_ed25519_key_id: str
    artifact_retention_days: int
    artifact_retention_keep_min: int
    artifact_object_store_enabled: bool
    artifact_object_store_endpoint: str
    artifact_object_store_bucket: str
    artifact_object_store_prefix: str
    artifact_object_store_region: str
    artifact_object_store_access_key_id: str
    artifact_object_store_secret_access_key: str
    rag_vector_backend: str
    rag_pgvector_table: str
    qdrant_url: str
    qdrant_collection: str
    rag_embedding_backend: str
    rag_embedding_model: str
    rag_embedding_dimensions: int
    openai_api_key: str
    openai_embedding_base_url: str
    rna_folding_backend: str
    rnafold_executable: str
    rnafold_timeout_seconds: int
    rnafold_window_nt: int

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_keys)

    @property
    def artifact_signing_enabled(self) -> bool:
        return bool(self.artifact_signing_key)

    @property
    def artifact_asymmetric_signing_enabled(self) -> bool:
        return bool(self.artifact_ed25519_private_key)

    @property
    def artifact_asymmetric_verification_enabled(self) -> bool:
        return bool(self.artifact_ed25519_public_key or self.artifact_ed25519_private_key)


def get_settings() -> AppSettings:
    default_data_dir = Path(__file__).resolve().parent / "data"
    data_dir = Path(os.getenv("APP_DATA_DIR") or os.getenv("RAG_DATA_DIR") or default_data_dir).expanduser().resolve()
    cors_origins = tuple(
        item.strip()
        for item in os.getenv(
            "CORS_ORIGINS",
            "http://127.0.0.1:3000,http://localhost:3000,http://127.0.0.1:3001,http://localhost:3001",
        ).split(",")
        if item.strip()
    )
    api_keys = tuple(item.strip() for item in os.getenv("API_KEYS", "").split(",") if item.strip())
    api_key_roles = _api_key_roles_env("API_KEY_ROLES")
    rate_limit_per_minute = _int_env("RATE_LIMIT_PER_MINUTE", 120)
    artifact_signing_key = os.getenv("ARTIFACT_SIGNING_KEY", "").strip()
    artifact_signing_key_id = os.getenv("ARTIFACT_SIGNING_KEY_ID", "local-hmac-sha256").strip() or "local-hmac-sha256"
    artifact_ed25519_private_key = os.getenv("ARTIFACT_ED25519_PRIVATE_KEY", "").strip()
    artifact_ed25519_public_key = os.getenv("ARTIFACT_ED25519_PUBLIC_KEY", "").strip()
    artifact_ed25519_key_id = os.getenv("ARTIFACT_ED25519_KEY_ID", "local-ed25519").strip() or "local-ed25519"
    artifact_retention_days = _int_env("ARTIFACT_RETENTION_DAYS", 0)
    artifact_retention_keep_min = _int_env("ARTIFACT_RETENTION_KEEP_MIN", 100)
    artifact_object_store_enabled = _bool_env("ARTIFACT_OBJECT_STORE_ENABLED", False)
    artifact_object_store_endpoint = os.getenv("ARTIFACT_OBJECT_STORE_ENDPOINT", "").strip().rstrip("/")
    artifact_object_store_bucket = os.getenv("ARTIFACT_OBJECT_STORE_BUCKET", "").strip()
    artifact_object_store_prefix = os.getenv("ARTIFACT_OBJECT_STORE_PREFIX", "agentic-rag/artifacts").strip().strip("/")
    artifact_object_store_region = os.getenv("ARTIFACT_OBJECT_STORE_REGION", "us-east-1").strip() or "us-east-1"
    artifact_object_store_access_key_id = os.getenv("ARTIFACT_OBJECT_STORE_ACCESS_KEY_ID", "").strip()
    artifact_object_store_secret_access_key = os.getenv("ARTIFACT_OBJECT_STORE_SECRET_ACCESS_KEY", "").strip()
    rag_vector_backend = os.getenv("RAG_VECTOR_BACKEND", "local_json").strip().lower() or "local_json"
    if rag_vector_backend not in {"local_json", "pgvector", "qdrant"}:
        rag_vector_backend = "local_json"
    rag_pgvector_table = os.getenv("RAG_PGVECTOR_TABLE", "rag_chunks").strip() or "rag_chunks"
    qdrant_url = os.getenv("QDRANT_URL", "").strip().rstrip("/")
    qdrant_collection = os.getenv("QDRANT_COLLECTION", "agentic_rag_chunks").strip() or "agentic_rag_chunks"
    rag_embedding_backend = os.getenv("RAG_EMBEDDING_BACKEND", "hash_bow").strip().lower().replace("-", "_") or "hash_bow"
    if rag_embedding_backend not in {"hash_bow", "sentence_transformers", "openai"}:
        rag_embedding_backend = "hash_bow"
    raw_embedding_model = os.getenv("RAG_EMBEDDING_MODEL", "").strip()
    if raw_embedding_model:
        rag_embedding_model = raw_embedding_model
    elif rag_embedding_backend == "sentence_transformers":
        rag_embedding_model = "pritamdeka/S-BioBert-snli-multinli-stsb"
    elif rag_embedding_backend == "openai":
        rag_embedding_model = "text-embedding-3-small"
    else:
        rag_embedding_model = "hash-bow-v1"
    rag_embedding_dimensions = _int_env("RAG_EMBEDDING_DIMENSIONS", 128)
    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    openai_embedding_base_url = os.getenv("OPENAI_EMBEDDING_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/") or "https://api.openai.com/v1"
    rna_folding_backend = os.getenv("RNA_FOLDING_BACKEND", "deterministic_proxy").strip().lower() or "deterministic_proxy"
    if rna_folding_backend not in {"deterministic_proxy", "rnafold"}:
        rna_folding_backend = "deterministic_proxy"
    rnafold_executable = os.getenv("RNAFOLD_EXECUTABLE", "RNAfold").strip() or "RNAfold"
    rnafold_timeout_seconds = _int_env("RNAFOLD_TIMEOUT_SECONDS", 10)
    rnafold_window_nt = _int_env("RNAFOLD_WINDOW_NT", 180)
    storage_backend = os.getenv("STORAGE_BACKEND", "sqlite").strip().lower() or "sqlite"
    if storage_backend not in {"sqlite", "postgres"}:
        storage_backend = "sqlite"
    database_url = os.getenv("DATABASE_URL", "").strip()
    return AppSettings(
        data_dir=data_dir,
        cors_origins=cors_origins,
        storage_backend=storage_backend,
        database_url=database_url,
        api_keys=api_keys,
        api_key_roles=api_key_roles,
        rate_limit_per_minute=max(0, rate_limit_per_minute),
        artifact_signing_key=artifact_signing_key,
        artifact_signing_key_id=artifact_signing_key_id,
        artifact_ed25519_private_key=artifact_ed25519_private_key,
        artifact_ed25519_public_key=artifact_ed25519_public_key,
        artifact_ed25519_key_id=artifact_ed25519_key_id,
        artifact_retention_days=max(0, artifact_retention_days),
        artifact_retention_keep_min=max(0, artifact_retention_keep_min),
        artifact_object_store_enabled=artifact_object_store_enabled,
        artifact_object_store_endpoint=artifact_object_store_endpoint,
        artifact_object_store_bucket=artifact_object_store_bucket,
        artifact_object_store_prefix=artifact_object_store_prefix,
        artifact_object_store_region=artifact_object_store_region,
        artifact_object_store_access_key_id=artifact_object_store_access_key_id,
        artifact_object_store_secret_access_key=artifact_object_store_secret_access_key,
        rag_vector_backend=rag_vector_backend,
        rag_pgvector_table=rag_pgvector_table,
        qdrant_url=qdrant_url,
        qdrant_collection=qdrant_collection,
        rag_embedding_backend=rag_embedding_backend,
        rag_embedding_model=rag_embedding_model,
        rag_embedding_dimensions=max(16, rag_embedding_dimensions),
        openai_api_key=openai_api_key,
        openai_embedding_base_url=openai_embedding_base_url,
        rna_folding_backend=rna_folding_backend,
        rnafold_executable=rnafold_executable,
        rnafold_timeout_seconds=max(1, rnafold_timeout_seconds),
        rnafold_window_nt=max(18, rnafold_window_nt),
    )


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _api_key_roles_env(name: str) -> dict[str, tuple[str, ...]]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    parsed: dict[str, tuple[str, ...]] = {}
    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        for key, roles in payload.items():
            normalized_key = str(key).strip()
            if not normalized_key:
                continue
            parsed[normalized_key] = _normalize_roles(roles)
        return parsed

    for item in raw.split(";"):
        key, separator, roles = item.partition("=")
        if not separator:
            continue
        normalized_key = key.strip()
        if normalized_key:
            parsed[normalized_key] = _normalize_roles(roles.split(","))
    return parsed


def _normalize_roles(value) -> tuple[str, ...]:
    if isinstance(value, str):
        raw_roles = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_roles = value
    else:
        raw_roles = []
    roles = tuple(sorted({str(role).strip().lower() for role in raw_roles if str(role).strip()}))
    return roles or ("viewer",)
