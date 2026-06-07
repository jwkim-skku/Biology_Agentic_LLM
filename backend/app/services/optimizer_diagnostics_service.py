from __future__ import annotations

from typing import Any

from app.optimizer.nsga2 import OptimizationConfig
from app.optimizer.scoring import ScoreConfig
from app.services.optimizer_benchmark_service import evaluate_optimizer_benchmark, optimizer_benchmark_cases
from app.services.optimizer_stress_service import optimizer_stress_gate
from app.services.rna_folding_service import rna_folding_status


def optimizer_diagnostics() -> dict[str, Any]:
    benchmark = evaluate_optimizer_benchmark()
    cases = optimizer_benchmark_cases()
    stress = optimizer_stress_gate()
    folding = rna_folding_status()
    weak_cases = [
        {
            "case_id": result["case_id"],
            "status": result["status"],
            "message": (result.get("errors") or result.get("warnings") or ["pass"])[0],
            "metrics": _selected_case_metrics(result.get("metrics") or {}),
        }
        for result in benchmark.get("results", [])
        if result.get("status") != "pass"
    ]
    warnings = _warnings(benchmark)
    folding_warning = not folding.get("production_ready")
    return {
        "status": "fail"
        if benchmark.get("fail_count", 0) or stress.get("status") == "fail"
        else "warning"
        if warnings or folding_warning or benchmark.get("warning_count", 0) or stress.get("status") == "warning"
        else "pass",
        "diagnostics_schema": "agentic-rag-optimizer-diagnostics-v1",
        "optimizer": {
            "algorithm": "seeded_nsga2",
            "default_config": OptimizationConfig().to_dict(),
            "default_score_config": ScoreConfig().to_dict(),
            "objectives": [
                "maximize_cai",
                "maximize_tissue_codon_adaptation",
                "minimize_gc_penalty",
                "minimize_cpg_density",
                "minimize_motif_policy_risk",
                "minimize_rare_codon_clusters",
                "minimize_codon_pair_risk",
                "minimize_local_gc_deviation",
                "minimize_5prime_gc_deviation",
                "minimize_hairpin_proxy",
                "minimize_secondary_structure_proxy",
                "minimize_low_complexity",
            ],
        },
        "benchmark": {
            "status": benchmark.get("status"),
            "case_count": benchmark.get("case_count"),
            "pass_count": benchmark.get("pass_count"),
            "warning_count": benchmark.get("warning_count"),
            "fail_count": benchmark.get("fail_count"),
            "cases_hash": benchmark.get("cases_hash"),
            "macro": benchmark.get("macro"),
            "weak_cases": weak_cases,
        },
        "quality_bands": _quality_bands(benchmark.get("macro") or {}),
        "stress_gate": stress,
        "rna_folding": folding,
        "case_catalog": {
            "cases_path": cases.get("cases_path"),
            "case_count": cases.get("case_count"),
            "cases_hash": cases.get("cases_hash"),
        },
        "warnings": warnings,
        "recommendations": _recommendations(benchmark, warnings, stress, folding),
    }


def _selected_case_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "candidate_count",
        "feasible_count",
        "unique_cds_count",
        "constraint_violation_rate",
        "recommended_composite_delta",
        "recommended_secondary_structure_proxy",
        "recommended_mfe_proxy_delta_g",
        "recommended_policy_violation_delta",
        "recommendation_best_metric_count",
        "recommendation_tradeoff_count",
        "recommendation_max_regret",
        "approx_hypervolume_2d",
        "runtime_ms",
    ]
    return {key: metrics.get(key) for key in keys}


def _quality_bands(macro: dict[str, Any]) -> dict[str, str]:
    return {
        "candidate_diversity": _band(float(macro.get("unique_cds_count") or 0), good=3.0, warning=2.0, higher_is_better=True),
        "constraint_control": _band(float(macro.get("constraint_violation_rate") or 0), good=0.15, warning=0.50, higher_is_better=False),
        "quality_delta": _band(float(macro.get("recommended_composite_delta") or 0), good=0.0, warning=-0.10, higher_is_better=True),
        "secondary_structure": _band(float(macro.get("recommended_secondary_structure_proxy") or 0), good=0.35, warning=0.60, higher_is_better=False),
        "recommendation_regret": _band(float(macro.get("recommendation_max_regret") or 0), good=0.05, warning=0.20, higher_is_better=False),
        "pareto_spread": _band(float(macro.get("approx_hypervolume_2d") or 0), good=0.20, warning=0.05, higher_is_better=True),
        "runtime": _band(float(macro.get("runtime_ms") or 0), good=2500.0, warning=7500.0, higher_is_better=False),
    }


def _band(value: float, *, good: float, warning: float, higher_is_better: bool) -> str:
    if higher_is_better:
        if value >= good:
            return "pass"
        if value >= warning:
            return "warning"
        return "fail"
    if value <= good:
        return "pass"
    if value <= warning:
        return "warning"
    return "fail"


def _warnings(benchmark: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if benchmark.get("fail_count", 0):
        warnings.append("Optimizer benchmark has failing cases.")
    if benchmark.get("warning_count", 0):
        warnings.append("Optimizer benchmark has warning cases.")
    macro = benchmark.get("macro") or {}
    if float(macro.get("constraint_violation_rate") or 0) > 0.35:
        warnings.append("Macro constraint violation rate is elevated.")
    if float(macro.get("unique_cds_count") or 0) < 2:
        warnings.append("Macro unique candidate diversity is low.")
    if float(macro.get("approx_hypervolume_2d") or 0) <= 0:
        warnings.append("Approximate Pareto hypervolume is zero.")
    if float(macro.get("recommendation_max_regret") or 0) > 0.20:
        warnings.append("Recommended candidates show high objective regret versus best-by-metric alternatives.")
    if float(macro.get("recommended_secondary_structure_proxy") or 0) > 0.60:
        warnings.append("Recommended candidates have elevated deterministic secondary-structure proxy risk.")
    return warnings


def _recommendations(benchmark: dict[str, Any], warnings: list[str], stress: dict[str, Any], folding: dict[str, Any]) -> list[str]:
    recommendations: list[str] = []
    macro = benchmark.get("macro") or {}
    if benchmark.get("status") != "pass":
        recommendations.append("Inspect /api/v1/optimizer/benchmark for weak cases before changing production objective settings.")
    if float(macro.get("constraint_violation_rate") or 0) > 0.35:
        recommendations.append("Increase repair_passes or tighten sequence-policy filters for motif-heavy CDS inputs.")
    if float(macro.get("unique_cds_count") or 0) < 2:
        recommendations.append("Increase population_size, generations, or mutation_rate to recover candidate diversity.")
    if float(macro.get("runtime_ms") or 0) > 7500:
        recommendations.append("Reduce population_size/generations for interactive runs and reserve larger searches for queued jobs.")
    if float(macro.get("recommendation_max_regret") or 0) > 0.20:
        recommendations.append("Inspect recommendation_audit tradeoffs before accepting candidates for high-stakes designs.")
    if float(macro.get("recommended_secondary_structure_proxy") or 0) > 0.60:
        recommendations.append("Review secondary_structure_proxy_score and confirm with a validated RNA folding backend before production release.")
    if not folding.get("production_ready"):
        recommendations.append("Configure RNA_FOLDING_BACKEND=rnafold with ViennaRNA RNAfold before treating structure-risk evidence as production thermodynamics.")
    if stress.get("status") != "pass":
        recommendations.extend((stress.get("recommendations") or [])[:2])
    if not recommendations and not warnings:
        recommendations.append("Optimizer diagnostics are clean under the configured benchmark suite.")
    elif not recommendations:
        recommendations.append("Review weak benchmark cases and compare candidate_diagnostics.json in exported run bundles.")
    return recommendations
