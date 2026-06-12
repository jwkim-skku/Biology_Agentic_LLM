from __future__ import annotations

import argparse
import json
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WRITE_RESULT_SCHEMA = "agentic-rag-cli-production-audit-write-result-v1"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify production_audit.py write-result JSON.")
    parser.add_argument("--path", type=Path, help="Path to write-result JSON. Reads stdin when omitted.")
    args = parser.parse_args()

    try:
        payload = load_payload(args.path)
    except Exception as exc:
        print(json.dumps({"status": "fail", "errors": [f"invalid JSON input: {exc}"]}, indent=2), file=sys.stderr)
        return 1

    errors = validate_write_result(payload, base_dir=args.path.parent if args.path else Path.cwd())
    status = "pass" if not errors else "fail"
    output = {
        "status": status,
        "result_schema": payload.get("result_schema") if isinstance(payload, dict) else None,
        "json_path": payload.get("json_path") if isinstance(payload, dict) else None,
        "markdown_path": payload.get("markdown_path") if isinstance(payload, dict) else None,
        "preflight_status": payload.get("preflight_status") if isinstance(payload, dict) else None,
        "errors": errors,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if not errors else 1


def load_payload(path: Path | None) -> Any:
    if path is None:
        return json.loads(sys.stdin.read())
    return json.loads(read_json_text(path))


def read_json_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8")


def validate_write_result(payload: Any, *, base_dir: Path | None = None) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["write result must be a JSON object"]

    if payload.get("result_schema") != WRITE_RESULT_SCHEMA:
        errors.append(f"result_schema must be {WRITE_RESULT_SCHEMA}")
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        errors.append("summary must be an object")
    elif summary.get("status") not in {"pass", "fail", "warning"}:
        errors.append("summary.status must be pass, warning, or fail")

    require_hex_hash(payload, "audit_hash", errors)
    validate_hashed_file(payload, "json_path", "json_sha256", base_dir=base_dir, errors=errors)
    validate_hashed_file(payload, "markdown_path", "markdown_sha256", base_dir=base_dir, errors=errors)
    validate_report_artifacts(payload, base_dir=base_dir, errors=errors)

    preflight_evidence = payload.get("preflight_evidence")
    if preflight_evidence is not None:
        evidence_path = resolve_path(preflight_evidence, base_dir=base_dir)
        if not evidence_path.exists():
            errors.append(f"preflight_evidence does not exist: {preflight_evidence}")
        if payload.get("preflight_status") != "pass":
            errors.append("preflight_status must be pass when preflight_evidence is present")
        if payload.get("preflight_failure_count") != 0:
            errors.append("preflight_failure_count must be 0 when preflight_evidence is present")
        require_hex_hash(payload, "preflight_hash", errors)
        require_hex_hash(payload, "preflight_checks_hash", errors)

    return errors


def validate_report_artifacts(payload: dict[str, Any], *, base_dir: Path | None, errors: list[str]) -> None:
    json_path_value = payload.get("json_path")
    markdown_path_value = payload.get("markdown_path")
    if not isinstance(json_path_value, str) or not isinstance(markdown_path_value, str):
        return

    json_path = resolve_path(json_path_value, base_dir=base_dir)
    markdown_path = resolve_path(markdown_path_value, base_dir=base_dir)
    if not json_path.exists() or not markdown_path.exists():
        return

    try:
        report = json.loads(read_json_text(json_path))
    except json.JSONDecodeError as exc:
        errors.append(f"json_path is not valid audit JSON: {exc}")
        return
    if not isinstance(report, dict):
        errors.append("json_path audit report must be a JSON object")
        return

    report_hash = report.get("audit_hash")
    if report_hash != payload.get("audit_hash"):
        errors.append("audit_hash does not match json_path audit_hash")
    expected_hash = audit_hash(report)
    if report_hash != expected_hash:
        errors.append("json_path audit_hash does not match recomputed audit report hash")
    if payload.get("summary") != report.get("summary"):
        errors.append("summary does not match json_path summary")

    markdown = markdown_path.read_text(encoding="utf-8")
    if "# Production Audit Report" not in markdown:
        errors.append("markdown_path does not look like a production audit markdown report")
    if report_hash and str(report_hash) not in markdown:
        errors.append("markdown_path does not contain the audit_hash from json_path")
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    status = summary.get("status")
    if status and f"- Status: `{status}`" not in markdown:
        errors.append("markdown_path status line does not match json_path summary.status")


def validate_hashed_file(payload: dict[str, Any], path_key: str, hash_key: str, *, base_dir: Path | None, errors: list[str]) -> None:
    raw_path = payload.get(path_key)
    if not isinstance(raw_path, str) or not raw_path:
        errors.append(f"{path_key} must be a non-empty string")
        return
    path = resolve_path(raw_path, base_dir=base_dir)
    if not path.exists():
        errors.append(f"{path_key} does not exist: {raw_path}")
        return
    expected = payload.get(hash_key)
    if not isinstance(expected, str) or len(expected) != 64:
        errors.append(f"{hash_key} must be a 64-character SHA-256 hex digest")
        return
    actual = file_sha256(path)
    if actual != expected:
        errors.append(f"{hash_key} mismatch for {path_key}: expected {expected}, got {actual}")


def resolve_path(value: Any, *, base_dir: Path | None) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path
    candidates = []
    if base_dir is not None:
        candidates.append((base_dir / path).resolve())
    candidates.append((ROOT / path).resolve())
    candidates.append((Path.cwd() / path).resolve())
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def require_hex_hash(payload: dict[str, Any], key: str, errors: list[str]) -> None:
    value = payload.get(key)
    if not isinstance(value, str) or len(value) != 64:
        errors.append(f"{key} must be a 64-character SHA-256 hex digest")
        return
    try:
        int(value, 16)
    except ValueError:
        errors.append(f"{key} must be hexadecimal")


def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def audit_hash(report: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in report.items() if key != "audit_hash"}
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return sha256(canonical).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
