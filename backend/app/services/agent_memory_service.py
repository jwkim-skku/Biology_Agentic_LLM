from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from app.config import get_settings
from app.services.storage_service import redact_database_url


DATA_DIR = get_settings().data_dir
MEMORY_DB_PATH = DATA_DIR / "runtime" / "agent_memory.sqlite3"
MEMORY_SCHEMA = "agentic-rag-agent-memory-v1"


def upsert_agent_memory_from_design(
    design: dict[str, Any],
    *,
    run_type: str,
    request_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ensure_schema()
    now = _utc_now()
    run_id = str(design.get("run_id") or "")
    if not run_id:
        raise ValueError("Agent memory requires design.run_id.")
    target = design.get("target") or {}
    semantic_memory = _semantic_memory(design)
    artifact_memory = _artifact_memory(design, run_type, request_payload or {})
    session_memory = _session_memory(design, run_type)
    memory_hash = _hash_payload(
        {
            "run_id": run_id,
            "session_memory": session_memory,
            "semantic_memory": semantic_memory,
            "artifact_memory": artifact_memory,
        }
    )
    row = {
        "run_id": run_id,
        "run_type": run_type,
        "created_at": design.get("timestamp") or now,
        "updated_at": now,
        "gene": target.get("gene"),
        "brain_region": target.get("brain_region"),
        "cell_type": target.get("cell_type"),
        "modality": target.get("modality"),
        "structured_manifest_hash": (design.get("provenance") or {}).get("structured_manifest_hash"),
        "recommended_candidate_id": (design.get("recommended_candidate") or {}).get("candidate_id"),
        "memory_hash": memory_hash,
        "session_json": _json_dumps(session_memory),
        "semantic_json": _json_dumps(semantic_memory),
        "artifact_json": _json_dumps(artifact_memory),
    }
    if _use_postgres():
        with _connect_postgres() as conn:
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
                tuple(row[key] for key in _ROW_FIELDS),
            )
    else:
        with _connect_sqlite() as conn:
            conn.execute(
                """
                insert into agent_memory (
                    run_id, run_type, created_at, updated_at, gene, brain_region, cell_type, modality,
                    structured_manifest_hash, recommended_candidate_id, memory_hash,
                    session_json, semantic_json, artifact_json
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                tuple(row[key] for key in _ROW_FIELDS),
            )
    return _memory_summary_row(row)


def list_agent_memory(limit: int = 25, gene: str | None = None, brain_region: str | None = None, cell_type: str | None = None) -> dict[str, Any]:
    _ensure_schema()
    limit = max(1, min(int(limit), 200))
    filters: list[str] = []
    values: list[Any] = []
    if gene:
        filters.append("lower(gene) = lower(?)")
        values.append(gene)
    if brain_region:
        filters.append("lower(brain_region) = lower(?)")
        values.append(brain_region)
    if cell_type:
        filters.append("lower(cell_type) = lower(?)")
        values.append(cell_type)
    where = f"where {' and '.join(filters)}" if filters else ""
    if _use_postgres():
        pg_filters = [item.replace("?", "%s") for item in filters]
        where = f"where {' and '.join(pg_filters)}" if pg_filters else ""
        with _connect_postgres() as conn:
            rows = conn.execute(
                f"""
                select run_id, run_type, created_at, updated_at, gene, brain_region, cell_type, modality,
                       structured_manifest_hash, recommended_candidate_id, memory_hash
                from agent_memory
                {where}
                order by updated_at desc
                limit %s
                """,
                (*values, limit),
            ).fetchall()
        memories = [_row_to_dict(row) for row in rows]
    else:
        with _connect_sqlite() as conn:
            rows = conn.execute(
                f"""
                select run_id, run_type, created_at, updated_at, gene, brain_region, cell_type, modality,
                       structured_manifest_hash, recommended_candidate_id, memory_hash
                from agent_memory
                {where}
                order by updated_at desc
                limit ?
                """,
                (*values, limit),
            ).fetchall()
        memories = [_row_to_dict(row) for row in rows]
    return {"memory_schema": MEMORY_SCHEMA, "limit": limit, "store_path": _store_path(), "memories": memories}


def get_agent_memory(run_id: str) -> dict[str, Any] | None:
    _ensure_schema()
    if _use_postgres():
        with _connect_postgres() as conn:
            row = conn.execute("select * from agent_memory where run_id = %s", (run_id,)).fetchone()
    else:
        with _connect_sqlite() as conn:
            row = conn.execute("select * from agent_memory where run_id = ?", (run_id,)).fetchone()
    if not row:
        return None
    data = _row_to_dict(row)
    data["session_memory"] = _json_loads(data.pop("session_json"))
    data["semantic_memory"] = _json_loads(data.pop("semantic_json"))
    data["artifact_memory"] = _json_loads(data.pop("artifact_json"))
    data["memory_schema"] = MEMORY_SCHEMA
    return data


def agent_memory_summary() -> dict[str, Any]:
    _ensure_schema()
    if _use_postgres():
        with _connect_postgres() as conn:
            rows = conn.execute(
                """
                select count(*) as count,
                       count(distinct gene) as genes,
                       count(distinct brain_region) as brain_regions,
                       count(distinct cell_type) as cell_types,
                       max(updated_at) as latest_updated_at
                from agent_memory
                """
            ).fetchall()
            by_run_type = conn.execute("select run_type, count(*) as count from agent_memory group by run_type").fetchall()
            memory_hash_rows = conn.execute(
                """
                select memory_hash
                from agent_memory
                order by updated_at desc, run_id asc
                """
            ).fetchall()
    else:
        with _connect_sqlite() as conn:
            rows = conn.execute(
                """
                select count(*) as count,
                       count(distinct gene) as genes,
                       count(distinct brain_region) as brain_regions,
                       count(distinct cell_type) as cell_types,
                       max(updated_at) as latest_updated_at
                from agent_memory
                """
            ).fetchall()
            by_run_type = conn.execute("select run_type, count(*) as count from agent_memory group by run_type").fetchall()
            memory_hash_rows = conn.execute(
                """
                select memory_hash
                from agent_memory
                order by updated_at desc, run_id asc
                """
            ).fetchall()
    row = _row_to_dict(rows[0]) if rows else {}
    memory_hashes = [str(item.get("memory_hash") or "") for item in map(_row_to_dict, memory_hash_rows) if item.get("memory_hash")]
    return {
        "memory_schema": MEMORY_SCHEMA,
        "status": "pass",
        "store_path": _store_path(),
        "memory_count": int(row.get("count") or 0),
        "distinct_genes": int(row.get("genes") or 0),
        "distinct_brain_regions": int(row.get("brain_regions") or 0),
        "distinct_cell_types": int(row.get("cell_types") or 0),
        "latest_updated_at": row.get("latest_updated_at"),
        "latest_memory_hash": memory_hashes[0] if memory_hashes else None,
        "memory_hash_count": len(memory_hashes),
        "memory_hash_aggregate": _hash_payload(memory_hashes) if memory_hashes else None,
        "by_run_type": {str(item.get("run_type") or "unknown"): int(item.get("count") or 0) for item in map(_row_to_dict, by_run_type)},
    }


def _session_memory(design: dict[str, Any], run_type: str) -> dict[str, Any]:
    target = design.get("target") or {}
    return {
        "run_type": run_type,
        "target": {
            "gene": target.get("gene"),
            "species": target.get("species"),
            "brain_region": target.get("brain_region"),
            "cell_type": target.get("cell_type"),
            "modality": target.get("modality"),
        },
        "workflow_id": (design.get("workflow") or {}).get("workflow_id"),
        "trace_steps": [step.get("name") for step in design.get("trace", [])],
    }


def _semantic_memory(design: dict[str, Any]) -> dict[str, Any]:
    report = design.get("qc_report") or {}
    evidence = report.get("evidence_summary") or {}
    diagnostics = design.get("candidate_diagnostics") or report.get("candidate_diagnostics") or {}
    return {
        "supported_rules": evidence.get("supported_rules") or [],
        "uncertain_rules": evidence.get("uncertain_rules") or [],
        "rejected_rules": evidence.get("rejected_rules") or [],
        "coverage": ((design.get("evidence") or {}).get("coverage")) or {},
        "candidate_diagnostics": {
            "feasible_count": diagnostics.get("feasible_count"),
            "candidate_count": diagnostics.get("candidate_count"),
            "selection_policy": diagnostics.get("selection_policy"),
            "recommendation_audit": diagnostics.get("recommendation_audit") or design.get("recommendation_audit"),
        },
        "open_questions": report.get("open_questions") or [],
    }


def _artifact_memory(design: dict[str, Any], run_type: str, request_payload: dict[str, Any]) -> dict[str, Any]:
    recommended = design.get("recommended_candidate") or {}
    recommended_scores = recommended.get("scores") or {}
    return {
        "run_id": design.get("run_id"),
        "run_type": run_type,
        "request_hash": _hash_payload(request_payload),
        "design_hash": _hash_payload(design),
        "trace_hash": _hash_payload(design.get("trace") or []),
        "recommended_candidate_id": recommended.get("candidate_id"),
        "recommended_scores": {
            "composite_quality": recommended_scores.get("composite_quality"),
            "cai": recommended_scores.get("cai"),
            "tissue_codon_adaptation": recommended_scores.get("tissue_codon_adaptation"),
            "constraint_risk": (recommended.get("constraint_risk") or {}).get("status"),
            "secondary_structure_proxy_score": recommended_scores.get("secondary_structure_proxy_score"),
        },
        "structured_manifest_hash": (design.get("provenance") or {}).get("structured_manifest_hash"),
        "optimizer_manifest_hash": ((design.get("qc_report") or {}).get("optimizer_reproducibility") or {}).get("manifest_hash"),
    }


def _ensure_schema() -> None:
    if _use_postgres():
        with _connect_postgres() as conn:
            conn.execute(_postgres_memory_schema())
        return
    MEMORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect_sqlite() as conn:
        conn.execute(_sqlite_memory_schema())
        conn.execute("create index if not exists idx_agent_memory_updated_at on agent_memory(updated_at)")
        conn.execute("create index if not exists idx_agent_memory_target on agent_memory(gene, brain_region, cell_type)")


def _sqlite_memory_schema() -> str:
    return """
    create table if not exists agent_memory (
        run_id text primary key,
        run_type text not null,
        created_at text not null,
        updated_at text not null,
        gene text,
        brain_region text,
        cell_type text,
        modality text,
        structured_manifest_hash text,
        recommended_candidate_id text,
        memory_hash text not null,
        session_json text not null,
        semantic_json text not null,
        artifact_json text not null
    )
    """


def _postgres_memory_schema() -> str:
    return """
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
    """


def _memory_summary_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "memory_schema": MEMORY_SCHEMA,
        "run_id": row["run_id"],
        "run_type": row["run_type"],
        "updated_at": row["updated_at"],
        "gene": row.get("gene"),
        "brain_region": row.get("brain_region"),
        "cell_type": row.get("cell_type"),
        "modality": row.get("modality"),
        "recommended_candidate_id": row.get("recommended_candidate_id"),
        "memory_hash": row.get("memory_hash"),
    }


def _connect_sqlite() -> sqlite3.Connection:
    conn = sqlite3.connect(MEMORY_DB_PATH)
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
    return str(MEMORY_DB_PATH)


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _json_loads(payload: Any) -> Any:
    if isinstance(payload, (dict, list)):
        return payload
    return json.loads(payload or "{}")


def _hash_payload(payload: Any) -> str:
    return sha256(_json_dumps(payload).encode("utf-8")).hexdigest()


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    return {key: row[key] for key in row.keys()}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_ROW_FIELDS = [
    "run_id",
    "run_type",
    "created_at",
    "updated_at",
    "gene",
    "brain_region",
    "cell_type",
    "modality",
    "structured_manifest_hash",
    "recommended_candidate_id",
    "memory_hash",
    "session_json",
    "semantic_json",
    "artifact_json",
]
