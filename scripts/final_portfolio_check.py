from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import portfolio_readiness_matrix
import portfolio_submission_summary
import preflight
import production_audit
import verify_ci_production_evidence_chain
import verify_portfolio_readiness_matrix
import verify_portfolio_submission_summary


ROOT = Path(__file__).resolve().parents[1]
CHECK_SCHEMA = "agentic-rag-final-portfolio-check-v1"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the final portfolio evidence generation and verification chain.")
    parser.add_argument("--workflow", type=Path, default=ROOT / ".github" / "workflows" / "ci.yml")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "backend" / "app" / "data" / "runtime")
    parser.add_argument("--output-json", type=Path, help="Optional path to write final check JSON.")
    args = parser.parse_args()

    result = run_final_check(workflow_path=args.workflow, output_dir=args.output_dir)
    if args.output_json:
        write_json(args.output_json, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


def run_final_check(*, workflow_path: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    matrix_path = output_dir / "portfolio_readiness_matrix.json"
    ci_path = output_dir / "ci_production_evidence_chain.json"
    summary_json_path = output_dir / "portfolio_submission_summary.json"
    summary_md_path = output_dir / "portfolio_submission_summary.md"
    required_check_alignment_path = output_dir / "preflight_required_check_alignment.json"

    matrix = portfolio_readiness_matrix.build_matrix(ROOT)
    write_json(matrix_path, matrix)
    matrix_errors = verify_portfolio_readiness_matrix.validate_matrix(matrix)

    ci_evidence = verify_ci_production_evidence_chain.build_evidence(workflow_path)
    verify_ci_production_evidence_chain.write_evidence(ci_path, ci_evidence)

    summary = portfolio_submission_summary.build_summary(matrix, ci_evidence)
    markdown = portfolio_submission_summary.render_markdown(summary)
    portfolio_submission_summary.write_text(summary_json_path, json.dumps(summary, indent=2, sort_keys=True))
    portfolio_submission_summary.write_text(summary_md_path, markdown)
    summary_errors = verify_portfolio_submission_summary.validate_summary(
        summary,
        markdown=markdown,
        matrix=matrix,
        ci_evidence=ci_evidence,
    )
    required_check_alignment = build_required_check_alignment()
    write_json(required_check_alignment_path, required_check_alignment)

    checks = [
        {
            "name": "portfolio_readiness_matrix",
            "status": "pass" if not matrix_errors else "fail",
            "errors": matrix_errors,
            "artifact": str(matrix_path),
            "artifact_sha256": file_sha256(matrix_path),
            "payload_hash": matrix.get("matrix_hash"),
        },
        {
            "name": "ci_production_evidence_chain",
            "status": ci_evidence.get("status"),
            "errors": ci_evidence.get("errors") or [],
            "artifact": str(ci_path),
            "artifact_sha256": file_sha256(ci_path),
            "payload_hash": ci_evidence.get("evidence_hash"),
        },
        {
            "name": "portfolio_submission_summary",
            "status": "pass" if not summary_errors else "fail",
            "errors": summary_errors,
            "artifact": str(summary_json_path),
            "markdown_artifact": str(summary_md_path),
            "artifact_sha256": file_sha256(summary_json_path),
            "markdown_sha256": file_sha256(summary_md_path),
            "payload_hash": summary.get("summary_hash"),
        },
        {
            "name": "preflight_required_check_alignment",
            "status": required_check_alignment["status"],
            "errors": required_check_alignment["errors"],
            "artifact": str(required_check_alignment_path),
            "artifact_sha256": file_sha256(required_check_alignment_path),
            "payload_hash": required_check_alignment["alignment_hash"],
        },
    ]
    failures = [check for check in checks if check["status"] != "pass" or check["errors"]]
    payload = {
        "schema": CHECK_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "fail" if failures else "pass",
        "root": str(ROOT),
        "workflow": str(workflow_path),
        "output_dir": str(output_dir),
        "check_count": len(checks),
        "checks": checks,
        "failures": [check["name"] for check in failures],
    }
    payload["checks_hash"] = hash_payload(checks)
    payload["final_check_hash"] = hash_payload({key: value for key, value in payload.items() if key != "final_check_hash"})
    return payload


def build_required_check_alignment() -> dict[str, Any]:
    preflight_checks = list(preflight.REQUIRED_CHECKS)
    audit_checks = list(production_audit.REQUIRED_PREFLIGHT_CHECKS)
    errors = []
    if preflight_checks != audit_checks:
        errors.append("preflight.REQUIRED_CHECKS does not match production_audit.REQUIRED_PREFLIGHT_CHECKS")
    for required in ("final_portfolio_check", "verify_final_portfolio_check"):
        if required not in preflight_checks:
            errors.append(f"preflight required checks missing {required}")
        if required not in audit_checks:
            errors.append(f"production audit required checks missing {required}")
    payload = {
        "schema": "agentic-rag-preflight-required-check-alignment-v1",
        "status": "pass" if not errors else "fail",
        "preflight_required_checks": preflight_checks,
        "production_audit_required_checks": audit_checks,
        "required_check_count": len(preflight_checks),
        "errors": errors,
    }
    payload["alignment_hash"] = hash_payload({key: value for key, value in payload.items() if key != "alignment_hash"})
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def hash_payload(payload: Any) -> str:
    return sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
