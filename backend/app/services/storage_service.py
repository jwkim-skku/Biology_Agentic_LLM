from __future__ import annotations

import importlib.util
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.config import get_settings


POSTGRES_SCHEMA_VERSION = "agentic-rag-storage-postgres-v1"


POSTGRES_SCHEMA_SQL = """-- Agentic RAG production storage schema
-- Version: agentic-rag-storage-postgres-v1

create table if not exists runs (
    run_id text primary key,
    run_type text not null,
    created_at timestamptz not null,
    updated_at timestamptz not null,
    gene text,
    brain_region text,
    cell_type text,
    modality text,
    pipeline_version text,
    structured_manifest_hash text,
    recommended_candidate_id text,
    composite_quality double precision,
    request_json jsonb not null default '{}'::jsonb,
    design_json jsonb not null default '{}'::jsonb,
    qc_report_json jsonb not null default '{}'::jsonb,
    trace_json jsonb not null default '[]'::jsonb
);

create index if not exists idx_runs_updated_at on runs(updated_at desc);
create index if not exists idx_runs_gene on runs(gene);
create index if not exists idx_runs_target on runs(brain_region, cell_type, modality);
create index if not exists idx_runs_structured_manifest_hash on runs(structured_manifest_hash);

create table if not exists run_artifacts (
    run_id text not null references runs(run_id) on delete cascade,
    artifact_type text not null,
    media_type text not null,
    content bytea not null,
    created_at timestamptz not null,
    primary key (run_id, artifact_type)
);

create index if not exists idx_run_artifacts_created_at on run_artifacts(created_at desc);

create table if not exists agent_memory (
    run_id text primary key references runs(run_id) on delete cascade,
    run_type text not null,
    created_at timestamptz not null,
    updated_at timestamptz not null,
    gene text,
    brain_region text,
    cell_type text,
    modality text,
    structured_manifest_hash text,
    recommended_candidate_id text,
    memory_hash text not null,
    session_json jsonb not null default '{}'::jsonb,
    semantic_json jsonb not null default '{}'::jsonb,
    artifact_json jsonb not null default '{}'::jsonb
);

create index if not exists idx_agent_memory_updated_at on agent_memory(updated_at desc);
create index if not exists idx_agent_memory_target on agent_memory(gene, brain_region, cell_type);

create table if not exists jobs (
    job_id text primary key,
    job_type text not null,
    status text not null,
    created_at timestamptz not null,
    updated_at timestamptz not null,
    started_at timestamptz,
    completed_at timestamptz,
    request_json jsonb not null default '{}'::jsonb,
    result_json jsonb not null default '{}'::jsonb,
    error text
);

create index if not exists idx_jobs_updated_at on jobs(updated_at desc);
create index if not exists idx_jobs_status on jobs(status);
create index if not exists idx_jobs_type_status on jobs(job_type, status);

create table if not exists audit_events (
    event_id text primary key,
    timestamp timestamptz not null,
    event_type text not null,
    action text not null,
    outcome text not null,
    actor text not null,
    request_id text,
    resource_type text,
    resource_id text,
    detail_json jsonb not null default '{}'::jsonb
);

create index if not exists idx_audit_timestamp on audit_events(timestamp desc);
create index if not exists idx_audit_event_type on audit_events(event_type);
create index if not exists idx_audit_resource on audit_events(resource_type, resource_id);
create index if not exists idx_audit_request_id on audit_events(request_id);

create extension if not exists vector;

create table if not exists rag_chunks (
    chunk_id text primary key,
    document_id text not null,
    text text not null,
    embedding vector({rag_embedding_dimensions}) not null,
    payload jsonb not null,
    structured_manifest_hash text,
    indexed_at timestamptz not null default now()
);

create index if not exists idx_rag_chunks_document_id on rag_chunks(document_id);
create index if not exists idx_rag_chunks_payload_gin on rag_chunks using gin(payload);
create index if not exists idx_rag_chunks_embedding_hnsw on rag_chunks using hnsw (embedding vector_cosine_ops);

create table if not exists schema_migrations (
    version text primary key,
    applied_at timestamptz not null default now(),
    schema_hash text not null
);

insert into schema_migrations (version, schema_hash)
values ('agentic-rag-storage-postgres-v1', '{schema_hash}')
on conflict(version) do nothing;
"""


def storage_status() -> dict[str, Any]:
    settings = get_settings()
    sqlite_files = _sqlite_files(settings.data_dir)
    postgres_configured = bool(settings.database_url)
    postgres_driver_available = importlib.util.find_spec("psycopg") is not None
    schema_hash = postgres_schema_hash()
    target_backend = settings.storage_backend
    status = "pass"
    warnings: list[str] = []

    if target_backend == "postgres" and not postgres_configured:
        status = "warning"
        warnings.append("STORAGE_BACKEND=postgres is set but DATABASE_URL is empty.")
    if target_backend == "postgres" and postgres_configured and not postgres_driver_available:
        status = "warning"
        warnings.append("Postgres storage requires installing the psycopg dependency.")
    active_runtime_adapter = "postgres" if target_backend == "postgres" and postgres_configured else "sqlite"
    if target_backend == "postgres" and postgres_configured:
        warnings.append("Postgres runtime adapter is enabled; run parity checks before multi-user cutover.")

    return {
        "status": status,
        "target_backend": target_backend,
        "active_runtime_adapter": active_runtime_adapter,
        "database_url_configured": postgres_configured,
        "database_url_redacted": _redact_database_url(settings.database_url) if postgres_configured else None,
        "sqlite": {
            "data_dir": str(settings.data_dir),
            "files": sqlite_files,
            "total_bytes": sum(item["size_bytes"] for item in sqlite_files),
        },
        "postgres": {
            "schema_version": POSTGRES_SCHEMA_VERSION,
            "schema_hash": schema_hash,
            "driver_available": postgres_driver_available,
            "ddl_endpoint": "/api/v1/storage/postgres/schema.sql",
            "required_extensions": ["vector"],
            "json_storage": "jsonb",
            "binary_artifact_storage": "bytea",
            "rag_vector_table": settings.rag_pgvector_table,
        },
        "migration_readiness": {
            "schema_available": True,
            "sqlite_source_files_present": any(item["exists"] for item in sqlite_files),
            "runtime_adapter_available": True,
            "recommended_sequence": [
                "Stop background jobs.",
                "Export run/job/audit bundles for archive retention.",
                "Apply the Postgres schema SQL.",
                "Bulk-load SQLite rows into matching Postgres tables.",
                "Switch runtime adapters after parity checks pass.",
            ],
        },
        "warnings": warnings,
    }


def postgres_schema_sql() -> str:
    return (
        POSTGRES_SCHEMA_SQL.replace("{rag_embedding_dimensions}", str(get_settings().rag_embedding_dimensions))
        .replace("{schema_hash}", postgres_schema_hash())
    )


def postgres_schema_hash() -> str:
    normalized = (
        POSTGRES_SCHEMA_SQL.replace("{rag_embedding_dimensions}", str(get_settings().rag_embedding_dimensions))
        .replace("{schema_hash}", "")
        .encode("utf-8")
    )
    return sha256(normalized).hexdigest()


def write_postgres_schema_file(path: Path | None = None) -> dict[str, Any]:
    settings = get_settings()
    target = path or settings.data_dir / "runtime" / "postgres_schema.sql"
    target.parent.mkdir(parents=True, exist_ok=True)
    content = postgres_schema_sql()
    target.write_text(content, encoding="utf-8")
    return {
        "path": str(target),
        "schema_version": POSTGRES_SCHEMA_VERSION,
        "schema_hash": postgres_schema_hash(),
        "bytes": len(content.encode("utf-8")),
    }


def redact_database_url(database_url: str) -> str:
    return _redact_database_url(database_url)


def _sqlite_files(data_dir: Path) -> list[dict[str, Any]]:
    runtime = data_dir / "runtime"
    files = [
        ("runs", runtime / "runs.sqlite3"),
        ("agent_memory", runtime / "agent_memory.sqlite3"),
        ("jobs", runtime / "jobs.sqlite3"),
        ("audit", runtime / "audit.sqlite3"),
    ]
    return [_file_status(name, path) for name, path in files]


def _file_status(name: str, path: Path) -> dict[str, Any]:
    exists = path.exists()
    return {
        "name": name,
        "path": str(path),
        "exists": exists,
        "size_bytes": path.stat().st_size if exists else 0,
    }


def _redact_database_url(database_url: str) -> str:
    parsed = urlsplit(database_url)
    if not parsed.netloc:
        return "***"
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    username = parsed.username or ""
    auth = f"{username}:***@" if username else ""
    netloc = f"{auth}{host}{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
