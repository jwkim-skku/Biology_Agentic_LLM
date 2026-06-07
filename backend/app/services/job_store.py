from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.services.storage_service import postgres_schema_sql, redact_database_url


JOB_DB_PATH = get_settings().data_dir / "runtime" / "jobs.sqlite3"


def create_job(job_type: str, request_payload: dict[str, Any]) -> dict[str, Any]:
    _ensure_schema()
    now = _utc_now()
    job_id = f"job_{uuid.uuid4().hex[:16]}"
    if _use_postgres():
        with _connect_postgres() as conn:
            conn.execute(
                """
                insert into jobs (
                    job_id, job_type, status, created_at, updated_at, started_at,
                    completed_at, request_json, result_json, error
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                """,
                (job_id, job_type, "queued", now, now, None, None, _json_dumps(request_payload), _json_dumps({}), None),
            )
        return get_job(job_id) or {"job_id": job_id, "job_type": job_type, "status": "queued"}

    with _connect_sqlite() as conn:
        conn.execute(
            """
            insert into jobs (
                job_id, job_type, status, created_at, updated_at, started_at,
                completed_at, request_json, result_json, error
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, job_type, "queued", now, now, None, None, _json_dumps(request_payload), _json_dumps({}), None),
        )
    return get_job(job_id) or {"job_id": job_id, "job_type": job_type, "status": "queued"}


def mark_job_running(job_id: str) -> dict[str, Any] | None:
    _ensure_schema()
    now = _utc_now()
    if _use_postgres():
        with _connect_postgres() as conn:
            conn.execute(
                """
                update jobs
                set status = %s, updated_at = %s, started_at = coalesce(started_at, %s)
                where job_id = %s
                """,
                ("running", now, now, job_id),
            )
        return get_job(job_id)

    with _connect_sqlite() as conn:
        conn.execute(
            """
            update jobs
            set status = ?, updated_at = ?, started_at = coalesce(started_at, ?)
            where job_id = ?
            """,
            ("running", now, now, job_id),
        )
    return get_job(job_id)


def complete_job(job_id: str, result_payload: dict[str, Any]) -> dict[str, Any] | None:
    _ensure_schema()
    now = _utc_now()
    if _use_postgres():
        with _connect_postgres() as conn:
            conn.execute(
                """
                update jobs
                set status = %s, updated_at = %s, completed_at = %s, result_json = %s::jsonb, error = null
                where job_id = %s
                """,
                ("succeeded", now, now, _json_dumps(result_payload), job_id),
            )
        return get_job(job_id)

    with _connect_sqlite() as conn:
        conn.execute(
            """
            update jobs
            set status = ?, updated_at = ?, completed_at = ?, result_json = ?, error = null
            where job_id = ?
            """,
            ("succeeded", now, now, _json_dumps(result_payload), job_id),
        )
    return get_job(job_id)


def fail_job(job_id: str, error: str) -> dict[str, Any] | None:
    _ensure_schema()
    now = _utc_now()
    if _use_postgres():
        with _connect_postgres() as conn:
            conn.execute(
                """
                update jobs
                set status = %s, updated_at = %s, completed_at = %s, error = %s
                where job_id = %s
                """,
                ("failed", now, now, error[:2000], job_id),
            )
        return get_job(job_id)

    with _connect_sqlite() as conn:
        conn.execute(
            """
            update jobs
            set status = ?, updated_at = ?, completed_at = ?, error = ?
            where job_id = ?
            """,
            ("failed", now, now, error[:2000], job_id),
        )
    return get_job(job_id)


def get_job(job_id: str) -> dict[str, Any] | None:
    _ensure_schema()
    if _use_postgres():
        with _connect_postgres() as conn:
            row = conn.execute("select * from jobs where job_id = %s", (job_id,)).fetchone()
    else:
        with _connect_sqlite() as conn:
            row = conn.execute("select * from jobs where job_id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    return _decode_job(_row_to_dict(row))


def list_jobs(limit: int = 25, status: str | None = None) -> dict[str, Any]:
    _ensure_schema()
    limit = max(1, min(limit, 200))
    if _use_postgres():
        with _connect_postgres() as conn:
            if status:
                rows = conn.execute(
                    """
                    select * from jobs
                    where status = %s
                    order by updated_at desc
                    limit %s
                    """,
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    select * from jobs
                    order by updated_at desc
                    limit %s
                    """,
                    (limit,),
                ).fetchall()
        return {"jobs": [_decode_job(_row_to_dict(row)) for row in rows], "limit": limit, "store_path": _store_path()}

    with _connect_sqlite() as conn:
        if status:
            rows = conn.execute(
                """
                select * from jobs
                where status = ?
                order by updated_at desc
                limit ?
                """,
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                select * from jobs
                order by updated_at desc
                limit ?
                """,
                (limit,),
            ).fetchall()
    return {"jobs": [_decode_job(_row_to_dict(row)) for row in rows], "limit": limit, "store_path": _store_path()}


def _ensure_schema() -> None:
    if _use_postgres():
        with _connect_postgres() as conn:
            conn.execute(postgres_schema_sql())
        return

    JOB_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect_sqlite() as conn:
        conn.execute(
            """
            create table if not exists jobs (
                job_id text primary key,
                job_type text not null,
                status text not null,
                created_at text not null,
                updated_at text not null,
                started_at text,
                completed_at text,
                request_json text not null,
                result_json text not null,
                error text
            )
            """
        )
        conn.execute("create index if not exists idx_jobs_updated_at on jobs(updated_at)")
        conn.execute("create index if not exists idx_jobs_status on jobs(status)")


def _connect_sqlite() -> sqlite3.Connection:
    conn = sqlite3.connect(JOB_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _connect_postgres():
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError("Postgres storage requires the optional psycopg dependency.") from exc

    database_url = get_settings().database_url
    if not database_url:
        raise RuntimeError("STORAGE_BACKEND=postgres requires DATABASE_URL.")
    return psycopg.connect(database_url, row_factory=dict_row)


def _use_postgres() -> bool:
    settings = get_settings()
    return settings.storage_backend == "postgres" and bool(settings.database_url)


def _store_path() -> str:
    settings = get_settings()
    if _use_postgres():
        return redact_database_url(settings.database_url)
    return str(JOB_DB_PATH)


def _decode_job(row: dict[str, Any]) -> dict[str, Any]:
    row["request"] = _json_loads(row.pop("request_json"))
    row["result"] = _json_loads(row.pop("result_json")) or {}
    return row


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _json_loads(payload: Any) -> Any:
    if not payload:
        return None
    if isinstance(payload, (dict, list)):
        return payload
    return json.loads(payload)


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    return {key: row[key] for key in row.keys()}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
