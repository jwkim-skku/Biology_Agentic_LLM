from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.services.storage_service import postgres_schema_sql, redact_database_url

DATA_DIR = get_settings().data_dir
RUN_DB_PATH = DATA_DIR / "runtime" / "runs.sqlite3"


def save_run(
    design: dict[str, Any],
    *,
    run_type: str,
    request_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ensure_schema()
    now = _utc_now()
    run_id = design["run_id"]
    target = design.get("target", {})
    recommended = design.get("recommended_candidate") or {}
    score = (recommended.get("scores") or {}).get("composite_quality")
    if _use_postgres():
        with _connect_postgres() as conn:
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
                    run_id,
                    run_type,
                    design.get("timestamp", now),
                    now,
                    target.get("gene"),
                    target.get("brain_region"),
                    target.get("cell_type"),
                    target.get("modality"),
                    design.get("pipeline_version"),
                    (design.get("provenance") or {}).get("structured_manifest_hash"),
                    recommended.get("candidate_id"),
                    score,
                    _json_dumps(request_payload or {}),
                    _json_dumps(design),
                    _json_dumps(design.get("qc_report") or {}),
                    _json_dumps(design.get("trace") or []),
                ),
            )
        _upsert_memory(design, run_type=run_type, request_payload=request_payload)
        return get_run_summary(run_id) or {"run_id": run_id}

    with _connect_sqlite() as conn:
        conn.execute(
            """
            insert into runs (
                run_id, run_type, created_at, updated_at, gene, brain_region, cell_type,
                modality, pipeline_version, structured_manifest_hash, recommended_candidate_id,
                composite_quality, request_json, design_json, qc_report_json, trace_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                run_id,
                run_type,
                design.get("timestamp", now),
                now,
                target.get("gene"),
                target.get("brain_region"),
                target.get("cell_type"),
                target.get("modality"),
                design.get("pipeline_version"),
                (design.get("provenance") or {}).get("structured_manifest_hash"),
                recommended.get("candidate_id"),
                score,
                _json_dumps(request_payload or {}),
                _json_dumps(design),
                _json_dumps(design.get("qc_report") or {}),
                _json_dumps(design.get("trace") or []),
            ),
        )
    _upsert_memory(design, run_type=run_type, request_payload=request_payload)
    return get_run_summary(run_id) or {"run_id": run_id}


def save_run_artifact(run_id: str, artifact_type: str, content: str | bytes, media_type: str) -> dict[str, Any]:
    _ensure_schema()
    now = _utc_now()
    if _use_postgres():
        with _connect_postgres() as conn:
            conn.execute(
                """
                insert into run_artifacts (run_id, artifact_type, media_type, content, created_at)
                values (%s, %s, %s, %s, %s)
                on conflict(run_id, artifact_type) do update set
                    media_type = excluded.media_type,
                    content = excluded.content,
                    created_at = excluded.created_at
                """,
                (run_id, artifact_type, media_type, _artifact_bytes(content), now),
            )
        return {"run_id": run_id, "artifact_type": artifact_type, "media_type": media_type, "created_at": now}

    with _connect_sqlite() as conn:
        conn.execute(
            """
            insert into run_artifacts (run_id, artifact_type, media_type, content, created_at)
            values (?, ?, ?, ?, ?)
            on conflict(run_id, artifact_type) do update set
                media_type = excluded.media_type,
                content = excluded.content,
                created_at = excluded.created_at
            """,
            (run_id, artifact_type, media_type, content, now),
        )
    return {"run_id": run_id, "artifact_type": artifact_type, "media_type": media_type, "created_at": now}


def list_runs(limit: int = 25) -> dict[str, Any]:
    _ensure_schema()
    if _use_postgres():
        with _connect_postgres() as conn:
            rows = conn.execute(
                """
                select run_id, run_type, created_at, updated_at, gene, brain_region, cell_type,
                       modality, pipeline_version, structured_manifest_hash,
                       recommended_candidate_id, composite_quality
                from runs
                order by updated_at desc
                limit %s
                """,
                (limit,),
            ).fetchall()
        return {"runs": [_row_to_dict(row) for row in rows], "limit": limit, "store_path": _store_path()}

    with _connect_sqlite() as conn:
        rows = conn.execute(
            """
            select run_id, run_type, created_at, updated_at, gene, brain_region, cell_type,
                   modality, pipeline_version, structured_manifest_hash,
                   recommended_candidate_id, composite_quality
            from runs
            order by updated_at desc
            limit ?
            """,
            (limit,),
        ).fetchall()
    return {"runs": [_row_to_dict(row) for row in rows], "limit": limit, "store_path": _store_path()}


def get_run(run_id: str) -> dict[str, Any] | None:
    _ensure_schema()
    if _use_postgres():
        with _connect_postgres() as conn:
            row = conn.execute("select * from runs where run_id = %s", (run_id,)).fetchone()
    else:
        with _connect_sqlite() as conn:
            row = conn.execute("select * from runs where run_id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    data = _row_to_dict(row)
    data["request"] = _json_loads(data.pop("request_json"))
    data["design"] = _json_loads(data.pop("design_json"))
    data["qc_report"] = _json_loads(data.pop("qc_report_json"))
    data["trace"] = _json_loads(data.pop("trace_json"))
    return data


def get_run_summary(run_id: str) -> dict[str, Any] | None:
    run = get_run(run_id)
    if not run:
        return None
    return {
        key: run.get(key)
        for key in [
            "run_id",
            "run_type",
            "created_at",
            "updated_at",
            "gene",
            "brain_region",
            "cell_type",
            "modality",
            "pipeline_version",
            "structured_manifest_hash",
            "recommended_candidate_id",
            "composite_quality",
        ]
    }


def get_run_artifact(run_id: str, artifact_type: str) -> dict[str, Any] | None:
    _ensure_schema()
    if _use_postgres():
        with _connect_postgres() as conn:
            row = conn.execute(
                """
                select run_id, artifact_type, media_type, content, created_at
                from run_artifacts
                where run_id = %s and artifact_type = %s
                """,
                (run_id, artifact_type),
            ).fetchone()
    else:
        with _connect_sqlite() as conn:
            row = conn.execute(
                """
                select run_id, artifact_type, media_type, content, created_at
                from run_artifacts
                where run_id = ? and artifact_type = ?
                """,
                (run_id, artifact_type),
            ).fetchone()
    if not row:
        return None
    artifact = _row_to_dict(row)
    if isinstance(artifact.get("content"), memoryview):
        artifact["content"] = artifact["content"].tobytes()
    return artifact


def build_trace_step(name: str, status: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "timestamp": _utc_now(),
        "detail": detail or {},
    }


def _ensure_schema() -> None:
    if _use_postgres():
        with _connect_postgres() as conn:
            conn.execute(postgres_schema_sql())
        return

    RUN_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect_sqlite() as conn:
        conn.execute(
            """
            create table if not exists runs (
                run_id text primary key,
                run_type text not null,
                created_at text not null,
                updated_at text not null,
                gene text,
                brain_region text,
                cell_type text,
                modality text,
                pipeline_version text,
                structured_manifest_hash text,
                recommended_candidate_id text,
                composite_quality real,
                request_json text not null,
                design_json text not null,
                qc_report_json text not null,
                trace_json text not null
            )
            """
        )
        conn.execute(
            """
            create table if not exists run_artifacts (
                run_id text not null,
                artifact_type text not null,
                media_type text not null,
                content text not null,
                created_at text not null,
                primary key (run_id, artifact_type),
                foreign key (run_id) references runs(run_id) on delete cascade
            )
            """
        )
        conn.execute("create index if not exists idx_runs_updated_at on runs(updated_at)")
        conn.execute("create index if not exists idx_runs_gene on runs(gene)")


def _connect_sqlite() -> sqlite3.Connection:
    conn = sqlite3.connect(RUN_DB_PATH)
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
    return str(RUN_DB_PATH)


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


def _artifact_bytes(content: str | bytes) -> bytes:
    return content if isinstance(content, bytes) else content.encode("utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _upsert_memory(design: dict[str, Any], *, run_type: str, request_payload: dict[str, Any] | None) -> None:
    try:
        from app.services.agent_memory_service import upsert_agent_memory_from_design

        upsert_agent_memory_from_design(design, run_type=run_type, request_payload=request_payload)
    except Exception:
        return
