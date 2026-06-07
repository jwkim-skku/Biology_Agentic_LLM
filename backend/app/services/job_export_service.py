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
from app.services.run_store import get_run
from app.services.structured_data_service import structured_manifest


def build_job_export_bundle(job: dict[str, Any]) -> bytes:
    result = job.get("result") or {}
    run_ids = _job_run_ids(result)
    buffer = BytesIO()
    files = [
        "artifact_manifest.json",
        "job_manifest.json",
        "request.json",
        "result.json",
        "provenance/structured_manifest.json",
        "provenance/rag_status.json",
    ]

    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        bundle = ManifestedZip(archive, "job_audit_bundle", _job_manifest(job, run_ids, files))
        bundle.writestr("request.json", _json(job.get("request") or {}))
        bundle.writestr("result.json", _json(result))
        bundle.writestr("provenance/structured_manifest.json", _json(structured_manifest()))
        bundle.writestr("provenance/rag_status.json", _json(rag_status()))

        if job.get("error"):
            bundle.writestr("error.txt", str(job["error"]))
            files.append("error.txt")

        if result.get("runs"):
            bundle.writestr("batch_results.csv", _batch_results_csv(result.get("runs") or []))
            bundle.writestr("batch_failures.csv", _batch_failures_csv(result.get("failures") or []))
            files.extend(["batch_results.csv", "batch_failures.csv"])

        run_summaries: list[dict[str, Any]] = []
        for run_id in run_ids:
            run = get_run(run_id)
            if not run:
                run_summaries.append({"run_id": run_id, "status": "missing"})
                continue
            run_summaries.append(_run_summary(run))
            base = f"runs/{run_id}"
            design = run.get("design") or {}
            bundle.writestr(f"{base}/run.json", _json(run))
            bundle.writestr(f"{base}/design.json", _json(design))
            files.extend([f"{base}/run.json", f"{base}/design.json"])
            qc_report = run.get("qc_report") or design.get("qc_report") or {}
            if qc_report:
                bundle.writestr(f"{base}/qc_report.json", _json(qc_report))
                bundle.writestr(f"{base}/qc_report.md", export_qc_report(qc_report, "markdown"))
                bundle.writestr(f"{base}/qc_report.pdf", export_qc_report(qc_report, "pdf"))
                files.extend([f"{base}/qc_report.json", f"{base}/qc_report.md", f"{base}/qc_report.pdf"])
            diagnostics = design.get("candidate_diagnostics") or qc_report.get("candidate_diagnostics") or {}
            if diagnostics:
                bundle.writestr(f"{base}/candidate_diagnostics.json", _json(diagnostics))
                files.append(f"{base}/candidate_diagnostics.json")

        bundle.writestr("run_summaries.json", _json(run_summaries))
        files.append("run_summaries.json")
        job_manifest = _job_manifest(job, run_ids, files)
        bundle.writestr("job_manifest.json", _json(job_manifest))
        bundle.bundle_metadata = job_manifest
        bundle.write_artifact_manifest()

    return buffer.getvalue()


def _job_manifest(job: dict[str, Any], run_ids: list[str], files: list[str]) -> dict[str, Any]:
    return {
        "bundle_schema": "agentic-rag-job-bundle-v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "job_id": job.get("job_id"),
        "job_type": job.get("job_type"),
        "status": job.get("status"),
        "created_at": job.get("created_at"),
        "completed_at": job.get("completed_at"),
        "run_ids": run_ids,
        "files": sorted(set(files)),
    }


def _job_run_ids(result: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    if result.get("run_id"):
        ids.append(str(result["run_id"]))
    for item in result.get("runs") or []:
        run_id = item.get("run_id")
        if run_id:
            ids.append(str(run_id))
    return list(dict.fromkeys(ids))


def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": run.get("run_id"),
        "run_type": run.get("run_type"),
        "gene": run.get("gene"),
        "brain_region": run.get("brain_region"),
        "cell_type": run.get("cell_type"),
        "modality": run.get("modality"),
        "recommended_candidate_id": run.get("recommended_candidate_id"),
        "composite_quality": run.get("composite_quality"),
        "structured_manifest_hash": run.get("structured_manifest_hash"),
    }


def _batch_results_csv(runs: list[dict[str, Any]]) -> str:
    output = StringIO()
    fieldnames = ["gene", "run_id", "recommended_candidate_id", "qc_gate_status", "warning_count", "warnings"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for run in runs:
        warnings = run.get("warnings") or []
        writer.writerow(
            {
                "gene": run.get("gene"),
                "run_id": run.get("run_id"),
                "recommended_candidate_id": run.get("recommended_candidate_id"),
                "qc_gate_status": run.get("qc_gate_status"),
                "warning_count": len(warnings),
                "warnings": " | ".join(str(item) for item in warnings),
            }
        )
    return output.getvalue()


def _batch_failures_csv(failures: list[dict[str, Any]]) -> str:
    output = StringIO()
    fieldnames = ["gene", "error"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for failure in failures:
        writer.writerow({"gene": failure.get("gene"), "error": failure.get("error")})
    return output.getvalue()


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
