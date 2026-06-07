from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO, StringIO
from typing import Any
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from app.services.export_manifest_service import ManifestedZip, verify_artifact_bundle
from app.services.optimizer_stress_service import optimizer_stress_gate
from app.services.optimizer_reproducibility_service import optimizer_reproducibility_manifest
from app.services.rag_service import rag_status
from app.services.report_service import export_qc_report
from app.services.structured_data_service import structured_manifest
from app.services.structured_quality_service import structured_quality_gate


REQUIRED_QC_BUNDLE_FILES = {
    "artifact_manifest.json",
    "bundle_manifest.json",
    "request.json",
    "design_summary.json",
    "qc_report.json",
    "qc_report.md",
    "qc_report.html",
    "qc_report.pdf",
    "optimizer_reproducibility.json",
    "recommendation_audit.json",
    "data_quality.json",
    "optimizer_stress.json",
    "candidate_ranking.csv",
    "provenance/structured_manifest.json",
    "provenance/rag_status.json",
}

REQUIRED_CANDIDATE_COLUMNS = {
    "candidate_id",
    "rank",
    "composite_quality",
    "aav_budget_pass",
}

EXPLAINABILITY_CANDIDATE_COLUMNS = {
    "constraint_risk_status",
    "constraint_risk_findings",
    "selection_trace",
}


def build_qc_report_bundle(design: dict[str, Any], request_payload: dict[str, Any], *, bundle_type: str) -> bytes:
    report = design.get("qc_report") or {}
    run_id = str(design.get("run_id") or "unsaved_run")
    report_json = _json(report)
    request_json = _json(request_payload)
    candidate_csv = _candidate_csv(design.get("candidates") or [])
    recommendation_audit = _recommendation_audit_payload(design, report)
    manifest = {
        "bundle_schema": "agentic-rag-qc-report-bundle-v1",
        "bundle_type": bundle_type,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "pipeline_version": design.get("pipeline_version"),
        "structured_manifest_hash": (design.get("provenance") or {}).get("structured_manifest_hash"),
        "optimizer_manifest_hash": ((report.get("optimizer_reproducibility") or {}).get("manifest_hash")),
        "request_hash": _hash_payload(request_payload),
        "qc_report_hash": _hash_payload(report),
        "candidate_ranking_hash": _hash_text(candidate_csv),
        "recommendation_audit_hash": _hash_payload(recommendation_audit),
        "recommended_candidate_id": (design.get("recommended_candidate") or {}).get("candidate_id"),
    }
    data_quality = structured_quality_gate()
    optimizer_stress = optimizer_stress_gate()
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        bundle = ManifestedZip(archive, "qc_report_bundle", manifest)
        bundle.writestr("bundle_manifest.json", _json(manifest))
        bundle.writestr("request.json", request_json)
        bundle.writestr("design_summary.json", _json(_design_summary(design)))
        bundle.writestr("qc_report.json", report_json)
        bundle.writestr("qc_report.md", export_qc_report(report, "markdown"))
        bundle.writestr("qc_report.html", export_qc_report(report, "html"))
        bundle.writestr("qc_report.pdf", export_qc_report(report, "pdf"))
        bundle.writestr("candidate_diagnostics.json", _json(design.get("candidate_diagnostics") or report.get("candidate_diagnostics") or {}))
        bundle.writestr("optimizer_reproducibility.json", _json(report.get("optimizer_reproducibility") or optimizer_reproducibility_manifest(design)))
        bundle.writestr("recommendation_audit.json", _json(recommendation_audit))
        bundle.writestr("data_quality.json", _json(data_quality))
        bundle.writestr("optimizer_stress.json", _json(optimizer_stress))
        bundle.writestr("candidate_ranking.csv", candidate_csv)
        bundle.writestr("provenance/structured_manifest.json", _json(structured_manifest()))
        bundle.writestr("provenance/rag_status.json", _json(rag_status()))
        bundle.write_artifact_manifest()
    return buffer.getvalue()


def verify_qc_report_bundle(bundle: bytes) -> dict[str, Any]:
    artifact_verification = verify_artifact_bundle(bundle)
    semantic_errors: list[str] = []
    semantic_warnings: list[str] = []
    semantic_checks: dict[str, str] = {}
    metadata: dict[str, Any] = {}

    try:
        with ZipFile(BytesIO(bundle), "r") as archive:
            names = set(archive.namelist())
            missing = sorted(REQUIRED_QC_BUNDLE_FILES - names)
            _record_check(semantic_checks, "required_files", not missing)
            if missing:
                semantic_errors.append(f"Required QC bundle files are missing: {', '.join(missing)}.")
                return _qc_verification_result(
                    artifact_verification,
                    semantic_errors,
                    semantic_warnings,
                    semantic_checks,
                    metadata,
                )

            bundle_manifest = _read_json_member(archive, "bundle_manifest.json")
            request = _read_json_member(archive, "request.json")
            design_summary = _read_json_member(archive, "design_summary.json")
            qc_report = _read_json_member(archive, "qc_report.json")
            optimizer_manifest = _read_json_member(archive, "optimizer_reproducibility.json")
            recommendation_audit_file = _read_json_member(archive, "recommendation_audit.json")
            data_quality = _read_json_member(archive, "data_quality.json")
            optimizer_stress = _read_json_member(archive, "optimizer_stress.json")
            candidate_diagnostics = _read_json_member(archive, "candidate_diagnostics.json")
            candidate_rows, candidate_headers, candidate_csv_text = _read_candidate_csv(archive)
    except (BadZipFile, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        semantic_errors.append(f"Invalid QC report bundle semantics: {exc}")
        return _qc_verification_result(
            artifact_verification,
            semantic_errors,
            semantic_warnings,
            semantic_checks,
            metadata,
        )

    project_metadata = qc_report.get("project_metadata") or {}
    recommended = qc_report.get("recommended_candidate") or {}
    optimizer_hash = optimizer_manifest.get("manifest_hash")
    data_quality_summary = qc_report.get("data_quality") or {}
    optimizer_stress_summary = qc_report.get("optimizer_stress") or {}
    target_definition = qc_report.get("target_definition") or {}
    run_id = project_metadata.get("run_id")
    recommended_candidate_id = recommended.get("candidate_id")
    metadata.update(
        {
            "run_id": run_id,
            "recommended_candidate_id": recommended_candidate_id,
            "optimizer_manifest_hash": optimizer_hash,
            "request_hash": _hash_payload(request),
            "qc_report_hash": _hash_payload(qc_report),
            "candidate_ranking_hash": _hash_text(candidate_csv_text),
            "recommendation_audit_hash": _hash_payload(recommendation_audit_file),
            "data_quality_status": data_quality.get("status"),
            "optimizer_stress_status": optimizer_stress.get("status"),
        }
    )

    _expect_equal(
        semantic_checks,
        semantic_errors,
        "artifact_type",
        artifact_verification.get("artifact_type"),
        "qc_report_bundle",
        "Artifact manifest artifact_type must be qc_report_bundle.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "bundle_schema",
        bundle_manifest.get("bundle_schema"),
        "agentic-rag-qc-report-bundle-v1",
        "bundle_manifest.json bundle_schema is not recognized.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "optimizer_schema",
        optimizer_manifest.get("manifest_schema"),
        "agentic-rag-optimizer-reproducibility-v1",
        "optimizer_reproducibility.json manifest_schema is not recognized.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "data_quality_schema",
        data_quality.get("quality_schema"),
        "agentic-rag-structured-quality-gate-v1",
        "data_quality.json quality_schema is not recognized.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "optimizer_stress_schema",
        optimizer_stress.get("stress_schema"),
        "agentic-rag-optimizer-stress-gate-v1",
        "optimizer_stress.json stress_schema is not recognized.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "data_quality_report_status",
        data_quality_summary.get("status"),
        data_quality.get("status"),
        "qc_report.json data_quality.status does not match data_quality.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "data_quality_manifest_hash",
        data_quality_summary.get("manifest_hash"),
        data_quality.get("manifest_hash"),
        "qc_report.json data_quality.manifest_hash does not match data_quality.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "optimizer_stress_report_status",
        optimizer_stress_summary.get("status"),
        optimizer_stress.get("status"),
        "qc_report.json optimizer_stress.status does not match optimizer_stress.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "optimizer_stress_cases_hash",
        optimizer_stress_summary.get("cases_hash"),
        optimizer_stress.get("cases_hash"),
        "qc_report.json optimizer_stress.cases_hash does not match optimizer_stress.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "run_id_consistency",
        bundle_manifest.get("run_id"),
        run_id,
        "bundle_manifest.json run_id does not match qc_report.json project_metadata.run_id.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "design_summary_run_id",
        design_summary.get("run_id"),
        run_id,
        "design_summary.json run_id does not match qc_report.json project_metadata.run_id.",
    )
    _record_check(semantic_checks, "request_payload", bool(request))
    if not request:
        semantic_errors.append("request.json must contain the source request payload.")
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "request_hash",
        bundle_manifest.get("request_hash"),
        _hash_payload(request),
        "bundle_manifest.json request_hash does not match request.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "qc_report_hash",
        bundle_manifest.get("qc_report_hash"),
        _hash_payload(qc_report),
        "bundle_manifest.json qc_report_hash does not match qc_report.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "candidate_ranking_hash",
        bundle_manifest.get("candidate_ranking_hash"),
        _hash_text(candidate_csv_text),
        "bundle_manifest.json candidate_ranking_hash does not match candidate_ranking.csv.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "recommendation_audit_hash",
        bundle_manifest.get("recommendation_audit_hash"),
        _hash_payload(recommendation_audit_file),
        "bundle_manifest.json recommendation_audit_hash does not match recommendation_audit.json.",
    )
    for key in ["gene", "species", "brain_region", "cell_type", "modality"]:
        if key not in request or key not in target_definition:
            continue
        _expect_equal(
            semantic_checks,
            semantic_errors,
            f"request_target_{key}",
            _normalized_request_value(request.get(key)),
            _normalized_request_value(target_definition.get(key)),
            f"request.json {key} does not match qc_report.json target_definition.{key}.",
        )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "optimizer_hash_bundle",
        bundle_manifest.get("optimizer_manifest_hash"),
        optimizer_hash,
        "bundle_manifest.json optimizer_manifest_hash does not match optimizer_reproducibility.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "optimizer_hash_report",
        project_metadata.get("optimizer_manifest_hash"),
        optimizer_hash,
        "qc_report.json project_metadata.optimizer_manifest_hash does not match optimizer_reproducibility.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "optimizer_hash_summary",
        ((design_summary.get("optimizer_reproducibility") or {}).get("manifest_hash")),
        optimizer_hash,
        "design_summary.json optimizer_reproducibility.manifest_hash does not match optimizer_reproducibility.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "score_config_hash",
        project_metadata.get("score_config_hash"),
        optimizer_manifest.get("score_config_hash"),
        "qc_report.json score_config_hash does not match optimizer_reproducibility.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "optimizer_config_hash",
        project_metadata.get("optimizer_config_hash"),
        optimizer_manifest.get("optimization_config_hash"),
        "qc_report.json optimizer_config_hash does not match optimizer_reproducibility.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "project_config_hash",
        project_metadata.get("config_hash"),
        str(optimizer_hash or "")[:16],
        "qc_report.json config_hash must be the short optimizer manifest hash.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "recommended_candidate_bundle",
        bundle_manifest.get("recommended_candidate_id"),
        recommended_candidate_id,
        "bundle_manifest.json recommended_candidate_id does not match qc_report.json.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "recommended_candidate_summary",
        design_summary.get("recommended_candidate_id"),
        recommended_candidate_id,
        "design_summary.json recommended_candidate_id does not match qc_report.json.",
    )

    missing_columns = sorted(REQUIRED_CANDIDATE_COLUMNS - set(candidate_headers))
    _record_check(semantic_checks, "candidate_csv_columns", not missing_columns)
    if missing_columns:
        semantic_errors.append(f"candidate_ranking.csv is missing required columns: {', '.join(missing_columns)}.")
    missing_explainability_columns = sorted(EXPLAINABILITY_CANDIDATE_COLUMNS - set(candidate_headers))
    if missing_explainability_columns:
        semantic_warnings.append(
            f"candidate_ranking.csv is missing optimizer explainability columns: {', '.join(missing_explainability_columns)}."
        )
        semantic_checks["candidate_csv_explainability_columns"] = "warning"
    else:
        semantic_checks["candidate_csv_explainability_columns"] = "pass"
    _record_check(semantic_checks, "candidate_csv_rows", bool(candidate_rows))
    if not candidate_rows:
        semantic_errors.append("candidate_ranking.csv does not contain any candidate rows.")
    elif recommended_candidate_id and recommended_candidate_id not in {row.get("candidate_id") for row in candidate_rows}:
        semantic_errors.append("Recommended candidate is not present in candidate_ranking.csv.")
        semantic_checks["recommended_candidate_in_csv"] = "fail"
    else:
        semantic_checks["recommended_candidate_in_csv"] = "pass"

    recommended_rows = [row for row in candidate_rows if row.get("candidate_id") == recommended_candidate_id]
    recommended_row = recommended_rows[0] if recommended_rows else {}
    has_explainability_columns = not missing_explainability_columns
    _record_check(
        semantic_checks,
        "candidate_csv_selection_trace",
        (not has_explainability_columns) or all(row.get("selection_trace") for row in candidate_rows),
    )
    if has_explainability_columns and candidate_rows and any(not row.get("selection_trace") for row in candidate_rows):
        semantic_errors.append("candidate_ranking.csv has candidate rows without selection_trace.")
    _record_check(
        semantic_checks,
        "candidate_csv_constraint_risk",
        (not has_explainability_columns) or all(row.get("constraint_risk_status") in {"pass", "warning", "fail"} for row in candidate_rows),
    )
    if has_explainability_columns and candidate_rows and any(row.get("constraint_risk_status") not in {"pass", "warning", "fail"} for row in candidate_rows):
        semantic_errors.append("candidate_ranking.csv has invalid constraint_risk_status values.")
    if has_explainability_columns and recommended_candidate_id and recommended_row:
        _expect_equal(
            semantic_checks,
            semantic_errors,
            "recommended_constraint_risk_csv",
            recommended_row.get("constraint_risk_status"),
            (recommended.get("constraint_risk") or {}).get("status"),
            "candidate_ranking.csv recommended constraint_risk_status does not match qc_report.json.",
        )

    if not optimizer_manifest.get("objective_inventory"):
        semantic_errors.append("optimizer_reproducibility.json objective_inventory is empty.")
        semantic_checks["objective_inventory"] = "fail"
    else:
        semantic_checks["objective_inventory"] = "pass"

    recommendation_audit = qc_report.get("recommendation_audit") or candidate_diagnostics.get("recommendation_audit") or {}
    if recommendation_audit.get("audit_schema") != "agentic-rag-recommendation-audit-v1":
        semantic_errors.append("recommendation_audit is missing or has an invalid schema.")
        semantic_checks["recommendation_audit"] = "fail"
    else:
        semantic_checks["recommendation_audit"] = "pass"
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "recommendation_audit_file_schema",
        recommendation_audit_file.get("audit_schema"),
        "agentic-rag-recommendation-audit-v1",
        "recommendation_audit.json audit_schema is not recognized.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "recommendation_audit_file_report",
        _hash_payload(recommendation_audit_file),
        _hash_payload(recommendation_audit),
        "recommendation_audit.json does not match qc_report.json/candidate_diagnostics recommendation_audit.",
    )
    _expect_equal(
        semantic_checks,
        semantic_errors,
        "recommendation_audit_candidate",
        recommendation_audit_file.get("recommended_candidate_id"),
        recommended_candidate_id,
        "recommendation_audit.json recommended_candidate_id does not match qc_report.json.",
    )

    if artifact_verification.get("status") == "warning":
        semantic_warnings.append("Underlying artifact manifest verification returned warning.")

    return _qc_verification_result(
        artifact_verification,
        semantic_errors,
        semantic_warnings,
        semantic_checks,
        metadata,
    )


def _design_summary(design: dict[str, Any]) -> dict[str, Any]:
    recommended = design.get("recommended_candidate") or {}
    return {
        "run_id": design.get("run_id"),
        "timestamp": design.get("timestamp"),
        "pipeline_version": design.get("pipeline_version"),
        "target": design.get("target"),
        "recommended_candidate_id": recommended.get("candidate_id"),
        "recommended_scores": recommended.get("scores"),
        "recommended_selection_trace": recommended.get("selection_trace", []),
        "recommended_constraint_risk": recommended.get("constraint_risk", {}),
        "qc_gate": design.get("qc_gate"),
        "optimizer_reproducibility": (design.get("qc_report") or {}).get("optimizer_reproducibility") or optimizer_reproducibility_manifest(design),
        "warnings": design.get("warnings", []),
        "provenance": design.get("provenance", {}),
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


def _recommendation_audit_payload(design: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    return (
        design.get("recommendation_audit")
        or (design.get("candidate_diagnostics") or {}).get("recommendation_audit")
        or report.get("recommendation_audit")
        or (report.get("candidate_diagnostics") or {}).get("recommendation_audit")
        or {}
    )


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _read_json_member(archive: ZipFile, path: str) -> dict[str, Any]:
    payload = json.loads(archive.read(path).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return payload


def _read_candidate_csv(archive: ZipFile) -> tuple[list[dict[str, str]], list[str], str]:
    text = archive.read("candidate_ranking.csv").decode("utf-8")
    reader = csv.DictReader(StringIO(text))
    return list(reader), list(reader.fieldnames or []), text


def _record_check(checks: dict[str, str], name: str, passed: bool) -> None:
    checks[name] = "pass" if passed else "fail"


def _normalized_request_value(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").split())


def _hash_payload(payload: Any) -> str:
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _hash_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _expect_equal(
    checks: dict[str, str],
    errors: list[str],
    name: str,
    actual: Any,
    expected: Any,
    message: str,
) -> None:
    passed = actual == expected and actual is not None
    _record_check(checks, name, passed)
    if not passed:
        errors.append(message)


def _qc_verification_result(
    artifact_verification: dict[str, Any],
    semantic_errors: list[str],
    semantic_warnings: list[str],
    semantic_checks: dict[str, str],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    artifact_errors = list(artifact_verification.get("errors") or [])
    artifact_warnings = list(artifact_verification.get("warnings") or [])
    errors = artifact_errors + semantic_errors
    warnings = artifact_warnings + semantic_warnings
    semantic_status = "fail" if semantic_errors else "warning" if semantic_warnings else "pass"
    status = "fail" if errors or artifact_verification.get("status") == "fail" else "warning" if warnings else "pass"
    return {
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "semantic_status": semantic_status,
        "semantic_errors": semantic_errors,
        "semantic_warnings": semantic_warnings,
        "semantic_checks": semantic_checks,
        "artifact_verification": artifact_verification,
        "artifact_type": artifact_verification.get("artifact_type"),
        "manifest_hash": artifact_verification.get("manifest_hash"),
        "signature": artifact_verification.get("signature"),
        "file_count": artifact_verification.get("file_count"),
        "checked_files": artifact_verification.get("checked_files"),
        "total_bytes": artifact_verification.get("total_bytes"),
        "bundle_metadata": artifact_verification.get("bundle_metadata") or {},
        **metadata,
    }
