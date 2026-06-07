from __future__ import annotations

from typing import Any

from app.optimizer.nsga2 import OptimizationConfig
from app.services.ensembl_client import EnsemblClient
from app.services.run_store import save_run
from app.services.workflow_service import run_gene_design_workflow


def run_batch_gene_design(
    request_payload: dict[str, Any],
    optimization_config: OptimizationConfig,
    *,
    client: EnsemblClient | None = None,
    persist: bool = True,
    run_type: str = "batch_gene_design",
) -> dict[str, Any]:
    genes = _normalize_genes(request_payload.get("genes", []))
    if not genes:
        raise ValueError("At least one gene symbol is required.")

    species = request_payload.get("species") or "human"
    base_target = dict(request_payload.get("target") or {})
    continue_on_error = bool(request_payload.get("continue_on_error", True))
    runs: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    for index, gene in enumerate(genes):
        gene_payload = {
            "gene": gene,
            "species": species,
            "target": {**base_target, "gene": gene, "species": species},
            "optimization_settings": request_payload.get("optimization_settings") or {},
        }
        try:
            design = run_gene_design_workflow(gene_payload, _config_with_seed(optimization_config, index), client=client)
            saved_run = save_run(design, run_type=run_type, request_payload=gene_payload) if persist else None
            runs.append(
                {
                    "gene": gene,
                    "run_id": design["run_id"],
                    "saved_run": saved_run,
                    "recommended_candidate_id": (design.get("recommended_candidate") or {}).get("candidate_id"),
                    "qc_gate_status": (design.get("qc_gate") or {}).get("status"),
                    "warnings": design.get("warnings", []),
                }
            )
        except Exception as exc:  # noqa: BLE001 - batch result should preserve per-gene failures.
            failures.append({"gene": gene, "error": str(exc)})
            if not continue_on_error:
                break

    status = "succeeded" if not failures else "partial" if runs else "failed"
    return {
        "batch_status": status,
        "summary": {
            "requested": len(genes),
            "attempted": len(runs) + len(failures),
            "succeeded": len(runs),
            "failed": len(failures),
        },
        "runs": runs,
        "failures": failures,
    }


def _normalize_genes(genes: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for gene in genes:
        symbol = str(gene).strip().upper()
        if symbol and symbol not in seen:
            normalized.append(symbol)
            seen.add(symbol)
    return normalized


def _config_with_seed(config: OptimizationConfig, offset: int) -> OptimizationConfig:
    return OptimizationConfig(
        population_size=config.population_size,
        generations=config.generations,
        mutation_rate=config.mutation_rate,
        crossover_rate=config.crossover_rate,
        seed=config.seed + offset,
        max_candidates=config.max_candidates,
        enable_repair=config.enable_repair,
        repair_passes=config.repair_passes,
        score_config=config.score_config,
    )
