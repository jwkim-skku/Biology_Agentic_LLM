from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from io import BytesIO, StringIO
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from app.services.export_manifest_service import ManifestedZip
from app.services.rag_service import rag_status
from app.services.report_service import export_qc_report
from app.services.structured_data_service import structured_manifest
from app.services.workflow_trace_bundle_service import workflow_trace_summary_from_run


def build_run_export_bundle(run: dict[str, Any]) -> bytes:
    manifest = _bundle_manifest(run)
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        bundle = ManifestedZip(archive, "run_audit_bundle", manifest)
        bundle.writestr("bundle_manifest.json", _json(manifest))
        bundle.writestr("request.json", _json(run.get("request") or {}))
        bundle.writestr("design.json", _json(run.get("design") or {}))
        bundle.writestr("trace.json", _json(run.get("trace") or []))
        bundle.writestr("workflow_summary.json", _json(workflow_trace_summary_from_run(run)))
        bundle.writestr("provenance/structured_manifest.json", _json(structured_manifest()))
        bundle.writestr("provenance/rag_status.json", _json(rag_status()))

        qc_report = run.get("qc_report") or (run.get("design") or {}).get("qc_report") or {}
        if qc_report:
            bundle.writestr("qc_report.json", _json(qc_report))
            bundle.writestr("qc_report.md", export_qc_report(qc_report, "markdown"))
            bundle.writestr("qc_report.html", export_qc_report(qc_report, "html"))
            bundle.writestr("qc_report.pdf", export_qc_report(qc_report, "pdf"))

        design = run.get("design") or {}
        diagnostics = design.get("candidate_diagnostics") or qc_report.get("candidate_diagnostics") or {}
        if diagnostics:
            bundle.writestr("candidate_diagnostics.json", _json(diagnostics))

        candidates = (design.get("candidates")) or []
        bundle.writestr("candidate_ranking.csv", _candidate_csv(candidates))
        bundle.write_artifact_manifest()
    return buffer.getvalue()


def _bundle_manifest(run: dict[str, Any]) -> dict[str, Any]:
    design = run.get("design") or {}
    return {
        "bundle_schema": "agentic-rag-run-bundle-v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run.get("run_id"),
        "run_type": run.get("run_type"),
        "pipeline_version": run.get("pipeline_version") or design.get("pipeline_version"),
        "structured_manifest_hash": run.get("structured_manifest_hash") or (design.get("provenance") or {}).get("structured_manifest_hash"),
        "recommended_candidate_id": run.get("recommended_candidate_id") or ((design.get("recommended_candidate") or {}).get("candidate_id")),
        "files": [
            "artifact_manifest.json",
            "request.json",
            "design.json",
            "qc_report.json",
            "qc_report.md",
            "qc_report.html",
            "qc_report.pdf",
            "candidate_diagnostics.json",
            "candidate_ranking.csv",
            "workflow_summary.json",
            "trace.json",
            "provenance/structured_manifest.json",
            "provenance/rag_status.json",
        ],
    }


def _candidate_csv(candidates: list[dict[str, Any]]) -> str:
    output = StringIO()
    fieldnames = [
        "candidate_id",
        "rank",
        "composite_quality",
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
        "sequence_complexity",
        "low_complexity_penalty",
        "tissue_codon_adaptation",
        "rare_codon_clusters",
        "aav_budget_pass",
        "length_nt",
        "constraint_risk_status",
        "constraint_risk_findings",
        "constraint_risk_fail_count",
        "constraint_risk_warning_count",
        "selection_trace",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for candidate in candidates:
        scores = candidate.get("scores") or {}
        risk = candidate.get("constraint_risk") or {}
        writer.writerow(
            {
                "candidate_id": candidate.get("candidate_id"),
                "rank": candidate.get("rank"),
                **{field: scores.get(field) for field in fieldnames if field not in {"candidate_id", "rank"}},
                "constraint_risk_status": risk.get("status"),
                "constraint_risk_findings": risk.get("finding_count"),
                "constraint_risk_fail_count": risk.get("fail_count"),
                "constraint_risk_warning_count": risk.get("warning_count"),
                "selection_trace": " | ".join(str(item) for item in candidate.get("selection_trace", [])),
            }
        )
    return output.getvalue()


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
