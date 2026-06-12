from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any


RUNBOOK_SCHEMA = "agentic-rag-production-promotion-runbook-v1"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify production promotion runbook JSON.")
    parser.add_argument("--path", type=Path, help="Path to production_promotion_runbook.json. Reads stdin when omitted.")
    parser.add_argument("--markdown-path", type=Path, help="Optional Markdown runbook rendered from the same JSON payload.")
    args = parser.parse_args()

    try:
        payload = load_payload(args.path)
        markdown_text = args.markdown_path.read_text(encoding="utf-8") if args.markdown_path else None
    except Exception as exc:
        print(json.dumps({"status": "fail", "errors": [f"invalid runbook input: {exc}"]}, indent=2), file=sys.stderr)
        return 1

    errors = validate_runbook(payload, markdown_text=markdown_text)
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


def validate_runbook(payload: Any, *, markdown_text: str | None = None) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["runbook must be a JSON object"]

    if payload.get("runbook_schema") != RUNBOOK_SCHEMA:
        errors.append(f"runbook_schema must be {RUNBOOK_SCHEMA}")
    require_hex_hash(payload, "source_audit_hash", errors)
    validate_source_metadata(payload, errors)

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
    for item in proof_checklist:
        if not isinstance(item, dict):
            errors.append("proof_checklist contains a non-object item")
            continue
        expected_item_hash = hash_payload({key: value for key, value in item.items() if key != "proof_item_hash"})
        if item.get("proof_item_hash") != expected_item_hash:
            errors.append(f"proof_item_hash does not match proof_checklist item for {item.get('area', 'unknown')}")

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
        scope_counts: dict[str, int] = {}
        mode_counts: dict[str, int] = {}
        for group in groups:
            if not isinstance(group, dict):
                errors.append("groups contains a non-object item")
                continue
            scope = str(group.get("resolution_scope") or "unknown")
            items = group.get("items")
            if not isinstance(items, list):
                errors.append(f"group {group.get('resolution_scope', 'unknown')!r} items must be a list")
                continue
            scope_counts[scope] = scope_counts.get(scope, 0) + len(items)
            group_items.extend(item for item in items if isinstance(item, dict))
            if safe_int(group.get("count"), -1) != len(items):
                errors.append(f"group {group.get('resolution_scope', 'unknown')!r} count does not match items")
            for item in items:
                if not isinstance(item, dict):
                    continue
                mode = str(item.get("resolution_mode") or "unknown")
                mode_counts[mode] = mode_counts.get(mode, 0) + 1
                for required in ("area", "priority", "resolution_mode", "proof_artifact", "proof_command", "gap_hash"):
                    if not item.get(required):
                        errors.append(f"group item {item.get('area', 'unknown')!r} is missing {required}")
        if len(group_items) != len(proof_checklist):
            errors.append("group item count does not match proof_checklist")
        if safe_int(payload.get("gap_count"), -1) != len(group_items):
            errors.append("gap_count does not match group items")
        if payload.get("resolution_scope_counts") != dict(sorted(scope_counts.items())):
            errors.append("resolution_scope_counts do not match groups")
        if payload.get("resolution_mode_counts") != dict(sorted(mode_counts.items())):
            errors.append("resolution_mode_counts do not match groups")
        blocking_count = sum(1 for item in group_items if item.get("priority") == "blocking")
        promotion_count = sum(1 for item in group_items if item.get("priority") == "promotion")
        if safe_int(payload.get("blocking_count"), -1) != blocking_count:
            errors.append("blocking_count does not match group items")
        if safe_int(payload.get("promotion_count"), -1) != promotion_count:
            errors.append("promotion_count does not match group items")
        if safe_int(payload.get("gap_count"), -1) != blocking_count + promotion_count:
            errors.append("gap_count does not match blocking plus promotion counts")
        if payload.get("status") == "pass" and group_items:
            errors.append("status pass requires zero runbook gaps")
        if payload.get("production_ready") is True and (payload.get("status") != "pass" or group_items):
            errors.append("production_ready true requires pass status and zero runbook gaps")
        expected_proof_checklist = proof_checklist_from_groups(groups)
        if expected_proof_checklist != proof_checklist:
            errors.append("proof_checklist does not match groups")

    expected_runbook_hash = hash_payload({key: value for key, value in payload.items() if key != "runbook_hash"})
    if payload.get("runbook_hash") != expected_runbook_hash:
        errors.append("runbook_hash does not match runbook payload")
    if markdown_text is not None:
        errors.extend(markdown_failures(payload, markdown_text))
    return errors


def markdown_failures(payload: dict[str, Any], markdown: str) -> list[str]:
    errors: list[str] = []
    if "# Production Promotion Runbook" not in markdown:
        errors.append("markdown_path does not look like a production promotion runbook")
    required_lines = {
        "runbook_hash": f"- Runbook hash: `{payload.get('runbook_hash')}`",
        "source_audit_hash": f"- Source audit hash: `{payload.get('source_audit_hash') or 'n/a'}`",
        "status": f"- Status: `{payload.get('status')}`",
        "production_ready": f"- Production ready: `{payload.get('production_ready')}`",
        "gaps": f"- Gaps: `{payload.get('gap_count')}`; blocking `{payload.get('blocking_count')}`; promotion `{payload.get('promotion_count')}`",
        "proof_checklist_hash": f"- Proof checklist: `{payload.get('proof_checklist_count')}` items; hash `{payload.get('proof_checklist_hash')}`",
        "proof_checklist_source_match": f"- Proof checklist source match: `{payload.get('proof_checklist_source_match')}`",
    }
    for name, line in required_lines.items():
        if line not in markdown:
            errors.append(f"markdown_path {name} line does not match runbook JSON")
    for item in payload.get("proof_checklist") or []:
        if not isinstance(item, dict):
            continue
        proof_hash = str(item.get("proof_item_hash") or "")[:12]
        for key in ("area", "proof_artifact", "proof_command"):
            value = str(item.get(key) or "")
            if value and value not in markdown:
                errors.append(f"markdown_path missing proof checklist {key} for {item.get('area', 'unknown')}")
        if proof_hash and proof_hash not in markdown:
            errors.append(f"markdown_path missing proof checklist hash for {item.get('area', 'unknown')}")
    for group in payload.get("groups") or []:
        if not isinstance(group, dict):
            continue
        header = f"## {group.get('resolution_scope')} ({group.get('count')})"
        if header not in markdown:
            errors.append(f"markdown_path missing group header for {group.get('resolution_scope', 'unknown')}")
    return errors


def validate_source_metadata(payload: dict[str, Any], errors: list[str]) -> None:
    if payload.get("status") not in {"pass", "warning", "fail"}:
        errors.append("status must be pass, warning, or fail")
    if not isinstance(payload.get("production_ready"), bool):
        errors.append("production_ready must be a boolean")
    generated_at = payload.get("source_generated_at")
    if not isinstance(generated_at, str) or not generated_at:
        errors.append("source_generated_at must be a non-empty ISO timestamp")
        return
    try:
        datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        errors.append("source_generated_at must be a valid ISO timestamp")


def proof_checklist_from_groups(groups: list[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        scope = group.get("resolution_scope")
        for item in group.get("items") or []:
            if not isinstance(item, dict):
                continue
            proof = {
                "area": item.get("area"),
                "priority": item.get("priority"),
                "resolution_scope": scope,
                "resolution_mode": item.get("resolution_mode"),
                "proof_artifact": item.get("proof_artifact"),
                "proof_command": item.get("proof_command"),
                "gap_hash": item.get("gap_hash"),
            }
            proof["proof_item_hash"] = hash_payload(proof)
            items.append(proof)
    return sorted(items, key=lambda item: (str(item.get("priority") or ""), str(item.get("area") or "")))


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
