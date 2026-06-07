from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.services.data_refresh_service import refresh_log
from app.services.ingestion_service import list_ingested_documents
from app.services.rag_service import rag_status
from app.services.structured_data_service import structured_manifest, structured_status


DATA_LOCK_PATH = get_settings().data_dir / "runtime" / "data_lock.json"


def build_data_lockfile() -> dict[str, Any]:
    state = _current_lock_state()
    return {
        "lock_schema": "agentic-rag-data-lock-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_dir": str(get_settings().data_dir),
        "lock_hash": _hash_payload(state),
        "state": state,
    }


def write_data_lockfile() -> dict[str, Any]:
    DATA_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lockfile = build_data_lockfile()
    DATA_LOCK_PATH.write_text(_json(lockfile), encoding="utf-8")
    return verify_data_lockfile()


def read_data_lockfile() -> dict[str, Any] | None:
    if not DATA_LOCK_PATH.exists():
        return None
    return json.loads(DATA_LOCK_PATH.read_text(encoding="utf-8"))


def verify_data_lockfile() -> dict[str, Any]:
    current = build_data_lockfile()
    stored = read_data_lockfile()
    if not stored:
        return {
            "status": "missing",
            "lock_path": str(DATA_LOCK_PATH),
            "current_hash": current["lock_hash"],
            "locked_hash": None,
            "generated_at": None,
            "diff": [{"section": "lockfile", "message": "No data lockfile has been written."}],
        }

    locked_state = stored.get("state") or {}
    locked_hash = stored.get("lock_hash") or _hash_payload(locked_state)
    current_hash = current["lock_hash"]
    diff = _diff_lock_state(locked_state, current["state"])
    return {
        "status": "current" if locked_hash == current_hash and not diff else "drift",
        "lock_path": str(DATA_LOCK_PATH),
        "current_hash": current_hash,
        "locked_hash": locked_hash,
        "generated_at": stored.get("generated_at"),
        "diff": diff,
    }


def _current_lock_state() -> dict[str, Any]:
    manifest = structured_manifest()
    status = structured_status()
    rag = rag_status()
    documents = list_ingested_documents()
    refresh = refresh_log(limit=1)
    return {
        "structured": {
            "manifest_hash": status.get("manifest_hash"),
            "records": status.get("records"),
            "datasets": status.get("datasets"),
            "files": [
                {
                    "file": item.get("file"),
                    "sha256": item.get("sha256"),
                    "bytes": item.get("bytes"),
                    "records": item.get("records"),
                    "datasets": item.get("datasets"),
                    "releases": item.get("releases"),
                }
                for item in manifest.get("files", [])
            ],
        },
        "rag": {
            key: rag.get(key)
            for key in ["chunks", "index_version", "embedding_model", "embedding_dimensions"]
        },
        "documents": [
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "source_path": item.get("source_path"),
                "source_sha256": item.get("source_sha256"),
                "chunks": item.get("chunks"),
            }
            for item in documents.get("documents", [])
        ],
        "refresh": {
            "latest": (refresh.get("entries") or [None])[-1],
        },
    }


def _diff_lock_state(locked: dict[str, Any], current: dict[str, Any]) -> list[dict[str, str]]:
    diff: list[dict[str, str]] = []
    for section in ["structured", "rag", "documents", "refresh"]:
        if locked.get(section) != current.get(section):
            diff.append({"section": section, "message": f"{section} state differs from lockfile."})
    return diff


def _hash_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
