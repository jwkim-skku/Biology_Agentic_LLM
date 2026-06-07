from __future__ import annotations

from typing import Any

from app.optimizer.codon_table import STOP_CODONS, normalize_dna, split_codons, translate
from app.optimizer.scoring import ScoreConfig, gc_fraction, gc_window_summary, longest_homopolymer, motif_count, motif_violations
from app.services.sequence_policy_service import audit_sequence_policy


def validate_cds(cds: str, config: ScoreConfig | None = None) -> dict[str, Any]:
    config = config or ScoreConfig()
    normalized = normalize_dna(cds)
    checks: list[dict[str, Any]] = []
    codons: list[str] = []
    protein = ""

    _add_check(checks, "non_empty", bool(normalized), "error", "CDS sequence is present.")
    invalid = sorted(set(normalized) - {"A", "C", "G", "T"})
    _add_check(checks, "valid_bases", not invalid, "error", f"Invalid DNA bases: {', '.join(invalid)}" if invalid else "CDS contains only A/C/G/T bases.")
    _add_check(checks, "codon_frame", len(normalized) % 3 == 0, "error", "CDS length is divisible by 3.")

    if normalized and not invalid and len(normalized) % 3 == 0:
        try:
            codons = split_codons(normalized)
            protein = translate(normalized)
        except ValueError as exc:
            _add_check(checks, "translation", False, "error", str(exc))
        else:
            _add_check(checks, "start_codon", bool(codons and codons[0] == "ATG"), "warning", "CDS starts with ATG.")
            _add_check(checks, "terminal_stop", bool(codons and codons[-1] in STOP_CODONS), "warning", "CDS ends with a terminal stop codon.")
            internal_stops = [idx + 1 for idx, codon in enumerate(codons[:-1]) if codon in STOP_CODONS]
            _add_check(
                checks,
                "internal_stop_codons",
                not internal_stops,
                "error",
                f"Internal stop codons at codon positions {internal_stops}." if internal_stops else "No internal stop codons.",
            )
            _add_check(checks, "protein_length", len(protein) > 0, "error", f"Protein length is {len(protein)} aa.")

    if normalized:
        gc = gc_fraction(normalized)
        window_min, window_max, window_deviation = gc_window_summary(normalized, config.gc_window_size_nt, config.target_gc)
        forbidden = motif_violations(normalized, config.forbidden_motifs)
        polyadenylation = motif_count(normalized, config.polyadenylation_signals)
        restriction = motif_count(normalized, config.restriction_sites)
        splice = motif_count(normalized, config.cryptic_splice_motifs)
        splice_donor = motif_count(normalized, config.splice_donor_motifs)
        splice_acceptor = motif_count(normalized, config.splice_acceptor_motifs)
        homopolymer = longest_homopolymer(normalized)
        _add_check(checks, "aav_payload_budget", len(normalized) <= config.aav_payload_limit_nt, "error", f"CDS length is {len(normalized)} nt.")
        _add_check(checks, "global_gc_range", config.gc_min <= gc <= config.gc_max, "warning", f"Global GC fraction is {gc:.4f}.")
        _add_check(checks, "local_gc_window", window_deviation <= 0.20, "warning", f"Local GC window range is {window_min:.4f}-{window_max:.4f}.")
        _add_check(checks, "forbidden_motifs", forbidden == 0, "error", f"Forbidden motif matches: {forbidden}.")
        _add_check(checks, "polyadenylation_signal_proxy", polyadenylation == 0, "error", f"Polyadenylation signal proxy matches: {polyadenylation}.")
        _add_check(checks, "restriction_sites", restriction == 0, "warning", f"Restriction-site matches: {restriction}.")
        _add_check(checks, "cryptic_splice_proxy", splice == 0, "warning", f"Cryptic splice proxy matches: {splice}.")
        _add_check(checks, "splice_donor_proxy", splice_donor == 0, "warning", f"Splice donor proxy matches: {splice_donor}.")
        _add_check(checks, "splice_acceptor_proxy", splice_acceptor == 0, "warning", f"Splice acceptor proxy matches: {splice_acceptor}.")
        _add_check(checks, "homopolymer_run", homopolymer <= 8, "warning", f"Longest homopolymer run is {homopolymer}.")
        policy_audit = audit_sequence_policy(normalized, config)
    else:
        policy_audit = audit_sequence_policy("", config)

    return {
        "status": _overall_status(checks),
        "errors": sum(1 for check in checks if check["result"] == "fail" and check["severity"] == "error"),
        "warnings": sum(1 for check in checks if check["result"] == "fail" and check["severity"] == "warning"),
        "normalized_cds": normalized,
        "length_nt": len(normalized),
        "protein_length_aa": len(protein) if protein else None,
        "terminal_stop": codons[-1] if codons and codons[-1] in STOP_CODONS else None,
        "sequence_policy": policy_audit,
        "checks": checks,
    }


def qc_gate_for_design(design: dict[str, Any]) -> dict[str, Any]:
    native = design.get("native") or {}
    recommended = design.get("recommended_candidate") or {}
    candidates = design.get("candidates") or []
    recommended_scores = recommended.get("scores") or {}
    native_protein = native.get("protein")
    checks: list[dict[str, Any]] = []

    _add_check(checks, "recommended_candidate_present", bool(recommended), "error", "Recommended candidate is present.")
    _add_check(
        checks,
        "protein_preserved_all_candidates",
        bool(candidates) and all(candidate.get("protein") == native_protein for candidate in candidates),
        "error",
        "All candidates preserve the native protein sequence.",
    )
    _add_check(checks, "aav_budget_pass", bool(recommended_scores.get("aav_budget_pass")), "error", "Recommended candidate passes payload budget.")
    _add_check(checks, "forbidden_motifs", recommended_scores.get("motif_violations", 1) == 0, "error", "Recommended candidate has no forbidden motif matches.")
    _add_check(checks, "polyadenylation_signal_proxy", recommended_scores.get("polyadenylation_signal_count", 1) == 0, "error", "Recommended candidate has no internal polyadenylation signal proxy matches.")
    _add_check(checks, "restriction_sites", recommended_scores.get("restriction_site_count", 1) == 0, "warning", "Recommended candidate has no configured restriction-site matches.")
    _add_check(checks, "cryptic_splice_proxy", recommended_scores.get("cryptic_splice_motif_count", 1) == 0, "warning", "Recommended candidate has no cryptic splice proxy matches.")
    _add_check(checks, "splice_donor_proxy", recommended_scores.get("splice_donor_motif_count", 1) == 0, "warning", "Recommended candidate has no splice donor proxy matches.")
    _add_check(checks, "splice_acceptor_proxy", recommended_scores.get("splice_acceptor_motif_count", 1) == 0, "warning", "Recommended candidate has no splice acceptor proxy matches.")
    _add_check(checks, "local_gc_window", recommended_scores.get("gc_window_max_deviation", 1.0) <= 0.20, "warning", "Recommended candidate local GC window deviation is within conservative bound.")
    _add_check(checks, "evidence_attached", bool(design.get("evidence")), "warning", "Evidence context is attached to the design.")
    _add_check(checks, "provenance_attached", bool(design.get("provenance")), "error", "Provenance is attached to the design.")

    return {
        "status": _overall_status(checks),
        "errors": sum(1 for check in checks if check["result"] == "fail" and check["severity"] == "error"),
        "warnings": sum(1 for check in checks if check["result"] == "fail" and check["severity"] == "warning"),
        "checks": checks,
    }


def _add_check(checks: list[dict[str, Any]], check_id: str, passed: bool, severity: str, message: str) -> None:
    checks.append({"id": check_id, "result": "pass" if passed else "fail", "severity": severity, "message": message})


def _overall_status(checks: list[dict[str, Any]]) -> str:
    if any(check["result"] == "fail" and check["severity"] == "error" for check in checks):
        return "fail"
    if any(check["result"] == "fail" for check in checks):
        return "warning"
    return "pass"
