from __future__ import annotations

import base64
import importlib.util
import json
import sqlite3
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from app.config import get_settings
from app.services.storage_service import postgres_schema_hash, postgres_schema_sql, redact_database_url


MIGRATION_TABLES = [
    ("runs", "runs", False),
    ("agent_memory", "agent_memory", False),
    ("run_artifacts", "run_artifacts", True),
    ("jobs", "jobs", False),
    ("audit_events", "audit_events", False),
]

PRIMARY_KEYS = {
    "runs": ("run_id",),
    "agent_memory": ("run_id",),
    "run_artifacts": ("run_id", "artifact_type"),
    "jobs": ("job_id",),
    "audit_events": ("event_id",),
}

JSON_FIELDS = {
    "runs": ("request_json", "design_json", "qc_report_json", "trace_json"),
    "agent_memory": ("session_json", "semantic_json", "artifact_json"),
    "jobs": ("request_json", "result_json"),
    "audit_events": ("detail_json",),
}


def build_sqlite_migration_bundle() -> bytes:
    settings = get_settings()
    runtime = settings.data_dir / "runtime"
    manifest = {
        "bundle_schema": "agentic-rag-sqlite-migration-bundle-v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source_backend": "sqlite",
        "target_backend": "postgres",
        "postgres_schema_hash": postgres_schema_hash(),
        "tables": [],
    }
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("postgres_schema.sql", postgres_schema_sql())
        for export_name, table_name, encode_binary in MIGRATION_TABLES:
            db_path = _sqlite_db_path(runtime, table_name)
            payload = _table_jsonl(db_path, table_name, encode_binary=encode_binary)
            path = f"{export_name}.jsonl"
            archive.writestr(path, payload["content"])
            manifest["tables"].append(
                {
                    "table": table_name,
                    "path": path,
                    "source_db": str(db_path),
                    "source_exists": db_path.exists(),
                    "records": payload["records"],
                    "sha256": payload["sha256"],
                    "row_fingerprint": payload["row_fingerprint"],
                    "binary_fields_base64": ["content"] if encode_binary else [],
                }
            )
        manifest["total_records"] = sum(item["records"] for item in manifest["tables"])
        manifest["manifest_hash"] = _manifest_hash(manifest)
        archive.writestr("migration_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return buffer.getvalue()


def sqlite_migration_summary() -> dict[str, Any]:
    bundle = build_sqlite_migration_bundle()
    with ZipFile(BytesIO(bundle)) as archive:
        manifest = json.loads(archive.read("migration_manifest.json").decode("utf-8"))
    return {
        "status": "ready",
        "manifest": manifest,
        "bytes": len(bundle),
    }


def sqlite_postgres_parity_report() -> dict[str, Any]:
    settings = get_settings()
    runtime = settings.data_dir / "runtime"
    source_tables = [_source_table_summary(runtime, table_name, encode_binary) for _, table_name, encode_binary in MIGRATION_TABLES]
    postgres = _postgres_target_summary()
    target_tables = postgres.get("tables", {})
    comparisons = []
    for source in source_tables:
        target = target_tables.get(source["table"])
        comparisons.append(
            {
                "table": source["table"],
                "source_records": source["records"],
                "source_sha256": source["sha256"],
                "source_row_hash": source["row_fingerprint"]["combined_row_hash"],
                "target_records": target.get("records") if target else None,
                "target_row_hash": (target.get("row_fingerprint") or {}).get("combined_row_hash") if target else None,
                "record_count_match": target is not None and target.get("records") == source["records"],
                "row_hash_match": target is not None
                and (target.get("row_fingerprint") or {}).get("combined_row_hash") == source["row_fingerprint"]["combined_row_hash"],
                "target_available": target is not None,
            }
        )
    target_available = postgres["status"] == "available"
    count_matches = [item["record_count_match"] for item in comparisons if item["target_available"]]
    row_hash_matches = [item["row_hash_match"] for item in comparisons if item["target_available"]]
    status = "pass" if target_available and count_matches and all(count_matches) and all(row_hash_matches) else "warning"
    return {
        "status": status,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source_backend": "sqlite",
        "target_backend": "postgres",
        "postgres": postgres,
        "source": {
            "total_records": sum(item["records"] for item in source_tables),
            "tables": source_tables,
        },
        "comparisons": comparisons,
        "notes": _parity_notes(target_available),
    }


def import_sqlite_to_postgres(*, dry_run: bool = True) -> dict[str, Any]:
    settings = get_settings()
    runtime = settings.data_dir / "runtime"
    plan = {
        "dry_run": dry_run,
        "source_backend": "sqlite",
        "target_backend": "postgres",
        "database_url_configured": bool(settings.database_url),
        "database_url_redacted": redact_database_url(settings.database_url) if settings.database_url else None,
        "schema_hash": postgres_schema_hash(),
        "tables": [_source_table_summary(runtime, table_name, encode_binary) for _, table_name, encode_binary in MIGRATION_TABLES],
    }
    plan["total_records"] = sum(item["records"] for item in plan["tables"])
    if dry_run:
        return {"status": "planned", **plan}
    if not settings.database_url:
        return {"status": "blocked", "error": "DATABASE_URL is required for Postgres import.", **plan}
    if importlib.util.find_spec("psycopg") is None:
        return {"status": "blocked", "error": "psycopg is not installed.", **plan}

    imported = _bulk_import(runtime)
    return {"status": "imported", **plan, "imported": imported, "parity": sqlite_postgres_parity_report()}


def _table_jsonl(db_path: Path, table_name: str, *, encode_binary: bool) -> dict[str, Any]:
    if not db_path.exists():
        return {"content": "", "records": 0, "sha256": sha256(b"").hexdigest(), "row_fingerprint": _row_fingerprint([], table_name)}

    rows = _sqlite_rows(db_path, table_name)

    lines: list[str] = []
    for row in rows:
        item = dict(row)
        if encode_binary and item.get("content") is not None:
            item["content"] = _base64_content(item["content"])
        lines.append(json.dumps(item, ensure_ascii=False, sort_keys=True))
    content = "\n".join(lines) + ("\n" if lines else "")
    encoded = content.encode("utf-8")
    return {
        "content": content,
        "records": len(lines),
        "sha256": sha256(encoded).hexdigest(),
        "row_fingerprint": _row_fingerprint(rows, table_name),
    }


def _source_table_summary(runtime: Path, table_name: str, encode_binary: bool) -> dict[str, Any]:
    db_path = _sqlite_db_path(runtime, table_name)
    payload = _table_jsonl(db_path, table_name, encode_binary=encode_binary)
    return {
        "table": table_name,
        "source_db": str(db_path),
        "source_exists": db_path.exists(),
        "records": payload["records"],
        "sha256": payload["sha256"],
        "row_fingerprint": payload["row_fingerprint"],
        "binary_fields_base64": ["content"] if encode_binary else [],
    }


def _postgres_target_summary() -> dict[str, Any]:
    settings = get_settings()
    if not settings.database_url:
        return {"status": "unconfigured", "database_url_configured": False, "tables": {}}
    if importlib.util.find_spec("psycopg") is None:
        return {
            "status": "driver_missing",
            "database_url_configured": True,
            "database_url_redacted": redact_database_url(settings.database_url),
            "tables": {},
        }
    try:
        with _connect_postgres() as conn:
            conn.execute(postgres_schema_sql())
            tables = {}
            for _, table_name, _ in MIGRATION_TABLES:
                rows = conn.execute(f"select * from {table_name} order by {_primary_key_order_sql(table_name)}").fetchall()
                tables[table_name] = {
                    "records": len(rows),
                    "row_fingerprint": _row_fingerprint([dict(row) for row in rows], table_name),
                }
        return {
            "status": "available",
            "database_url_configured": True,
            "database_url_redacted": redact_database_url(settings.database_url),
            "tables": tables,
        }
    except Exception as exc:  # noqa: BLE001 - status payload should surface connection failures.
        return {
            "status": "unavailable",
            "database_url_configured": True,
            "database_url_redacted": redact_database_url(settings.database_url),
            "error": str(exc),
            "tables": {},
        }


def _bulk_import(runtime: Path) -> dict[str, Any]:
    rows = {table_name: _sqlite_rows(_sqlite_db_path(runtime, table_name), table_name) for _, table_name, _ in MIGRATION_TABLES}
    imported = {}
    with _connect_postgres() as conn:
        conn.execute(postgres_schema_sql())
        for row in rows["runs"]:
            conn.execute(
                """
                insert into runs (
                    run_id, run_type, created_at, updated_at, gene, brain_region, cell_type,
                    modality, pipeline_version, structured_manifest_hash, recommended_candidate_id,
                    composite_quality, request_json, design_json, qc_report_json, trace_json
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)
                on conflict(run_id) do update set
                    updated_at = excluded.updated_at,
                    run_type = excluded.run_type,
                    gene = excluded.gene,
                    brain_region = excluded.brain_region,
                    cell_type = excluded.cell_type,
                    modality = excluded.modality,
                    pipeline_version = excluded.pipeline_version,
                    structured_manifest_hash = excluded.structured_manifest_hash,
                    recommended_candidate_id = excluded.recommended_candidate_id,
                    composite_quality = excluded.composite_quality,
                    request_json = excluded.request_json,
                    design_json = excluded.design_json,
                    qc_report_json = excluded.qc_report_json,
                    trace_json = excluded.trace_json
                """,
                (
                    row.get("run_id"),
                    row.get("run_type"),
                    row.get("created_at"),
                    row.get("updated_at"),
                    row.get("gene"),
                    row.get("brain_region"),
                    row.get("cell_type"),
                    row.get("modality"),
                    row.get("pipeline_version"),
                    row.get("structured_manifest_hash"),
                    row.get("recommended_candidate_id"),
                    row.get("composite_quality"),
                    row.get("request_json") or "{}",
                    row.get("design_json") or "{}",
                    row.get("qc_report_json") or "{}",
                    row.get("trace_json") or "[]",
                ),
            )
        imported["runs"] = len(rows["runs"])

        for row in rows["agent_memory"]:
            conn.execute(
                """
                insert into agent_memory (
                    run_id, run_type, created_at, updated_at, gene, brain_region, cell_type, modality,
                    structured_manifest_hash, recommended_candidate_id, memory_hash,
                    session_json, semantic_json, artifact_json
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)
                on conflict(run_id) do update set
                    run_type = excluded.run_type,
                    updated_at = excluded.updated_at,
                    gene = excluded.gene,
                    brain_region = excluded.brain_region,
                    cell_type = excluded.cell_type,
                    modality = excluded.modality,
                    structured_manifest_hash = excluded.structured_manifest_hash,
                    recommended_candidate_id = excluded.recommended_candidate_id,
                    memory_hash = excluded.memory_hash,
                    session_json = excluded.session_json,
                    semantic_json = excluded.semantic_json,
                    artifact_json = excluded.artifact_json
                """,
                (
                    row.get("run_id"),
                    row.get("run_type"),
                    row.get("created_at"),
                    row.get("updated_at"),
                    row.get("gene"),
                    row.get("brain_region"),
                    row.get("cell_type"),
                    row.get("modality"),
                    row.get("structured_manifest_hash"),
                    row.get("recommended_candidate_id"),
                    row.get("memory_hash"),
                    row.get("session_json") or "{}",
                    row.get("semantic_json") or "{}",
                    row.get("artifact_json") or "{}",
                ),
            )
        imported["agent_memory"] = len(rows["agent_memory"])

        for row in rows["run_artifacts"]:
            conn.execute(
                """
                insert into run_artifacts (run_id, artifact_type, media_type, content, created_at)
                values (%s, %s, %s, %s, %s)
                on conflict(run_id, artifact_type) do update set
                    media_type = excluded.media_type,
                    content = excluded.content,
                    created_at = excluded.created_at
                """,
                (row.get("run_id"), row.get("artifact_type"), row.get("media_type"), _artifact_bytes(row.get("content")), row.get("created_at")),
            )
        imported["run_artifacts"] = len(rows["run_artifacts"])

        for row in rows["jobs"]:
            conn.execute(
                """
                insert into jobs (
                    job_id, job_type, status, created_at, updated_at, started_at,
                    completed_at, request_json, result_json, error
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                on conflict(job_id) do update set
                    job_type = excluded.job_type,
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    started_at = excluded.started_at,
                    completed_at = excluded.completed_at,
                    request_json = excluded.request_json,
                    result_json = excluded.result_json,
                    error = excluded.error
                """,
                (
                    row.get("job_id"),
                    row.get("job_type"),
                    row.get("status"),
                    row.get("created_at"),
                    row.get("updated_at"),
                    row.get("started_at"),
                    row.get("completed_at"),
                    row.get("request_json") or "{}",
                    row.get("result_json") or "{}",
                    row.get("error"),
                ),
            )
        imported["jobs"] = len(rows["jobs"])

        for row in rows["audit_events"]:
            conn.execute(
                """
                insert into audit_events (
                    event_id, timestamp, event_type, action, outcome, actor,
                    request_id, resource_type, resource_id, detail_json
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                on conflict(event_id) do update set
                    timestamp = excluded.timestamp,
                    event_type = excluded.event_type,
                    action = excluded.action,
                    outcome = excluded.outcome,
                    actor = excluded.actor,
                    request_id = excluded.request_id,
                    resource_type = excluded.resource_type,
                    resource_id = excluded.resource_id,
                    detail_json = excluded.detail_json
                """,
                (
                    row.get("event_id"),
                    row.get("timestamp"),
                    row.get("event_type"),
                    row.get("action"),
                    row.get("outcome"),
                    row.get("actor"),
                    row.get("request_id"),
                    row.get("resource_type"),
                    row.get("resource_id"),
                    row.get("detail_json") or "{}",
                ),
            )
        imported["audit_events"] = len(rows["audit_events"])
    return imported


def _sqlite_rows(db_path: Path, table_name: str) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(f"select * from {table_name}").fetchall()
        except sqlite3.OperationalError:
            return []
    return [{key: row[key] for key in row.keys()} for row in rows]


def _row_fingerprint(rows: list[dict[str, Any]], table_name: str) -> dict[str, Any]:
    hashed_rows = [_row_hash_entry(row, table_name) for row in rows]
    hashed_rows.sort(key=lambda item: item["primary_key"])
    combined_payload = "\n".join(f"{item['primary_key']}:{item['sha256']}" for item in hashed_rows).encode("utf-8")
    return {
        "algorithm": "sha256-canonical-jsonl-v1",
        "primary_key_fields": list(PRIMARY_KEYS[table_name]),
        "hash_count": len(hashed_rows),
        "combined_row_hash": sha256(combined_payload).hexdigest(),
        "sample": hashed_rows[:5],
    }


def _row_hash_entry(row: dict[str, Any], table_name: str) -> dict[str, str]:
    primary_key = _primary_key(row, table_name)
    canonical = _canonical_row(row, table_name)
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"primary_key": primary_key, "sha256": sha256(encoded).hexdigest()}


def _primary_key(row: dict[str, Any], table_name: str) -> str:
    fields = PRIMARY_KEYS[table_name]
    return "|".join(str(row.get(field) or "") for field in fields)


def _canonical_row(row: dict[str, Any], table_name: str) -> dict[str, Any]:
    canonical: dict[str, Any] = {}
    for key in sorted(row):
        value = row[key]
        if key in JSON_FIELDS.get(table_name, ()):
            canonical[key] = _canonical_json_field(value)
        elif key == "content" and table_name == "run_artifacts":
            canonical[key] = _base64_content(value)
        elif isinstance(value, memoryview):
            canonical[key] = base64.b64encode(value.tobytes()).decode("ascii")
        elif isinstance(value, bytes):
            canonical[key] = base64.b64encode(value).decode("ascii")
        else:
            canonical[key] = value
    return canonical


def _canonical_json_field(value: Any) -> Any:
    if value is None:
        return None
    if value == "":
        return ""
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def _primary_key_order_sql(table_name: str) -> str:
    return ", ".join(PRIMARY_KEYS[table_name])


def _connect_postgres():
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(get_settings().database_url, row_factory=dict_row)


def _sqlite_db_path(runtime: Path, table_name: str) -> Path:
    if table_name in {"runs", "run_artifacts"}:
        return runtime / "runs.sqlite3"
    if table_name == "agent_memory":
        return runtime / "agent_memory.sqlite3"
    if table_name == "jobs":
        return runtime / "jobs.sqlite3"
    return runtime / "audit.sqlite3"


def _base64_content(content: Any) -> str:
    if isinstance(content, memoryview):
        raw = content.tobytes()
    elif isinstance(content, bytes):
        raw = content
    else:
        raw = str(content).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _artifact_bytes(content: Any) -> bytes:
    if content is None:
        return b""
    if isinstance(content, bytes):
        return content
    return str(content).encode("utf-8")


def _parity_notes(target_available: bool) -> list[str]:
    if target_available:
        return ["Postgres target was reachable; compare count matches before cutover."]
    return ["Postgres target was not reachable/configured; this report validates the SQLite source side only."]


def _manifest_hash(manifest: dict[str, Any]) -> str:
    payload = {key: value for key, value in manifest.items() if key != "manifest_hash"}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return sha256(encoded).hexdigest()
