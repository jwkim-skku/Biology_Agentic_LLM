from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from app.optimizer.codon_table import HUMAN_CODON_WEIGHTS, normalize_dna, split_codons


DEFAULT_FORBIDDEN_MOTIFS = (
    "AATAAA",
    "ATTAAA",
    "GGGGGG",
    "CCCCCC",
    "TTTTTT",
    "AAAAAA",
)

DEFAULT_POLYADENYLATION_SIGNALS = (
    "AATAAA",
    "ATTAAA",
    "AGTAAA",
    "TATAAA",
    "CATAAA",
    "GATAAA",
)

DEFAULT_RESTRICTION_SITES = (
    "GAATTC",  # EcoRI
    "GGATCC",  # BamHI
    "AAGCTT",  # HindIII
    "GCGGCCGC",  # NotI
    "TCTAGA",  # XbaI
    "ACTAGT",  # SpeI
)

DEFAULT_CRYPTIC_SPLICE_MOTIFS = (
    "CAGG",
    "GTAGT",
    "GTAAGT",
    "TTTTCAG",
)

DEFAULT_SPLICE_DONOR_MOTIFS = (
    "GTAAGT",
    "GTGAGT",
    "GTATGT",
)

DEFAULT_SPLICE_ACCEPTOR_MOTIFS = (
    "TTTTCAG",
    "CTTTCAG",
    "TCTTCAG",
)


@dataclass(frozen=True)
class ScoreConfig:
    gc_min: float = 0.40
    gc_max: float = 0.65
    target_gc: float = 0.55
    aav_payload_limit_nt: int = 4300
    forbidden_motifs: tuple[str, ...] = DEFAULT_FORBIDDEN_MOTIFS
    polyadenylation_signals: tuple[str, ...] = DEFAULT_POLYADENYLATION_SIGNALS
    restriction_sites: tuple[str, ...] = DEFAULT_RESTRICTION_SITES
    cryptic_splice_motifs: tuple[str, ...] = DEFAULT_CRYPTIC_SPLICE_MOTIFS
    splice_donor_motifs: tuple[str, ...] = DEFAULT_SPLICE_DONOR_MOTIFS
    splice_acceptor_motifs: tuple[str, ...] = DEFAULT_SPLICE_ACCEPTOR_MOTIFS
    gc_window_size_nt: int = 60
    codon_weight_multipliers: tuple[tuple[str, float], ...] = ()
    codon_availability_weights: tuple[tuple[str, float], ...] = ()

    def to_dict(self) -> dict:
        data = asdict(self)
        data["forbidden_motifs"] = list(self.forbidden_motifs)
        data["polyadenylation_signals"] = list(self.polyadenylation_signals)
        data["restriction_sites"] = list(self.restriction_sites)
        data["cryptic_splice_motifs"] = list(self.cryptic_splice_motifs)
        data["splice_donor_motifs"] = list(self.splice_donor_motifs)
        data["splice_acceptor_motifs"] = list(self.splice_acceptor_motifs)
        data["codon_weight_multipliers"] = dict(self.codon_weight_multipliers)
        data["codon_availability_weights"] = dict(self.codon_availability_weights)
        return data


@dataclass(frozen=True)
class SequenceScores:
    cai: float
    gc_fraction: float
    gc_penalty: float
    cpg_count: int
    cpg_density_per_100nt: float
    motif_violations: int
    polyadenylation_signal_count: int
    restriction_site_count: int
    cryptic_splice_motif_count: int
    splice_donor_motif_count: int
    splice_acceptor_motif_count: int
    sequence_policy_violation_score: float
    longest_homopolymer: int
    gc_window_min: float
    gc_window_max: float
    gc_window_max_deviation: float
    tissue_codon_adaptation: float
    rare_codon_clusters: int
    codon_pair_risk: int
    five_prime_gc_fraction: float
    five_prime_gc_deviation: float
    hairpin_proxy_score: float
    secondary_structure_proxy_score: float
    mfe_proxy_delta_g: float
    sequence_complexity: float
    low_complexity_penalty: float
    length_nt: int
    aav_budget_pass: bool
    composite_quality: float

    def to_dict(self) -> dict:
        return asdict(self)


def calculate_cai(cds: str, weight_multipliers: dict[str, float] | None = None) -> float:
    codons = [codon for codon in split_codons(cds) if codon not in {"TAA", "TAG", "TGA"}]
    if not codons:
        return 0.0
    multipliers = weight_multipliers or {}
    log_sum = sum(
        math.log(max(HUMAN_CODON_WEIGHTS.get(codon, 0.01) * multipliers.get(codon, 1.0), 0.01))
        for codon in codons
    )
    return round(math.exp(log_sum / len(codons)), 4)


def tissue_codon_adaptation(cds: str, availability_weights: dict[str, float] | None = None) -> float:
    codons = [codon for codon in split_codons(cds) if codon not in {"TAA", "TAG", "TGA"}]
    weights = availability_weights or {}
    if not codons or not weights:
        return 0.0
    log_sum = sum(math.log(max(weights.get(codon, 1.0), 0.01)) for codon in codons)
    return round(math.exp(log_sum / len(codons)), 4)


def rare_codon_clusters(cds: str, availability_weights: dict[str, float] | None = None, threshold: float = 0.90) -> int:
    codons = [codon for codon in split_codons(cds) if codon not in {"TAA", "TAG", "TGA"}]
    weights = availability_weights or {}
    if not codons or not weights:
        return 0
    clusters = 0
    run = 0
    for codon in codons:
        if weights.get(codon, 1.0) < threshold:
            run += 1
            if run == 3:
                clusters += 1
        else:
            run = 0
    return clusters


def gc_fraction(cds: str) -> float:
    sequence = normalize_dna(cds)
    if not sequence:
        return 0.0
    return round((sequence.count("G") + sequence.count("C")) / len(sequence), 4)


def gc_penalty(value: float, config: ScoreConfig) -> float:
    if config.gc_min <= value <= config.gc_max:
        return round(abs(value - config.target_gc), 4)
    if value < config.gc_min:
        return round(config.gc_min - value, 4)
    return round(value - config.gc_max, 4)


def cpg_count(cds: str) -> int:
    sequence = normalize_dna(cds)
    return sum(1 for idx in range(len(sequence) - 1) if sequence[idx : idx + 2] == "CG")


def motif_violations(cds: str, motifs: tuple[str, ...]) -> int:
    sequence = normalize_dna(cds)
    return sum(sequence.count(motif.upper()) for motif in motifs)


def motif_count(cds: str, motifs: tuple[str, ...]) -> int:
    return motif_violations(cds, motifs)


def motif_positions(cds: str, motifs: tuple[str, ...]) -> dict[str, list[int]]:
    sequence = normalize_dna(cds)
    output: dict[str, list[int]] = {}
    for motif in motifs:
        normalized_motif = motif.upper()
        positions: list[int] = []
        start = 0
        while normalized_motif:
            idx = sequence.find(normalized_motif, start)
            if idx == -1:
                break
            positions.append(idx + 1)
            start = idx + 1
        if positions:
            output[normalized_motif] = positions
    return output


def sequence_policy_violation_score(
    polyadenylation_signals: int,
    restriction_sites: int,
    splice_donor_motifs: int,
    splice_acceptor_motifs: int,
    forbidden_motifs: int,
) -> float:
    score = (
        polyadenylation_signals * 1.0
        + restriction_sites * 0.70
        + (splice_donor_motifs + splice_acceptor_motifs) * 0.50
        + forbidden_motifs * 0.35
    )
    return round(score, 4)


def gc_window_summary(cds: str, window_size_nt: int, target_gc: float) -> tuple[float, float, float]:
    sequence = normalize_dna(cds)
    if not sequence:
        return 0.0, 0.0, 0.0
    window = max(3, min(window_size_nt, len(sequence)))
    step = 3 if window >= 9 else 1
    values = [gc_fraction(sequence[idx : idx + window]) for idx in range(0, len(sequence) - window + 1, step)]
    if not values:
        values = [gc_fraction(sequence)]
    minimum = min(values)
    maximum = max(values)
    deviation = max(abs(minimum - target_gc), abs(maximum - target_gc))
    return round(minimum, 4), round(maximum, 4), round(deviation, 4)


def longest_homopolymer(cds: str) -> int:
    sequence = normalize_dna(cds)
    if not sequence:
        return 0
    longest = current = 1
    for left, right in zip(sequence, sequence[1:]):
        current = current + 1 if left == right else 1
        longest = max(longest, current)
    return longest


def codon_pair_risk(cds: str) -> int:
    codons = [codon for codon in split_codons(cds) if codon not in {"TAA", "TAG", "TGA"}]
    risk = 0
    for left, right in zip(codons, codons[1:]):
        boundary = left[-1] + right[0]
        if boundary == "CG":
            risk += 1
        if HUMAN_CODON_WEIGHTS.get(left, 1.0) < 0.55 and HUMAN_CODON_WEIGHTS.get(right, 1.0) < 0.55:
            risk += 1
        if left == right and HUMAN_CODON_WEIGHTS.get(left, 1.0) < 0.70:
            risk += 1
    return risk


def five_prime_gc_summary(cds: str, target_gc: float, window_nt: int = 90) -> tuple[float, float]:
    sequence = normalize_dna(cds)
    if not sequence:
        return 0.0, 0.0
    window = sequence[: max(3, min(window_nt, len(sequence)))]
    value = gc_fraction(window)
    return value, round(abs(value - target_gc), 4)


def hairpin_proxy_score(cds: str, window_nt: int = 120) -> float:
    sequence = normalize_dna(cds)[:window_nt]
    if len(sequence) < 12:
        return 0.0
    hits = 0
    windows = 0
    for kmer_size in (4, 5, 6):
        for idx in range(0, len(sequence) - kmer_size + 1):
            kmer = sequence[idx : idx + kmer_size]
            downstream = sequence[idx + kmer_size + 3 :]
            windows += 1
            if _reverse_complement(kmer) in downstream:
                hits += 1
    return round(min(hits / max(windows, 1) * 4.0, 1.0), 4)


def secondary_structure_proxy(cds: str, window_nt: int = 180) -> tuple[float, float]:
    sequence = normalize_dna(cds)[:window_nt].replace("T", "U")
    if len(sequence) < 18:
        return 0.0, 0.0
    best_energy = 0.0
    best_pairs = 0
    stem_lengths = range(4, 9)
    loop_lengths = range(3, 31)
    for left in range(len(sequence)):
        for stem_len in stem_lengths:
            left_stem = sequence[left : left + stem_len]
            if len(left_stem) < stem_len:
                continue
            for loop_len in loop_lengths:
                right_start = left + stem_len + loop_len
                right_stem = sequence[right_start : right_start + stem_len]
                if len(right_stem) < stem_len:
                    continue
                energy, pairs = _stem_energy(left_stem, right_stem)
                if energy < best_energy:
                    best_energy = energy
                    best_pairs = pairs
    normalized = min(abs(best_energy) / 18.0, 1.0)
    pair_bonus = min(best_pairs / 8.0, 1.0)
    score = (normalized * 0.75) + (pair_bonus * 0.25 if best_energy < 0 else 0.0)
    return round(score, 4), round(best_energy, 4)


def sequence_complexity(cds: str, kmer_size: int = 4) -> float:
    sequence = normalize_dna(cds)
    if len(sequence) < kmer_size:
        return 1.0 if sequence else 0.0
    kmers = [sequence[idx : idx + kmer_size] for idx in range(0, len(sequence) - kmer_size + 1)]
    return round(len(set(kmers)) / len(kmers), 4)


def score_sequence(cds: str, config: ScoreConfig | None = None) -> SequenceScores:
    config = config or ScoreConfig()
    sequence = normalize_dna(cds)
    gc = gc_fraction(sequence)
    cpg = cpg_count(sequence)
    motifs = motif_violations(sequence, config.forbidden_motifs)
    polyadenylation_signals = motif_count(sequence, config.polyadenylation_signals)
    restriction_sites = motif_count(sequence, config.restriction_sites)
    splice_motifs = motif_count(sequence, config.cryptic_splice_motifs)
    splice_donors = motif_count(sequence, config.splice_donor_motifs)
    splice_acceptors = motif_count(sequence, config.splice_acceptor_motifs)
    policy_score = sequence_policy_violation_score(
        polyadenylation_signals,
        restriction_sites,
        splice_donors,
        splice_acceptors,
        motifs,
    )
    homopolymer = longest_homopolymer(sequence)
    window_min, window_max, window_deviation = gc_window_summary(sequence, config.gc_window_size_nt, config.target_gc)
    cai = calculate_cai(sequence, dict(config.codon_weight_multipliers))
    tissue_adaptation = tissue_codon_adaptation(sequence, dict(config.codon_availability_weights))
    rare_clusters = rare_codon_clusters(sequence, dict(config.codon_availability_weights))
    pair_risk = codon_pair_risk(sequence)
    five_prime_gc, five_prime_deviation = five_prime_gc_summary(sequence, config.target_gc)
    hairpin_risk = hairpin_proxy_score(sequence)
    structure_risk, mfe_delta = secondary_structure_proxy(sequence)
    complexity = sequence_complexity(sequence)
    low_complexity = round(1.0 - complexity, 4)
    cpg_density = round(cpg / max(len(sequence), 1) * 100, 4)
    gc_cost = gc_penalty(gc, config)
    aav_pass = len(sequence) <= config.aav_payload_limit_nt
    composite = (
        cai
        - gc_cost * 0.75
        - cpg_density * 0.05
        - motifs * 0.10
        - polyadenylation_signals * 0.18
        - restriction_sites * 0.08
        - splice_motifs * 0.03
        - (splice_donors + splice_acceptors) * 0.05
        - policy_score * 0.04
        - max(homopolymer - 5, 0) * 0.03
        - window_deviation * 0.20
        + (tissue_adaptation - 1.0) * 0.40
        - rare_clusters * 0.05
        - pair_risk * 0.025
        - five_prime_deviation * 0.20
        - hairpin_risk * 0.08
        - structure_risk * 0.10
        - low_complexity * 0.08
        + (0.05 if aav_pass else -0.20)
    )
    return SequenceScores(
        cai=cai,
        gc_fraction=gc,
        gc_penalty=gc_cost,
        cpg_count=cpg,
        cpg_density_per_100nt=cpg_density,
        motif_violations=motifs,
        polyadenylation_signal_count=polyadenylation_signals,
        restriction_site_count=restriction_sites,
        cryptic_splice_motif_count=splice_motifs,
        splice_donor_motif_count=splice_donors,
        splice_acceptor_motif_count=splice_acceptors,
        sequence_policy_violation_score=policy_score,
        longest_homopolymer=homopolymer,
        gc_window_min=window_min,
        gc_window_max=window_max,
        gc_window_max_deviation=window_deviation,
        tissue_codon_adaptation=tissue_adaptation,
        rare_codon_clusters=rare_clusters,
        codon_pair_risk=pair_risk,
        five_prime_gc_fraction=five_prime_gc,
        five_prime_gc_deviation=five_prime_deviation,
        hairpin_proxy_score=hairpin_risk,
        secondary_structure_proxy_score=structure_risk,
        mfe_proxy_delta_g=mfe_delta,
        sequence_complexity=complexity,
        low_complexity_penalty=low_complexity,
        length_nt=len(sequence),
        aav_budget_pass=aav_pass,
        composite_quality=round(composite, 4),
    )


def _reverse_complement(sequence: str) -> str:
    return sequence.translate(str.maketrans("ACGT", "TGCA"))[::-1]


def _stem_energy(left_stem: str, right_stem: str) -> tuple[float, int]:
    energy = 0.0
    pairs = 0
    for left_base, right_base in zip(left_stem, reversed(right_stem)):
        pair = left_base + right_base
        if pair in {"GC", "CG"}:
            energy -= 3.0
            pairs += 1
        elif pair in {"AU", "UA"}:
            energy -= 2.0
            pairs += 1
        elif pair in {"GU", "UG"}:
            energy -= 1.0
            pairs += 1
        else:
            energy += 0.5
    if pairs < 4:
        return 0.0, pairs
    return energy, pairs
