from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any


SUMMARY_SCHEMA = "agentic-rag-portfolio-submission-summary-v1"


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a portfolio submission summary from readiness evidence.")
    parser.add_argument("--matrix", type=Path, required=True, help="Path to portfolio_readiness_matrix.json.")
    parser.add_argument("--ci-evidence", type=Path, required=True, help="Path to ci_production_evidence_chain.json.")
    parser.add_argument("--output-json", type=Path, help="Optional path to write the structured summary JSON.")
    parser.add_argument("--output-md", type=Path, help="Optional path to write the Markdown summary.")
    args = parser.parse_args()

    matrix = read_json(args.matrix)
    ci_evidence = read_json(args.ci_evidence)
    summary = build_summary(matrix, ci_evidence)
    markdown = render_markdown(summary)
    if args.output_json:
        write_text(args.output_json, json.dumps(summary, indent=2, sort_keys=True))
    if args.output_md:
        write_text(args.output_md, markdown)
    if not args.output_json and not args.output_md:
        print(markdown)
    return 0 if summary["status"] == "pass" else 1


def build_summary(matrix: dict[str, Any], ci_evidence: dict[str, Any]) -> dict[str, Any]:
    requirements = matrix.get("requirements") if isinstance(matrix.get("requirements"), list) else []
    ci_checks = ci_evidence.get("checks") if isinstance(ci_evidence.get("checks"), list) else []
    requirement_rows = [
        {
            "id": str(requirement.get("id")),
            "title": str(requirement.get("title")),
            "status": requirement.get("status"),
            "evidence_count": safe_int(requirement.get("evidence_count")),
            "passing_evidence_count": safe_int(requirement.get("passing_evidence_count")),
            "evidence_hash": requirement.get("evidence_hash"),
        }
        for requirement in requirements
        if isinstance(requirement, dict)
    ]
    ci_rows = [
        {
            "name": str(check.get("name")),
            "status": check.get("status"),
            "token_count": safe_int(check.get("token_count")),
            "tokens_hash": check.get("tokens_hash"),
        }
        for check in ci_checks
        if isinstance(check, dict)
    ]
    status = "pass" if matrix.get("summary", {}).get("status") == "pass" and ci_evidence.get("status") == "pass" else "fail"
    payload = {
        "schema": SUMMARY_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "readiness_statement": "All portfolio readiness requirements and CI production evidence-chain checks passed."
        if status == "pass"
        else "One or more portfolio readiness or CI evidence-chain checks failed.",
        "matrix_hash": matrix.get("matrix_hash"),
        "matrix_status": (matrix.get("summary") or {}).get("status") if isinstance(matrix.get("summary"), dict) else None,
        "matrix_requirement_count": len(requirement_rows),
        "ci_evidence_hash": ci_evidence.get("evidence_hash"),
        "ci_status": ci_evidence.get("status"),
        "ci_check_count": len(ci_rows),
        "requirements": requirement_rows,
        "ci_checks": ci_rows,
        "artifact_references": [
            "backend-portfolio-readiness-matrix",
            "backend-ci-production-evidence-chain",
            "backend-portfolio-submission-summary",
            "backend-preflight-evidence",
            "backend-production-audit",
            "backend-production-audit-write-result",
            "backend-production-promotion-runbook",
        ],
    }
    payload["requirements_hash"] = hash_payload(requirement_rows)
    payload["ci_checks_hash"] = hash_payload(ci_rows)
    payload["summary_hash"] = hash_payload({key: value for key, value in payload.items() if key != "summary_hash"})
    return payload


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Agentic RAG Codon Optimization Portfolio Summary",
        "",
        f"- Status: `{summary.get('status')}`",
        f"- Readiness: {summary.get('readiness_statement')}",
        f"- Matrix hash: `{summary.get('matrix_hash')}`",
        f"- CI evidence hash: `{summary.get('ci_evidence_hash')}`",
        f"- Summary hash: `{summary.get('summary_hash')}`",
        "",
        "## Requirement Coverage",
        "",
        "| Requirement | Status | Evidence | Evidence hash |",
        "| --- | --- | ---: | --- |",
    ]
    for requirement in summary.get("requirements") or []:
        lines.append(
            "| {title} | `{status}` | {passing}/{total} | `{hash}` |".format(
                title=escape_cell(requirement.get("title")),
                status=requirement.get("status"),
                passing=requirement.get("passing_evidence_count"),
                total=requirement.get("evidence_count"),
                hash=str(requirement.get("evidence_hash") or "")[:12],
            )
        )
    lines.extend(
        [
            "",
            "## CI Evidence Chain",
            "",
            "| Check | Status | Tokens | Tokens hash |",
            "| --- | --- | ---: | --- |",
        ]
    )
    for check in summary.get("ci_checks") or []:
        lines.append(
            "| {name} | `{status}` | {tokens} | `{hash}` |".format(
                name=escape_cell(check.get("name")),
                status=check.get("status"),
                tokens=check.get("token_count"),
                hash=str(check.get("tokens_hash") or "")[:12],
            )
        )
    lines.extend(["", "## CI Artifacts", ""])
    for artifact in summary.get("artifact_references") or []:
        lines.append(f"- `{artifact}`")
    return "\n".join(lines) + "\n"


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


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def escape_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|")


def hash_payload(payload: Any) -> str:
    return sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
