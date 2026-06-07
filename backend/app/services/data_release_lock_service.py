from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from app.config import get_settings
from app.services.structured_data_service import load_structured_records, structured_manifest


DATA_RELEASE_LOCK_PATH = get_settings().data_dir / "runtime" / "data_release_lock.json"


def build_data_release_lock() -> dict[str, Any]:
    state = _current_release_state()
    return {
        "release_lock_schema": "agentic-rag-data-release-lock-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_dir": str(get_settings().data_dir),
        "release_hash": _hash_payload(state),
        "state": state,
    }


def write_data_release_lock() -> dict[str, Any]:
    DATA_RELEASE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lockfile = build_data_release_lock()
    DATA_RELEASE_LOCK_PATH.write_text(_json(lockfile), encoding="utf-8")
    return verify_data_release_lock()


def read_data_release_lock() -> dict[str, Any] | None:
    if not DATA_RELEASE_LOCK_PATH.exists():
        return None
    return json.loads(DATA_RELEASE_LOCK_PATH.read_text(encoding="utf-8"))


def verify_data_release_lock() -> dict[str, Any]:
    current = build_data_release_lock()
    stored = read_data_release_lock()
    if not stored:
        return {
            "status": "missing",
            "lock_path": str(DATA_RELEASE_LOCK_PATH),
            "current_hash": current["release_hash"],
            "locked_hash": None,
            "generated_at": None,
            "summary": current["state"]["summary"],
            "diff": [{"section": "release_lock", "message": "No data release lockfile has been written."}],
        }

    locked_state = stored.get("state") or {}
    locked_hash = stored.get("release_hash") or _hash_payload(locked_state)
    current_hash = current["release_hash"]
    diff = _diff_release_state(locked_state, current["state"])
    return {
        "status": "current" if locked_hash == current_hash and not diff else "drift",
        "lock_path": str(DATA_RELEASE_LOCK_PATH),
        "current_hash": current_hash,
        "locked_hash": locked_hash,
        "generated_at": stored.get("generated_at"),
        "summary": current["state"]["summary"],
        "diff": diff,
    }


def _current_release_state() -> dict[str, Any]:
    manifest = structured_manifest()
    records = load_structured_records()
    by_dataset: dict[str, dict[str, Any]] = {}
    for record in records:
        dataset = str(record.get("dataset") or "unknown")
        release = str(record.get("release") or "unreleased")
        source_file = str(record.get("_source_file") or "unknown")
        source_sha256 = str(record.get("_source_sha256") or "")
        entry = by_dataset.setdefault(dataset, {"dataset": dataset, "records": 0, "releases": {}, "files": {}})
        entry["records"] += 1
        release_entry = entry["releases"].setdefault(release, {"release": release, "records": 0})
        release_entry["records"] += 1
        if source_file:
            entry["files"][source_file] = source_sha256

    datasets = []
    for dataset in sorted(by_dataset):
        entry = by_dataset[dataset]
        datasets.append(
            {
                "dataset": dataset,
                "records": entry["records"],
                "releases": sorted(entry["releases"].values(), key=lambda item: item["release"]),
                "files": [
                    {"file": file_name, "sha256": entry["files"][file_name]}
                    for file_name in sorted(entry["files"])
                ],
            }
        )

    files = [
        {
            "file": item.get("file"),
            "sha256": item.get("sha256"),
            "bytes": item.get("bytes"),
            "records": item.get("records"),
            "datasets": item.get("datasets"),
            "releases": item.get("releases"),
        }
        for item in manifest.get("files", [])
    ]
    return {
        "manifest_hash": manifest.get("manifest_hash"),
        "summary": {
            "datasets": len(datasets),
            "files": len(files),
            "records": sum(int(item.get("records") or 0) for item in datasets),
            "release_values": sorted({release["release"] for dataset in datasets for release in dataset["releases"]}),
        },
        "datasets": datasets,
        "files": files,
    }


def _diff_release_state(locked: dict[str, Any], current: dict[str, Any]) -> list[dict[str, str]]:
    diff: list[dict[str, str]] = []
    for section in ["manifest_hash", "summary", "datasets", "files"]:
        if locked.get(section) != current.get(section):
            diff.append({"section": section, "message": f"{section} differs from data release lockfile."})
    return diff


def _hash_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
