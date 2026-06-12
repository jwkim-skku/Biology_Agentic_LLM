from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any


CHECK_SCHEMA = "agentic-rag-final-portfolio-check-v1"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify final_portfolio_check.json artifact integrity.")
    parser.add_argument("--path", type=Path, required=True, help="Path to final_portfolio_check.json.")
    args = parser.parse_args()

    try:
        payload = read_json(args.path)
    except Exception as exc:
        print(json.dumps({"status": "fail", "errors": [f"invalid final check input: {exc}"]}, indent=2), file=sys.stderr)
        return 1

    errors = validate_final_check(payload, base_dir=args.path.parent)
    print(
        json.dumps(
            {
                "status": "pass" if not errors else "fail",
                "schema": payload.get("schema") if isinstance(payload, dict) else None,
                "final_check_hash": payload.get("final_check_hash") if isinstance(payload, dict) else None,
                "errors": errors,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not errors else 1


def validate_final_check(payload: Any, *, base_dir: Path | None = None) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["final check must be a JSON object"]
    if payload.get("schema") != CHECK_SCHEMA:
        errors.append(f"schema must be {CHECK_SCHEMA}")
    if payload.get("status") not in {"pass", "fail"}:
        errors.append("status must be pass or fail")
    validate_timestamp(payload.get("generated_at"), errors)

    checks = payload.get("checks") if isinstance(payload.get("checks"), list) else []
    if not isinstance(payload.get("checks"), list):
        errors.append("checks must be a list")
    if payload.get("check_count") != len(checks):
        errors.append("check_count does not match checks")
    if payload.get("checks_hash") != hash_payload(checks):
        errors.append("checks_hash does not match checks")

    expected_failures = [str(check.get("name")) for check in checks if isinstance(check, dict) and (check.get("status") != "pass" or check.get("errors"))]
    if payload.get("failures") != expected_failures:
        errors.append("failures do not match non-pass checks")
    if payload.get("status") != ("fail" if expected_failures else "pass"):
        errors.append("status does not match failures")

    seen_names: set[str] = set()
    for check in checks:
        if not isinstance(check, dict):
            errors.append("checks contains a non-object item")
            continue
        name = str(check.get("name") or "")
        if not name:
            errors.append("check name must be non-empty")
        if name in seen_names:
            errors.append(f"duplicate check name: {name}")
        seen_names.add(name)
        if check.get("status") not in {"pass", "fail"}:
            errors.append(f"check {name!r} status must be pass or fail")
        if not isinstance(check.get("errors"), list):
            errors.append(f"check {name!r} errors must be a list")
        validate_artifact_hash(check, "artifact", "artifact_sha256", base_dir=base_dir, errors=errors)
        if check.get("markdown_artifact") is not None:
            validate_artifact_hash(check, "markdown_artifact", "markdown_sha256", base_dir=base_dir, errors=errors)
        require_hash(check, "payload_hash", errors)

    for expected in (
        "portfolio_readiness_matrix",
        "ci_production_evidence_chain",
        "portfolio_submission_summary",
        "preflight_required_check_alignment",
    ):
        if expected not in seen_names:
            errors.append(f"missing required check {expected}")

    expected_final_hash = hash_payload({key: value for key, value in payload.items() if key != "final_check_hash"})
    if payload.get("final_check_hash") != expected_final_hash:
        errors.append("final_check_hash does not match final check payload")
    return errors


def validate_artifact_hash(
    check: dict[str, Any],
    path_key: str,
    hash_key: str,
    *,
    base_dir: Path | None,
    errors: list[str],
) -> None:
    raw_path = check.get(path_key)
    if not isinstance(raw_path, str) or not raw_path:
        errors.append(f"check {check.get('name', 'unknown')!r} {path_key} must be a non-empty string")
        return
    path = resolve_path(raw_path, base_dir=base_dir)
    if not path.exists():
        errors.append(f"check {check.get('name', 'unknown')!r} {path_key} does not exist: {raw_path}")
        return
    expected = check.get(hash_key)
    if not isinstance(expected, str) or len(expected) != 64:
        errors.append(f"check {check.get('name', 'unknown')!r} {hash_key} must be a 64-character SHA-256 hex digest")
        return
    actual = sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        errors.append(f"check {check.get('name', 'unknown')!r} {hash_key} does not match artifact")


def resolve_path(value: str, *, base_dir: Path | None) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    candidates = []
    if base_dir is not None:
        candidates.append((base_dir / path).resolve())
    candidates.append((Path.cwd() / path).resolve())
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def read_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            payload = json.loads(raw.decode(encoding))
            break
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    else:
        payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def validate_timestamp(value: Any, errors: list[str]) -> None:
    if not isinstance(value, str) or not value:
        errors.append("generated_at must be a non-empty ISO timestamp")
        return
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append("generated_at must be a valid ISO timestamp")


def require_hash(payload: dict[str, Any], key: str, errors: list[str]) -> None:
    value = payload.get(key)
    if not isinstance(value, str) or len(value) != 64:
        errors.append(f"{key} must be a 64-character SHA-256 hex digest")
        return
    try:
        int(value, 16)
    except ValueError:
        errors.append(f"{key} must be hexadecimal")


def hash_payload(payload: Any) -> str:
    return sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
