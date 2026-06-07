from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / ".env.production"
TEMPLATE_PATH = ROOT / ".env.production.example"

REQUIRED_KEYS = {
    "NEXT_PUBLIC_API_BASE_URL",
    "NEXT_PUBLIC_API_KEY",
    "APP_DATA_DIR",
    "RAG_DATA_DIR",
    "CORS_ORIGINS",
    "STORAGE_BACKEND",
    "DATABASE_URL",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "RAG_VECTOR_BACKEND",
    "RAG_PGVECTOR_TABLE",
    "QDRANT_URL",
    "QDRANT_COLLECTION",
    "RAG_EMBEDDING_BACKEND",
    "RAG_EMBEDDING_MODEL",
    "RAG_EMBEDDING_DIMENSIONS",
    "RNA_FOLDING_BACKEND",
    "RNAFOLD_EXECUTABLE",
    "RNAFOLD_TIMEOUT_SECONDS",
    "RNAFOLD_WINDOW_NT",
    "API_KEYS",
    "API_KEY_ROLES",
    "RATE_LIMIT_PER_MINUTE",
    "ARTIFACT_SIGNING_KEY",
    "ARTIFACT_SIGNING_KEY_ID",
    "ARTIFACT_ED25519_PRIVATE_KEY",
    "ARTIFACT_ED25519_PUBLIC_KEY",
    "ARTIFACT_ED25519_KEY_ID",
    "ARTIFACT_RETENTION_DAYS",
    "ARTIFACT_RETENTION_KEEP_MIN",
    "ARTIFACT_OBJECT_STORE_ENABLED",
    "ARTIFACT_OBJECT_STORE_ENDPOINT",
    "ARTIFACT_OBJECT_STORE_BUCKET",
    "ARTIFACT_OBJECT_STORE_PREFIX",
    "ARTIFACT_OBJECT_STORE_REGION",
    "ARTIFACT_OBJECT_STORE_ACCESS_KEY_ID",
    "ARTIFACT_OBJECT_STORE_SECRET_ACCESS_KEY",
}

PLACEHOLDER_PATTERNS = (
    "replace-with",
    "your-",
    "example.com",
    "changeme",
    "password",
)

ROLE_LEVELS = {"viewer", "operator", "admin"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate production environment files for deployment readiness.")
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH, help="Environment file to validate.")
    parser.add_argument("--template", action="store_true", help="Validate the example template shape instead of concrete secret values.")
    args = parser.parse_args()

    env_path = args.path
    if args.template and args.path == DEFAULT_PATH:
        env_path = TEMPLATE_PATH

    values = parse_env(env_path)
    failures: list[str] = []
    warnings: list[str] = []

    missing = sorted(REQUIRED_KEYS - values.keys())
    failures.extend(f"missing required key: {key}" for key in missing)

    if args.template:
        validate_template(values, failures, warnings)
    else:
        validate_strict(values, failures, warnings)

    result = {
        "path": str(env_path),
        "mode": "template" if args.template else "strict",
        "required_keys": len(REQUIRED_KEYS),
        "keys": len(values),
        "failures": failures,
        "warnings": warnings,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if failures else 0


def parse_env(path: Path) -> dict[str, str]:
    if not path.exists():
        raise SystemExit(f"Environment file does not exist: {path}")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def validate_template(values: dict[str, str], failures: list[str], warnings: list[str]) -> None:
    for key in REQUIRED_KEYS:
        if key in values and values[key] == "":
            warnings.append(f"{key} is intentionally blank in the template.")
    _require(values.get("STORAGE_BACKEND") == "postgres", failures, "template STORAGE_BACKEND must show postgres.")
    _require(values.get("DATABASE_URL", "").startswith("postgresql://"), failures, "template DATABASE_URL must be postgresql://.")
    _require(values.get("RAG_VECTOR_BACKEND") in {"pgvector", "qdrant"}, failures, "template RAG_VECTOR_BACKEND must show a production vector backend.")
    _require(bool(values.get("RAG_PGVECTOR_TABLE")), failures, "template RAG_PGVECTOR_TABLE must be set.")
    _require(bool(values.get("QDRANT_COLLECTION")), failures, "template QDRANT_COLLECTION must be set for qdrant cutover planning.")
    _require(values.get("RAG_EMBEDDING_BACKEND") in {"sentence_transformers", "openai"}, failures, "template RAG_EMBEDDING_BACKEND must show sentence_transformers or openai.")
    _require(bool(values.get("RAG_EMBEDDING_MODEL")), failures, "template RAG_EMBEDDING_MODEL must be set.")
    _require(int_or_none(values.get("RAG_EMBEDDING_DIMENSIONS")) and int(values["RAG_EMBEDDING_DIMENSIONS"]) >= 128, failures, "template RAG_EMBEDDING_DIMENSIONS must be at least 128.")
    _require(values.get("RNA_FOLDING_BACKEND") == "rnafold", failures, "template RNA_FOLDING_BACKEND must show rnafold.")
    _require(bool(values.get("RNAFOLD_EXECUTABLE")), failures, "template RNAFOLD_EXECUTABLE must be set.")
    _require(int_or_none(values.get("RNAFOLD_TIMEOUT_SECONDS")) and int(values["RNAFOLD_TIMEOUT_SECONDS"]) > 0, failures, "template RNAFOLD_TIMEOUT_SECONDS must be positive.")
    _require(int_or_none(values.get("RNAFOLD_WINDOW_NT")) and int(values["RNAFOLD_WINDOW_NT"]) >= 18, failures, "template RNAFOLD_WINDOW_NT must be at least 18.")
    _require("admin" in values.get("API_KEY_ROLES", ""), failures, "template API_KEY_ROLES must include an admin role.")
    _require("operator" in values.get("API_KEY_ROLES", ""), failures, "template API_KEY_ROLES must include an operator role.")
    _require("viewer" in values.get("API_KEY_ROLES", ""), failures, "template API_KEY_ROLES must include a viewer role.")
    _require(
        values.get("NEXT_PUBLIC_API_KEY") in parse_role_map(values.get("API_KEY_ROLES", "")),
        failures,
        "template NEXT_PUBLIC_API_KEY must be represented in API_KEY_ROLES.",
    )
    _require(int_or_none(values.get("RATE_LIMIT_PER_MINUTE")) and int(values["RATE_LIMIT_PER_MINUTE"]) > 0, failures, "template rate limit must be positive.")
    _require(bool(values.get("ARTIFACT_SIGNING_KEY")), failures, "template ARTIFACT_SIGNING_KEY must be set.")
    _require(bool(values.get("ARTIFACT_SIGNING_KEY_ID")), failures, "template ARTIFACT_SIGNING_KEY_ID must be set.")
    _require(bool(values.get("ARTIFACT_ED25519_KEY_ID")), failures, "template ARTIFACT_ED25519_KEY_ID must be set.")
    _require(int_or_none(values.get("ARTIFACT_RETENTION_DAYS")) and int(values["ARTIFACT_RETENTION_DAYS"]) > 0, failures, "template retention days must be positive.")
    _require(values.get("ARTIFACT_OBJECT_STORE_ENABLED", "").lower() == "true", failures, "template object-store mirror must be enabled.")
    _require(values.get("ARTIFACT_OBJECT_STORE_ENDPOINT", "").startswith("https://"), failures, "template object-store endpoint must be https://.")
    _require(bool(values.get("ARTIFACT_OBJECT_STORE_BUCKET")), failures, "template object-store bucket must be set.")
    _require(bool(values.get("ARTIFACT_OBJECT_STORE_REGION")), failures, "template object-store region must be set.")


def validate_strict(values: dict[str, str], failures: list[str], warnings: list[str]) -> None:
    for key, value in values.items():
        if is_placeholder(value):
            failures.append(f"{key} still contains a placeholder value.")

    _require(values.get("STORAGE_BACKEND") == "postgres", failures, "STORAGE_BACKEND must be postgres for production.")
    _require(values.get("RAG_VECTOR_BACKEND") in {"pgvector", "qdrant"}, failures, "RAG_VECTOR_BACKEND must be pgvector or qdrant for production.")
    _require(values.get("RAG_EMBEDDING_BACKEND") in {"sentence_transformers", "openai"}, failures, "RAG_EMBEDDING_BACKEND must be sentence_transformers or openai for production.")
    _require(bool(values.get("RAG_EMBEDDING_MODEL")), failures, "RAG_EMBEDDING_MODEL must be set.")
    embedding_dimensions = int_or_none(values.get("RAG_EMBEDDING_DIMENSIONS"))
    _require(
        embedding_dimensions is not None and embedding_dimensions >= 128,
        failures,
        "RAG_EMBEDDING_DIMENSIONS must be an integer >= 128.",
    )
    if values.get("RAG_EMBEDDING_BACKEND") == "openai":
        _require(bool(values.get("OPENAI_API_KEY")), failures, "OPENAI_API_KEY must be set when RAG_EMBEDDING_BACKEND=openai.")
        _require(
            values.get("OPENAI_EMBEDDING_BASE_URL", "").startswith("https://"),
            failures,
            "OPENAI_EMBEDDING_BASE_URL must be an https:// URL when RAG_EMBEDDING_BACKEND=openai.",
        )
    _require(values.get("RNA_FOLDING_BACKEND") == "rnafold", failures, "RNA_FOLDING_BACKEND must be rnafold for production.")
    _require(bool(values.get("RNAFOLD_EXECUTABLE")), failures, "RNAFOLD_EXECUTABLE must be set.")
    timeout = int_or_none(values.get("RNAFOLD_TIMEOUT_SECONDS"))
    window = int_or_none(values.get("RNAFOLD_WINDOW_NT"))
    _require(timeout is not None and timeout > 0, failures, "RNAFOLD_TIMEOUT_SECONDS must be a positive integer.")
    _require(window is not None and window >= 18, failures, "RNAFOLD_WINDOW_NT must be an integer >= 18.")
    if values.get("RAG_VECTOR_BACKEND") == "pgvector":
        _require(bool(values.get("RAG_PGVECTOR_TABLE")), failures, "RAG_PGVECTOR_TABLE must be set for pgvector.")
    if values.get("RAG_VECTOR_BACKEND") == "qdrant":
        _require(bool(values.get("QDRANT_URL")), failures, "QDRANT_URL must be set for qdrant.")
        _require(bool(values.get("QDRANT_COLLECTION")), failures, "QDRANT_COLLECTION must be set for qdrant.")
    database_url = values.get("DATABASE_URL", "")
    parsed = urlparse(database_url)
    _require(parsed.scheme in {"postgresql", "postgres"}, failures, "DATABASE_URL must be a Postgres URL.")
    _require(bool(parsed.hostname), failures, "DATABASE_URL must include a hostname.")

    api_keys = split_csv(values.get("API_KEYS", ""))
    _require(bool(api_keys), failures, "API_KEYS must include at least one key.")
    weak_keys = [key for key in api_keys if len(key) < 24]
    if weak_keys:
        failures.append("API_KEYS must be at least 24 characters each.")

    role_map = parse_role_map(values.get("API_KEY_ROLES", ""))
    _require(set(role_map) == set(api_keys), failures, "API_KEY_ROLES must map every API key and only configured API keys.")
    assigned_roles = {role for roles in role_map.values() for role in roles}
    _require("admin" in assigned_roles, failures, "API_KEY_ROLES must include an admin role.")
    _require("viewer" in assigned_roles or "operator" in assigned_roles, failures, "API_KEY_ROLES should include a non-admin browser/operator role.")
    browser_key = values.get("NEXT_PUBLIC_API_KEY", "")
    _require(browser_key in api_keys, failures, "NEXT_PUBLIC_API_KEY must match one configured API_KEYS entry.")
    browser_roles = role_map.get(browser_key, set())
    _require(bool(browser_roles), failures, "NEXT_PUBLIC_API_KEY must have an explicit API_KEY_ROLES mapping.")
    _require("admin" not in browser_roles, failures, "NEXT_PUBLIC_API_KEY must not be assigned the admin role.")
    _require(
        bool(browser_roles & {"viewer", "operator"}),
        failures,
        "NEXT_PUBLIC_API_KEY must be assigned a viewer or operator role.",
    )

    rate_limit = int_or_none(values.get("RATE_LIMIT_PER_MINUTE"))
    _require(rate_limit is not None and rate_limit > 0, failures, "RATE_LIMIT_PER_MINUTE must be a positive integer.")

    signing_key = values.get("ARTIFACT_SIGNING_KEY", "")
    ed25519_private = values.get("ARTIFACT_ED25519_PRIVATE_KEY", "")
    ed25519_public = values.get("ARTIFACT_ED25519_PUBLIC_KEY", "")
    _require(bool(signing_key or ed25519_private or ed25519_public), failures, "Artifact signing or verification must be configured.")
    if signing_key and len(signing_key) < 32:
        failures.append("ARTIFACT_SIGNING_KEY should be at least 32 characters.")
    if (ed25519_private or ed25519_public) and not values.get("ARTIFACT_ED25519_KEY_ID"):
        failures.append("ARTIFACT_ED25519_KEY_ID must be set when Ed25519 signing or verification is configured.")

    retention_days = int_or_none(values.get("ARTIFACT_RETENTION_DAYS"))
    keep_min = int_or_none(values.get("ARTIFACT_RETENTION_KEEP_MIN"))
    _require(retention_days is not None and retention_days > 0, failures, "ARTIFACT_RETENTION_DAYS must be positive.")
    _require(keep_min is not None and keep_min >= 100, failures, "ARTIFACT_RETENTION_KEEP_MIN should be at least 100.")

    object_store_enabled = values.get("ARTIFACT_OBJECT_STORE_ENABLED", "").lower() in {"1", "true", "yes", "on"}
    if not object_store_enabled:
        warnings.append("ARTIFACT_OBJECT_STORE_ENABLED is not enabled; use managed object storage for production archive retention.")
    else:
        object_endpoint = values.get("ARTIFACT_OBJECT_STORE_ENDPOINT", "")
        object_parsed = urlparse(object_endpoint)
        _require(object_parsed.scheme == "https", failures, "ARTIFACT_OBJECT_STORE_ENDPOINT must be an https:// URL.")
        _require(bool(object_parsed.hostname), failures, "ARTIFACT_OBJECT_STORE_ENDPOINT must include a hostname.")
        _require(bool(values.get("ARTIFACT_OBJECT_STORE_BUCKET")), failures, "ARTIFACT_OBJECT_STORE_BUCKET must be set.")
        _require(bool(values.get("ARTIFACT_OBJECT_STORE_PREFIX")), failures, "ARTIFACT_OBJECT_STORE_PREFIX must be set.")
        _require(bool(values.get("ARTIFACT_OBJECT_STORE_REGION")), failures, "ARTIFACT_OBJECT_STORE_REGION must be set.")
        _require(bool(values.get("ARTIFACT_OBJECT_STORE_ACCESS_KEY_ID")), failures, "ARTIFACT_OBJECT_STORE_ACCESS_KEY_ID must be set.")
        _require(bool(values.get("ARTIFACT_OBJECT_STORE_SECRET_ACCESS_KEY")), failures, "ARTIFACT_OBJECT_STORE_SECRET_ACCESS_KEY must be set.")

    cors = split_csv(values.get("CORS_ORIGINS", ""))
    _require(bool(cors), failures, "CORS_ORIGINS must include at least one deployed dashboard origin.")
    if "*" in cors:
        failures.append("CORS_ORIGINS must not contain wildcard '*'.")
    if any(origin.startswith("http://") for origin in cors):
        failures.append("CORS_ORIGINS must use https:// origins in production.")
    if any(origin.endswith("/") for origin in cors):
        warnings.append("CORS_ORIGINS should omit trailing slashes.")
    if len(cors) > 5:
        warnings.append("CORS_ORIGINS has more than five origins; keep the browser attack surface narrow.")

    api_base = values.get("NEXT_PUBLIC_API_BASE_URL", "")
    _require(api_base.startswith("https://"), failures, "NEXT_PUBLIC_API_BASE_URL must be an https:// URL.")
    parsed_api_base = urlparse(api_base)
    _require(bool(parsed_api_base.hostname), failures, "NEXT_PUBLIC_API_BASE_URL must include a hostname.")
    if api_base.endswith("/"):
        warnings.append("NEXT_PUBLIC_API_BASE_URL should omit a trailing slash.")


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_role_map(value: str) -> dict[str, set[str]]:
    parsed: dict[str, set[str]] = {}
    for item in value.split(";"):
        key, separator, roles = item.partition("=")
        if not separator:
            continue
        normalized_key = key.strip()
        role_set = {role.strip().lower() for role in roles.split(",") if role.strip().lower() in ROLE_LEVELS}
        if normalized_key and role_set:
            parsed[normalized_key] = role_set
    return parsed


def int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def is_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(pattern in lowered for pattern in PLACEHOLDER_PATTERNS)


def _require(condition: bool, failures: list[str], message: str) -> None:
    if not condition:
        failures.append(message)


if __name__ == "__main__":
    raise SystemExit(main())
