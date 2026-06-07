from __future__ import annotations

from typing import Any

from app.optimizer.codon_table import normalize_dna
from app.optimizer.scoring import ScoreConfig, motif_positions, sequence_policy_violation_score


def audit_sequence_policy(cds: str, config: ScoreConfig | None = None) -> dict[str, Any]:
    config = config or ScoreConfig()
    sequence = normalize_dna(cds)
    finding_groups = [
        (
            "forbidden_motif",
            "error",
            "Configured forbidden motif match.",
            config.forbidden_motifs,
        ),
        (
            "polyadenylation_signal",
            "error",
            "Internal polyadenylation signal proxy match.",
            config.polyadenylation_signals,
        ),
        (
            "restriction_site",
            "warning",
            "Configured restriction-enzyme recognition site match.",
            config.restriction_sites,
        ),
        (
            "splice_donor_proxy",
            "warning",
            "Cryptic splice donor proxy match.",
            config.splice_donor_motifs,
        ),
        (
            "splice_acceptor_proxy",
            "warning",
            "Cryptic splice acceptor proxy match.",
            config.splice_acceptor_motifs,
        ),
        (
            "cryptic_splice_proxy",
            "warning",
            "Configured cryptic splice motif proxy match.",
            config.cryptic_splice_motifs,
        ),
    ]
    findings: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for category, severity, description, motifs in finding_groups:
        positions_by_motif = motif_positions(sequence, tuple(motifs))
        count = sum(len(positions) for positions in positions_by_motif.values())
        counts[category] = count
        for motif, positions in positions_by_motif.items():
            findings.append(
                {
                    "category": category,
                    "severity": severity,
                    "motif": motif,
                    "count": len(positions),
                    "positions_1based": positions[:20],
                    "truncated_positions": len(positions) > 20,
                    "description": description,
                    "recommended_action": _recommended_action(category),
                }
            )

    error_count = sum(item["count"] for item in findings if item["severity"] == "error")
    warning_count = sum(item["count"] for item in findings if item["severity"] == "warning")
    policy_score = sequence_policy_violation_score(
        counts.get("polyadenylation_signal", 0),
        counts.get("restriction_site", 0),
        counts.get("splice_donor_proxy", 0),
        counts.get("splice_acceptor_proxy", 0),
        counts.get("forbidden_motif", 0),
    )
    return {
        "status": "fail" if error_count else "warning" if warning_count else "pass",
        "length_nt": len(sequence),
        "summary": {
            "errors": error_count,
            "warnings": warning_count,
            "policy_violation_score": policy_score,
            **counts,
        },
        "findings": findings,
    }


def _recommended_action(category: str) -> str:
    if category == "polyadenylation_signal":
        return "Prefer synonymous edits that remove internal polyadenylation-like hexamers."
    if category == "restriction_site":
        return "Review enzyme policy for cloning/manufacturing; remove sites that conflict with the intended workflow."
    if category in {"splice_donor_proxy", "splice_acceptor_proxy", "cryptic_splice_proxy"}:
        return "Treat as a splice-screening proxy and confirm with a dedicated splicing model before production use."
    return "Prefer synonymous edits that remove the configured motif while preserving the protein sequence."
