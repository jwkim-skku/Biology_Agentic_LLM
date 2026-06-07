from __future__ import annotations

from dataclasses import dataclass


DNA_CODON_TABLE: dict[str, str] = {
    "TTT": "F",
    "TTC": "F",
    "TTA": "L",
    "TTG": "L",
    "TCT": "S",
    "TCC": "S",
    "TCA": "S",
    "TCG": "S",
    "TAT": "Y",
    "TAC": "Y",
    "TAA": "*",
    "TAG": "*",
    "TGT": "C",
    "TGC": "C",
    "TGA": "*",
    "TGG": "W",
    "CTT": "L",
    "CTC": "L",
    "CTA": "L",
    "CTG": "L",
    "CCT": "P",
    "CCC": "P",
    "CCA": "P",
    "CCG": "P",
    "CAT": "H",
    "CAC": "H",
    "CAA": "Q",
    "CAG": "Q",
    "CGT": "R",
    "CGC": "R",
    "CGA": "R",
    "CGG": "R",
    "ATT": "I",
    "ATC": "I",
    "ATA": "I",
    "ATG": "M",
    "ACT": "T",
    "ACC": "T",
    "ACA": "T",
    "ACG": "T",
    "AAT": "N",
    "AAC": "N",
    "AAA": "K",
    "AAG": "K",
    "AGT": "S",
    "AGC": "S",
    "AGA": "R",
    "AGG": "R",
    "GTT": "V",
    "GTC": "V",
    "GTA": "V",
    "GTG": "V",
    "GCT": "A",
    "GCC": "A",
    "GCA": "A",
    "GCG": "A",
    "GAT": "D",
    "GAC": "D",
    "GAA": "E",
    "GAG": "E",
    "GGT": "G",
    "GGC": "G",
    "GGA": "G",
    "GGG": "G",
}


STOP_CODONS = {"TAA", "TAG", "TGA"}


@dataclass(frozen=True)
class CodonChoice:
    codon: str
    amino_acid: str
    weight: float


def normalize_dna(sequence: str) -> str:
    return "".join(sequence.upper().replace("U", "T").split())


def split_codons(cds: str) -> list[str]:
    normalized = normalize_dna(cds)
    if len(normalized) % 3 != 0:
        raise ValueError("CDS length must be divisible by 3.")
    invalid = sorted(set(normalized) - {"A", "C", "G", "T"})
    if invalid:
        raise ValueError(f"CDS contains invalid DNA bases: {', '.join(invalid)}")
    return [normalized[i : i + 3] for i in range(0, len(normalized), 3)]


def translate(cds: str, *, trim_terminal_stop: bool = True) -> str:
    amino_acids = [DNA_CODON_TABLE[codon] for codon in split_codons(cds)]
    if trim_terminal_stop and amino_acids and amino_acids[-1] == "*":
        amino_acids.pop()
    if "*" in amino_acids:
        raise ValueError("CDS contains an internal stop codon.")
    return "".join(amino_acids)


def _build_synonymous_codons() -> dict[str, list[str]]:
    synonyms: dict[str, list[str]] = {}
    for codon, amino_acid in DNA_CODON_TABLE.items():
        synonyms.setdefault(amino_acid, []).append(codon)
    return {aa: sorted(codons) for aa, codons in synonyms.items()}


SYNONYMOUS_CODONS = _build_synonymous_codons()


# Human codon adaptiveness weights, kept intentionally compact for MVP scoring.
# They are not a replacement for a release-pinned codon-usage resource.
HUMAN_CODON_WEIGHTS: dict[str, float] = {
    "GCT": 0.74,
    "GCC": 1.00,
    "GCA": 0.59,
    "GCG": 0.28,
    "TGT": 0.45,
    "TGC": 1.00,
    "GAT": 0.79,
    "GAC": 1.00,
    "GAA": 0.70,
    "GAG": 1.00,
    "TTT": 0.77,
    "TTC": 1.00,
    "GGT": 0.44,
    "GGC": 1.00,
    "GGA": 0.66,
    "GGG": 0.66,
    "CAT": 0.69,
    "CAC": 1.00,
    "ATT": 0.74,
    "ATC": 1.00,
    "ATA": 0.40,
    "AAA": 0.76,
    "AAG": 1.00,
    "TTA": 0.20,
    "TTG": 0.32,
    "CTT": 0.40,
    "CTC": 0.50,
    "CTA": 0.23,
    "CTG": 1.00,
    "ATG": 1.00,
    "AAT": 0.88,
    "AAC": 1.00,
    "CCT": 0.72,
    "CCC": 0.74,
    "CCA": 0.79,
    "CCG": 0.27,
    "CAA": 0.34,
    "CAG": 1.00,
    "CGT": 0.36,
    "CGC": 0.68,
    "CGA": 0.24,
    "CGG": 0.45,
    "AGA": 0.74,
    "AGG": 0.75,
    "TCT": 0.71,
    "TCC": 0.88,
    "TCA": 0.61,
    "TCG": 0.19,
    "AGT": 0.55,
    "AGC": 1.00,
    "ACT": 0.72,
    "ACC": 1.00,
    "ACA": 0.75,
    "ACG": 0.28,
    "GTT": 0.47,
    "GTC": 0.59,
    "GTA": 0.30,
    "GTG": 1.00,
    "TGG": 1.00,
    "TAT": 0.74,
    "TAC": 1.00,
    "TAA": 0.30,
    "TAG": 0.24,
    "TGA": 1.00,
}


def preferred_codon(amino_acid: str, weight_multipliers: dict[str, float] | None = None) -> str:
    multipliers = weight_multipliers or {}
    codons = SYNONYMOUS_CODONS[amino_acid]
    return max(codons, key=lambda codon: HUMAN_CODON_WEIGHTS.get(codon, 0.01) * multipliers.get(codon, 1.0))
