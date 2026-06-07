from __future__ import annotations

import random

from app.optimizer.codon_table import SYNONYMOUS_CODONS, split_codons, translate
from app.optimizer.scoring import ScoreConfig, calculate_cai, cpg_count, gc_fraction, longest_homopolymer, motif_violations


def repair_cds(
    cds: str,
    protein: str,
    config: ScoreConfig,
    rng: random.Random,
    terminal_stop: str = "",
    max_passes: int = 3,
) -> str:
    """Repair constraint-heavy synonymous candidates without changing protein sequence."""
    codons = split_codons(cds)
    coding_len = len(protein)
    codons = codons[:coding_len] + ([terminal_stop] if terminal_stop else [])

    for _ in range(max(0, max_passes)):
        before = _repair_cost("".join(codons), config)
        changed = False
        indices = list(range(coding_len))
        rng.shuffle(indices)

        for idx in indices:
            amino_acid = protein[idx]
            current = codons[idx]
            alternatives = [codon for codon in SYNONYMOUS_CODONS[amino_acid] if codon != current]
            if not alternatives:
                continue

            best_codons = codons
            best_cost = _repair_cost("".join(codons), config)
            for alternative in alternatives:
                trial = list(codons)
                trial[idx] = alternative
                trial_cds = "".join(trial)
                if translate(trial_cds) != protein:
                    continue
                trial_cost = _repair_cost(trial_cds, config)
                if trial_cost < best_cost:
                    best_cost = trial_cost
                    best_codons = trial

            if best_codons is not codons:
                codons = best_codons
                changed = True

        after = _repair_cost("".join(codons), config)
        if not changed or after >= before:
            break

    repaired = "".join(codons)
    if translate(repaired) != protein:
        return cds
    return repaired


def _repair_cost(cds: str, config: ScoreConfig) -> tuple[float, float, float, float, float, float]:
    gc = gc_fraction(cds)
    gc_window_violation = 0.0 if config.gc_min <= gc <= config.gc_max else 1.0
    homopolymer_overage = max(longest_homopolymer(cds) - 5, 0)
    return (
        float(motif_violations(cds, config.forbidden_motifs)),
        gc_window_violation,
        abs(gc - config.target_gc),
        cpg_count(cds) / max(len(cds), 1),
        float(homopolymer_overage),
        -calculate_cai(cds),
    )
