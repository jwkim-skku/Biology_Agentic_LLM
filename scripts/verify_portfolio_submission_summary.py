from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any


SUMMARY_SCHEMA = "agentic-rag-portfolio-submission-summary-v1"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify portfolio submission summary JSON/Markdown artifacts.")
    parser.add_argument("--path", type=Path, required=True, help="Path to portfolio_submission_summary.json.")
    parser.add_argument("--markdown-path", type=Path, help="Optional path to portfolio_submission_summary.md.")
    parser.add_argument("--matrix", type=Path, help="Optional path to portfolio_readiness_matrix.json.")
    parser.add_argument("--ci-evidence", type=Path, help="Optional path to ci_production_evidence_chain.json.")
    args = parser.parse_args()

    try:
        payload = read_json(args.path)
        markdown = args.markdown_path.read_text(encoding="utf-8") if args.markdown_path else None
        matrix = read_json(args.matrix) if args.matrix else None
        ci_evidence = read_json(args.ci_evidence) if args.ci_evidence else None
    except Exception as exc:
        print(json.dumps({"status": "fail", "errors": [f"invalid summary input: {exc}"]}, indent=2), file=sys.stderr)
        return 1

    errors = validate_summary(payload, markdown=markdown, matrix=matrix, ci_evidence=ci_evidence)
    print(
        json.dumps(
            {
                "status": "pass" if not errors else "fail",
                "schema": payload.get("schema") if isinstance(payload, dict) else None,
                "summary_hash": payload.get("summary_hash") if isinstance(payload, dict) else None,
                "errors": errors,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not errors else 1


def validate_summary(
    payload: Any,
    *,
    markdown: str | None = None,
    matrix: dict[str, Any] | None = None,
    ci_evidence: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["summary must be a JSON object"]
    if payload.get("schema") != SUMMARY_SCHEMA:
        errors.append(f"schema must be {SUMMARY_SCHEMA}")
    if payload.get("status") not in {"pass", "fail"}:
        errors.append("status must be pass or fail")
    validate_timestamp(payload.get("generated_at"), errors)

    requirements = payload.get("requirements") if isinstance(payload.get("requirements"), list) else []
    ci_checks = payload.get("ci_checks") if isinstance(payload.get("ci_checks"), list) else []
    if not isinstance(payload.get("requirements"), list):
        errors.append("requirements must be a list")
    if not isinstance(payload.get("ci_checks"), list):
        errors.append("ci_checks must be a list")
    if payload.get("matrix_requirement_count") != len(requirements):
        errors.append("matrix_requirement_count does not match requirements")
    if payload.get("ci_check_count") != len(ci_checks):
        errors.append("ci_check_count does not match ci_checks")
    if payload.get("requirements_hash") != hash_payload(requirements):
        errors.append("requirements_hash does not match requirements")
    if payload.get("ci_checks_hash") != hash_payload(ci_checks):
        errors.append("ci_checks_hash does not match ci_checks")

    expected_status = "pass" if payload.get("matrix_status") == "pass" and payload.get("ci_status") == "pass" else "fail"
    if payload.get("status") != expected_status:
        errors.append("status does not match matrix_status and ci_status")
    if expected_status == "pass" and "All portfolio readiness requirements" not in str(payload.get("readiness_statement") or ""):
        errors.append("readiness_statement does not match pass status")

    for item in requirements:
        if not isinstance(item, dict):
            errors.append("requirements contains a non-object item")
            continue
        if item.get("status") not in {"pass", "fail", "warning"}:
            errors.append(f"requirement {item.get('id', 'unknown')!r} has invalid status")
        if safe_int(item.get("passing_evidence_count"), -1) > safe_int(item.get("evidence_count"), -1):
            errors.append(f"requirement {item.get('id', 'unknown')!r} passing_evidence_count exceeds evidence_count")
        require_hash(item, "evidence_hash", errors)
    for item in ci_checks:
        if not isinstance(item, dict):
            errors.append("ci_checks contains a non-object item")
            continue
        if item.get("status") not in {"pass", "fail"}:
            errors.append(f"ci check {item.get('name', 'unknown')!r} has invalid status")
        require_hash(item, "tokens_hash", errors)

    for artifact in (
        "backend-portfolio-readiness-matrix",
        "backend-ci-production-evidence-chain",
        "backend-portfolio-submission-summary",
        "backend-production-audit-write-result",
        "backend-production-promotion-runbook",
    ):
        if artifact not in (payload.get("artifact_references") or []):
            errors.append(f"artifact_references missing {artifact}")

    if matrix is not None:
        validate_matrix_linkage(payload, matrix, errors)
    if ci_evidence is not None:
        validate_ci_linkage(payload, ci_evidence, errors)
    if markdown is not None:
        errors.extend(markdown_failures(payload, markdown))

    expected_summary_hash = hash_payload({key: value for key, value in payload.items() if key != "summary_hash"})
    if payload.get("summary_hash") != expected_summary_hash:
        errors.append("summary_hash does not match summary payload")
    return errors


def validate_matrix_linkage(payload: dict[str, Any], matrix: dict[str, Any], errors: list[str]) -> None:
    if payload.get("matrix_hash") != matrix.get("matrix_hash"):
        errors.append("matrix_hash does not match matrix artifact")
    matrix_summary = matrix.get("summary") if isinstance(matrix.get("summary"), dict) else {}
    if payload.get("matrix_status") != matrix_summary.get("status"):
        errors.append("matrix_status does not match matrix artifact")
    matrix_requirements = matrix.get("requirements") if isinstance(matrix.get("requirements"), list) else []
    expected_rows = [
        {
            "id": str(item.get("id")),
            "title": str(item.get("title")),
            "status": item.get("status"),
            "evidence_count": safe_int(item.get("evidence_count")),
            "passing_evidence_count": safe_int(item.get("passing_evidence_count")),
            "evidence_hash": item.get("evidence_hash"),
        }
        for item in matrix_requirements
        if isinstance(item, dict)
    ]
    if payload.get("requirements") != expected_rows:
        errors.append("requirements do not match matrix artifact")


def validate_ci_linkage(payload: dict[str, Any], ci_evidence: dict[str, Any], errors: list[str]) -> None:
    if payload.get("ci_evidence_hash") != ci_evidence.get("evidence_hash"):
        errors.append("ci_evidence_hash does not match CI evidence artifact")
    if payload.get("ci_status") != ci_evidence.get("status"):
        errors.append("ci_status does not match CI evidence artifact")
    ci_checks = ci_evidence.get("checks") if isinstance(ci_evidence.get("checks"), list) else []
    expected_rows = [
        {
            "name": str(item.get("name")),
            "status": item.get("status"),
            "token_count": safe_int(item.get("token_count")),
            "tokens_hash": item.get("tokens_hash"),
        }
        for item in ci_checks
        if isinstance(item, dict)
    ]
    if payload.get("ci_checks") != expected_rows:
        errors.append("ci_checks do not match CI evidence artifact")


def markdown_failures(payload: dict[str, Any], markdown: str) -> list[str]:
    errors: list[str] = []
    required = [
        "# Agentic RAG Codon Optimization Portfolio Summary",
        f"- Status: `{payload.get('status')}`",
        f"- Matrix hash: `{payload.get('matrix_hash')}`",
        f"- CI evidence hash: `{payload.get('ci_evidence_hash')}`",
        f"- Summary hash: `{payload.get('summary_hash')}`",
        "## Requirement Coverage",
        "## CI Evidence Chain",
        "## CI Artifacts",
    ]
    for token in required:
        if token not in markdown:
            errors.append(f"markdown missing {token}")
    for requirement in payload.get("requirements") or []:
        if isinstance(requirement, dict) and str(requirement.get("title") or "") not in markdown:
            errors.append(f"markdown missing requirement {requirement.get('title')}")
    for check in payload.get("ci_checks") or []:
        if isinstance(check, dict) and str(check.get("name") or "") not in markdown:
            errors.append(f"markdown missing CI check {check.get('name')}")
    return errors


def read_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return json.loads(sys.stdin.read())
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


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def hash_payload(payload: Any) -> str:
    return sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
