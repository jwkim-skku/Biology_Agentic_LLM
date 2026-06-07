from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.services.storage_service import postgres_schema_sql, redact_database_url


DATA_DIR = get_settings().data_dir
AUDIT_DB_PATH = DATA_DIR / "runtime" / "audit.sqlite3"


def record_audit_event(
    event_type: str,
    action: str,
    *,
    outcome: str = "success",
    actor: str = "system",
    request_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ensure_schema()
    event = {
        "event_id": f"audit_{uuid.uuid4().hex[:16]}",
        "timestamp": _utc_now(),
        "event_type": event_type,
        "action": action,
        "outcome": outcome,
        "actor": actor,
        "request_id": request_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "detail": detail or {},
    }
    if _use_postgres():
        with _connect_postgres() as conn:
            conn.execute(
                """
                insert into audit_events (
                    event_id, timestamp, event_type, action, outcome, actor,
                    request_id, resource_type, resource_id, detail_json
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    event["event_id"],
                    event["timestamp"],
                    event["event_type"],
                    event["action"],
                    event["outcome"],
                    event["actor"],
                    event["request_id"],
                    event["resource_type"],
                    event["resource_id"],
                    _json_dumps(event["detail"]),
                ),
            )
        return event

    with _connect_sqlite() as conn:
        conn.execute(
            """
            insert into audit_events (
                event_id, timestamp, event_type, action, outcome, actor,
                request_id, resource_type, resource_id, detail_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["event_id"],
                event["timestamp"],
                event["event_type"],
                event["action"],
                event["outcome"],
                event["actor"],
                event["request_id"],
                event["resource_type"],
                event["resource_id"],
                _json_dumps(event["detail"]),
            ),
        )
    return event


def list_audit_events(
    *,
    limit: int = 50,
    event_type: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> dict[str, Any]:
    _ensure_schema()
    clauses: list[str] = []
    params: list[Any] = []
    placeholder = "%s" if _use_postgres() else "?"
    if event_type:
        clauses.append(f"event_type = {placeholder}")
        params.append(event_type)
    if resource_type:
        clauses.append(f"resource_type = {placeholder}")
        params.append(resource_type)
    if resource_id:
        clauses.append(f"resource_id = {placeholder}")
        params.append(resource_id)

    where_sql = f"where {' and '.join(clauses)}" if clauses else ""
    params.append(max(1, min(limit, 500)))
    limit_placeholder = "%s" if _use_postgres() else "?"
    if _use_postgres():
        conn_context = _connect_postgres()
    else:
        conn_context = _connect_sqlite()
    with conn_context as conn:
        rows = conn.execute(
            f"""
            select event_id, timestamp, event_type, action, outcome, actor,
                   request_id, resource_type, resource_id, detail_json
            from audit_events
            {where_sql}
            order by timestamp desc
            limit {limit_placeholder}
            """,
            params,
        ).fetchall()
    return {
        "events": [_event_row_to_dict(row) for row in rows],
        "limit": params[-1],
        "filters": {
            "event_type": event_type,
            "resource_type": resource_type,
            "resource_id": resource_id,
        },
        "store_path": _store_path(),
    }


def audit_summary() -> dict[str, Any]:
    _ensure_schema()
    if _use_postgres():
        conn_context = _connect_postgres()
    else:
        conn_context = _connect_sqlite()
    with conn_context as conn:
        total = conn.execute("select count(*) as count from audit_events").fetchone()["count"]
        by_event_type = {
            row["event_type"]: row["count"]
            for row in conn.execute(
                """
                select event_type, count(*) as count
                from audit_events
                group by event_type
                order by event_type
                """
            ).fetchall()
        }
        by_outcome = {
            row["outcome"]: row["count"]
            for row in conn.execute(
                """
                select outcome, count(*) as count
                from audit_events
                group by outcome
                order by outcome
                """
            ).fetchall()
        }
        latest = conn.execute(
            """
            select event_id, timestamp, event_type, action, outcome, actor,
                   request_id, resource_type, resource_id, detail_json
            from audit_events
            order by timestamp desc
            limit 1
            """
        ).fetchone()
    return {
        "total_events": total,
        "by_event_type": by_event_type,
        "by_outcome": by_outcome,
        "latest_event": _event_row_to_dict(latest) if latest else None,
        "store_path": _store_path(),
    }


def _ensure_schema() -> None:
    if _use_postgres():
        with _connect_postgres() as conn:
            conn.execute(postgres_schema_sql())
        return

    AUDIT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect_sqlite() as conn:
        conn.execute(
            """
            create table if not exists audit_events (
                event_id text primary key,
                timestamp text not null,
                event_type text not null,
                action text not null,
                outcome text not null,
                actor text not null,
                request_id text,
                resource_type text,
                resource_id text,
                detail_json text not null
            )
            """
        )
        conn.execute("create index if not exists idx_audit_timestamp on audit_events(timestamp)")
        conn.execute("create index if not exists idx_audit_event_type on audit_events(event_type)")
        conn.execute("create index if not exists idx_audit_resource on audit_events(resource_type, resource_id)")


def _connect_sqlite() -> sqlite3.Connection:
    conn = sqlite3.connect(AUDIT_DB_PATH)
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
    return str(AUDIT_DB_PATH)


def _event_row_to_dict(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    row = _row_to_dict(row)
    return {
        "event_id": row["event_id"],
        "timestamp": row["timestamp"],
        "event_type": row["event_type"],
        "action": row["action"],
        "outcome": row["outcome"],
        "actor": row["actor"],
        "request_id": row["request_id"],
        "resource_type": row["resource_type"],
        "resource_id": row["resource_id"],
        "detail": _json_loads(row["detail_json"]) or {},
    }


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
