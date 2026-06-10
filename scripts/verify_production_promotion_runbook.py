from __future__ import annotations

import argparse
import json
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any


RUNBOOK_SCHEMA = "agentic-rag-production-promotion-runbook-v1"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify production promotion runbook JSON.")
    parser.add_argument("--path", type=Path, help="Path to production_promotion_runbook.json. Reads stdin when omitted.")
    args = parser.parse_args()

    try:
        payload = load_payload(args.path)
    except Exception as exc:
        print(json.dumps({"status": "fail", "errors": [f"invalid JSON input: {exc}"]}, indent=2), file=sys.stderr)
        return 1

    errors = validate_runbook(payload)
    output = {
        "status": "pass" if not errors else "fail",
        "runbook_schema": payload.get("runbook_schema") if isinstance(payload, dict) else None,
        "source_audit_hash": payload.get("source_audit_hash") if isinstance(payload, dict) else None,
        "runbook_hash": payload.get("runbook_hash") if isinstance(payload, dict) else None,
        "proof_checklist_source_match": payload.get("proof_checklist_source_match") if isinstance(payload, dict) else None,
        "errors": errors,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if not errors else 1


def load_payload(path: Path | None) -> Any:
    if path is None:
        return json.loads(sys.stdin.read())
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return json.loads(raw.decode("utf-8"))


def validate_runbook(payload: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["runbook must be a JSON object"]

    if payload.get("runbook_schema") != RUNBOOK_SCHEMA:
        errors.append(f"runbook_schema must be {RUNBOOK_SCHEMA}")
    require_hex_hash(payload, "source_audit_hash", errors)

    verification = payload.get("verification")
    if not isinstance(verification, dict):
        errors.append("verification must be an object")
    elif verification.get("status") not in {"pass", "warning"}:
        errors.append("verification.status must be pass or warning")

    proof_checklist = payload.get("proof_checklist") if isinstance(payload.get("proof_checklist"), list) else []
    if not isinstance(payload.get("proof_checklist"), list):
        errors.append("proof_checklist must be a list")
    if safe_int(payload.get("proof_checklist_count"), -1) != len(proof_checklist):
        errors.append("proof_checklist_count does not match proof_checklist")
    if payload.get("proof_checklist_hash") != hash_payload(proof_checklist):
        errors.append("proof_checklist_hash does not match proof_checklist")

    source_count = safe_int(payload.get("source_proof_checklist_count"), -1)
    if source_count != len(proof_checklist):
        errors.append("source_proof_checklist_count does not match proof_checklist")
    source_hash = payload.get("source_proof_checklist_hash")
    if source_hash is not None and source_hash != payload.get("proof_checklist_hash"):
        errors.append("source_proof_checklist_hash does not match proof_checklist_hash")
    if payload.get("proof_checklist_source_match") is not True:
        errors.append("proof_checklist_source_match must be true")

    groups = payload.get("groups")
    if not isinstance(groups, list):
        errors.append("groups must be a list")
    else:
        group_items = []
        for group in groups:
            if not isinstance(group, dict):
                errors.append("groups contains a non-object item")
                continue
            items = group.get("items")
            if not isinstance(items, list):
                errors.append(f"group {group.get('resolution_scope', 'unknown')!r} items must be a list")
                continue
            group_items.extend(item for item in items if isinstance(item, dict))
            if safe_int(group.get("count"), -1) != len(items):
                errors.append(f"group {group.get('resolution_scope', 'unknown')!r} count does not match items")
        if len(group_items) != len(proof_checklist):
            errors.append("group item count does not match proof_checklist")

    expected_runbook_hash = hash_payload({key: value for key, value in payload.items() if key != "runbook_hash"})
    if payload.get("runbook_hash") != expected_runbook_hash:
        errors.append("runbook_hash does not match runbook payload")
    return errors


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def require_hex_hash(payload: dict[str, Any], key: str, errors: list[str]) -> None:
    value = payload.get(key)
    if not isinstance(value, str) or len(value) != 64:
        errors.append(f"{key} must be a 64-character SHA-256 hex digest")
        return
    try:
        int(value, 16)
    except ValueError:
        errors.append(f"{key} must be hexadecimal")


def hash_payload(payload: Any) -> str:
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
