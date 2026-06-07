from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from io import BytesIO, StringIO
from typing import Any
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from app.optimizer.nsga2 import OptimizationConfig
from app.optimizer.scoring import ScoreConfig
from app.services.export_manifest_service import ManifestedZip, verify_artifact_bundle
from app.services.optimizer_benchmark_service import evaluate_optimizer_benchmark, optimizer_benchmark_cases
from app.services.optimizer_diagnostics_service import optimizer_diagnostics
from app.services.optimizer_stress_service import optimizer_stress_gate
from app.services.rna_folding_service import rna_folding_status
from app.services.structured_data_service import structured_manifest


REQUIRED_OPTIMIZER_BENCHMARK_FILES = {
    "bundle_manifest.json",
    "benchmark.json",
    "diagnostics.json",
    "cases.json",
    "case_metrics.csv",
    "candidate_diagnostics.json",
    "stress_gate.json",
    "rna_folding_status.json",
    "optimizer_config.json",
    "score_config.json",
    "structured_manifest.json",
}


def build_optimizer_benchmark_bundle() -> bytes:
    benchmark = evaluate_optimizer_benchmark()
    diagnostics = optimizer_diagnostics()
    stress = optimizer_stress_gate()
    folding = rna_folding_status()
    cases = optimizer_benchmark_cases()
    metadata = {
        "bundle_schema": "agentic-rag-optimizer-benchmark-bundle-v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_status": benchmark.get("status"),
        "case_count": benchmark.get("case_count"),
        "cases_hash": benchmark.get("cases_hash"),
        "diagnostics_status": diagnostics.get("status"),
        "stress_status": stress.get("status"),
        "rna_folding_status": folding.get("status"),
        "rna_folding_backend": folding.get("active_backend"),
        "structured_manifest_hash": structured_manifest().get("manifest_hash"),
    }
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        bundle = ManifestedZip(archive, "optimizer_benchmark_bundle", metadata)
        bundle.writestr("bundle_manifest.json", _json(metadata))
        bundle.writestr("benchmark.json", _json(benchmark))
        bundle.writestr("diagnostics.json", _json(diagnostics))
        bundle.writestr("stress_gate.json", _json(stress))
        bundle.writestr("rna_folding_status.json", _json(folding))
        bundle.writestr("cases.json", _json(cases))
        bundle.writestr("case_metrics.csv", _case_metrics_csv(benchmark.get("results") or []))
        bundle.writestr("candidate_diagnostics.json", _json(_candidate_diagnostics(benchmark.get("results") or [])))
        bundle.writestr("optimizer_config.json", _json(OptimizationConfig().to_dict()))
        bundle.writestr("score_config.json", _json(ScoreConfig().to_dict()))
        bundle.writestr("structured_manifest.json", _json(structured_manifest()))
        bundle.write_artifact_manifest()
    return buffer.getvalue()


def verify_optimizer_benchmark_bundle(bundle: bytes) -> dict[str, Any]:
    base = verify_artifact_bundle(bundle)
    errors = list(base.get("errors") or [])
    warnings = list(base.get("warnings") or [])
    semantic_errors: list[str] = []
    semantic_warnings: list[str] = []
    semantic_checks: dict[str, str] = {}

    if base.get("artifact_type") != "optimizer_benchmark_bundle":
        semantic_errors.append("Artifact manifest artifact_type must be optimizer_benchmark_bundle.")
        semantic_checks["artifact_type"] = "fail"
    else:
        semantic_checks["artifact_type"] = "pass"

    try:
        with ZipFile(BytesIO(bundle), "r") as archive:
            names = set(archive.namelist())
            missing = sorted(REQUIRED_OPTIMIZER_BENCHMARK_FILES - names)
            if missing:
                semantic_errors.append(f"Required optimizer benchmark files are missing: {', '.join(missing)}.")
                semantic_checks["required_files"] = "fail"
                payloads: dict[str, Any] = {}
                metric_rows: list[dict[str, str]] = []
            else:
                semantic_checks["required_files"] = "pass"
                payloads = {
                    "bundle_manifest": _read_json(archive, "bundle_manifest.json"),
                    "benchmark": _read_json(archive, "benchmark.json"),
                    "diagnostics": _read_json(archive, "diagnostics.json"),
                    "stress_gate": _read_json(archive, "stress_gate.json"),
                    "rna_folding_status": _read_json(archive, "rna_folding_status.json"),
                    "cases": _read_json(archive, "cases.json"),
                    "candidate_diagnostics": _read_json(archive, "candidate_diagnostics.json"),
                    "optimizer_config": _read_json(archive, "optimizer_config.json"),
                    "score_config": _read_json(archive, "score_config.json"),
                    "structured_manifest": _read_json(archive, "structured_manifest.json"),
                }
                metric_rows = list(csv.DictReader(StringIO(archive.read("case_metrics.csv").decode("utf-8"))))
    except (BadZipFile, json.JSONDecodeError, UnicodeDecodeError, csv.Error) as exc:
        semantic_errors.append(f"Invalid optimizer benchmark bundle: {exc}")
        payloads = {}
        metric_rows = []

    manifest = payloads.get("bundle_manifest") or {}
    benchmark = payloads.get("benchmark") or {}
    diagnostics = payloads.get("diagnostics") or {}
    stress = payloads.get("stress_gate") or {}
    folding = payloads.get("rna_folding_status") or {}
    cases = payloads.get("cases") or {}
    candidate_diagnostics = payloads.get("candidate_diagnostics") or {}
    structured = payloads.get("structured_manifest") or {}
    metadata = base.get("bundle_metadata") or {}

    if manifest and manifest.get("bundle_schema") != "agentic-rag-optimizer-benchmark-bundle-v1":
        semantic_errors.append("bundle_manifest.json bundle_schema is invalid.")
        semantic_checks["bundle_schema"] = "fail"
    elif manifest:
        semantic_checks["bundle_schema"] = "pass"

    case_count = int(benchmark.get("case_count") or 0)
    if case_count != int(cases.get("case_count") or -1) or case_count != len(metric_rows):
        semantic_errors.append("case_count does not match cases.json and case_metrics.csv.")
        semantic_checks["case_count"] = "fail"
    else:
        semantic_checks["case_count"] = "pass"

    case_hashes = {str(value) for value in [manifest.get("cases_hash"), metadata.get("cases_hash"), benchmark.get("cases_hash"), cases.get("cases_hash")] if value}
    if len(case_hashes) != 1:
        semantic_errors.append("Optimizer benchmark case hashes are missing or disagree.")
        semantic_checks["cases_hash"] = "fail"
    else:
        semantic_checks["cases_hash"] = "pass"

    if diagnostics.get("diagnostics_schema") != "agentic-rag-optimizer-diagnostics-v1":
        semantic_errors.append("diagnostics.json diagnostics_schema is invalid.")
        semantic_checks["diagnostics_schema"] = "fail"
    else:
        semantic_checks["diagnostics_schema"] = "pass"

    if stress.get("stress_schema") != "agentic-rag-optimizer-stress-gate-v1":
        semantic_errors.append("stress_gate.json stress_schema is invalid.")
        semantic_checks["stress_schema"] = "fail"
    else:
        semantic_checks["stress_schema"] = "pass"

    if folding.get("folding_schema") != "agentic-rag-rna-folding-v1":
        semantic_errors.append("rna_folding_status.json folding_schema is invalid.")
        semantic_checks["rna_folding_schema"] = "fail"
    else:
        semantic_checks["rna_folding_schema"] = "pass"

    folding_statuses = {str(value) for value in [manifest.get("rna_folding_status"), metadata.get("rna_folding_status"), folding.get("status")] if value}
    if len(folding_statuses) > 1:
        semantic_errors.append("RNA folding status disagrees across optimizer benchmark bundle files.")
        semantic_checks["rna_folding_status"] = "fail"
    elif folding_statuses:
        semantic_checks["rna_folding_status"] = "pass"

    if benchmark.get("benchmark_schema") != "agentic-rag-optimizer-benchmark-v1":
        semantic_errors.append("benchmark.json benchmark_schema is invalid.")
        semantic_checks["benchmark_schema"] = "fail"
    else:
        semantic_checks["benchmark_schema"] = "pass"

    results = benchmark.get("results") or []
    diag_cases = candidate_diagnostics.get("cases") or []
    if len(results) != len(diag_cases):
        semantic_errors.append("candidate_diagnostics.json does not include every benchmark result.")
        semantic_checks["candidate_diagnostics"] = "fail"
    else:
        semantic_checks["candidate_diagnostics"] = "pass"
    if any((case.get("recommendation_audit") or {}).get("audit_schema") != "agentic-rag-recommendation-audit-v1" for case in diag_cases):
        semantic_errors.append("candidate_diagnostics.json is missing recommendation audit metadata.")
        semantic_checks["recommendation_audit"] = "fail"
    else:
        semantic_checks["recommendation_audit"] = "pass"

    if any(row.get("status") not in {"pass", "warning", "fail"} for row in metric_rows):
        semantic_errors.append("case_metrics.csv contains invalid case status values.")
        semantic_checks["case_metric_status"] = "fail"
    elif metric_rows:
        semantic_checks["case_metric_status"] = "pass"
    else:
        semantic_warnings.append("Optimizer benchmark bundle contains no case metric rows.")
        semantic_checks["case_metric_status"] = "warning"

    manifest_hashes = {str(value) for value in [manifest.get("structured_manifest_hash"), metadata.get("structured_manifest_hash"), structured.get("manifest_hash")] if value}
    if len(manifest_hashes) > 1:
        semantic_errors.append("Structured manifest hashes disagree across optimizer benchmark bundle files.")
        semantic_checks["structured_manifest_hash"] = "fail"
    elif manifest_hashes:
        semantic_checks["structured_manifest_hash"] = "pass"
    else:
        semantic_warnings.append("Structured manifest hash is not recorded in optimizer benchmark bundle.")
        semantic_checks["structured_manifest_hash"] = "warning"

    if not (payloads.get("optimizer_config") or {}).get("score_config"):
        semantic_errors.append("optimizer_config.json does not include score_config.")
        semantic_checks["optimizer_config"] = "fail"
    else:
        semantic_checks["optimizer_config"] = "pass"

    semantic_status = "fail" if semantic_errors else "warning" if semantic_warnings else "pass"
    status = "fail" if errors or semantic_errors else "warning" if warnings or semantic_warnings else "pass"
    return {
        **base,
        "status": status,
        "errors": errors + semantic_errors,
        "warnings": warnings + semantic_warnings,
        "semantic_status": semantic_status,
        "semantic_errors": semantic_errors,
        "semantic_warnings": semantic_warnings,
        "semantic_checks": semantic_checks,
        "benchmark_status": benchmark.get("status"),
        "diagnostics_status": diagnostics.get("status"),
        "case_count": case_count,
        "cases_hash": next(iter(case_hashes), None),
        "structured_manifest_hash": next(iter(manifest_hashes), None),
    }


def _case_metrics_csv(results: list[dict[str, Any]]) -> str:
    fields = [
        "case_id",
        "status",
        "candidate_count",
        "feasible_count",
        "unique_cds_count",
        "constraint_violation_rate",
        "recommended_composite_delta",
        "recommended_secondary_structure_proxy",
        "recommended_mfe_proxy_delta_g",
        "recommended_policy_violation_delta",
        "approx_hypervolume_2d",
        "runtime_ms",
        "recommended_candidate_id",
        "errors",
        "warnings",
    ]
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for result in results:
        metrics = result.get("metrics") or {}
        writer.writerow(
            {
                "case_id": result.get("case_id"),
                "status": result.get("status"),
                "candidate_count": metrics.get("candidate_count"),
                "feasible_count": metrics.get("feasible_count"),
                "unique_cds_count": metrics.get("unique_cds_count"),
                "constraint_violation_rate": metrics.get("constraint_violation_rate"),
                "recommended_composite_delta": metrics.get("recommended_composite_delta"),
                "recommended_secondary_structure_proxy": metrics.get("recommended_secondary_structure_proxy"),
                "recommended_mfe_proxy_delta_g": metrics.get("recommended_mfe_proxy_delta_g"),
                "recommended_policy_violation_delta": metrics.get("recommended_policy_violation_delta"),
                "approx_hypervolume_2d": metrics.get("approx_hypervolume_2d"),
                "runtime_ms": metrics.get("runtime_ms"),
                "recommended_candidate_id": result.get("recommended_candidate_id"),
                "errors": " | ".join(result.get("errors") or []),
                "warnings": " | ".join(result.get("warnings") or []),
            }
        )
    return output.getvalue()


def _candidate_diagnostics(results: list[dict[str, Any]]) -> dict[str, Any]:
    cases = []
    for result in results:
        diagnostics = result.get("candidate_diagnostics") or {}
        cases.append(
            {
                "case_id": result.get("case_id"),
                "status": result.get("status"),
                "run_id": result.get("run_id"),
                "recommended_candidate_id": result.get("recommended_candidate_id"),
                "candidate_count": diagnostics.get("candidate_count"),
                "feasible_count": diagnostics.get("feasible_count"),
                "pareto_front": diagnostics.get("pareto_front"),
                "diversity": diagnostics.get("diversity"),
                "constraint_risk_summary": diagnostics.get("constraint_risk_summary"),
                "best_by_metric": diagnostics.get("best_by_metric"),
                "recommendation_audit": diagnostics.get("recommendation_audit"),
            }
        )
    return {
        "diagnostics_schema": "agentic-rag-optimizer-benchmark-candidate-diagnostics-v1",
        "cases": cases,
    }


def _read_json(archive: ZipFile, name: str) -> dict[str, Any]:
    payload = json.loads(archive.read(name).decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
