from __future__ import annotations

import json
from hashlib import sha256
from typing import Any


OBJECTIVE_INVENTORY = [
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
]


def optimizer_reproducibility_manifest(design: dict[str, Any]) -> dict[str, Any]:
    optimization_config = design.get("optimization_config") or {}
    score_config = optimization_config.get("score_config") or {}
    source_cds = design.get("source_cds") or {}
    native_cds = (design.get("native") or {}).get("cds") or ""
    target = design.get("target") or {}
    manifest = {
        "manifest_schema": "agentic-rag-optimizer-reproducibility-v1",
        "algorithm": optimization_config.get("algorithm", "seeded_nsga2"),
        "seed": optimization_config.get("seed"),
        "search_budget": {
            "population_size": optimization_config.get("population_size"),
            "generations": optimization_config.get("generations"),
            "mutation_rate": optimization_config.get("mutation_rate"),
            "crossover_rate": optimization_config.get("crossover_rate"),
            "max_candidates": optimization_config.get("max_candidates"),
        },
        "repair_policy": {
            "enabled": optimization_config.get("enable_repair"),
            "repair_passes": optimization_config.get("repair_passes"),
        },
        "seed_strategy": {
            "version": "deterministic-tradeoff-seeds-v1",
            "deterministic_seeds": [
                "native_cds",
                "human_preferred_codon",
                "target_availability_preferred_codon",
                "low_gc_extreme",
                "high_gc_extreme",
                "target_gc_greedy",
                "low_cpg_greedy",
            ],
            "stochastic_fill": "synonymous random fill from configured RNG seed",
        },
        "objective_inventory": OBJECTIVE_INVENTORY,
        "score_config_hash": _hash_payload(score_config),
        "optimization_config_hash": _hash_payload(optimization_config),
        "target_hash": _hash_payload(target),
        "source_cds_hash": sha256(native_cds.encode("utf-8")).hexdigest() if native_cds else None,
        "selected_transcript": {
            "transcript_id": ((source_cds.get("selected_transcript") or {}).get("id")),
            "selection_reason": ((source_cds.get("selected_transcript") or {}).get("selection_reason")),
            "cds_length_nt": source_cds.get("cds_length_nt") or len(native_cds),
            "protein_length_aa": source_cds.get("protein_length_aa") or len((design.get("native") or {}).get("protein") or ""),
        },
        "config": optimization_config,
    }
    manifest["manifest_hash"] = _hash_payload({key: value for key, value in manifest.items() if key != "manifest_hash"})
    return manifest


def _hash_payload(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()
