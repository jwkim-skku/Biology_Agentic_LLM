from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from typing import Callable

from app.optimizer.codon_table import (
    HUMAN_CODON_WEIGHTS,
    STOP_CODONS,
    SYNONYMOUS_CODONS,
    preferred_codon,
    split_codons,
    translate,
)
from app.optimizer.repair import repair_cds
from app.optimizer.scoring import ScoreConfig, SequenceScores, score_sequence


OPTIMIZER_ALGORITHM = "seeded_nsga2"
SEED_STRATEGY_VERSION = "deterministic-tradeoff-seeds-v1"
DETERMINISTIC_SEED_VARIANTS = [
    "native_cds",
    "max_human_codon_usage",
    "max_target_codon_availability",
    "min_gc",
    "max_gc",
    "target_gc_balance",
    "min_cpg",
]


@dataclass(frozen=True)
class OptimizationConfig:
    population_size: int = 48
    generations: int = 24
    mutation_rate: float = 0.04
    crossover_rate: float = 0.80
    seed: int = 42
    max_candidates: int = 8
    enable_repair: bool = True
    repair_passes: int = 3
    score_config: ScoreConfig = ScoreConfig()

    def to_dict(self) -> dict:
        data = asdict(self)
        data["score_config"] = self.score_config.to_dict()
        return data


def optimizer_search_strategy(config: OptimizationConfig | None = None) -> dict:
    config = config or OptimizationConfig()
    return {
        "strategy_schema": "agentic-rag-optimizer-search-strategy-v1",
        "algorithm": OPTIMIZER_ALGORITHM,
        "seed_strategy": SEED_STRATEGY_VERSION,
        "deterministic_seed_variants": list(DETERMINISTIC_SEED_VARIANTS),
        "stochastic_operator": {
            "rng_seed": config.seed,
            "mutation_rate": config.mutation_rate,
            "crossover_rate": config.crossover_rate,
            "population_size": config.population_size,
            "generations": config.generations,
        },
        "repair_policy": {
            "enabled": config.enable_repair,
            "repair_passes": config.repair_passes,
        },
        "selection_policy": "NSGA-II non-dominated sorting with crowding-distance truncation; final recommendation is selected by downstream feasible composite policy.",
        "reproducibility": {
            "deterministic_given_config": True,
            "config_hash_inputs": [
                "native_cds",
                "seed",
                "population_size",
                "generations",
                "mutation_rate",
                "crossover_rate",
                "repair_passes",
                "score_config",
            ],
        },
    }


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    cds: str
    protein: str
    scores: SequenceScores
    rank: int

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "cds": self.cds,
            "protein": self.protein,
            "scores": self.scores.to_dict(),
            "rank": self.rank,
        }


def optimize_cds(native_cds: str, config: OptimizationConfig | None = None) -> list[Candidate]:
    config = config or OptimizationConfig()
    rng = random.Random(config.seed)
    protein = translate(native_cds)
    terminal_stop = _terminal_stop(native_cds)
    population = _seed_population(native_cds, protein, config.population_size, rng, terminal_stop, config.score_config)

    for _ in range(config.generations):
        offspring: list[str] = []
        while len(offspring) < config.population_size:
            parent_a, parent_b = rng.sample(population, 2)
            child = _crossover(parent_a, parent_b, rng, config.crossover_rate)
            child = _mutate(child, protein, rng, config.mutation_rate, terminal_stop)
            if config.enable_repair:
                child = repair_cds(child, protein, config.score_config, rng, terminal_stop, config.repair_passes)
            offspring.append(child)
        combined = _dedupe(population + offspring)
        population = _select_next_generation(combined, config.population_size, config.score_config)

    ranked = _select_next_generation(population, len(population), config.score_config)
    candidates: list[Candidate] = []
    seen: set[str] = set()
    for idx, cds in enumerate(ranked):
        if cds in seen:
            continue
        seen.add(cds)
        candidates.append(
            Candidate(
                candidate_id=f"cand_{idx + 1:03d}",
                cds=cds,
                protein=protein,
                scores=score_sequence(cds, config.score_config),
                rank=idx + 1,
            )
        )
        if len(candidates) >= config.max_candidates:
            break
    return candidates


def _terminal_stop(cds: str) -> str:
    codons = split_codons(cds)
    if codons and codons[-1] in STOP_CODONS:
        return codons[-1]
    return ""


def _seed_population(
    native_cds: str,
    protein: str,
    population_size: int,
    rng: random.Random,
    terminal_stop: str,
    score_config: ScoreConfig,
) -> list[str]:
    seeds = [native_cds]
    multipliers = dict(score_config.codon_weight_multipliers)
    availability = dict(score_config.codon_availability_weights)
    seeds.extend(_deterministic_seed_variants(protein, terminal_stop, multipliers, availability, score_config))
    while len(seeds) < population_size:
        seeds.append(_random_synonymous_cds(protein, rng, terminal_stop))
    return _dedupe(seeds)[:population_size]


def _deterministic_seed_variants(
    protein: str,
    terminal_stop: str,
    multipliers: dict[str, float],
    availability: dict[str, float],
    score_config: ScoreConfig,
) -> list[str]:
    variants = [
        _greedy_synonymous_cds(protein, terminal_stop, lambda codon, _idx, _prev: HUMAN_CODON_WEIGHTS.get(codon, 0.01) * multipliers.get(codon, 1.0)),
        _greedy_synonymous_cds(
            protein,
            terminal_stop,
            lambda codon, _idx, _prev: HUMAN_CODON_WEIGHTS.get(codon, 0.01) * multipliers.get(codon, 1.0) * availability.get(codon, 1.0),
        ),
        _greedy_synonymous_cds(protein, terminal_stop, lambda codon, _idx, _prev: -_gc_count(codon)),
        _greedy_synonymous_cds(protein, terminal_stop, lambda codon, _idx, _prev: _gc_count(codon)),
        _greedy_synonymous_cds(protein, terminal_stop, lambda codon, _idx, _prev: -abs((_gc_count(codon) / 3.0) - score_config.target_gc)),
        _greedy_synonymous_cds(protein, terminal_stop, lambda codon, _idx, prev: -(_cpg_count(codon) + (1 if prev == "C" and codon.startswith("G") else 0))),
    ]
    return _dedupe(variants)


def _greedy_synonymous_cds(
    protein: str,
    terminal_stop: str,
    objective: Callable[[str, int, str], float],
) -> str:
    codons: list[str] = []
    for idx, amino_acid in enumerate(protein):
        previous_base = codons[-1][-1] if codons else ""
        choices = SYNONYMOUS_CODONS[amino_acid]
        selected = max(choices, key=lambda codon: (objective(codon, idx, previous_base), HUMAN_CODON_WEIGHTS.get(codon, 0.01), codon))
        codons.append(selected)
    return "".join(codons) + terminal_stop


def _gc_count(codon: str) -> int:
    return codon.count("G") + codon.count("C")


def _cpg_count(codon: str) -> int:
    return sum(1 for idx in range(len(codon) - 1) if codon[idx : idx + 2] == "CG")


def _random_synonymous_cds(protein: str, rng: random.Random, terminal_stop: str) -> str:
    return "".join(rng.choice(SYNONYMOUS_CODONS[aa]) for aa in protein) + terminal_stop


def _crossover(parent_a: str, parent_b: str, rng: random.Random, rate: float) -> str:
    codons_a = split_codons(parent_a)
    codons_b = split_codons(parent_b)
    if len(codons_a) != len(codons_b) or rng.random() > rate or len(codons_a) < 3:
        return parent_a
    point = rng.randrange(1, len(codons_a) - 1)
    return "".join(codons_a[:point] + codons_b[point:])


def _mutate(
    cds: str,
    protein: str,
    rng: random.Random,
    mutation_rate: float,
    terminal_stop: str,
) -> str:
    codons = split_codons(cds)
    coding_len = len(protein)
    changed = False
    for idx in range(coding_len):
        if rng.random() < mutation_rate:
            codons[idx] = rng.choice(SYNONYMOUS_CODONS[protein[idx]])
            changed = True
    if terminal_stop:
        codons = codons[:coding_len] + [terminal_stop]
    return "".join(codons) if changed else cds


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _objectives(cds: str, config: ScoreConfig) -> tuple[float, ...]:
    scores = score_sequence(cds, config)
    return (
        -scores.cai,
        -scores.tissue_codon_adaptation,
        scores.gc_penalty,
        scores.cpg_density_per_100nt,
        float(scores.motif_violations),
        float(scores.polyadenylation_signal_count),
        float(scores.restriction_site_count),
        float(scores.cryptic_splice_motif_count),
        float(scores.splice_donor_motif_count),
        float(scores.splice_acceptor_motif_count),
        scores.sequence_policy_violation_score,
        float(scores.rare_codon_clusters),
        float(scores.codon_pair_risk),
        scores.gc_window_max_deviation,
        scores.five_prime_gc_deviation,
        scores.hairpin_proxy_score,
        scores.secondary_structure_proxy_score,
        scores.low_complexity_penalty,
        float(max(scores.longest_homopolymer - 5, 0)),
    )


def _dominates(left: tuple[float, ...], right: tuple[float, ...]) -> bool:
    return all(a <= b for a, b in zip(left, right)) and any(a < b for a, b in zip(left, right))


def _fast_non_dominated_sort(items: list[str], config: ScoreConfig) -> list[list[str]]:
    objectives = {item: _objectives(item, config) for item in items}
    dominates: dict[str, list[str]] = {item: [] for item in items}
    dominated_count: dict[str, int] = {item: 0 for item in items}
    fronts: list[list[str]] = [[]]

    for item in items:
        for other in items:
            if item == other:
                continue
            if _dominates(objectives[item], objectives[other]):
                dominates[item].append(other)
            elif _dominates(objectives[other], objectives[item]):
                dominated_count[item] += 1
        if dominated_count[item] == 0:
            fronts[0].append(item)

    idx = 0
    while idx < len(fronts) and fronts[idx]:
        next_front: list[str] = []
        for item in fronts[idx]:
            for other in dominates[item]:
                dominated_count[other] -= 1
                if dominated_count[other] == 0:
                    next_front.append(other)
        if next_front:
            fronts.append(next_front)
        idx += 1
    return fronts


def _crowding_distance(front: list[str], config: ScoreConfig) -> dict[str, float]:
    if not front:
        return {}
    distances = {item: 0.0 for item in front}
    objective_map = {item: _objectives(item, config) for item in front}
    objective_count = len(next(iter(objective_map.values())))

    for objective_idx in range(objective_count):
        ordered = sorted(front, key=lambda item: objective_map[item][objective_idx])
        distances[ordered[0]] = distances[ordered[-1]] = float("inf")
        min_value = objective_map[ordered[0]][objective_idx]
        max_value = objective_map[ordered[-1]][objective_idx]
        if max_value == min_value:
            continue
        for pos in range(1, len(ordered) - 1):
            prev_value = objective_map[ordered[pos - 1]][objective_idx]
            next_value = objective_map[ordered[pos + 1]][objective_idx]
            distances[ordered[pos]] += (next_value - prev_value) / (max_value - min_value)
    return distances


def _select_next_generation(items: list[str], size: int, config: ScoreConfig) -> list[str]:
    selected: list[str] = []
    for front in _fast_non_dominated_sort(_dedupe(items), config):
        if len(selected) + len(front) <= size:
            selected.extend(front)
            continue
        distances = _crowding_distance(front, config)
        selected.extend(sorted(front, key=lambda item: distances[item], reverse=True)[: size - len(selected)])
        break
    return selected
