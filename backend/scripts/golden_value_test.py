from __future__ import annotations

import argparse
import json
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.optimizer.nsga2 import OptimizationConfig
from app.services.design_service import score_cds
from app.services.validation_service import validate_cds
from app.services.workflow_service import run_cds_design_workflow


SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "tests" / "golden" / "api_values_v1.json"
SCORE_CDS = "ATGGCTGACGAGTTCGCCAAGGGTTACTAA"
POLICY_CDS = "ATGAATAAAGAATTCGTAAGTTAA"
FLOAT_TOLERANCE = 1e-6


def main() -> int:
    parser = argparse.ArgumentParser(description="Check deterministic value-level golden fixtures for scoring, validation, and QC.")
    parser.add_argument("--update", action="store_true", help="Rewrite the golden value snapshot.")
    args = parser.parse_args()

    current = build_snapshot()
    if args.update:
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"updated": str(SNAPSHOT_PATH), "cases": list(current)}, indent=2, sort_keys=True))
        return 0

    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    failures = _diff(expected, current)
    print(json.dumps({"snapshot": str(SNAPSHOT_PATH), "cases": list(current), "failures": failures}, indent=2, sort_keys=True))
    return 1 if failures else 0


def build_snapshot() -> dict[str, Any]:
    score_clean = score_cds(SCORE_CDS)
    score_policy = score_cds(POLICY_CDS)
    validation_policy = validate_cds(POLICY_CDS)
    design = run_cds_design_workflow(
        SCORE_CDS,
        {"gene": "DEMO", "species": "human", "modality": "AAV"},
        OptimizationConfig(population_size=16, generations=2, max_candidates=2, seed=101),
    )
    recommended = design["recommended_candidate"]
    native_scores = score_clean["scores"]
    recommended_scores = recommended["scores"]

    return {
        "score_clean": {
            "protein": score_clean["protein"],
            "scores": _selected_scores(native_scores),
        },
        "score_policy_risk": {
            "protein": score_policy["protein"],
            "scores": _selected_scores(score_policy["scores"]),
        },
        "validation_policy_risk": {
            "status": validation_policy["status"],
            "errors": validation_policy["errors"],
            "warnings": validation_policy["warnings"],
            "terminal_stop": validation_policy["terminal_stop"],
            "sequence_policy": {
                "status": validation_policy["sequence_policy"]["status"],
                "summary": validation_policy["sequence_policy"]["summary"],
                "finding_categories": sorted({item["category"] for item in validation_policy["sequence_policy"]["findings"]}),
            },
            "failed_checks": sorted(item["id"] for item in validation_policy["checks"] if item["result"] == "fail"),
        },
        "design_seeded": {
            "run_id": design["run_id"],
            "pipeline_version": design["pipeline_version"],
            "candidate_count": len(design["candidates"]),
            "recommended_candidate_id": recommended["candidate_id"],
            "recommended_cds_sha256": sha256(recommended["cds"].encode("ascii")).hexdigest(),
            "native_scores": _selected_scores(native_scores),
            "recommended_scores": _selected_scores(recommended_scores),
            "qc_gate": {
                "status": design["qc_gate"]["status"],
                "errors": design["qc_gate"]["errors"],
                "warnings": design["qc_gate"]["warnings"],
                "failed_checks": sorted(item["id"] for item in design["qc_gate"]["checks"] if item["result"] == "fail"),
            },
            "qc_report": {
                "supported_rules": len(design["qc_report"]["evidence_summary"]["supported_rules"]),
                "uncertain_rules": len(design["qc_report"]["evidence_summary"]["uncertain_rules"]),
                "open_questions": len(design["qc_report"]["open_questions"]),
                "warnings": len(design["qc_report"]["warnings"]),
            },
            "trace_steps": [step["name"] for step in design["trace"]],
        },
    }


def _selected_scores(scores: dict[str, Any]) -> dict[str, Any]:
    selected = [
        "aav_budget_pass",
        "cai",
        "codon_pair_risk",
        "composite_quality",
        "cpg_density_per_100nt",
        "five_prime_gc_deviation",
        "gc_fraction",
        "gc_window_max_deviation",
        "hairpin_proxy_score",
        "length_nt",
        "low_complexity_penalty",
        "mfe_proxy_delta_g",
        "motif_violations",
        "polyadenylation_signal_count",
        "restriction_site_count",
        "secondary_structure_proxy_score",
        "sequence_complexity",
        "sequence_policy_violation_score",
        "splice_acceptor_motif_count",
        "splice_donor_motif_count",
        "tissue_codon_adaptation",
    ]
    return {key: _round_value(scores.get(key)) for key in selected}


def _round_value(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    return value


def _diff(expected: Any, current: Any, path: str = "$") -> list[str]:
    if isinstance(expected, dict) and isinstance(current, dict):
        failures: list[str] = []
        for key in sorted(set(expected) - set(current)):
            failures.append(f"{path}.{key}: missing")
        for key in sorted(set(current) - set(expected)):
            failures.append(f"{path}.{key}: unexpected")
        for key in sorted(set(expected) & set(current)):
            failures.extend(_diff(expected[key], current[key], f"{path}.{key}"))
        return failures
    if isinstance(expected, list) and isinstance(current, list):
        failures = []
        if len(expected) != len(current):
            failures.append(f"{path}: length changed from {len(expected)} to {len(current)}")
        for idx, (expected_item, current_item) in enumerate(zip(expected, current)):
            failures.extend(_diff(expected_item, current_item, f"{path}[{idx}]"))
        return failures
    if isinstance(expected, (int, float)) and isinstance(current, (int, float)) and not isinstance(expected, bool) and not isinstance(current, bool):
        return [] if abs(float(expected) - float(current)) <= FLOAT_TOLERANCE else [f"{path}: expected {expected}, got {current}"]
    return [] if expected == current else [f"{path}: expected {expected!r}, got {current!r}"]


if __name__ == "__main__":
    raise SystemExit(main())
