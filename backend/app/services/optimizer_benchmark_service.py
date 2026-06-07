from __future__ import annotations

import json
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.optimizer.codon_table import split_codons, translate
from app.optimizer.nsga2 import OptimizationConfig
from app.optimizer.scoring import ScoreConfig
from app.services.design_service import optimize_design


OPTIMIZER_BENCHMARK_CASES_PATH = get_settings().data_dir / "optimizer_benchmark_cases.json"


def optimizer_benchmark_cases() -> dict[str, Any]:
    cases = _load_cases()
    return {
        "cases_path": str(OPTIMIZER_BENCHMARK_CASES_PATH),
        "case_count": len(cases),
        "cases_hash": _hash_payload(cases),
        "cases": cases,
    }


def evaluate_optimizer_benchmark() -> dict[str, Any]:
    cases = _load_cases()
    results = [_evaluate_case(case) for case in cases]
    failed = [result for result in results if result["status"] == "fail"]
    warnings = [result for result in results if result["status"] == "warning"]
    return {
        "status": "fail" if failed else "warning" if warnings else "pass",
        "benchmark_schema": "agentic-rag-optimizer-benchmark-v1",
        "case_count": len(results),
        "pass_count": sum(1 for result in results if result["status"] == "pass"),
        "warning_count": len(warnings),
        "fail_count": len(failed),
        "cases_hash": _hash_payload(cases),
        "macro": _macro_metrics(results),
        "results": results,
    }


def _evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    config = _optimization_config(case.get("optimization_settings") or {})
    design = optimize_design(case["cds"], config, case.get("target") or {}, evidence_used=True)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    metrics = _metrics(case["cds"], design, elapsed_ms)
    thresholds = _thresholds(case)
    errors, warnings = _evaluate_thresholds(metrics, thresholds)
    return {
        "case_id": case["case_id"],
        "description": case.get("description", ""),
        "status": "fail" if errors else "warning" if warnings else "pass",
        "errors": errors,
        "warnings": warnings,
        "thresholds": thresholds,
        "metrics": metrics,
        "run_id": design["run_id"],
        "recommended_candidate_id": (design.get("recommended_candidate") or {}).get("candidate_id"),
        "candidate_diagnostics": design.get("candidate_diagnostics", {}),
    }


def _metrics(native_cds: str, design: dict[str, Any], elapsed_ms: float) -> dict[str, Any]:
    candidates = design.get("candidates") or []
    native_scores = (design.get("native") or {}).get("scores") or {}
    recommended = design.get("recommended_candidate") or {}
    recommended_scores = recommended.get("scores") or {}
    diagnostics = design.get("candidate_diagnostics") or {}
    recommendation_audit = design.get("recommendation_audit") or diagnostics.get("recommendation_audit") or {}
    diversity = diagnostics.get("diversity") or {}
    native_protein = translate(native_cds)
    protein_preservation_failures = sum(1 for candidate in candidates if candidate.get("protein") != native_protein)
    violation_count = sum(1 for candidate in candidates if _constraint_violations(candidate.get("scores") or {}) > 0)
    return {
        "runtime_ms": elapsed_ms,
        "candidate_count": len(candidates),
        "feasible_count": int(diagnostics.get("feasible_count") or 0),
        "pareto_front_size": int((diagnostics.get("pareto_front") or {}).get("size") or 0),
        "unique_cds_count": int(diversity.get("unique_cds_count") or 0),
        "mean_pairwise_codon_distance": float(diversity.get("mean_pairwise_codon_distance") or 0.0),
        "constraint_violation_rate": round(violation_count / max(len(candidates), 1), 4),
        "protein_preservation_failures": protein_preservation_failures,
        "native_composite_quality": _float(native_scores.get("composite_quality")),
        "recommended_composite_quality": _float(recommended_scores.get("composite_quality")),
        "recommended_composite_delta": round(_float(recommended_scores.get("composite_quality")) - _float(native_scores.get("composite_quality")), 6),
        "recommended_cai_delta": round(_float(recommended_scores.get("cai")) - _float(native_scores.get("cai")), 6),
        "recommended_secondary_structure_proxy": _float(recommended_scores.get("secondary_structure_proxy_score")),
        "recommended_mfe_proxy_delta_g": _float(recommended_scores.get("mfe_proxy_delta_g")),
        "recommended_aav_budget_pass": 1.0 if recommended_scores.get("aav_budget_pass") is True else 0.0,
        "recommended_motif_violations": _float(recommended_scores.get("motif_violations")),
        "recommended_polyadenylation_signal_count": _float(recommended_scores.get("polyadenylation_signal_count")),
        "recommended_restriction_site_count": _float(recommended_scores.get("restriction_site_count")),
        "recommended_splice_donor_motif_count": _float(recommended_scores.get("splice_donor_motif_count")),
        "recommended_splice_acceptor_motif_count": _float(recommended_scores.get("splice_acceptor_motif_count")),
        "recommended_rare_codon_clusters": _float(recommended_scores.get("rare_codon_clusters")),
        "recommended_low_complexity_penalty": _float(recommended_scores.get("low_complexity_penalty")),
        "recommended_hairpin_proxy_score": _float(recommended_scores.get("hairpin_proxy_score")),
        "recommended_policy_violation_delta": round(
            _float(recommended_scores.get("sequence_policy_violation_score")) - _float(native_scores.get("sequence_policy_violation_score")),
            6,
        ),
        "recommended_codon_distance_from_native": _codon_distance(native_cds, recommended.get("cds", "")),
        "recommendation_best_metric_count": int(recommendation_audit.get("best_metric_count") or 0),
        "recommendation_tradeoff_count": int(recommendation_audit.get("tradeoff_count") or 0),
        "recommendation_max_regret": _float(recommendation_audit.get("max_regret")),
        "approx_hypervolume_2d": _approx_hypervolume(candidates),
    }


def _evaluate_thresholds(metrics: dict[str, Any], thresholds: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if metrics["candidate_count"] < thresholds["min_candidates"]:
        errors.append(f"candidate_count {metrics['candidate_count']} < {thresholds['min_candidates']}.")
    if metrics["unique_cds_count"] < thresholds["min_unique_cds"]:
        errors.append(f"unique_cds_count {metrics['unique_cds_count']} < {thresholds['min_unique_cds']}.")
    if metrics["protein_preservation_failures"] > 0:
        errors.append(f"{metrics['protein_preservation_failures']} candidates changed the protein sequence.")
    if metrics["constraint_violation_rate"] > thresholds["max_constraint_violation_rate"]:
        warnings.append(
            f"constraint_violation_rate {metrics['constraint_violation_rate']} > {thresholds['max_constraint_violation_rate']}."
        )
    if metrics["recommended_composite_delta"] < thresholds["min_recommended_composite_delta"]:
        warnings.append(
            f"recommended_composite_delta {metrics['recommended_composite_delta']} < {thresholds['min_recommended_composite_delta']}."
        )
    return errors, warnings


def _macro_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {
            "candidate_count": 0.0,
            "unique_cds_count": 0.0,
            "constraint_violation_rate": 0.0,
            "mean_pairwise_codon_distance": 0.0,
            "recommended_composite_delta": 0.0,
            "approx_hypervolume_2d": 0.0,
            "recommendation_max_regret": 0.0,
            "runtime_ms": 0.0,
        }
    metrics = [result["metrics"] for result in results]
    return {
        key: round(sum(float(item.get(key) or 0.0) for item in metrics) / len(metrics), 6)
        for key in [
            "candidate_count",
            "unique_cds_count",
            "constraint_violation_rate",
            "mean_pairwise_codon_distance",
            "recommended_composite_delta",
            "recommended_secondary_structure_proxy",
            "recommended_aav_budget_pass",
            "recommended_motif_violations",
            "recommended_polyadenylation_signal_count",
            "recommended_restriction_site_count",
            "recommended_splice_donor_motif_count",
            "recommended_splice_acceptor_motif_count",
            "recommended_rare_codon_clusters",
            "recommended_low_complexity_penalty",
            "recommended_hairpin_proxy_score",
            "approx_hypervolume_2d",
            "recommendation_max_regret",
            "runtime_ms",
        ]
    }


def _load_cases() -> list[dict[str, Any]]:
    if not OPTIMIZER_BENCHMARK_CASES_PATH.exists():
        return []
    payload = json.loads(OPTIMIZER_BENCHMARK_CASES_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Optimizer benchmark cases must be a JSON list.")
    return [case for case in payload if isinstance(case, dict) and case.get("case_id") and case.get("cds")]


def _optimization_config(settings: dict[str, Any]) -> OptimizationConfig:
    score_settings = settings.get("score_settings") or {}
    score_config = ScoreConfig(
        gc_min=float(score_settings.get("gc_min", 0.40)),
        gc_max=float(score_settings.get("gc_max", 0.65)),
        target_gc=float(score_settings.get("target_gc", 0.55)),
        aav_payload_limit_nt=int(score_settings.get("aav_payload_limit_nt", 4300)),
        forbidden_motifs=tuple(score_settings.get("forbidden_motifs", ["AATAAA", "ATTAAA", "GGGGGG", "CCCCCC", "TTTTTT", "AAAAAA"])),
        polyadenylation_signals=tuple(score_settings.get("polyadenylation_signals", ["AATAAA", "ATTAAA", "AGTAAA", "TATAAA", "CATAAA", "GATAAA"])),
        restriction_sites=tuple(score_settings.get("restriction_sites", ["GAATTC", "GGATCC", "AAGCTT", "GCGGCCGC", "TCTAGA", "ACTAGT"])),
        cryptic_splice_motifs=tuple(score_settings.get("cryptic_splice_motifs", ["CAGG", "GTAGT", "GTAAGT", "TTTTCAG"])),
        splice_donor_motifs=tuple(score_settings.get("splice_donor_motifs", ["GTAAGT", "GTGAGT", "GTATGT"])),
        splice_acceptor_motifs=tuple(score_settings.get("splice_acceptor_motifs", ["TTTTCAG", "CTTTCAG", "TCTTCAG"])),
        gc_window_size_nt=int(score_settings.get("gc_window_size_nt", 60)),
        codon_weight_multipliers=tuple(sorted((score_settings.get("codon_weight_multipliers") or {}).items())),
        codon_availability_weights=tuple(sorted((score_settings.get("codon_availability_weights") or {}).items())),
    )
    return OptimizationConfig(
        population_size=int(settings.get("population_size", 24)),
        generations=int(settings.get("generations", 4)),
        mutation_rate=float(settings.get("mutation_rate", 0.05)),
        crossover_rate=float(settings.get("crossover_rate", 0.80)),
        seed=int(settings.get("seed", 42)),
        max_candidates=int(settings.get("max_candidates", 4)),
        enable_repair=bool(settings.get("enable_repair", True)),
        repair_passes=int(settings.get("repair_passes", 3)),
        score_config=score_config,
    )


def _thresholds(case: dict[str, Any]) -> dict[str, Any]:
    values = case.get("thresholds") or {}
    return {
        "min_candidates": int(values.get("min_candidates", 2)),
        "min_unique_cds": int(values.get("min_unique_cds", 2)),
        "max_constraint_violation_rate": float(values.get("max_constraint_violation_rate", 0.5)),
        "min_recommended_composite_delta": float(values.get("min_recommended_composite_delta", -0.10)),
    }


def _constraint_violations(scores: dict[str, Any]) -> int:
    return sum(
        [
            0 if scores.get("aav_budget_pass") is True else 1,
            int(scores.get("motif_violations") or 0),
            int(scores.get("polyadenylation_signal_count") or 0),
            int(scores.get("restriction_site_count") or 0),
            int(scores.get("splice_donor_motif_count") or 0),
            int(scores.get("splice_acceptor_motif_count") or 0),
            1 if _float(scores.get("hairpin_proxy_score")) > 0.50 else 0,
            1 if _float(scores.get("secondary_structure_proxy_score")) > 0.60 else 0,
            1 if _float(scores.get("low_complexity_penalty")) > 0.50 else 0,
        ]
    )


def _approx_hypervolume(candidates: list[dict[str, Any]]) -> float:
    points = sorted(
        {
            (
                max(0.0, min(1.0, _float((candidate.get("scores") or {}).get("composite_quality")))),
                max(0.0, min(1.0, 1.0 - _float((candidate.get("scores") or {}).get("sequence_policy_violation_score")))),
            )
            for candidate in candidates
        },
        reverse=True,
    )
    hypervolume = 0.0
    best_y = 0.0
    for x, y in points:
        if y <= best_y:
            continue
        hypervolume += x * (y - best_y)
        best_y = y
    return round(hypervolume, 6)


def _codon_distance(left: str, right: str) -> float:
    left_codons = split_codons(left)
    right_codons = split_codons(right)
    comparable = min(len(left_codons), len(right_codons))
    if comparable == 0:
        return 0.0
    mismatches = sum(1 for idx in range(comparable) if left_codons[idx] != right_codons[idx])
    length_penalty = abs(len(left_codons) - len(right_codons))
    return round((mismatches + length_penalty) / max(len(left_codons), len(right_codons), 1), 6)


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _hash_payload(payload: Any) -> str:
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
