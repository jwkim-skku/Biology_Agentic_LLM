from __future__ import annotations

import json
from html import escape
from typing import Any

from app.optimizer.scoring import ScoreConfig
from app.services.optimizer_stress_service import optimizer_stress_gate
from app.services.optimizer_reproducibility_service import optimizer_reproducibility_manifest
from app.services.sequence_policy_service import audit_sequence_policy
from app.services.structured_quality_service import structured_quality_gate


def synthesize_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    coverage = evidence.get("coverage", {})
    records = evidence.get("records", [])
    titles = {record.get("title", "") for record in records}
    collections = {record.get("collection", "") for record in records}

    supported_rules: list[dict[str, Any]] = []
    uncertain_rules: list[dict[str, Any]] = []
    rejected_rules: list[dict[str, Any]] = []

    if coverage.get("transcript") == "MANE Select":
        supported_rules.append(
            _rule(
                "Use MANE Select CDS as the transcript anchor.",
                "high",
                _record_ids(records, "MANE"),
            )
        )
    elif coverage.get("transcript"):
        uncertain_rules.append(
            _rule(
                f"Transcript anchor is {coverage['transcript']}; confirm MANE availability before final design.",
                coverage.get("transcript_confidence", "medium"),
                [],
            )
        )

    if coverage.get("region") == "GTEx tissue prior":
        supported_rules.append(
            _rule(
                "Use GTEx as a human tissue-level prior for brain-region context.",
                "medium",
                _record_ids(records, "GTEx"),
            )
        )
        uncertain_rules.append(
            _rule(
                "GTEx does not provide cell-type-resolved translation evidence; treat region-specific optimization as proxy-based.",
                "medium",
                _record_ids(records, "GTEx"),
            )
        )

    if coverage.get("cell_type") == "Allen cell-type prior":
        supported_rules.append(
            _rule(
                "Use Allen brain atlas evidence for cell-type context and coverage labeling.",
                "medium",
                _record_ids(records, "Allen"),
            )
        )

    if coverage.get("translation") == "tissue-aware proxy" or "Tissue-aware codon optimization concept" in titles:
        supported_rules.append(
            _rule(
                "Do not rank candidates by CAI alone; preserve a multi-objective trade-off among CAI, GC, CpG, motif, and payload constraints.",
                "medium",
                _record_ids(records, "CUSTOM"),
            )
        )
        rejected_rules.append(
            _rule(
                "Reject single-objective CAI maximization as the default recommendation policy.",
                "medium",
                _record_ids(records, "CUSTOM"),
            )
        )

    if coverage.get("modality") == "AAV size rule":
        supported_rules.append(
            _rule(
                "Treat AAV payload size as a hard feasibility check.",
                "medium",
                _record_ids(records, "AAV"),
            )
        )

    if "design_rules" in collections:
        supported_rules.append(
            _rule(
                "Apply conservative penalties for CpG density and simple forbidden motifs in DNA cargo.",
                "medium",
                _record_ids(records, "CpG"),
            )
        )

    uncertain_rules.append(
        _rule(
            "No wet-lab validation is attached to this run; interpret outputs as in silico design support only.",
            "high",
            [],
        )
    )

    return {
        "supported_rules": _dedupe_rules(supported_rules),
        "uncertain_rules": _dedupe_rules(uncertain_rules),
        "rejected_rules": _dedupe_rules(rejected_rules),
        "citations": [
            {
                "id": record.get("id"),
                "title": record.get("title"),
                "source": record.get("source"),
                "source_url": record.get("source_url"),
                "retrieval": record.get("retrieval", {}),
            }
            for record in records
        ],
    }


def generate_qc_report(design: dict[str, Any]) -> dict[str, Any]:
    best = design.get("recommended_candidate") or {}
    native_scores = (design.get("native") or {}).get("scores", {})
    best_scores = best.get("scores", {})
    evidence_summary = synthesize_evidence(design.get("evidence", {}))
    source_cds = design.get("source_cds", {})
    score_config = _score_config_from_design(design)
    optimizer_manifest = optimizer_reproducibility_manifest(design)
    data_quality = structured_quality_gate()
    optimizer_stress = optimizer_stress_gate()
    target_structured_evidence = _target_structured_evidence_summary(design.get("evidence", {}))

    return {
        "project_metadata": {
            "run_id": design.get("run_id"),
            "timestamp": design.get("timestamp"),
            "pipeline_version": design.get("pipeline_version"),
            "optimizer_seed": (design.get("optimization_config") or {}).get("seed"),
            "config_hash": optimizer_manifest["manifest_hash"][:16],
            "optimizer_config_hash": optimizer_manifest["optimization_config_hash"],
            "score_config_hash": optimizer_manifest["score_config_hash"],
            "optimizer_manifest_hash": optimizer_manifest["manifest_hash"],
            "structured_manifest_hash": (design.get("provenance") or {}).get("structured_manifest_hash"),
        },
        "target_definition": design.get("target", {}),
        "transcript_provenance": {
            "gene": (source_cds.get("gene") or {}).get("symbol"),
            "ensembl_gene_id": (source_cds.get("gene") or {}).get("ensembl_gene_id"),
            "transcript_id": (source_cds.get("selected_transcript") or {}).get("id"),
            "selection_reason": (source_cds.get("selected_transcript") or {}).get("selection_reason"),
            "mane_select": (source_cds.get("selected_transcript") or {}).get("mane_select"),
            "cds_length_nt": source_cds.get("cds_length_nt"),
            "protein_length_aa": source_cds.get("protein_length_aa"),
        },
        "evidence_summary": evidence_summary,
        "optimization_settings": design.get("optimization_config", {}),
        "optimizer_reproducibility": optimizer_manifest,
        "data_quality": _data_quality_summary(data_quality),
        "target_structured_evidence": target_structured_evidence,
        "optimizer_stress": _optimizer_stress_summary(optimizer_stress),
        "score_summary": {
            "native": native_scores,
            "recommended": best_scores,
            "delta": _score_delta(native_scores, best_scores),
        },
        "sequence_policy": {
            "native": audit_sequence_policy((design.get("native") or {}).get("cds", ""), score_config),
            "recommended": audit_sequence_policy(best.get("cds", ""), score_config) if best else {},
        },
        "candidate_ranking": [
            {
                "candidate_id": candidate.get("candidate_id"),
                "rank": candidate.get("rank"),
                "scores": candidate.get("scores"),
                "selection_trace": candidate.get("selection_trace", []),
                "constraint_risk": candidate.get("constraint_risk", {}),
                "sequence_policy": audit_sequence_policy(candidate.get("cds", ""), score_config).get("summary", {}),
                "is_recommended": candidate.get("candidate_id") == best.get("candidate_id"),
            }
            for candidate in design.get("candidates", [])
        ],
        "candidate_diagnostics": design.get("candidate_diagnostics", {}),
        "recommendation_audit": design.get("recommendation_audit") or (design.get("candidate_diagnostics") or {}).get("recommendation_audit") or {},
        "recommended_candidate": {
            "candidate_id": best.get("candidate_id"),
            "rationale": [*_candidate_rationale(native_scores, best_scores), *best.get("selection_trace", [])],
            "selection_trace": best.get("selection_trace", []),
            "constraint_risk": best.get("constraint_risk", {}),
            "constraint_status": {
                "aav_budget_pass": best_scores.get("aav_budget_pass"),
                "motif_violations": best_scores.get("motif_violations"),
                "polyadenylation_signal_count": best_scores.get("polyadenylation_signal_count"),
                "restriction_site_count": best_scores.get("restriction_site_count"),
                "cryptic_splice_motif_count": best_scores.get("cryptic_splice_motif_count"),
                "splice_donor_motif_count": best_scores.get("splice_donor_motif_count"),
                "splice_acceptor_motif_count": best_scores.get("splice_acceptor_motif_count"),
                "sequence_policy_violation_score": best_scores.get("sequence_policy_violation_score"),
                "gc_window_max_deviation": best_scores.get("gc_window_max_deviation"),
                "five_prime_gc_deviation": best_scores.get("five_prime_gc_deviation"),
                "hairpin_proxy_score": best_scores.get("hairpin_proxy_score"),
                "secondary_structure_proxy_score": best_scores.get("secondary_structure_proxy_score"),
                "mfe_proxy_delta_g": best_scores.get("mfe_proxy_delta_g"),
                "codon_pair_risk": best_scores.get("codon_pair_risk"),
                "low_complexity_penalty": best_scores.get("low_complexity_penalty"),
                "cpg_density_per_100nt": best_scores.get("cpg_density_per_100nt"),
            },
        },
        "validation": design.get("validation", {}),
        "qc_gate": design.get("qc_gate", {}),
        "warnings": design.get("warnings", []),
        "open_questions": _open_questions(design),
        "provenance": design.get("provenance", {}),
    }


def export_qc_report(report: dict[str, Any], export_format: str = "markdown") -> str | bytes:
    export_format = export_format.lower()
    if export_format in {"md", "markdown"}:
        return export_qc_report_markdown(report)
    if export_format == "html":
        return export_qc_report_html(report)
    if export_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    if export_format == "pdf":
        return export_qc_report_pdf(report)
    raise ValueError("Unsupported report export format. Use markdown, html, json, or pdf.")


def export_qc_report_markdown(report: dict[str, Any]) -> str:
    metadata = report.get("project_metadata", {})
    target = report.get("target_definition", {})
    transcript = report.get("transcript_provenance", {})
    score = report.get("score_summary", {})
    recommended = report.get("recommended_candidate", {})
    evidence = report.get("evidence_summary", {})
    diagnostics = report.get("candidate_diagnostics", {})
    optimizer_reproducibility = report.get("optimizer_reproducibility", {})
    data_quality = report.get("data_quality", {})
    target_structured = report.get("target_structured_evidence", {})
    optimizer_stress = report.get("optimizer_stress", {})
    lines = [
        "# Gene Therapy Design QC Report",
        "",
        "## Project Metadata",
        f"- Run ID: {metadata.get('run_id', 'n/a')}",
        f"- Timestamp: {metadata.get('timestamp', 'n/a')}",
        f"- Pipeline version: {metadata.get('pipeline_version', 'n/a')}",
        f"- Optimizer seed: {metadata.get('optimizer_seed', 'n/a')}",
        f"- Config hash: {metadata.get('config_hash', 'n/a')}",
        f"- Optimizer manifest hash: {metadata.get('optimizer_manifest_hash', 'n/a')}",
        f"- Score config hash: {metadata.get('score_config_hash', 'n/a')}",
        f"- Structured manifest hash: {metadata.get('structured_manifest_hash', 'n/a')}",
        "",
        "## Target Definition",
        f"- Gene: {target.get('gene', 'n/a')}",
        f"- Species: {target.get('species', 'n/a')}",
        f"- Brain region: {target.get('brain_region', 'n/a')}",
        f"- Cell type: {target.get('cell_type', 'n/a')}",
        f"- Modality: {target.get('modality', 'n/a')}",
        "",
        "## Transcript Provenance",
        f"- Ensembl gene ID: {transcript.get('ensembl_gene_id', 'n/a')}",
        f"- Transcript ID: {transcript.get('transcript_id', 'n/a')}",
        f"- Selection reason: {transcript.get('selection_reason', 'n/a')}",
        f"- CDS length: {transcript.get('cds_length_nt', 'n/a')} nt",
        f"- Protein length: {transcript.get('protein_length_aa', 'n/a')} aa",
        "",
        "## Optimizer Reproducibility",
        *_optimizer_reproducibility_lines(optimizer_reproducibility),
        "",
        "## Data Quality Evidence",
        *_data_quality_lines(data_quality),
        "",
        "## Target Structured Evidence",
        *_target_structured_evidence_lines(target_structured),
        "",
        "## Optimizer Stress Evidence",
        *_optimizer_stress_lines(optimizer_stress),
        "",
        "## Score Summary",
        _score_table(score),
        "",
        "## Evidence Rules",
        "### Supported",
        *_rule_lines(evidence.get("supported_rules", [])),
        "",
        "### Uncertain",
        *_rule_lines(evidence.get("uncertain_rules", [])),
        "",
        "### Rejected",
        *_rule_lines(evidence.get("rejected_rules", [])),
        "",
        "## Recommended Candidate",
        f"- Candidate ID: {recommended.get('candidate_id', 'n/a')}",
        *_prefixed_lines(recommended.get("rationale", [])),
        "",
        "## Candidate Diagnostics",
        *_candidate_diagnostic_lines(diagnostics),
        "",
        "## QC Gate",
        *_gate_lines(report.get("qc_gate", {})),
        "",
        "## Warnings",
        *_prefixed_lines(report.get("warnings", []) or ["No blocking QC warnings."]),
        "",
        "## Open Questions",
        *_prefixed_lines(report.get("open_questions", [])),
    ]
    return "\n".join(lines).strip() + "\n"


def export_qc_report_html(report: dict[str, Any]) -> str:
    markdown = export_qc_report_markdown(report)
    body = "\n".join(_markdown_line_to_html(line) for line in markdown.splitlines())
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Gene Therapy Design QC Report</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 920px; margin: 32px auto; color: #18231f; line-height: 1.55; }}
    h1, h2, h3 {{ color: #0f3429; }}
    table {{ width: 100%; border-collapse: collapse; margin: 14px 0; }}
    th, td {{ border: 1px solid #d8e3df; padding: 8px 10px; text-align: left; }}
    th {{ background: #eef7f3; }}
    code {{ background: #f4f7f6; padding: 2px 4px; border-radius: 4px; }}
    @media print {{ body {{ margin: 0.5in; }} }}
  </style>
</head>
<body>
{body}
</body>
</html>
"""


def export_qc_report_pdf(report: dict[str, Any]) -> bytes:
    markdown = export_qc_report_markdown(report)
    lines = _pdf_lines(markdown)
    pages = [lines[index : index + 48] for index in range(0, len(lines), 48)] or [["Gene Therapy Design QC Report"]]
    objects: list[bytes] = []

    def add_object(payload: bytes) -> int:
        objects.append(payload)
        return len(objects)

    catalog_id = add_object(b"<< /Type /Catalog /Pages 2 0 R >>")
    pages_id = add_object(b"")
    font_id = add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids: list[int] = []
    for page_lines in pages:
        content_id = add_object(_pdf_content_stream(page_lines))
        page_id = add_object(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>".encode(
                "ascii"
            )
        )
        page_ids.append(page_id)
    objects[pages_id - 1] = f"<< /Type /Pages /Kids [{' '.join(f'{page_id} 0 R' for page_id in page_ids)}] /Count {len(page_ids)} >>".encode(
        "ascii"
    )
    assert catalog_id == 1
    return _assemble_pdf(objects)


def _rule(statement: str, confidence: str, citations: list[str]) -> dict[str, Any]:
    return {"statement": statement, "confidence": confidence, "citations": citations}


def _record_ids(records: list[dict[str, Any]], token: str) -> list[str]:
    token_lower = token.lower()
    return [
        record["id"]
        for record in records
        if token_lower in f"{record.get('title', '')} {record.get('source', '')} {record.get('summary', '')}".lower()
    ]


def _dedupe_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output = []
    for rule in rules:
        if rule["statement"] in seen:
            continue
        seen.add(rule["statement"])
        output.append(rule)
    return output


def _score_delta(native: dict[str, Any], recommended: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "cai",
        "gc_fraction",
        "gc_window_max_deviation",
        "cpg_density_per_100nt",
        "motif_violations",
        "polyadenylation_signal_count",
        "restriction_site_count",
        "cryptic_splice_motif_count",
        "splice_donor_motif_count",
        "splice_acceptor_motif_count",
        "sequence_policy_violation_score",
        "codon_pair_risk",
        "five_prime_gc_deviation",
        "hairpin_proxy_score",
        "secondary_structure_proxy_score",
        "mfe_proxy_delta_g",
        "low_complexity_penalty",
        "composite_quality",
    ]
    return {
        key: round(float(recommended.get(key, 0)) - float(native.get(key, 0)), 6)
        for key in keys
        if key in native and key in recommended
    }


def _candidate_rationale(native: dict[str, Any], recommended: dict[str, Any]) -> list[str]:
    rationale = []
    if recommended.get("aav_budget_pass"):
        rationale.append("Candidate passes the configured AAV payload feasibility check.")
    if recommended.get("motif_violations", 0) == 0:
        rationale.append("Candidate has no configured forbidden motif violations.")
    if recommended.get("polyadenylation_signal_count", 0) == 0:
        rationale.append("Candidate has no internal polyadenylation signal proxy matches.")
    if recommended.get("restriction_site_count", 0) == 0:
        rationale.append("Candidate has no configured restriction enzyme recognition-site matches.")
    if recommended.get("splice_donor_motif_count", 0) == 0 and recommended.get("splice_acceptor_motif_count", 0) == 0:
        rationale.append("Candidate has no configured splice donor/acceptor proxy matches.")
    if recommended.get("gc_window_max_deviation", 0) <= native.get("gc_window_max_deviation", 1):
        rationale.append("Candidate preserves or improves local GC-window balance relative to native CDS.")
    if recommended.get("composite_quality", 0) >= native.get("composite_quality", 0):
        rationale.append("Candidate improves or preserves the composite quality score relative to the native CDS.")
    else:
        rationale.append("Candidate is retained as a Pareto trade-off even though its composite score is lower than native.")
    if recommended.get("cpg_density_per_100nt", 0) <= native.get("cpg_density_per_100nt", 0):
        rationale.append("Candidate does not increase CpG density relative to native CDS.")
    if recommended.get("tissue_codon_adaptation", 0) > 0:
        rationale.append("Candidate was scored with target-context tRNA/codon availability priors.")
    if recommended.get("rare_codon_clusters", 0) == 0:
        rationale.append("Candidate has no configured low-availability codon clusters.")
    if recommended.get("codon_pair_risk", 0) == 0:
        rationale.append("Candidate has no codon-pair proxy risk events.")
    if recommended.get("hairpin_proxy_score", 1) <= 0.25:
        rationale.append("Candidate has low 5-prime reverse-complement hairpin proxy risk.")
    if recommended.get("secondary_structure_proxy_score", 1) <= 0.35:
        rationale.append("Candidate has low deterministic secondary-structure proxy risk.")
    if recommended.get("low_complexity_penalty", 1) <= 0.35:
        rationale.append("Candidate preserves acceptable local k-mer sequence complexity.")
    return rationale


def _open_questions(design: dict[str, Any]) -> list[str]:
    questions = [
        "Replace hash-bow-v1 with a validated biomedical embedding model before production use.",
        "Add release-pinned GTEx/Allen/CUSTOM data tables before using tissue or cell-type scores quantitatively.",
        "Validate candidate expression and safety claims experimentally before therapeutic interpretation.",
    ]
    if (design.get("target") or {}).get("modality") == "AAV":
        questions.append("Confirm full cassette size with promoter, regulatory elements, barcode, and vector-specific margins.")
    return questions


def _data_quality_summary(gate: dict[str, Any]) -> dict[str, Any]:
    coverage = gate.get("coverage") or {}
    summary = gate.get("summary") or {}
    return {
        "quality_schema": gate.get("quality_schema"),
        "status": gate.get("status"),
        "manifest_hash": gate.get("manifest_hash"),
        "record_count": gate.get("record_count"),
        "dataset_count": gate.get("dataset_count"),
        "live_record_fraction": coverage.get("live_record_fraction"),
        "release_pinned_fraction": coverage.get("release_pinned_fraction"),
        "blocking_count": summary.get("blocking_count"),
        "warning_count": summary.get("warning_count_total"),
        "operator_actions": gate.get("operator_actions", []),
    }


def _target_structured_evidence_summary(evidence: dict[str, Any]) -> dict[str, Any]:
    context = evidence.get("structured_context") or {}
    records = list(context.get("records") or [])
    coverage = dict(context.get("coverage") or {})
    live_records = [record for record in records if _target_record_is_live(record)]
    seed_records = [record for record in records if _target_record_is_seed(record)]
    snapshot_records = [record for record in records if record.get("source_snapshot_path")]
    release_pinned_records = [record for record in records if record.get("release")]
    datasets = sorted({str(record.get("dataset")) for record in records if record.get("dataset")})
    genes = sorted({str(record.get("gene")) for record in records if record.get("gene")})
    regions = sorted({str(record.get("brain_region")) for record in records if record.get("brain_region")})
    cell_types = sorted({str(record.get("cell_type")) for record in records if record.get("cell_type")})
    status = "missing" if not records else "warning" if seed_records else "pass"
    return {
        "status": status,
        "coverage": coverage,
        "matched_record_count": len(records),
        "live_record_count": len(live_records),
        "seed_record_count": len(seed_records),
        "release_pinned_record_count": len(release_pinned_records),
        "snapshot_record_count": len(snapshot_records),
        "datasets": datasets,
        "genes": genes[:12],
        "brain_regions": regions[:12],
        "cell_types": cell_types[:12],
        "top_records": [_target_record_summary(record) for record in records[:8]],
    }


def _target_record_summary(record: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "id": record.get("id"),
        "dataset": record.get("dataset"),
        "release": record.get("release"),
        "gene": record.get("gene"),
        "brain_region": record.get("brain_region"),
        "cell_type": record.get("cell_type"),
        "confidence": record.get("confidence"),
        "match_score": record.get("match_score"),
        "source_file": record.get("_source_file"),
        "source_payload_sha256": record.get("source_payload_sha256"),
        "source_snapshot_path": record.get("source_snapshot_path"),
        "is_live": _target_record_is_live(record),
        "is_seed": _target_record_is_seed(record),
    }
    if record.get("median_expression") is not None:
        summary["median_expression"] = record.get("median_expression")
        summary["unit"] = record.get("unit")
    if record.get("number_of_cells") is not None:
        summary["number_of_cells"] = record.get("number_of_cells")
    return summary


def _optimizer_stress_summary(gate: dict[str, Any]) -> dict[str, Any]:
    return {
        "stress_schema": gate.get("stress_schema"),
        "status": gate.get("status"),
        "benchmark_status": gate.get("benchmark_status"),
        "case_count": gate.get("case_count"),
        "cases_hash": gate.get("cases_hash"),
        "summary": gate.get("summary", {}),
        "recommendations": gate.get("recommendations", []),
    }


def _score_table(score_summary: dict[str, Any]) -> str:
    native = score_summary.get("native", {})
    recommended = score_summary.get("recommended", {})
    delta = score_summary.get("delta", {})
    rows = ["| Metric | Native | Recommended | Delta |", "| --- | ---: | ---: | ---: |"]
    for key in [
        "composite_quality",
        "cai",
        "tissue_codon_adaptation",
        "rare_codon_clusters",
        "gc_fraction",
        "gc_window_max_deviation",
        "five_prime_gc_deviation",
        "hairpin_proxy_score",
        "secondary_structure_proxy_score",
        "mfe_proxy_delta_g",
        "sequence_complexity",
        "low_complexity_penalty",
        "cpg_density_per_100nt",
        "motif_violations",
        "polyadenylation_signal_count",
        "restriction_site_count",
        "cryptic_splice_motif_count",
        "splice_donor_motif_count",
        "splice_acceptor_motif_count",
        "sequence_policy_violation_score",
        "codon_pair_risk",
    ]:
        rows.append(
            f"| {key} | {_format_score(native.get(key))} | {_format_score(recommended.get(key))} | {_format_score(delta.get(key), signed=True)} |"
        )
    return "\n".join(rows)


def _rule_lines(rules: list[dict[str, Any]]) -> list[str]:
    if not rules:
        return ["- n/a"]
    return [
        f"- {rule.get('statement', 'n/a')} ({rule.get('confidence', 'unknown')}; citations: {', '.join(rule.get('citations', [])) or 'n/a'})"
        for rule in rules
    ]


def _prefixed_lines(items: list[str]) -> list[str]:
    return [f"- {item}" for item in items] if items else ["- n/a"]


def _gate_lines(gate: dict[str, Any]) -> list[str]:
    if not gate:
        return ["- n/a"]
    lines = [f"- Status: {gate.get('status', 'n/a')}"]
    for check in gate.get("checks", []):
        lines.append(f"- {check.get('id')}: {check.get('result')} ({check.get('severity')}) - {check.get('message')}")
    return lines


def _candidate_diagnostic_lines(diagnostics: dict[str, Any]) -> list[str]:
    if not diagnostics:
        return ["- n/a"]
    pareto = diagnostics.get("pareto_front") or {}
    diversity = diagnostics.get("diversity") or {}
    best_by_metric = diagnostics.get("best_by_metric") or {}
    lines = [
        f"- Candidate count: {diagnostics.get('candidate_count', 'n/a')}",
        f"- Feasible candidates: {diagnostics.get('feasible_count', 'n/a')}",
        f"- Selection policy: {diagnostics.get('selection_policy', 'n/a')}",
        f"- Pareto front size: {pareto.get('size', 'n/a')} ({', '.join(pareto.get('candidate_ids', [])) or 'n/a'})",
        f"- Unique CDS count: {diversity.get('unique_cds_count', 'n/a')}",
        f"- Mean pairwise codon distance: {_format_score(diversity.get('mean_pairwise_codon_distance'))}",
    ]
    if best_by_metric:
        lines.append("- Best metric representatives:")
        for metric, selected in best_by_metric.items():
            if not selected:
                continue
            lines.append(
                f"- {metric}: {selected.get('candidate_id')} "
                f"(rank {selected.get('rank')}, value {_format_score(selected.get('value'))})"
            )
    return lines


def _data_quality_lines(summary: dict[str, Any]) -> list[str]:
    if not summary:
        return ["- n/a"]
    lines = [
        f"- Schema: {summary.get('quality_schema', 'n/a')}",
        f"- Status: {summary.get('status', 'n/a')}",
        f"- Manifest hash: {summary.get('manifest_hash', 'n/a')}",
        f"- Records: {summary.get('record_count', 'n/a')} across {summary.get('dataset_count', 'n/a')} datasets",
        f"- Live record fraction: {_format_score(summary.get('live_record_fraction'))}",
        f"- Release-pinned fraction: {_format_score(summary.get('release_pinned_fraction'))}",
        f"- Blocking / warning items: {summary.get('blocking_count', 'n/a')} / {summary.get('warning_count', 'n/a')}",
    ]
    for action in summary.get("operator_actions") or []:
        lines.append(f"- Action: {action}")
    return lines


def _target_structured_evidence_lines(summary: dict[str, Any]) -> list[str]:
    if not summary:
        return ["- n/a"]
    coverage = summary.get("coverage") or {}
    lines = [
        f"- Status: {summary.get('status', 'n/a')}",
        f"- Matched records: {summary.get('matched_record_count', 'n/a')}",
        f"- Live / seed records: {summary.get('live_record_count', 'n/a')} / {summary.get('seed_record_count', 'n/a')}",
        f"- Release-pinned / snapshot records: {summary.get('release_pinned_record_count', 'n/a')} / {summary.get('snapshot_record_count', 'n/a')}",
        f"- Coverage: GTEx {coverage.get('gtex_gene_expression', coverage.get('gtex', 'n/a'))}, Allen {coverage.get('allen', 'n/a')}, CUSTOM {coverage.get('custom', 'n/a')}, tRNA {coverage.get('trna', 'n/a')}",
        f"- Datasets: {', '.join(summary.get('datasets') or []) or 'n/a'}",
    ]
    top_records = summary.get("top_records") or []
    if top_records:
        lines.append("- Top matched structured records:")
        for record in top_records[:5]:
            detail = []
            if record.get("gene"):
                detail.append(str(record["gene"]))
            if record.get("brain_region"):
                detail.append(str(record["brain_region"]))
            if record.get("cell_type"):
                detail.append(str(record["cell_type"]))
            if record.get("median_expression") is not None:
                detail.append(f"{record.get('median_expression')} {record.get('unit', '')}".strip())
            if record.get("number_of_cells") is not None:
                detail.append(f"{record.get('number_of_cells')} cells")
            lines.append(
                "- {dataset}: {record_id} ({release}; match {match}; {kind}){detail}".format(
                    dataset=record.get("dataset", "unknown"),
                    record_id=record.get("id", "n/a"),
                    release=record.get("release", "n/a"),
                    match=_format_score(record.get("match_score")),
                    kind="live" if record.get("is_live") else "seed/local" if record.get("is_seed") else "release-pinned",
                    detail=f" - {', '.join(detail)}" if detail else "",
                )
            )
    return lines


def _optimizer_stress_lines(summary: dict[str, Any]) -> list[str]:
    if not summary:
        return ["- n/a"]
    counts = summary.get("summary") or {}
    lines = [
        f"- Schema: {summary.get('stress_schema', 'n/a')}",
        f"- Status: {summary.get('status', 'n/a')}",
        f"- Benchmark status: {summary.get('benchmark_status', 'n/a')}",
        f"- Cases: {summary.get('case_count', 'n/a')}",
        f"- Cases hash: {summary.get('cases_hash', 'n/a')}",
        f"- Pass / warning / fail checks: {counts.get('pass_count', 'n/a')} / {counts.get('warning_count', 'n/a')} / {counts.get('fail_count', 'n/a')}",
    ]
    for recommendation in summary.get("recommendations") or []:
        lines.append(f"- Recommendation: {recommendation}")
    return lines


def _optimizer_reproducibility_lines(manifest: dict[str, Any]) -> list[str]:
    if not manifest:
        return ["- n/a"]
    search = manifest.get("search_budget") or {}
    repair = manifest.get("repair_policy") or {}
    seed_strategy = manifest.get("seed_strategy") or {}
    transcript = manifest.get("selected_transcript") or {}
    return [
        f"- Manifest hash: {manifest.get('manifest_hash', 'n/a')}",
        f"- Algorithm: {manifest.get('algorithm', 'n/a')}",
        f"- Seed: {manifest.get('seed', 'n/a')}",
        f"- Seed strategy: {seed_strategy.get('version', 'n/a')}",
        f"- Search budget: population {search.get('population_size', 'n/a')}, generations {search.get('generations', 'n/a')}, max candidates {search.get('max_candidates', 'n/a')}",
        f"- Repair: {'enabled' if repair.get('enabled') else 'disabled'} / passes {repair.get('repair_passes', 'n/a')}",
        f"- Objectives: {len(manifest.get('objective_inventory') or [])}",
        f"- Optimization config hash: {manifest.get('optimization_config_hash', 'n/a')}",
        f"- Score config hash: {manifest.get('score_config_hash', 'n/a')}",
        f"- Target hash: {manifest.get('target_hash', 'n/a')}",
        f"- Source CDS hash: {manifest.get('source_cds_hash', 'n/a')}",
        f"- Transcript: {transcript.get('transcript_id', 'n/a')} ({transcript.get('selection_reason', 'n/a')})",
    ]


def _format_score(value: Any, signed: bool = False) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"{value:+d}" if signed else str(value)
    if isinstance(value, float):
        return f"{value:+.4f}" if signed else f"{value:.4f}"
    return "n/a"


def _score_config_from_design(design: dict[str, Any]) -> ScoreConfig:
    score_config = ((design.get("optimization_config") or {}).get("score_config")) or {}
    if not score_config:
        return ScoreConfig()
    kwargs = {
        "gc_min": score_config.get("gc_min", 0.40),
        "gc_max": score_config.get("gc_max", 0.65),
        "target_gc": score_config.get("target_gc", 0.55),
        "aav_payload_limit_nt": score_config.get("aav_payload_limit_nt", 4300),
        "forbidden_motifs": tuple(score_config.get("forbidden_motifs") or ()),
        "polyadenylation_signals": tuple(score_config.get("polyadenylation_signals") or ()),
        "restriction_sites": tuple(score_config.get("restriction_sites") or ()),
        "cryptic_splice_motifs": tuple(score_config.get("cryptic_splice_motifs") or ()),
        "splice_donor_motifs": tuple(score_config.get("splice_donor_motifs") or ()),
        "splice_acceptor_motifs": tuple(score_config.get("splice_acceptor_motifs") or ()),
        "gc_window_size_nt": score_config.get("gc_window_size_nt", 60),
        "codon_weight_multipliers": tuple(sorted((score_config.get("codon_weight_multipliers") or {}).items())),
        "codon_availability_weights": tuple(sorted((score_config.get("codon_availability_weights") or {}).items())),
    }
    return ScoreConfig(**kwargs)


def _target_record_is_live(record: dict[str, Any]) -> bool:
    release = str(record.get("release") or "").lower()
    return bool(record.get("source_request_url") and record.get("source_payload_sha256") and record.get("source_snapshot_path") and "seed" not in release)


def _target_record_is_seed(record: dict[str, Any]) -> bool:
    release = str(record.get("release") or "").lower()
    source_file = str(record.get("_source_file") or "").lower()
    summary = str(record.get("summary") or "").lower()
    return "seed" in release or "seed" in source_file or "placeholder" in summary


def _markdown_line_to_html(line: str) -> str:
    if line.startswith("# "):
        return f"<h1>{escape(line[2:])}</h1>"
    if line.startswith("## "):
        return f"<h2>{escape(line[3:])}</h2>"
    if line.startswith("### "):
        return f"<h3>{escape(line[4:])}</h3>"
    if line.startswith("- "):
        return f"<p>&bull; {escape(line[2:])}</p>"
    if line.startswith("| "):
        cells = [escape(cell.strip()) for cell in line.strip("|").split("|")]
        tag = "th" if "---" in line else "td"
        if tag == "th" and all(set(cell) <= {"-", ":", " "} for cell in cells):
            return ""
        return "<table><tr>" + "".join(f"<{tag}>{cell}</{tag}>" for cell in cells) + "</tr></table>"
    if not line.strip():
        return ""
    return f"<p>{escape(line)}</p>"


def _pdf_lines(markdown: str) -> list[str]:
    lines: list[str] = []
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            lines.append("")
            continue
        if line.startswith("#"):
            line = line.lstrip("#").strip().upper()
        elif line.startswith("- "):
            line = "* " + line[2:]
        if line.startswith("| "):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if all(set(cell) <= {"-", ":", " "} for cell in cells):
                continue
            line = " | ".join(cells)
        while len(line) > 96:
            split_at = line.rfind(" ", 0, 96)
            if split_at < 48:
                split_at = 96
            lines.append(line[:split_at].strip())
            line = line[split_at:].strip()
        lines.append(line)
    return lines


def _pdf_content_stream(lines: list[str]) -> bytes:
    text_ops = ["BT", "/F1 9 Tf", "50 756 Td", "12 TL"]
    for line in lines:
        text_ops.append(f"({_pdf_escape(line)}) Tj")
        text_ops.append("T*")
    text_ops.append("ET")
    payload = "\n".join(text_ops).encode("latin-1", errors="replace")
    return b"<< /Length " + str(len(payload)).encode("ascii") + b" >>\nstream\n" + payload + b"\nendstream"


def _pdf_escape(text: str) -> str:
    safe = text.encode("latin-1", errors="replace").decode("latin-1")
    return safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _assemble_pdf(objects: list[bytes]) -> bytes:
    chunks = [b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"]
    offsets = [0]
    for index, payload in enumerate(objects, start=1):
        offsets.append(sum(len(chunk) for chunk in chunks))
        chunks.append(f"{index} 0 obj\n".encode("ascii"))
        chunks.append(payload)
        chunks.append(b"\nendobj\n")
    xref_offset = sum(len(chunk) for chunk in chunks)
    chunks.append(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    chunks.append(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        chunks.append(f"{offset:010d} 00000 n \n".encode("ascii"))
    chunks.append(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return b"".join(chunks)
