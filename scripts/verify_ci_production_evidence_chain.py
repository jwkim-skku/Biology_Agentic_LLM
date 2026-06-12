from __future__ import annotations

import argparse
import json
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
EVIDENCE_CHAIN_SCHEMA = "agentic-rag-ci-production-evidence-chain-v1"

REQUIRED_TOKENS: dict[str, list[str]] = {
    "portfolio_readiness_matrix": [
        "python ../scripts/portfolio_readiness_matrix.py --strict > app/data/runtime/portfolio_readiness_matrix.json",
        "python ../scripts/verify_portfolio_readiness_matrix.py --path app/data/runtime/portfolio_readiness_matrix.json",
        "name: backend-portfolio-readiness-matrix",
        "backend/app/data/runtime/portfolio_readiness_matrix.json",
    ],
    "portfolio_submission_summary": [
        "python ../scripts/portfolio_submission_summary.py --matrix app/data/runtime/portfolio_readiness_matrix.json --ci-evidence app/data/runtime/ci_production_evidence_chain.json --output-json app/data/runtime/portfolio_submission_summary.json --output-md app/data/runtime/portfolio_submission_summary.md",
        "python ../scripts/verify_portfolio_submission_summary.py --path app/data/runtime/portfolio_submission_summary.json --markdown-path app/data/runtime/portfolio_submission_summary.md --matrix app/data/runtime/portfolio_readiness_matrix.json --ci-evidence app/data/runtime/ci_production_evidence_chain.json",
        "name: backend-portfolio-submission-summary",
        "backend/app/data/runtime/portfolio_submission_summary.json",
        "backend/app/data/runtime/portfolio_submission_summary.md",
    ],
    "preflight_evidence": [
        "python ../scripts/preflight.py",
        "--output-json backend/app/data/runtime/preflight_ci.json",
        "name: backend-preflight-evidence",
        "backend/app/data/runtime/preflight_ci.json",
    ],
    "production_audit_write_result": [
        "python ../scripts/production_audit.py --template --skip-api --preflight-evidence backend/app/data/runtime/preflight_ci.json",
        "python ../scripts/verify_production_audit_write_result.py --path app/data/runtime/production_audit_write_result.json",
        "name: backend-production-audit-write-result",
        "backend/app/data/runtime/production_audit_write_result.json",
    ],
    "production_promotion_runbook": [
        "python ../scripts/production_promotion_runbook.py --write-result app/data/runtime/production_audit_write_result.json --output app/data/runtime/production_promotion_runbook.md",
        "python ../scripts/production_promotion_runbook.py --write-result app/data/runtime/production_audit_write_result.json --format json --output app/data/runtime/production_promotion_runbook.json",
        "python ../scripts/verify_production_promotion_runbook.py --path app/data/runtime/production_promotion_runbook.json --markdown-path app/data/runtime/production_promotion_runbook.md",
        "name: backend-production-promotion-runbook",
        "backend/app/data/runtime/production_promotion_runbook.md",
        "backend/app/data/runtime/production_promotion_runbook.json",
    ],
    "production_audit_bundle": [
        "name: backend-production-audit",
        "backend/app/data/runtime/production_audits/*",
    ],
    "ci_production_evidence_chain": [
        "python ../scripts/verify_ci_production_evidence_chain.py --workflow ../.github/workflows/ci.yml --output app/data/runtime/ci_production_evidence_chain.json",
        "name: backend-ci-production-evidence-chain",
        "backend/app/data/runtime/ci_production_evidence_chain.json",
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify CI preserves production evidence artifacts.")
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW, help="Path to .github/workflows/ci.yml.")
    parser.add_argument("--output", type=Path, help="Optional path to write the evidence-chain JSON.")
    args = parser.parse_args()

    try:
        evidence = build_evidence(args.workflow)
        if args.output:
            write_evidence(args.output, evidence)
    except Exception as exc:
        print(json.dumps({"status": "fail", "errors": [f"invalid workflow input: {exc}"]}, indent=2), file=sys.stderr)
        return 1

    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0 if evidence["status"] == "pass" else 1


def build_evidence(workflow_path: Path = DEFAULT_WORKFLOW) -> dict[str, Any]:
    text = workflow_path.read_text(encoding="utf-8")
    checks = [check_token_group(name, tokens, text) for name, tokens in REQUIRED_TOKENS.items()]
    errors = [error for check in checks for error in check["missing_tokens"]]
    payload = {
        "schema": EVIDENCE_CHAIN_SCHEMA,
        "workflow": str(workflow_path),
        "workflow_sha256": sha256(text.encode("utf-8")).hexdigest(),
        "status": "pass" if not errors else "fail",
        "check_count": len(checks),
        "checks": checks,
        "errors": errors,
    }
    payload["checks_hash"] = hash_payload(checks)
    payload["evidence_hash"] = hash_payload({key: value for key, value in payload.items() if key != "evidence_hash"})
    return payload


def write_evidence(path: Path, evidence: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")


def check_token_group(name: str, tokens: list[str], workflow_text: str) -> dict[str, Any]:
    missing = [token for token in tokens if token not in workflow_text]
    return {
        "name": name,
        "status": "pass" if not missing else "fail",
        "token_count": len(tokens),
        "missing_tokens": missing,
        "tokens_hash": hash_payload(tokens),
    }


def hash_payload(payload: Any) -> str:
    return sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
