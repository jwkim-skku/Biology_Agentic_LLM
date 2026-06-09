from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE_COMPOSE = ROOT / "docker-compose.yml"
PRODUCTION_COMPOSE = ROOT / "docker-compose.production.yml"


SYNTHETIC_ENV = {
    "NEXT_PUBLIC_API_BASE_URL": "http://127.0.0.1:8000/api/v1",
    "NEXT_PUBLIC_API_KEY": "viewer-key-for-compose-preflight-0001",
    "BACKEND_PORT": "18000",
    "FRONTEND_PORT": "13000",
    "APP_DATA_DIR": "/app/app/data",
    "RAG_DATA_DIR": "/app/app/data",
    "CORS_ORIGINS": "http://127.0.0.1:13000",
    "STORAGE_BACKEND": "postgres",
    "DATABASE_URL": "postgresql://agentic_rag:compose-preflight-password@postgres:5432/agentic_rag",
    "POSTGRES_DB": "agentic_rag",
    "POSTGRES_USER": "agentic_rag",
    "POSTGRES_PASSWORD": "compose-preflight-postgres-password",
    "RAG_VECTOR_BACKEND": "pgvector",
    "RAG_PGVECTOR_TABLE": "rag_chunks",
    "QDRANT_URL": "",
    "QDRANT_COLLECTION": "agentic_rag_chunks",
    "RAG_EMBEDDING_BACKEND": "openai",
    "RAG_EMBEDDING_MODEL": "text-embedding-3-small",
    "RAG_EMBEDDING_DIMENSIONS": "128",
    "OPENAI_API_KEY": "compose-preflight-openai-key",
    "OPENAI_EMBEDDING_BASE_URL": "https://api.openai.com/v1",
    "OPENAI_EMBEDDING_PRICE_PER_1K_TOKENS": "0.001",
    "OPENAI_EMBEDDING_BUDGET_USD": "0.05",
    "RNA_FOLDING_BACKEND": "rnafold",
    "RNAFOLD_EXECUTABLE": "RNAfold",
    "RNAFOLD_TIMEOUT_SECONDS": "10",
    "RNAFOLD_WINDOW_NT": "180",
    "API_KEYS": "admin-compose-key-000000000001,operator-compose-key-0000000001,viewer-compose-key-000000000001",
    "API_KEY_ROLES": (
        "admin-compose-key-000000000001=admin;"
        "operator-compose-key-0000000001=operator;"
        "viewer-compose-key-000000000001=viewer"
    ),
    "RATE_LIMIT_PER_MINUTE": "120",
    "ARTIFACT_SIGNING_KEY": "compose-preflight-artifact-signing-key-with-32-bytes",
    "ARTIFACT_SIGNING_KEY_ID": "compose-preflight-hmac-key",
    "ARTIFACT_RETENTION_DAYS": "365",
    "ARTIFACT_RETENTION_KEEP_MIN": "1000",
    "ARTIFACT_OBJECT_STORE_ENABLED": "true",
    "ARTIFACT_OBJECT_STORE_ENDPOINT": "https://s3.compose-preflight.example.com",
    "ARTIFACT_OBJECT_STORE_BUCKET": "agentic-rag-compose-preflight",
    "ARTIFACT_OBJECT_STORE_PREFIX": "agentic-rag/artifacts",
    "ARTIFACT_OBJECT_STORE_REGION": "us-east-1",
    "ARTIFACT_OBJECT_STORE_ACCESS_KEY_ID": "compose-preflight-object-access-key",
    "ARTIFACT_OBJECT_STORE_SECRET_ACCESS_KEY": "compose-preflight-object-secret-key",
    "ARTIFACT_EXTERNAL_TIMESTAMP_REQUIRED": "true",
    "ARTIFACT_EXTERNAL_TIMESTAMP_URL": "https://timestamp.compose-preflight.example.com/rfc3161",
    "ARTIFACT_EXTERNAL_TIMESTAMP_KEY_ID": "compose-preflight-rfc3161",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Docker Compose deployment configuration.")
    parser.add_argument("--require-docker", action="store_true", help="Fail when Docker Compose cannot be executed.")
    parser.add_argument("--static-only", action="store_true", help="Run only static compose-file checks.")
    args = parser.parse_args()

    failures: list[str] = []
    warnings: list[str] = []
    static_checks(failures)
    docker_result = (
        {"available": shutil.which("docker") is not None, "config_checked": False, "static_only": True}
        if args.static_only
        else run_docker_compose_config(require_docker=args.require_docker, failures=failures, warnings=warnings)
    )

    result = {
        "base_compose": str(BASE_COMPOSE),
        "production_compose": str(PRODUCTION_COMPOSE),
        "docker": docker_result,
        "failures": failures,
        "warnings": warnings,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if failures else 0


def static_checks(failures: list[str]) -> None:
    base = read_required(BASE_COMPOSE, failures)
    production = read_required(PRODUCTION_COMPOSE, failures)
    if not base or not production:
        return

    required_base_tokens = [
        "${BACKEND_PORT:-8000}:8000",
        "${FRONTEND_PORT:-3000}:3000",
        "${NEXT_PUBLIC_API_BASE_URL:-http://127.0.0.1:8000/api/v1}",
        "${OPENAI_API_KEY:-}",
        "${OPENAI_EMBEDDING_BASE_URL:-https://api.openai.com/v1}",
        "${OPENAI_EMBEDDING_PRICE_PER_1K_TOKENS:-0}",
        "${OPENAI_EMBEDDING_BUDGET_USD:-0}",
        "${ARTIFACT_EXTERNAL_TIMESTAMP_REQUIRED:-false}",
        "${ARTIFACT_EXTERNAL_TIMESTAMP_URL:-}",
        "${ARTIFACT_EXTERNAL_TIMESTAMP_KEY_ID:-}",
        "condition: service_healthy",
    ]
    required_production_tokens = [
        "DATABASE_URL is required for production compose",
        "API_KEYS is required for production compose",
        "API_KEY_ROLES is required for production compose",
        "ARTIFACT_SIGNING_KEY is required for production compose",
        "ARTIFACT_SIGNING_KEY_ID is required for production compose",
        "NEXT_PUBLIC_API_BASE_URL is required for production compose",
        "POSTGRES_PASSWORD is required for production compose",
        "OPENAI_API_KEY",
        "OPENAI_EMBEDDING_BASE_URL",
        "OPENAI_EMBEDDING_PRICE_PER_1K_TOKENS",
        "OPENAI_EMBEDDING_BUDGET_USD",
        "ARTIFACT_OBJECT_STORE_ENDPOINT is required for production compose",
        "ARTIFACT_OBJECT_STORE_BUCKET is required for production compose",
        "ARTIFACT_OBJECT_STORE_PREFIX is required for production compose",
        "ARTIFACT_OBJECT_STORE_REGION is required for production compose",
        "ARTIFACT_OBJECT_STORE_ACCESS_KEY_ID is required for production compose",
        "ARTIFACT_OBJECT_STORE_SECRET_ACCESS_KEY is required for production compose",
        "ARTIFACT_EXTERNAL_TIMESTAMP_URL is required for production compose",
        "ARTIFACT_EXTERNAL_TIMESTAMP_KEY_ID is required for production compose",
        "STORAGE_BACKEND: postgres",
    ]
    failures.extend(f"base compose missing token: {token}" for token in required_base_tokens if token not in base)
    failures.extend(f"production compose missing token: {token}" for token in required_production_tokens if token not in production)


def read_required(path: Path, failures: list[str]) -> str:
    if not path.exists():
        failures.append(f"missing compose file: {path}")
        return ""
    return path.read_text(encoding="utf-8")


def run_docker_compose_config(*, require_docker: bool, failures: list[str], warnings: list[str]) -> dict[str, object]:
    if shutil.which("docker") is None:
        message = "docker executable not found; static compose checks only."
        if require_docker:
            failures.append(message)
        else:
            warnings.append(message)
        return {"available": False, "config_checked": False}

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".env") as handle:
        env_path = Path(handle.name)
        for key, value in SYNTHETIC_ENV.items():
            handle.write(f"{key}={value}\n")

    try:
        base = compose_config(
            ["-f", str(BASE_COMPOSE), "--env-file", str(env_path)],
            require_docker=require_docker,
            failures=failures,
            warnings=warnings,
        )
        production = compose_config(
            ["--profile", "postgres", "-f", str(BASE_COMPOSE), "-f", str(PRODUCTION_COMPOSE), "--env-file", str(env_path)],
            require_docker=require_docker,
            failures=failures,
            warnings=warnings,
        )
    finally:
        try:
            env_path.unlink()
        except OSError:
            pass

    if base:
        require_config_token(base, "18000:8000", failures, "base config did not apply BACKEND_PORT.")
        require_config_token(base, "13000:3000", failures, "base config did not apply FRONTEND_PORT.")
    if production:
        require_config_token(production, "STORAGE_BACKEND: postgres", failures, "production config did not force Postgres storage.")
        require_config_token(production, "RAG_EMBEDDING_BACKEND: openai", failures, "production config did not apply RAG_EMBEDDING_BACKEND.")
        require_config_token(production, "OPENAI_API_KEY: compose-preflight-openai-key", failures, "production config did not pass OPENAI_API_KEY.")
        require_config_token(production, "OPENAI_EMBEDDING_BASE_URL: https://api.openai.com/v1", failures, "production config did not pass OPENAI_EMBEDDING_BASE_URL.")
        require_any_config_token(
            production,
            [
                "OPENAI_EMBEDDING_PRICE_PER_1K_TOKENS: '0.001'",
                'OPENAI_EMBEDDING_PRICE_PER_1K_TOKENS: "0.001"',
                "OPENAI_EMBEDDING_PRICE_PER_1K_TOKENS: 0.001",
            ],
            failures,
            "production config did not pass OPENAI_EMBEDDING_PRICE_PER_1K_TOKENS.",
        )
        require_any_config_token(
            production,
            [
                "OPENAI_EMBEDDING_BUDGET_USD: '0.05'",
                'OPENAI_EMBEDDING_BUDGET_USD: "0.05"',
                "OPENAI_EMBEDDING_BUDGET_USD: 0.05",
            ],
            failures,
            "production config did not pass OPENAI_EMBEDDING_BUDGET_USD.",
        )
        require_config_token(production, "condition: service_healthy", failures, "production config did not wait for Postgres health.")
        require_config_token(production, "compose-preflight-artifact-signing-key", failures, "production config did not require artifact signing.")
        require_config_token(production, "https://s3.compose-preflight.example.com", failures, "production config did not require object-store endpoint.")
        require_config_token(production, "agentic-rag-compose-preflight", failures, "production config did not require object-store bucket.")
        require_config_token(production, "agentic-rag/artifacts", failures, "production config did not pass object-store prefix.")
        require_config_token(production, "us-east-1", failures, "production config did not pass object-store region.")
        require_config_token(production, "compose-preflight-object-access-key", failures, "production config did not pass object-store access key.")
        require_config_token(production, "compose-preflight-object-secret-key", failures, "production config did not pass object-store secret key.")
        require_any_config_token(
            production,
            [
                "ARTIFACT_EXTERNAL_TIMESTAMP_REQUIRED: \"true\"",
                "ARTIFACT_EXTERNAL_TIMESTAMP_REQUIRED: 'true'",
                "ARTIFACT_EXTERNAL_TIMESTAMP_REQUIRED: true",
            ],
            failures,
            "production config did not require external timestamping.",
        )
        require_config_token(production, "https://timestamp.compose-preflight.example.com/rfc3161", failures, "production config did not pass external timestamp URL.")
        require_config_token(production, "compose-preflight-rfc3161", failures, "production config did not pass external timestamp key id.")

    return {
        "available": True,
        "config_checked": bool(base and production),
        "base_bytes": len(base),
        "production_bytes": len(production),
    }


def compose_config(args: list[str], *, require_docker: bool, failures: list[str], warnings: list[str]) -> str:
    command = ["docker", "compose", *args, "config"]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if completed.returncode != 0:
        message = f"{' '.join(command)} failed: {completed.stderr.strip()}"
        if require_docker:
            failures.append(message)
        else:
            warnings.append(message)
        return ""
    return completed.stdout


def require_config_token(config: str, token: str, failures: list[str], message: str) -> None:
    if token not in config:
        failures.append(message)


def require_any_config_token(config: str, tokens: list[str], failures: list[str], message: str) -> None:
    if not any(token in config for token in tokens):
        failures.append(message)


if __name__ == "__main__":
    raise SystemExit(main())
