from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MATRIX_SCHEMA = "agentic-rag-portfolio-readiness-matrix-v1"


REQUIREMENTS: list[dict[str, Any]] = [
    {
        "id": "real_data_ingestion",
        "title": "Real data ingestion and provenance",
        "evidence": [
            {"path": "backend/app/services/data_refresh_service.py", "contains": ["GTEx", "Allen", "refresh_reference_data"]},
            {"path": "backend/app/services/structured_data_service.py", "contains": ["structured_coverage_matrix", "source_payload_sha256"]},
            {"path": "backend/app/services/data_release_bundle_service.py", "contains": ["record_source_summary", "release_handoff_hash", "release_lock_current_hash"]},
            {"path": "docs/DATA_IMPORT.md", "contains": ["GTEx", "Allen", "coverage matrix"]},
            {"glob": "backend/app/data/structured/*.json", "min_count": 1},
        ],
    },
    {
        "id": "agentic_rag_search",
        "title": "Agentic RAG search, evaluation, and retrieval evidence",
        "evidence": [
            {"path": "backend/app/services/rag_service.py", "contains": ["retrieval_trace", "facet_gap_analysis", "query_term_coverage"]},
            {"path": "backend/app/services/rag_evaluation_bundle_service.py", "contains": ["source_provenance", "facet_gap_analysis_hash"]},
            {"path": "backend/app/services/rag_regression_service.py", "contains": ["rag_regression_cases", "evaluate_rag_regression"]},
            {
                "path": "backend/app/services/rag_vector_index_bundle_service.py",
                "contains": ["vector_store_import_plan", "vector_row_hash", "migration_target_backend_consistency"],
            },
            {"path": "frontend/scripts/frontend_smoke.mjs", "contains": ["Facet gaps", "RAG"]},
        ],
    },
    {
        "id": "multi_objective_optimizer",
        "title": "Multi-objective codon optimization and diagnostics",
        "evidence": [
            {"path": "backend/app/optimizer/nsga2.py", "contains": ["OptimizationConfig", "_fast_non_dominated_sort", "_crowding_distance"]},
            {"path": "backend/app/optimizer/scoring.py", "contains": ["codon_pair", "secondary_structure"]},
            {"path": "backend/app/services/optimizer_benchmark_bundle_service.py", "contains": ["recommendation_summary", "candidate_diagnostics"]},
            {"path": "backend/app/services/rna_folding_service.py", "contains": ["RNAfold", "production_ready"]},
            {"path": "backend/app/services/design_service.py", "contains": ["candidate_folding_audit", "thermodynamic_risk_score"]},
            {"path": "backend/tests/test_optimizer.py", "contains": ["optimizer_benchmark", "recommendation_readiness"]},
        ],
    },
    {
        "id": "qc_export_artifacts",
        "title": "QC reports, candidate evidence, and export bundles",
        "evidence": [
            {"path": "backend/app/services/report_service.py", "contains": ["recommendation_readiness", "candidate_ranking", "PDF"]},
            {
                "path": "backend/app/services/qc_report_bundle_service.py",
                "contains": ["recommendation_readiness_hash", "candidate_folding_audit", "recommendation_linkage_hash"],
            },
            {"path": "backend/app/services/export_manifest_service.py", "contains": ["artifact_manifest", "sha256"]},
            {"path": "backend/tests/test_optimizer.py", "contains": ["qc_report.pdf", "candidate_ranking.csv"]},
        ],
    },
    {
        "id": "operator_ui",
        "title": "Operator dashboard and smoke-tested UI",
        "evidence": [
            {"path": "frontend/app/page.tsx", "contains": ["productionAudit", "ragInspection", "candidate"]},
            {"path": "frontend/app/globals.css", "contains": ["audit", "candidate"]},
            {"path": "frontend/scripts/frontend_smoke.mjs", "contains": ["Production audit", "Facet gaps", "Gap hash"]},
            {"path": ".github/workflows/ci.yml", "contains": ["ui-smoke", "npm run smoke:ui"]},
        ],
    },
    {
        "id": "operations_and_deployment",
        "title": "Production operations, storage, security, and deployment",
        "evidence": [
            {"path": "backend/app/services/deployment_readiness_service.py", "contains": ["deployment_ready", "required_actions_hash"]},
            {"path": "backend/app/services/storage_service.py", "contains": ["postgres_schema", "migration_readiness"]},
            {"path": "backend/app/services/artifact_archive_service.py", "contains": ["artifact_ledger", "verify_artifact_ledger"]},
            {"path": "backend/app/services/artifact_object_store_service.py", "contains": ["candidate_hash", "plan_hash", "mirror_result_hash"]},
            {"path": "backend/app/services/governance_service.py", "contains": ["attestation", "signature"]},
            {"path": "docker-compose.production.yml", "contains": ["postgres", "frontend"]},
            {"path": ".env.production.example", "contains": ["STORAGE_BACKEND=postgres", "API_KEYS", "ARTIFACT_SIGNING_KEY"]},
        ],
    },
    {
        "id": "verification_and_audit",
        "title": "Automated tests, preflight, production audit, and CI evidence",
        "evidence": [
            {"path": "scripts/preflight.py", "contains": ["preflight_hash", "frontend_smoke"]},
            {"path": "scripts/compose_preflight.py", "contains": ["static_evidence_hash", "synthetic_env_keys_hash", "required_production_tokens_hash"]},
            {
                "path": "scripts/production_audit.py",
                "contains": ["audit_hash", "preflight_evidence", "api_consistency", "archive_freshness_failures", "proof_checklist_hash"],
            },
            {
                "path": "backend/app/services/production_audit_service.py",
                "contains": ["production_gap_proof_checklist_hash", "production_gap_proof_checklist_consistency"],
            },
            {
                "path": "scripts/production_promotion_runbook.py",
                "contains": ["production_gap_summary", "runbook_hash", "proof_checklist_hash", "proof_item_hash"],
            },
            {
                "path": "scripts/verify_production_promotion_runbook.py",
                "contains": [
                    "RUNBOOK_SCHEMA",
                    "runbook_hash",
                    "proof_item_hash",
                    "proof_checklist_from_groups",
                    "validate_source_metadata",
                    "markdown_failures",
                ],
            },
            {
                "path": "scripts/verify_production_audit_write_result.py",
                "contains": ["WRITE_RESULT_SCHEMA", "json_sha256", "audit_hash", "summarize_report_checks"],
            },
            {"path": ".github/workflows/ci.yml", "contains": ["production_audit_write_result", "backend-production-promotion-runbook", "--markdown-path"]},
            {"path": "backend/tests/test_optimizer.py", "contains": ["production_audit", "rag_vector_index", "qc_report"]},
            {"path": "docs/DEPLOYMENT.md", "contains": ["Preflight verification", "Production audit bundle"]},
        ],
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a portfolio readiness matrix for the Agentic RAG platform.")
    parser.add_argument("--strict", action="store_true", help="Exit nonzero if any requirement is not fully evidenced.")
    args = parser.parse_args()

    matrix = build_matrix(ROOT)
    print(json.dumps(matrix, indent=2, sort_keys=True))
    if args.strict and matrix["summary"]["status"] != "pass":
        return 1
    return 0


def build_matrix(root: Path = ROOT) -> dict[str, Any]:
    requirements = [evaluate_requirement(root, requirement) for requirement in REQUIREMENTS]
    status_counts = {
        "pass": sum(1 for item in requirements if item["status"] == "pass"),
        "fail": sum(1 for item in requirements if item["status"] == "fail"),
        "warning": sum(1 for item in requirements if item["status"] == "warning"),
    }
    payload = {
        "schema": MATRIX_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "summary": {
            "status": "fail" if status_counts["fail"] else "warning" if status_counts["warning"] else "pass",
            "requirement_count": len(requirements),
            "pass_count": status_counts["pass"],
            "warning_count": status_counts["warning"],
            "fail_count": status_counts["fail"],
        },
        "requirements": requirements,
    }
    payload["matrix_hash"] = hash_payload({key: value for key, value in payload.items() if key != "matrix_hash"})
    return payload


def evaluate_requirement(root: Path, requirement: dict[str, Any]) -> dict[str, Any]:
    evidence = [evaluate_evidence(root, item) for item in requirement["evidence"]]
    missing = [item for item in evidence if item["status"] == "fail"]
    status = "fail" if missing else "pass"
    payload = {
        "id": requirement["id"],
        "title": requirement["title"],
        "status": status,
        "evidence_count": len(evidence),
        "passing_evidence_count": sum(1 for item in evidence if item["status"] == "pass"),
        "evidence": evidence,
    }
    payload["evidence_hash"] = hash_payload(evidence)
    return payload


def evaluate_evidence(root: Path, spec: dict[str, Any]) -> dict[str, Any]:
    if "glob" in spec:
        matches = sorted(root.glob(str(spec["glob"])))
        min_count = int(spec.get("min_count", 1))
        return {
            "kind": "glob",
            "pattern": spec["glob"],
            "status": "pass" if len(matches) >= min_count else "fail",
            "count": len(matches),
            "min_count": min_count,
            "sample": [str(path.relative_to(root)) for path in matches[:5]],
        }

    path = root / str(spec["path"])
    required_tokens = [str(item) for item in spec.get("contains", [])]
    result = {
        "kind": "file",
        "path": spec["path"],
        "exists": path.exists(),
        "required_tokens": required_tokens,
        "missing_tokens": [],
        "sha256": None,
        "status": "fail",
    }
    if not path.exists():
        result["missing_tokens"] = required_tokens
        return result

    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="ignore")
    missing_tokens = [token for token in required_tokens if token not in text]
    result.update(
        {
            "missing_tokens": missing_tokens,
            "sha256": sha256(raw).hexdigest(),
            "status": "fail" if missing_tokens else "pass",
        }
    )
    return result


def hash_payload(payload: Any) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
