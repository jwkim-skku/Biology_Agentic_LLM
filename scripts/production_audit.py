from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "backend" / "app" / "data" / "runtime" / "production_audits"
DEFAULT_API_BASE = "http://127.0.0.1:8000/api/v1"

RAG_EVALUATION_AUDIT_PAYLOAD = {
    "query": "SNCA substantia nigra dopaminergic neuron AAV",
    "filters": {
        "species": "human",
        "brain_region": "substantia nigra",
        "cell_type": "dopaminergic neuron",
        "modality": "AAV",
    },
    "limit": 8,
}
QC_BUNDLE_AUDIT_PAYLOAD = {
    "cds": "ATGGCTGCTGCTGCTTAA",
    "target": {
        "gene": "DEMO",
        "species": "human",
        "brain_region": "cortex",
        "cell_type": "neuron",
        "modality": "AAV",
    },
    "optimization_settings": {
        "population_size": 16,
        "generations": 2,
        "mutation_rate": 0.04,
        "crossover_rate": 0.8,
        "seed": 42,
        "max_candidates": 3,
    },
}

API_CHECKS = [
    {"name": "health_ready", "path": "/health/ready"},
    {"name": "deployment_readiness", "path": "/deployment/readiness"},
    {"name": "security_status", "path": "/security/status"},
    {"name": "storage_status", "path": "/storage/status"},
    {"name": "data_provenance", "path": "/data/provenance"},
    {"name": "data_release_bundle_verify", "path": "/data/release/export/verify"},
    {"name": "external_sources", "path": "/data/external-sources"},
    {"name": "rag_diagnostics", "path": "/rag/diagnostics"},
    {"name": "rag_evaluation_bundle_verify", "path": "/rag/evaluate/export/verify", "method": "POST", "json": RAG_EVALUATION_AUDIT_PAYLOAD},
    {"name": "rag_regression_bundle_verify", "path": "/rag/regression/export/verify"},
    {"name": "optimizer_diagnostics", "path": "/optimizer/diagnostics"},
    {"name": "optimizer_benchmark_bundle_verify", "path": "/optimizer/benchmark/export/verify"},
    {"name": "qc_report_bundle_verify", "path": "/report/export-bundle/verify", "method": "POST", "json": QC_BUNDLE_AUDIT_PAYLOAD},
    {"name": "governance_attestation_verify", "path": "/governance/attestation/verify"},
    {"name": "artifact_ledger_verify", "path": "/artifacts/ledger/verify"},
    {"name": "qc_bundle_archive_semantics", "path": "/artifacts/qc-bundles/semantic-summary?limit=6&verify_files=false"},
    {"name": "structured_import_archive_semantics", "path": "/artifacts/structured-imports/semantic-summary?limit=6&verify_files=false"},
    {"name": "data_refresh_plan_archive_semantics", "path": "/artifacts/data-refresh-plans/semantic-summary?limit=6&verify_files=false"},
    {"name": "data_release_archive_semantics", "path": "/artifacts/data-releases/semantic-summary?limit=6&verify_files=false"},
    {"name": "rag_evaluation_archive_semantics", "path": "/artifacts/rag-evaluations/semantic-summary?limit=6&verify_files=false"},
    {"name": "rag_regression_archive_semantics", "path": "/artifacts/rag-regressions/semantic-summary?limit=6&verify_files=false"},
    {"name": "rag_vector_index_archive_semantics", "path": "/artifacts/rag-vector-indexes/semantic-summary?limit=6&verify_files=false"},
    {"name": "optimizer_benchmark_archive_semantics", "path": "/artifacts/optimizer-benchmarks/semantic-summary?limit=6&verify_files=false"},
]

REQUIRED_PREFLIGHT_CHECKS = [
    "backend_compile",
    "production_env_template",
    "compose_preflight",
    "production_audit_template",
    "api_contract",
    "structured_import_cli_preview",
    "data_refresh_cli_plan",
    "data_refresh_cli_validate",
    "golden_response",
    "golden_value",
    "manual_backend_tests",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a production audit report for the Agentic RAG platform.")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="Backend API base URL.")
    parser.add_argument("--api-key", default=os.environ.get("PRODUCTION_AUDIT_API_KEY", ""), help="Optional API key.")
    parser.add_argument("--api-timeout", type=float, default=20.0, help="Seconds to wait for each live API check.")
    parser.add_argument("--env-path", type=Path, default=ROOT / ".env.production", help="Production env file path.")
    parser.add_argument("--template", action="store_true", help="Audit .env.production.example shape instead of strict secrets.")
    parser.add_argument("--skip-api", action="store_true", help="Skip live API checks.")
    parser.add_argument("--require-api", action="store_true", help="Fail if live API checks cannot be completed.")
    parser.add_argument("--require-docker", action="store_true", help="Require docker compose config execution.")
    parser.add_argument(
        "--preflight-evidence",
        type=Path,
        help="Optional preflight JSON evidence file to validate as part of the audit.",
    )
    parser.add_argument(
        "--max-preflight-age-hours",
        type=float,
        default=24.0,
        help="Maximum allowed preflight evidence age when --preflight-evidence is provided.",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Directory for JSON/Markdown reports.")
    parser.add_argument("--no-write", action="store_true", help="Print the audit JSON without writing files.")
    args = parser.parse_args()

    started = datetime.now(timezone.utc)
    checks: list[dict[str, Any]] = []
    checks.append(run_local_check("production_env", env_command(args), cwd=ROOT))
    checks.append(run_local_check("compose_preflight", compose_command(args), cwd=ROOT))
    if args.preflight_evidence:
        checks.append(validate_preflight_evidence(args.preflight_evidence, max_age_hours=args.max_preflight_age_hours))
    if not args.skip_api:
        checks.extend(run_api_checks(args.api_base.rstrip("/"), args.api_key, require_api=args.require_api, timeout=args.api_timeout))

    summary = summarize(checks)
    report = {
        "audit_schema": "agentic-rag-cli-production-audit-v1",
        "generated_at": started.isoformat(),
        "purpose": "deployment promotion evidence for the Agentic RAG codon optimization platform",
        "duration_seconds": round(time.perf_counter() - STARTED_MONOTONIC, 3),
        "mode": {
            "env": "template" if args.template else "strict",
            "api_base": args.api_base.rstrip("/"),
            "skip_api": args.skip_api,
            "require_api": args.require_api,
            "require_docker": args.require_docker,
            "preflight_evidence": str(resolve_rooted_path(args.preflight_evidence)) if args.preflight_evidence else None,
            "max_preflight_age_hours": args.max_preflight_age_hours if args.preflight_evidence else None,
        },
        "summary": summary,
        "checks": checks,
    }
    report["audit_hash"] = audit_hash(report)

    if args.no_write:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        stem = f"production_audit_{started.strftime('%Y%m%dT%H%M%SZ')}"
        json_path = args.output_dir / f"{stem}.json"
        md_path = args.output_dir / f"{stem}.md"
        json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        md_path.write_text(render_markdown(report), encoding="utf-8")
        print(json.dumps({"summary": summary, "json_path": str(json_path), "markdown_path": str(md_path)}, indent=2, sort_keys=True))

    return 1 if summary["failures"] else 0


STARTED_MONOTONIC = time.perf_counter()


def env_command(args: argparse.Namespace) -> list[str]:
    command = [sys.executable, "scripts/validate_production_env.py"]
    if args.template:
        command.append("--template")
    else:
        command.extend(["--path", str(args.env_path)])
    return command


def compose_command(args: argparse.Namespace) -> list[str]:
    command = [sys.executable, "scripts/compose_preflight.py"]
    if args.require_docker:
        command.append("--require-docker")
    return command


def resolve_rooted_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def run_local_check(name: str, command: list[str], *, cwd: Path) -> dict[str, Any]:
    started = time.perf_counter()
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    parsed_stdout = parse_json(completed.stdout)
    failures, warnings = extract_failures_warnings(parsed_stdout, completed.stderr)
    if completed.returncode != 0 and not failures:
        failures.append(f"command returned {completed.returncode}")
    return {
        "name": name,
        "kind": "local",
        "status": "fail" if completed.returncode != 0 or failures else "pass",
        "returncode": completed.returncode,
        "duration_seconds": round(time.perf_counter() - started, 3),
        "command": command,
        "failures": failures,
        "warnings": warnings,
        "details": parsed_stdout if parsed_stdout is not None else compact_text(completed.stdout),
        "stderr": compact_text(completed.stderr),
    }


def validate_preflight_evidence(path: Path, *, max_age_hours: float) -> dict[str, Any]:
    started = time.perf_counter()
    evidence_path = resolve_rooted_path(path)
    failures: list[str] = []
    warnings: list[str] = []
    details: dict[str, Any] = {
        "path": str(evidence_path),
        "max_age_hours": max_age_hours,
    }
    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        failures.append("preflight evidence file was not found")
        payload = {}
    except json.JSONDecodeError as exc:
        failures.append(f"preflight evidence file is not valid JSON: {exc}")
        payload = {}

    if isinstance(payload, dict):
        details.update(extract_preflight_summary(payload, evidence_path, max_age_hours=max_age_hours, failures=failures, warnings=warnings))
    else:
        failures.append("preflight evidence root must be a JSON object")

    return {
        "name": "preflight_evidence",
        "kind": "evidence",
        "status": "fail" if failures else "pass",
        "duration_seconds": round(time.perf_counter() - started, 3),
        "path": str(evidence_path),
        "failures": failures,
        "warnings": warnings,
        "details": details,
    }


def extract_preflight_summary(
    payload: dict[str, Any],
    evidence_path: Path,
    *,
    max_age_hours: float,
    failures: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    checks = payload.get("checks")
    if not isinstance(checks, list):
        failures.append("preflight evidence is missing checks[]")
        checks = []

    status = payload.get("status")
    failed = payload.get("failed")
    skipped = payload.get("skipped")
    generated_at = str(payload.get("generated_at") or "")
    check_names = [str(check.get("name")) for check in checks if isinstance(check, dict) and check.get("name")]
    missing_checks = sorted(set(REQUIRED_PREFLIGHT_CHECKS) - set(check_names))

    if status != "pass":
        failures.append(f"preflight status is {status!r}, expected 'pass'")
    if failed:
        failures.append(f"preflight failed checks are present: {', '.join(stringify_list(failed))}")
    if missing_checks:
        failures.append(f"preflight evidence is missing required checks: {', '.join(missing_checks)}")
    if not any(isinstance(check, dict) and "details" in check for check in checks):
        warnings.append("preflight evidence does not include parsed details fields")
    validate_preflight_data_evidence(checks, failures)
    if skipped:
        warnings.append(f"preflight skipped checks: {', '.join(stringify_list(skipped))}")

    age_hours = preflight_age_hours(generated_at)
    if age_hours is None:
        warnings.append("preflight generated_at could not be parsed")
    elif age_hours > max_age_hours:
        failures.append(f"preflight evidence is stale: {age_hours:.2f}h old exceeds {max_age_hours:.2f}h")

    return {
        "status": status,
        "generated_at": generated_at,
        "age_hours": round(age_hours, 3) if age_hours is not None else None,
        "checks": len(checks),
        "required_checks": REQUIRED_PREFLIGHT_CHECKS,
        "missing_required_checks": missing_checks,
        "failed": failed if isinstance(failed, list) else stringify_list(failed),
        "skipped": skipped if isinstance(skipped, list) else stringify_list(skipped),
        "path_mtime": datetime.fromtimestamp(evidence_path.stat().st_mtime, tz=timezone.utc).isoformat() if evidence_path.exists() else None,
    }


def validate_preflight_data_evidence(checks: list[Any], failures: list[str]) -> None:
    by_name = {str(check.get("name")): check for check in checks if isinstance(check, dict) and check.get("name")}
    structured = check_details(by_name.get("structured_import_cli_preview"))
    preview = structured.get("preview") if isinstance(structured.get("preview"), dict) else {}
    preview_validation = preview.get("validation") if isinstance(preview.get("validation"), dict) else {}
    projected = preview_validation.get("projected") if isinstance(preview_validation.get("projected"), dict) else {}
    records = preview.get("records") if isinstance(preview.get("records"), dict) else {}
    source = preview.get("source") if isinstance(preview.get("source"), dict) else {}
    if structured.get("status") not in {"pass", "warning"} or preview.get("status") not in {"pass", "warning"}:
        failures.append("structured_import_cli_preview did not produce a pass/warning preview.")
    if not source.get("sha256"):
        failures.append("structured_import_cli_preview did not record source sha256.")
    if int_value(records.get("import_count")) <= 0:
        failures.append("structured_import_cli_preview did not report imported records.")
    if int_value(projected.get("error_count")) != 0:
        failures.append("structured_import_cli_preview projected validation errors.")

    plan_details = check_details(by_name.get("data_refresh_cli_plan"))
    plan = plan_details.get("plan") if isinstance(plan_details.get("plan"), dict) else {}
    plan_summary = plan.get("summary") if isinstance(plan.get("summary"), dict) else {}
    if plan_details.get("status") != "pass":
        failures.append("data_refresh_cli_plan status is not pass.")
    if plan.get("dry_run") is not True:
        failures.append("data_refresh_cli_plan did not run as a dry run.")
    if int_value(plan_summary.get("planned")) <= 0:
        failures.append("data_refresh_cli_plan did not plan any operations.")
    if not plan.get("manifest_hash"):
        failures.append("data_refresh_cli_plan did not include a manifest hash.")

    validation_details = check_details(by_name.get("data_refresh_cli_validate"))
    validation = validation_details.get("validation") if isinstance(validation_details.get("validation"), dict) else {}
    refresh_plan = validation.get("plan") if isinstance(validation.get("plan"), dict) else {}
    normalized = validation.get("normalized_request") if isinstance(validation.get("normalized_request"), dict) else {}
    if validation.get("validation_schema") != "agentic-rag-data-refresh-plan-validation-v1":
        failures.append("data_refresh_cli_validate did not include the expected validation schema.")
    if validation.get("status") not in {"pass", "warning"}:
        failures.append("data_refresh_cli_validate status is not pass/warning.")
    if int_value(refresh_plan.get("operation_count")) <= 0:
        failures.append("data_refresh_cli_validate did not report planned operations.")
    if not normalized.get("genes") or not normalized.get("brain_regions"):
        failures.append("data_refresh_cli_validate did not record normalized genes and brain regions.")


def check_details(check: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(check, dict):
        return {}
    details = check.get("details")
    return details if isinstance(details, dict) else {}


def int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def preflight_age_hours(generated_at: str) -> float | None:
    if not generated_at:
        return None
    try:
        parsed = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds() / 3600


def run_api_checks(api_base: str, api_key: str, *, require_api: bool, timeout: float) -> list[dict[str, Any]]:
    checks = []
    for check in API_CHECKS:
        checks.append(fetch_api_check(check, f"{api_base}{check['path']}", api_key, require_api=require_api, timeout=timeout))
    return checks


def fetch_api_check(check: dict[str, Any], url: str, api_key: str, *, require_api: bool, timeout: float) -> dict[str, Any]:
    name = str(check["name"])
    started = time.perf_counter()
    body = None
    headers: dict[str, str] = {}
    if "json" in check:
        body = json.dumps(check["json"], ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, method=str(check.get("method") or ("POST" if body else "GET")), headers=headers)
    if api_key:
        request.add_header("X-API-Key", api_key)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            details = parse_json(body)
            warnings = api_warnings(name, details)
            failures = api_failures(name, details)
            return {
                "name": name,
                "kind": "api",
                "status": "fail" if failures else "pass",
                "http_status": response.status,
                "duration_seconds": round(time.perf_counter() - started, 3),
                "url": url,
                "failures": failures,
                "warnings": warnings,
                "details": details if details is not None else compact_text(body),
            }
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        message = f"{type(exc).__name__}: {exc}"
        return {
            "name": name,
            "kind": "api",
            "status": "fail" if require_api else "warning",
            "duration_seconds": round(time.perf_counter() - started, 3),
            "url": url,
            "failures": [message] if require_api else [],
            "warnings": [] if require_api else [message],
            "details": {},
        }


def parse_json(text: str) -> Any | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def extract_failures_warnings(details: Any, stderr: str) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    if isinstance(details, dict):
        failures.extend(stringify_list(details.get("failures")))
        failures.extend(stringify_list(details.get("failed")))
        warnings.extend(stringify_list(details.get("warnings")))
    if stderr.strip():
        warnings.append(compact_text(stderr))
    return failures, warnings


def api_failures(name: str, details: Any) -> list[str]:
    payload = api_payload(details)
    if not isinstance(payload, dict):
        return []
    failures: list[str] = []
    if name == "health_ready" and payload.get("status") != "ready":
        failures.append("health readiness endpoint is not ready")
    if name == "deployment_readiness" and not payload.get("deployment_ready"):
        failures.append("deployment readiness has blocking failures")
    if name == "data_provenance" and payload.get("status") == "fail":
        failures.append("data provenance status is fail")
    if name == "rag_diagnostics" and payload.get("status") == "fail":
        failures.append("RAG diagnostics status is fail")
    if name == "optimizer_diagnostics" and payload.get("status") == "fail":
        failures.append("optimizer diagnostics status is fail")
    if name in {
        "data_release_bundle_verify",
        "rag_evaluation_bundle_verify",
        "rag_regression_bundle_verify",
        "optimizer_benchmark_bundle_verify",
        "qc_report_bundle_verify",
    }:
        if payload.get("status") == "fail":
            failures.append(f"{name} status is fail")
        if payload.get("semantic_status") == "fail":
            failures.append(f"{name} semantic_status is fail")
    if name == "rag_evaluation_bundle_verify":
        checks = payload.get("semantic_checks") or {}
        required_rag_explainability = [
            "retrieval_trace_schema",
            "evidence_sufficiency_schema",
            "facet_gap_analysis",
            "query_term_coverage",
        ]
        missing = [check for check in required_rag_explainability if checks.get(check) != "pass"]
        if missing:
            failures.append(f"RAG evaluation bundle does not verify explainability checks: {', '.join(missing)}.")
    if name == "data_release_bundle_verify":
        checks = payload.get("semantic_checks") or {}
        for hash_check in ["records_hash", "records_csv_hash"]:
            if checks.get(hash_check) != "pass" or not payload.get(hash_check):
                failures.append(f"Data release bundle does not verify {hash_check}.")
        if checks.get("external_snapshot_reference_coverage") != "pass":
            failures.append("Data release bundle does not verify external_snapshot_reference_coverage.")
    if name == "rag_regression_bundle_verify":
        checks = payload.get("semantic_checks") or {}
        if checks.get("results_hash") != "pass" or not payload.get("results_hash"):
            failures.append("RAG regression bundle does not verify results_hash.")
    if name == "optimizer_benchmark_bundle_verify":
        checks = payload.get("semantic_checks") or {}
        required_optimizer_checks = [
            "search_strategy_schema",
            "optimizer_seed_strategy",
            "candidate_diagnostics",
            "recommendation_audit",
            "case_metric_columns",
        ]
        missing = [check for check in required_optimizer_checks if checks.get(check) != "pass"]
        if missing:
            failures.append(f"Optimizer benchmark bundle does not verify semantic checks: {', '.join(missing)}.")
        if checks.get("results_hash") != "pass" or not payload.get("results_hash"):
            failures.append("Optimizer benchmark bundle does not verify results_hash.")
    if name == "qc_report_bundle_verify":
        checks = payload.get("semantic_checks") or {}
        if checks.get("request_payload") != "pass":
            failures.append("QC report bundle does not verify request_payload.")
        for hash_check in ["request_hash", "qc_report_hash", "candidate_ranking_hash", "recommendation_audit_hash"]:
            if checks.get(hash_check) != "pass" or not payload.get(hash_check):
                failures.append(f"QC report bundle does not verify {hash_check}.")
        required_qc_explainability = [
            "optimizer_hash_report",
            "candidate_csv_explainability_columns",
            "recommended_constraint_risk_csv",
            "objective_inventory",
            "recommendation_audit",
        ]
        missing = [check for check in required_qc_explainability if checks.get(check) != "pass"]
        if missing:
            failures.append(f"QC report bundle does not verify explainability checks: {', '.join(missing)}.")
        failed_targets = [key for key, value in checks.items() if key.startswith("request_target_") and value != "pass"]
        if failed_targets:
            failures.append(f"QC report bundle request target checks failed: {', '.join(failed_targets)}")
    if name == "governance_attestation_verify" and payload.get("status") not in {None, "pass"}:
        failures.append("governance attestation verification did not pass")
    if name == "artifact_ledger_verify" and payload.get("status") not in {None, "pass"}:
        failures.append("artifact ledger verification did not pass")
    if name.endswith("_archive_semantics") and payload.get("status") == "fail":
        failures.append(f"{name} archive semantic summary is fail")
    if name == "data_release_archive_semantics":
        latest = (payload.get("latest_artifacts") or [{}])[0]
        checked_count = int(payload.get("checked_count") or 0)
        if checked_count and not latest.get("records_hash"):
            failures.append("latest data release archive is missing records_hash")
        if checked_count and not latest.get("records_csv_hash"):
            failures.append("latest data release archive is missing records_csv_hash")
        if checked_count and latest.get("external_snapshot_missing_count") not in {0, None}:
            failures.append("latest data release archive has missing external source snapshots")
        if checked_count and int(latest.get("external_snapshot_referenced_count") or 0) < 1:
            failures.append("latest data release archive is missing external snapshot reference evidence")
        if checked_count and int(latest.get("external_snapshot_contained_count") or 0) < int(latest.get("external_snapshot_referenced_count") or 0):
            failures.append("latest data release archive does not contain all referenced external snapshots")
    if name == "data_refresh_plan_archive_semantics":
        latest = (payload.get("latest_artifacts") or [{}])[0]
        checked_count = int(payload.get("checked_count") or 0)
        if checked_count and int(latest.get("operation_count") or 0) < 1:
            failures.append("latest data refresh plan archive is missing operation_count")
        if checked_count and not latest.get("validation_status"):
            failures.append("latest data refresh plan archive is missing validation_status")
        if checked_count and not latest.get("dataset_id"):
            failures.append("latest data refresh plan archive is missing dataset_id")
        if checked_count and not latest.get("structured_manifest_hash"):
            failures.append("latest data refresh plan archive is missing structured_manifest_hash")
    if name == "rag_vector_index_archive_semantics":
        latest = (payload.get("latest_artifacts") or [{}])[0]
        checked_count = int(payload.get("checked_count") or 0)
        if checked_count and int(latest.get("chunk_count") or 0) < 1:
            failures.append("latest RAG vector index archive is missing chunk_count")
        if checked_count and int(latest.get("embedding_dimensions") or 0) < 1:
            failures.append("latest RAG vector index archive is missing embedding_dimensions")
        if checked_count and not latest.get("embedding_model"):
            failures.append("latest RAG vector index archive is missing embedding_model")
        if checked_count and not latest.get("retrieval_model"):
            failures.append("latest RAG vector index archive is missing retrieval_model")
        if checked_count and not latest.get("structured_manifest_hash"):
            failures.append("latest RAG vector index archive is missing structured_manifest_hash")
    if name == "optimizer_benchmark_archive_semantics":
        latest = (payload.get("latest_artifacts") or [{}])[0]
        checked_count = int(payload.get("checked_count") or 0)
        if checked_count and latest.get("benchmark_status") not in {"pass", "warning"}:
            failures.append("latest optimizer benchmark archive is missing pass/warning benchmark_status")
        if checked_count and latest.get("diagnostics_status") not in {"pass", "warning"}:
            failures.append("latest optimizer benchmark archive is missing pass/warning diagnostics_status")
        if checked_count and latest.get("stress_status") not in {"pass", "warning"}:
            failures.append("latest optimizer benchmark archive is missing pass/warning stress_status")
        if checked_count and int(latest.get("case_count") or 0) < 1:
            failures.append("latest optimizer benchmark archive is missing case_count")
        if checked_count and not latest.get("cases_hash"):
            failures.append("latest optimizer benchmark archive is missing cases_hash")
    return failures


def api_warnings(name: str, details: Any) -> list[str]:
    payload = api_payload(details)
    if not isinstance(payload, dict):
        return []
    warnings: list[str] = []
    if name == "deployment_readiness":
        if not payload.get("production_ready"):
            warnings.append("production_ready is false")
        gates = payload.get("gates")
        if isinstance(gates, list):
            warnings.extend(str(gate.get("message", gate.get("name"))) for gate in gates if isinstance(gate, dict) and gate.get("status") == "warning")
    if name in {"rag_diagnostics", "optimizer_diagnostics", "data_provenance"} and payload.get("status") == "warning":
        warnings.append(f"{name} status is warning")
    if name in {
        "data_release_bundle_verify",
        "rag_evaluation_bundle_verify",
        "rag_regression_bundle_verify",
        "optimizer_benchmark_bundle_verify",
        "qc_report_bundle_verify",
    }:
        if payload.get("status") == "warning":
            warnings.append(f"{name} status is warning")
        if payload.get("semantic_status") == "warning":
            warnings.append(f"{name} semantic_status is warning")
    if name.endswith("_archive_semantics"):
        if int(payload.get("checked_count") or 0) == 0:
            warnings.append(f"{name} has no archived artifacts checked")
        if payload.get("freshness_status") in {"stale", "unknown"}:
            warnings.append(f"{name} freshness is {payload.get('freshness_status')}")
    if name == "qc_bundle_archive_semantics":
        latest = (payload.get("latest_artifacts") or [{}])[0]
        if latest.get("request_payload_status") not in {None, "pass"}:
            warnings.append("latest QC archive request_payload_status is not pass")
    if name == "data_refresh_plan_archive_semantics":
        latest = (payload.get("latest_artifacts") or [{}])[0]
        if latest.get("validation_status") not in {None, "pass", "warning"}:
            warnings.append("latest data refresh plan validation_status is not pass/warning")
        if latest.get("release_lock_status") not in {None, "pass", "warning"}:
            warnings.append("latest data refresh plan release_lock_status is not pass/warning")
    if name == "security_status":
        if not payload.get("auth_enabled"):
            warnings.append("API authentication is not enabled")
        if not payload.get("rate_limit_enabled"):
            warnings.append("rate limiting is not enabled")
    if name == "storage_status" and payload.get("backend") != "postgres":
        warnings.append("runtime storage backend is not postgres")
    return warnings


def api_payload(details: Any) -> Any:
    if isinstance(details, dict) and "data" in details and isinstance(details["data"], dict):
        return details["data"]
    return details


def stringify_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def compact_text(text: str, limit: int = 2000) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[:limit] + "...<truncated>"


def audit_hash(report: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in report.items() if key != "audit_hash"}
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return sha256(canonical).hexdigest()


def summarize(checks: list[dict[str, Any]]) -> dict[str, Any]:
    failures = [check["name"] for check in checks if check.get("status") == "fail"]
    warnings = [check["name"] for check in checks if check.get("warnings") or check.get("status") == "warning"]
    return {
        "status": "fail" if failures else "pass",
        "checks": len(checks),
        "failures": failures,
        "warnings": warnings,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Production Audit Report",
        "",
        f"- Audit schema: `{report.get('audit_schema', 'n/a')}`",
        f"- Audit hash: `{report.get('audit_hash', 'n/a')}`",
        f"- Generated at: `{report['generated_at']}`",
        f"- Purpose: `{report.get('purpose', 'n/a')}`",
        f"- Status: `{summary['status']}`",
        f"- Checks: `{summary['checks']}`",
        f"- Failures: `{len(summary['failures'])}`",
        f"- Warnings: `{len(summary['warnings'])}`",
        "",
        "## Checks",
        "",
        "| Check | Kind | Status | Failures | Warnings |",
        "| --- | --- | --- | --- | --- |",
    ]
    for check in report["checks"]:
        lines.append(
            "| {name} | {kind} | {status} | {failures} | {warnings} |".format(
                name=check.get("name", ""),
                kind=check.get("kind", ""),
                status=check.get("status", ""),
                failures=escape_cell("; ".join(check.get("failures", [])) or "-"),
                warnings=escape_cell("; ".join(check.get("warnings", [])) or "-"),
            )
        )
    preflight = next((check for check in report["checks"] if check.get("name") == "preflight_evidence"), None)
    if preflight:
        details = preflight.get("details") if isinstance(preflight.get("details"), dict) else {}
        lines.extend(
            [
                "",
                "## Preflight Evidence",
                "",
                f"- Status: `{preflight.get('status', 'n/a')}`",
                f"- Evidence path: `{details.get('path', preflight.get('path', 'n/a'))}`",
                f"- Generated at: `{details.get('generated_at', 'n/a')}`",
                f"- Age hours: `{details.get('age_hours', 'n/a')}`",
                f"- Required checks: `{len(details.get('required_checks') or [])}`",
                f"- Missing required checks: `{len(details.get('missing_required_checks') or [])}`",
                f"- Failed checks: `{len(details.get('failed') or [])}`",
                f"- Skipped checks: `{len(details.get('skipped') or [])}`",
            ]
        )
    lines.extend(["", "## Operator Notes", ""])
    if summary["failures"]:
        lines.append("Resolve all failed checks before production promotion.")
    elif summary["warnings"]:
        lines.append("No blocking failures were detected. Review warnings before production promotion.")
    else:
        lines.append("No blocking failures or warnings were detected by this audit profile.")
    lines.append("")
    return "\n".join(lines)


def escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
