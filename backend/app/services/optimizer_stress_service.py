from __future__ import annotations

from typing import Any

from app.services.optimizer_benchmark_service import evaluate_optimizer_benchmark


STRESS_SCHEMA = "agentic-rag-optimizer-stress-gate-v1"


def optimizer_stress_gate() -> dict[str, Any]:
    benchmark = evaluate_optimizer_benchmark()
    results = benchmark.get("results") or []
    cases = [_case_stress(result) for result in results]
    stress_checks = _stress_checks(cases)
    failed = [item for item in stress_checks if item["status"] == "fail"]
    warnings = [item for item in stress_checks if item["status"] == "warning"]
    return {
        "stress_schema": STRESS_SCHEMA,
        "status": "fail" if failed else "warning" if warnings else "pass",
        "benchmark_status": benchmark.get("status"),
        "case_count": len(cases),
        "cases_hash": benchmark.get("cases_hash"),
        "cases": cases,
        "stress_checks": stress_checks,
        "summary": {
            "pass_count": sum(1 for item in stress_checks if item["status"] == "pass"),
            "warning_count": len(warnings),
            "fail_count": len(failed),
        },
        "recommendations": _recommendations(stress_checks),
    }


def _case_stress(result: dict[str, Any]) -> dict[str, Any]:
    metrics = result.get("metrics") or {}
    checks = [
        _check(
            "protein_preservation",
            int(metrics.get("protein_preservation_failures") or 0) == 0,
            "error",
            f"{metrics.get('protein_preservation_failures', 0)} protein preservation failures",
        ),
        _check("aav_budget", float(metrics.get("recommended_aav_budget_pass") or 0) >= 1.0, "error", "recommended candidate must fit AAV payload budget"),
        _check("forbidden_motifs", float(metrics.get("recommended_motif_violations") or 0) == 0, "error", "recommended candidate has configured forbidden motif matches"),
        _check(
            "polyadenylation_signals",
            float(metrics.get("recommended_polyadenylation_signal_count") or 0) == 0,
            "warning",
            "recommended candidate has polyadenylation signal matches",
        ),
        _check(
            "restriction_sites",
            float(metrics.get("recommended_restriction_site_count") or 0) == 0,
            "warning",
            "recommended candidate has configured restriction-site matches",
        ),
        _check(
            "splice_proxy",
            float(metrics.get("recommended_splice_donor_motif_count") or 0) == 0
            and float(metrics.get("recommended_splice_acceptor_motif_count") or 0) == 0,
            "warning",
            "recommended candidate has splice donor/acceptor proxy motifs",
        ),
        _check(
            "secondary_structure_proxy",
            float(metrics.get("recommended_secondary_structure_proxy") or 0) <= 0.60,
            "warning",
            "recommended candidate has elevated deterministic secondary-structure proxy risk",
        ),
        _check(
            "low_complexity",
            float(metrics.get("recommended_low_complexity_penalty") or 0) <= 0.50,
            "warning",
            "recommended candidate has elevated low-complexity penalty",
        ),
        _check(
            "rare_codon_clusters",
            float(metrics.get("recommended_rare_codon_clusters") or 0) <= 1,
            "warning",
            "recommended candidate has multiple rare-codon clusters",
        ),
        _check(
            "candidate_diversity",
            float(metrics.get("unique_cds_count") or 0) >= 2,
            "warning",
            "case generated low synonymous candidate diversity",
        ),
        _check(
            "recommendation_regret",
            float(metrics.get("recommendation_max_regret") or 0) <= 0.20,
            "warning",
            "recommended candidate has high objective regret versus alternatives",
        ),
    ]
    failed = [item for item in checks if item["status"] == "fail"]
    warnings = [item for item in checks if item["status"] == "warning"]
    return {
        "case_id": result.get("case_id"),
        "status": "fail" if failed else "warning" if warnings else "pass",
        "checks": checks,
        "metrics": {
            key: metrics.get(key)
            for key in [
                "candidate_count",
                "unique_cds_count",
                "constraint_violation_rate",
                "recommended_aav_budget_pass",
                "recommended_motif_violations",
                "recommended_polyadenylation_signal_count",
                "recommended_restriction_site_count",
                "recommended_secondary_structure_proxy",
                "recommended_low_complexity_penalty",
                "recommended_rare_codon_clusters",
                "recommendation_max_regret",
            ]
        },
    }


def _stress_checks(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    check_ids = sorted({check["id"] for case in cases for check in case["checks"]})
    output = []
    for check_id in check_ids:
        matching = [check for case in cases for check in case["checks"] if check["id"] == check_id]
        fail_count = sum(1 for check in matching if check["status"] == "fail")
        warning_count = sum(1 for check in matching if check["status"] == "warning")
        output.append(
            {
                "id": check_id,
                "status": "fail" if fail_count else "warning" if warning_count else "pass",
                "case_count": len(matching),
                "fail_count": fail_count,
                "warning_count": warning_count,
            }
        )
    return output


def _check(check_id: str, passed: bool, severity: str, message: str) -> dict[str, Any]:
    if passed:
        return {"id": check_id, "status": "pass", "severity": severity, "message": "pass"}
    return {"id": check_id, "status": "fail" if severity == "error" else "warning", "severity": severity, "message": message}


def _recommendations(stress_checks: list[dict[str, Any]]) -> list[str]:
    recommendations: list[str] = []
    statuses = {item["id"]: item["status"] for item in stress_checks}
    if statuses.get("forbidden_motifs") == "fail":
        recommendations.append("Increase repair passes or tighten motif filters before accepting motif-heavy designs.")
    if statuses.get("aav_budget") == "fail":
        recommendations.append("Use stricter payload budgeting or reject candidates that exceed AAV cargo constraints.")
    if statuses.get("secondary_structure_proxy") in {"warning", "fail"}:
        recommendations.append("Confirm high-risk candidates with a pinned RNA folding backend before production release.")
    if statuses.get("candidate_diversity") in {"warning", "fail"}:
        recommendations.append("Increase population size, generations, or mutation rate for low-diversity cases.")
    if statuses.get("recommendation_regret") in {"warning", "fail"}:
        recommendations.append("Inspect recommendation tradeoffs before using the automatic recommended candidate.")
    if not recommendations:
        recommendations.append("Optimizer stress gates are clean under the current benchmark suite.")
    return recommendations
