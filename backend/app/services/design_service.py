from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256

from app.optimizer.codon_table import normalize_dna, split_codons, translate
from app.optimizer.nsga2 import OptimizationConfig, optimize_cds
from app.optimizer.scoring import ScoreConfig, score_sequence
from app.services.sequence_policy_service import audit_sequence_policy
from app.services.rna_folding_service import evaluate_rna_folding
from app.services.structured_data_service import codon_availability_weights, codon_weight_multipliers, structured_manifest_hash
from app.services.validation_service import qc_gate_for_design, validate_cds


PIPELINE_VERSION = "0.1.0-mvp"


def build_run_id(cds: str, seed: int) -> str:
    digest = sha256(f"{normalize_dna(cds)}:{seed}:{PIPELINE_VERSION}".encode("utf-8")).hexdigest()
    return f"run_{digest[:12]}"


def score_cds(cds: str, score_config: ScoreConfig | None = None) -> dict:
    normalized = normalize_dna(cds)
    protein = translate(normalized)
    return {
        "cds": normalized,
        "protein": protein,
        "scores": score_sequence(normalized, score_config).to_dict(),
    }


def optimize_design(
    cds: str,
    optimization_config: OptimizationConfig | None = None,
    target: dict | None = None,
    evidence_used: bool = False,
) -> dict:
    optimization_config = optimization_config or OptimizationConfig()
    optimization_config, codon_source = _apply_target_priors(optimization_config, target or {})
    normalized = normalize_dna(cds)
    native = score_cds(normalized, optimization_config.score_config)
    candidates = optimize_cds(normalized, optimization_config)
    candidate_payloads = [candidate.to_dict() for candidate in candidates]
    recommended = _recommend_candidate(candidate_payloads)
    _annotate_candidate_selection(candidate_payloads, recommended)
    candidate_diagnostics = _candidate_diagnostics(candidate_payloads, recommended, optimization_config.score_config)
    recommendation_audit = _recommendation_audit(candidate_payloads, recommended, candidate_diagnostics)
    recommended_folding_evidence = _recommended_folding_evidence(recommended)
    run_id = build_run_id(normalized, optimization_config.seed)
    design = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": PIPELINE_VERSION,
        "target": target or {},
        "native": native,
        "optimization_config": optimization_config.to_dict(),
        "candidates": candidate_payloads,
        "recommended_candidate": recommended,
        "candidate_diagnostics": candidate_diagnostics,
        "recommendation_audit": recommendation_audit,
        "recommended_folding_evidence": recommended_folding_evidence,
        "warnings": _warnings(native["scores"], candidate_payloads, evidence_used),
        "provenance": {
            "input_source": "user_supplied_cds",
            "codon_weight_source": codon_source,
            "structured_manifest_hash": structured_manifest_hash(),
            "external_databases": [],
            "note": "In silico design-support output. Experimental validation is required before therapeutic interpretation.",
        },
    }
    design["validation"] = {
        "native": validate_cds(normalized, optimization_config.score_config),
        "recommended": validate_cds(recommended["cds"], optimization_config.score_config) if recommended else None,
    }
    design["qc_gate"] = qc_gate_for_design(design)
    return design


def _recommended_folding_evidence(recommended: dict | None) -> dict:
    cds = (recommended or {}).get("cds")
    if not cds:
        payload = {
            "folding_schema": "agentic-rag-rna-folding-v1",
            "status": "warning",
            "active_backend": "missing_recommended_candidate",
            "fallback_active": True,
            "warnings": ["No recommended candidate was available for folding evidence."],
        }
        return {**payload, "folding_evidence_hash": _hash_payload(payload)}
    payload = evaluate_rna_folding(str(cds))
    return {**payload, "folding_evidence_hash": _hash_payload(payload)}


def _warnings(native_scores: dict, candidates: list[dict], evidence_used: bool) -> list[str]:
    warnings: list[str] = []
    if not native_scores["aav_budget_pass"]:
        warnings.append("Native CDS exceeds the configured AAV payload limit.")
    if candidates and not candidates[0]["scores"]["aav_budget_pass"]:
        warnings.append("Top-ranked candidate exceeds the configured AAV payload limit.")
    if candidates and candidates[0]["scores"]["motif_violations"] > 0:
        warnings.append("Top-ranked candidate still contains forbidden motif matches.")
    if candidates and candidates[0]["scores"].get("polyadenylation_signal_count", 0) > 0:
        warnings.append("Top-ranked candidate contains internal polyadenylation signal proxy matches.")
    if candidates and candidates[0]["scores"].get("restriction_site_count", 0) > 0:
        warnings.append("Top-ranked candidate contains configured restriction enzyme recognition sites.")
    if candidates and candidates[0]["scores"].get("cryptic_splice_motif_count", 0) > 0:
        warnings.append("Top-ranked candidate contains cryptic splice motif proxy matches.")
    if candidates and candidates[0]["scores"].get("splice_donor_motif_count", 0) > 0:
        warnings.append("Top-ranked candidate contains splice donor proxy matches.")
    if candidates and candidates[0]["scores"].get("splice_acceptor_motif_count", 0) > 0:
        warnings.append("Top-ranked candidate contains splice acceptor proxy matches.")
    if candidates and candidates[0]["scores"].get("gc_window_max_deviation", 0) > 0.18:
        warnings.append("Top-ranked candidate has a high local GC-window deviation from the configured target.")
    if candidates and candidates[0]["scores"].get("five_prime_gc_deviation", 0) > 0.20:
        warnings.append("Top-ranked candidate has a high 5-prime coding-window GC deviation from the configured target.")
    if candidates and candidates[0]["scores"].get("hairpin_proxy_score", 0) > 0.40:
        warnings.append("Top-ranked candidate has elevated 5-prime reverse-complement hairpin proxy risk.")
    if candidates and candidates[0]["scores"].get("secondary_structure_proxy_score", 0) > 0.55:
        warnings.append("Top-ranked candidate has elevated secondary-structure proxy risk.")
    if candidates and candidates[0]["scores"].get("low_complexity_penalty", 0) > 0.45:
        warnings.append("Top-ranked candidate has low k-mer sequence complexity.")
    if not evidence_used:
        warnings.append("No tissue/cell-type evidence was used in this MVP run.")
    return warnings


def _recommend_candidate(candidates: list[dict]) -> dict | None:
    if not candidates:
        return None
    feasible = [candidate for candidate in candidates if _is_feasible_candidate(candidate)]
    pool = feasible or candidates
    return max(pool, key=lambda candidate: candidate["scores"]["composite_quality"])


def _annotate_candidate_selection(candidates: list[dict], recommended: dict | None) -> None:
    if not candidates:
        return
    feasible_ids = {candidate.get("candidate_id") for candidate in candidates if _is_feasible_candidate(candidate)}
    recommended_id = (recommended or {}).get("candidate_id")
    best_composite = max(float((candidate.get("scores") or {}).get("composite_quality", 0.0)) for candidate in candidates)
    best_cai = max(float((candidate.get("scores") or {}).get("cai", 0.0)) for candidate in candidates)
    lowest_policy_risk = min(float((candidate.get("scores") or {}).get("sequence_policy_violation_score", 0.0)) for candidate in candidates)
    for candidate in candidates:
        scores = candidate.get("scores") or {}
        candidate["constraint_risk"] = _candidate_constraint_risk(scores)
        trace = [
            f"rank {candidate.get('rank')} with composite {float(scores.get('composite_quality', 0.0)):.4f}",
            "feasible under hard selection criteria" if candidate.get("candidate_id") in feasible_ids else "kept as Pareto trade-off despite constraint risk",
        ]
        if candidate.get("candidate_id") == recommended_id:
            trace.append("selected as recommended candidate by highest feasible composite score")
        if float(scores.get("composite_quality", 0.0)) >= best_composite:
            trace.append("best composite_quality among returned candidates")
        if float(scores.get("cai", 0.0)) >= best_cai:
            trace.append("best CAI among returned candidates")
        if float(scores.get("sequence_policy_violation_score", 0.0)) <= lowest_policy_risk:
            trace.append("lowest sequence policy risk among returned candidates")
        if scores.get("tissue_codon_adaptation", 0) and float(scores.get("tissue_codon_adaptation", 0.0)) > 1.0:
            trace.append("uses target-context codon availability prior")
        candidate["selection_trace"] = trace


def _is_feasible_candidate(candidate: dict) -> bool:
    scores = candidate.get("scores") or {}
    return (
        scores.get("aav_budget_pass") is True
        and scores.get("motif_violations", 0) == 0
        and scores.get("polyadenylation_signal_count", 0) == 0
        and scores.get("restriction_site_count", 0) == 0
        and scores.get("splice_donor_motif_count", 0) == 0
        and scores.get("splice_acceptor_motif_count", 0) == 0
        and scores.get("hairpin_proxy_score", 0) <= 0.50
        and scores.get("secondary_structure_proxy_score", 0) <= 0.60
        and scores.get("low_complexity_penalty", 0) <= 0.50
    )


def _candidate_constraint_risk(scores: dict) -> dict:
    findings: list[dict] = []

    def add(condition: bool, key: str, severity: str, value: object, message: str) -> None:
        if condition:
            findings.append({"key": key, "severity": severity, "value": value, "message": message})

    add(scores.get("aav_budget_pass") is not True, "aav_budget_pass", "fail", scores.get("length_nt"), "CDS exceeds configured payload budget.")
    add(scores.get("motif_violations", 0) > 0, "motif_violations", "fail", scores.get("motif_violations"), "Forbidden motif matches remain.")
    add(scores.get("polyadenylation_signal_count", 0) > 0, "polyadenylation_signal_count", "fail", scores.get("polyadenylation_signal_count"), "Internal polyadenylation signal proxy remains.")
    add(scores.get("restriction_site_count", 0) > 0, "restriction_site_count", "warning", scores.get("restriction_site_count"), "Configured restriction-site matches remain.")
    add(scores.get("splice_donor_motif_count", 0) > 0, "splice_donor_motif_count", "warning", scores.get("splice_donor_motif_count"), "Splice donor proxy motifs remain.")
    add(scores.get("splice_acceptor_motif_count", 0) > 0, "splice_acceptor_motif_count", "warning", scores.get("splice_acceptor_motif_count"), "Splice acceptor proxy motifs remain.")
    add(scores.get("gc_window_max_deviation", 0) > 0.20, "gc_window_max_deviation", "warning", scores.get("gc_window_max_deviation"), "Local GC window deviates from target.")
    add(scores.get("five_prime_gc_deviation", 0) > 0.20, "five_prime_gc_deviation", "warning", scores.get("five_prime_gc_deviation"), "5-prime coding-window GC deviates from target.")
    add(scores.get("hairpin_proxy_score", 0) > 0.50, "hairpin_proxy_score", "warning", scores.get("hairpin_proxy_score"), "Elevated 5-prime hairpin proxy risk.")
    add(
        scores.get("secondary_structure_proxy_score", 0) > 0.60,
        "secondary_structure_proxy_score",
        "warning",
        scores.get("secondary_structure_proxy_score"),
        "Elevated deterministic secondary-structure proxy risk.",
    )
    add(scores.get("low_complexity_penalty", 0) > 0.50, "low_complexity_penalty", "warning", scores.get("low_complexity_penalty"), "Low k-mer sequence complexity.")
    status = "fail" if any(item["severity"] == "fail" for item in findings) else "warning" if findings else "pass"
    return {
        "status": status,
        "finding_count": len(findings),
        "fail_count": sum(1 for item in findings if item["severity"] == "fail"),
        "warning_count": sum(1 for item in findings if item["severity"] == "warning"),
        "findings": findings,
    }


def _candidate_diagnostics(candidates: list[dict], recommended: dict | None, score_config: ScoreConfig | None = None) -> dict:
    feasible = [candidate for candidate in candidates if _is_feasible_candidate(candidate)]
    pareto_ids = _pareto_front_candidate_ids(candidates)
    score_metrics = [
        "composite_quality",
        "cai",
        "tissue_codon_adaptation",
        "gc_window_max_deviation",
        "cpg_density_per_100nt",
        "sequence_policy_violation_score",
        "hairpin_proxy_score",
        "secondary_structure_proxy_score",
        "low_complexity_penalty",
    ]
    pareto_quality = _pareto_quality(candidates, pareto_ids, recommended)
    diagnostics = {
        "candidate_count": len(candidates),
        "feasible_count": len(feasible),
        "recommended_candidate_id": (recommended or {}).get("candidate_id"),
        "recommended_rank": (recommended or {}).get("rank"),
        "selection_policy": "highest composite_quality among feasible candidates; fallback to full Pareto-ranked pool",
        "constraint_risk_summary": _constraint_risk_summary(candidates),
        "feasibility_criteria": [
            "AAV payload pass",
            "no configured forbidden motifs",
            "no internal polyadenylation signal proxy matches",
            "no configured restriction-site matches",
            "no splice donor/acceptor proxy matches",
            "hairpin_proxy_score <= 0.50",
            "secondary_structure_proxy_score <= 0.60",
            "low_complexity_penalty <= 0.50",
        ],
        "pareto_front": {
            "size": len(pareto_ids),
            "candidate_ids": pareto_ids,
        },
        "pareto_quality": pareto_quality,
        "diversity": _candidate_diversity(candidates),
        "score_ranges": {metric: _score_range(candidates, metric) for metric in score_metrics},
        "best_by_metric": {
            "composite_quality": _best_candidate_by_metric(candidates, "composite_quality", maximize=True),
            "cai": _best_candidate_by_metric(candidates, "cai", maximize=True),
            "tissue_codon_adaptation": _best_candidate_by_metric(candidates, "tissue_codon_adaptation", maximize=True),
            "gc_window_max_deviation": _best_candidate_by_metric(candidates, "gc_window_max_deviation", maximize=False),
            "cpg_density_per_100nt": _best_candidate_by_metric(candidates, "cpg_density_per_100nt", maximize=False),
            "sequence_policy_violation_score": _best_candidate_by_metric(candidates, "sequence_policy_violation_score", maximize=False),
            "hairpin_proxy_score": _best_candidate_by_metric(candidates, "hairpin_proxy_score", maximize=False),
            "secondary_structure_proxy_score": _best_candidate_by_metric(candidates, "secondary_structure_proxy_score", maximize=False),
            "low_complexity_penalty": _best_candidate_by_metric(candidates, "low_complexity_penalty", maximize=False),
        },
        "sequence_policy_audit": _candidate_sequence_policy_audit(candidates, score_config),
    }
    diagnostics["recommendation_audit"] = _recommendation_audit(candidates, recommended, diagnostics)
    return diagnostics


def _candidate_sequence_policy_audit(candidates: list[dict], score_config: ScoreConfig | None = None) -> dict:
    status_counts = {"pass": 0, "warning": 0, "fail": 0}
    aggregate_summary: dict[str, int | float] = {
        "errors": 0,
        "warnings": 0,
        "policy_violation_score": 0.0,
        "forbidden_motif": 0,
        "polyadenylation_signal": 0,
        "restriction_site": 0,
        "splice_donor_proxy": 0,
        "splice_acceptor_proxy": 0,
        "cryptic_splice_proxy": 0,
    }
    motif_counts: dict[str, int] = {}
    candidate_summaries: list[dict] = []
    for candidate in candidates:
        audit = audit_sequence_policy(candidate.get("cds", ""), score_config)
        status = str(audit.get("status") or "warning")
        if status in status_counts:
            status_counts[status] += 1
        summary = audit.get("summary") or {}
        for key in aggregate_summary:
            value = summary.get(key, 0)
            if isinstance(value, (int, float)):
                aggregate_summary[key] = round(float(aggregate_summary[key]) + float(value), 6)
        top_findings = []
        for finding in audit.get("findings") or []:
            motif = str(finding.get("motif") or "unknown")
            count = int(finding.get("count") or 0)
            motif_counts[motif] = motif_counts.get(motif, 0) + count
            top_findings.append(
                {
                    "category": finding.get("category"),
                    "severity": finding.get("severity"),
                    "motif": motif,
                    "count": count,
                    "positions_1based": finding.get("positions_1based") or [],
                    "truncated_positions": bool(finding.get("truncated_positions")),
                    "recommended_action": finding.get("recommended_action"),
                }
            )
        candidate_summaries.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "rank": candidate.get("rank"),
                "status": status,
                "error_count": summary.get("errors", 0),
                "warning_count": summary.get("warnings", 0),
                "policy_violation_score": summary.get("policy_violation_score", 0.0),
                "finding_count": len(audit.get("findings") or []),
                "top_findings": top_findings[:6],
            }
        )
    payload = {
        "audit_schema": "agentic-rag-candidate-sequence-policy-audit-v1",
        "candidate_count": len(candidates),
        "status_counts": status_counts,
        "aggregate_summary": aggregate_summary,
        "top_motifs": [
            {"motif": motif, "count": count}
            for motif, count in sorted(motif_counts.items(), key=lambda item: item[1], reverse=True)[:10]
        ],
        "candidate_summaries": candidate_summaries,
    }
    return {**payload, "audit_hash": _hash_payload(payload)}


def _recommendation_audit(candidates: list[dict], recommended: dict | None, diagnostics: dict | None = None) -> dict:
    diagnostics = diagnostics or {}
    recommended_scores = (recommended or {}).get("scores") or {}
    metrics = [
        ("composite_quality", True),
        ("cai", True),
        ("tissue_codon_adaptation", True),
        ("gc_window_max_deviation", False),
        ("cpg_density_per_100nt", False),
        ("sequence_policy_violation_score", False),
        ("hairpin_proxy_score", False),
        ("secondary_structure_proxy_score", False),
        ("low_complexity_penalty", False),
    ]
    best_by_metric = diagnostics.get("best_by_metric") or {
        metric: _best_candidate_by_metric(candidates, metric, maximize=maximize) for metric, maximize in metrics
    }
    tradeoffs = []
    for metric, maximize in metrics:
        selected_value = _safe_float(recommended_scores.get(metric))
        best = best_by_metric.get(metric) or {}
        best_value = _safe_float(best.get("value"))
        if selected_value is None or best_value is None:
            continue
        regret = best_value - selected_value if maximize else selected_value - best_value
        tradeoffs.append(
            {
                "metric": metric,
                "direction": "maximize" if maximize else "minimize",
                "recommended_value": round(selected_value, 6),
                "best_value": round(best_value, 6),
                "best_candidate_id": best.get("candidate_id"),
                "regret": round(max(0.0, regret), 6),
                "is_metric_best": max(0.0, regret) <= 1e-9,
            }
        )
    nonzero_regrets = [item for item in tradeoffs if item["regret"] > 1e-9]
    hard_constraint_status = (recommended or {}).get("constraint_risk", {}).get("status") or (
        "pass" if recommended and _is_feasible_candidate(recommended) else "fail" if recommended else "missing"
    )
    sequence_policy = diagnostics.get("sequence_policy_audit") or {}
    recommended_sequence_policy = next(
        (
            item
            for item in sequence_policy.get("candidate_summaries") or []
            if item.get("candidate_id") == (recommended or {}).get("candidate_id")
        ),
        {},
    )
    return {
        "audit_schema": "agentic-rag-recommendation-audit-v1",
        "recommended_candidate_id": (recommended or {}).get("candidate_id"),
        "recommended_rank": (recommended or {}).get("rank"),
        "selection_policy": diagnostics.get("selection_policy") or "highest composite_quality among feasible candidates; fallback to full Pareto-ranked pool",
        "hard_constraint_status": hard_constraint_status,
        "is_feasible": bool(recommended and _is_feasible_candidate(recommended)),
        "recommended_sequence_policy_status": recommended_sequence_policy.get("status"),
        "recommended_sequence_policy_findings": recommended_sequence_policy.get("finding_count"),
        "sequence_policy_audit_hash": sequence_policy.get("audit_hash"),
        "pareto_front_member": (recommended or {}).get("candidate_id") in set((diagnostics.get("pareto_front") or {}).get("candidate_ids") or []),
        "pareto_quality_hash": (diagnostics.get("pareto_quality") or {}).get("quality_hash"),
        "best_metric_count": sum(1 for item in tradeoffs if item["is_metric_best"]),
        "tradeoff_count": len(nonzero_regrets),
        "max_regret": round(max((item["regret"] for item in tradeoffs), default=0.0), 6),
        "tradeoffs": tradeoffs,
        "primary_tradeoff": max(nonzero_regrets, key=lambda item: item["regret"], default=None),
    }


def _constraint_risk_summary(candidates: list[dict]) -> dict:
    statuses = {"pass": 0, "warning": 0, "fail": 0}
    top_findings: dict[str, int] = {}
    for candidate in candidates:
        risk = candidate.get("constraint_risk") or {}
        status = str(risk.get("status") or "warning")
        if status in statuses:
            statuses[status] += 1
        for finding in risk.get("findings") or []:
            key = str(finding.get("key") or "unknown")
            top_findings[key] = top_findings.get(key, 0) + 1
    return {
        "status_counts": statuses,
        "top_findings": [
            {"key": key, "count": count}
            for key, count in sorted(top_findings.items(), key=lambda item: item[1], reverse=True)[:8]
        ],
    }


def _score_range(candidates: list[dict], metric: str) -> dict:
    values = [
        float((candidate.get("scores") or {}).get(metric))
        for candidate in candidates
        if (candidate.get("scores") or {}).get(metric) is not None
    ]
    if not values:
        return {"min": None, "max": None, "spread": None}
    minimum = min(values)
    maximum = max(values)
    return {"min": round(minimum, 6), "max": round(maximum, 6), "spread": round(maximum - minimum, 6)}


def _best_candidate_by_metric(candidates: list[dict], metric: str, *, maximize: bool) -> dict | None:
    scored = [
        candidate
        for candidate in candidates
        if (candidate.get("scores") or {}).get(metric) is not None
    ]
    if not scored:
        return None
    selected = max(scored, key=lambda item: item["scores"][metric]) if maximize else min(scored, key=lambda item: item["scores"][metric])
    return {
        "candidate_id": selected.get("candidate_id"),
        "rank": selected.get("rank"),
        "value": round(float(selected["scores"][metric]), 6),
    }


def _safe_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _hash_payload(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _pareto_front_candidate_ids(candidates: list[dict]) -> list[str]:
    front: list[str] = []
    for candidate in candidates:
        vector = _diagnostic_objectives(candidate)
        if any(_dominates(_diagnostic_objectives(other), vector) for other in candidates if other is not candidate):
            continue
        front.append(candidate.get("candidate_id"))
    return [candidate_id for candidate_id in front if candidate_id]


def _pareto_quality(candidates: list[dict], pareto_ids: list[str], recommended: dict | None) -> dict:
    pareto_set = set(pareto_ids)
    front = [candidate for candidate in candidates if candidate.get("candidate_id") in pareto_set]
    recommended_id = (recommended or {}).get("candidate_id")
    payload = {
        "quality_schema": "agentic-rag-pareto-quality-v1",
        "candidate_count": len(candidates),
        "front_size": len(front),
        "front_fraction": round(len(front) / max(len(candidates), 1), 6),
        "feasible_front_count": sum(1 for candidate in front if _is_feasible_candidate(candidate)),
        "recommended_candidate_id": recommended_id,
        "recommended_on_front": bool(recommended_id and recommended_id in pareto_set),
        "approx_hypervolume_2d": _approx_hypervolume(candidates),
        "front_score_ranges": {
            metric: _score_range(front, metric)
            for metric in [
                "composite_quality",
                "cai",
                "tissue_codon_adaptation",
                "sequence_policy_violation_score",
                "secondary_structure_proxy_score",
                "low_complexity_penalty",
            ]
        },
    }
    return {**payload, "quality_hash": _hash_payload(payload)}


def _diagnostic_objectives(candidate: dict) -> tuple[float, ...]:
    scores = candidate.get("scores") or {}
    return (
        -float(scores.get("composite_quality", 0)),
        -float(scores.get("cai", 0)),
        -float(scores.get("tissue_codon_adaptation", 0)),
        float(scores.get("gc_window_max_deviation", 0)),
        float(scores.get("cpg_density_per_100nt", 0)),
        float(scores.get("sequence_policy_violation_score", 0)),
        float(scores.get("hairpin_proxy_score", 0)),
        float(scores.get("secondary_structure_proxy_score", 0)),
        float(scores.get("low_complexity_penalty", 0)),
    )


def _dominates(left: tuple[float, ...], right: tuple[float, ...]) -> bool:
    return all(left_value <= right_value for left_value, right_value in zip(left, right)) and any(
        left_value < right_value for left_value, right_value in zip(left, right)
    )


def _candidate_diversity(candidates: list[dict]) -> dict:
    distances: list[float] = []
    for left_index, left in enumerate(candidates):
        for right in candidates[left_index + 1 :]:
            distances.append(_codon_distance(left.get("cds", ""), right.get("cds", "")))
    if not distances:
        return {
            "unique_cds_count": len({candidate.get("cds") for candidate in candidates if candidate.get("cds")}),
            "mean_pairwise_codon_distance": 0.0,
            "min_pairwise_codon_distance": 0.0,
            "max_pairwise_codon_distance": 0.0,
        }
    return {
        "unique_cds_count": len({candidate.get("cds") for candidate in candidates if candidate.get("cds")}),
        "mean_pairwise_codon_distance": round(sum(distances) / len(distances), 6),
        "min_pairwise_codon_distance": round(min(distances), 6),
        "max_pairwise_codon_distance": round(max(distances), 6),
    }


def _codon_distance(left: str, right: str) -> float:
    left_codons = split_codons(left)
    right_codons = split_codons(right)
    comparable = min(len(left_codons), len(right_codons))
    if comparable == 0:
        return 0.0
    mismatches = sum(1 for idx in range(comparable) if left_codons[idx] != right_codons[idx])
    length_penalty = abs(len(left_codons) - len(right_codons))
    return (mismatches + length_penalty) / max(len(left_codons), len(right_codons), 1)


def _approx_hypervolume(candidates: list[dict]) -> float:
    points = sorted(
        {
            (
                max(0.0, min(1.0, float((candidate.get("scores") or {}).get("composite_quality") or 0.0))),
                max(0.0, min(1.0, 1.0 - float((candidate.get("scores") or {}).get("sequence_policy_violation_score") or 0.0))),
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


def _apply_target_priors(config: OptimizationConfig, target: dict) -> tuple[OptimizationConfig, str]:
    structured_multipliers = codon_weight_multipliers(target)
    structured_availability = codon_availability_weights(target)
    existing_multipliers = dict(config.score_config.codon_weight_multipliers)
    existing_availability = dict(config.score_config.codon_availability_weights)
    if not structured_multipliers and not existing_multipliers and not structured_availability and not existing_availability:
        return config, "built_in_human_mvp_table"

    merged = {**structured_multipliers, **existing_multipliers}
    merged_availability = {**structured_availability, **existing_availability}
    score_config = replace(
        config.score_config,
        codon_weight_multipliers=tuple(sorted(merged.items())),
        codon_availability_weights=tuple(sorted(merged_availability.items())),
    )
    source = "built_in_human_mvp_table"
    if structured_multipliers:
        source += "+CUSTOM_structured_seed"
    if existing_multipliers:
        source += "+user_override"
    if structured_availability:
        source += "+tRNA_availability_seed"
    if existing_availability:
        source += "+user_tRNA_override"
    return replace(config, score_config=score_config), source
