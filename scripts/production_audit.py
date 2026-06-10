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
PREFLIGHT_SCHEMA = "agentic-rag-production-preflight-evidence-v1"
PREFLIGHT_SKIP_MODE_TO_CHECK = {
    "skip_frontend": "frontend_build",
    "skip_smoke": "http_smoke",
    "skip_signing_smoke": "signed_http_smoke",
    "skip_ui_smoke": "frontend_smoke",
    "skip_manual_backend_tests": "manual_backend_tests",
}

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
    {"name": "production_audit_status", "path": "/deployment/audit?refresh=true"},
    {"name": "production_audit_bundle_verify", "path": "/deployment/audit/verify?refresh=true"},
    {"name": "security_status", "path": "/security/status"},
    {"name": "storage_status", "path": "/storage/status"},
    {"name": "data_provenance", "path": "/data/provenance"},
    {"name": "data_snapshot_bundle_verify", "path": "/data/snapshot/verify"},
    {"name": "data_release_bundle_verify", "path": "/data/release/export/verify"},
    {"name": "external_sources", "path": "/data/external-sources"},
    {"name": "rag_diagnostics", "path": "/rag/diagnostics"},
    {"name": "rag_embedding_status", "path": "/rag/embedding/status"},
    {"name": "rag_evaluation_bundle_verify", "path": "/rag/evaluate/export/verify", "method": "POST", "json": RAG_EVALUATION_AUDIT_PAYLOAD},
    {"name": "rag_regression_bundle_verify", "path": "/rag/regression/export/verify"},
    {"name": "optimizer_diagnostics", "path": "/optimizer/diagnostics"},
    {"name": "rna_folding_status", "path": "/optimizer/rna-folding/status"},
    {"name": "optimizer_benchmark_bundle_verify", "path": "/optimizer/benchmark/export/verify"},
    {"name": "qc_report_bundle_verify", "path": "/report/export-bundle/verify", "method": "POST", "json": QC_BUNDLE_AUDIT_PAYLOAD},
    {"name": "governance_attestation_verify", "path": "/governance/attestation/verify"},
    {"name": "artifact_ledger_verify", "path": "/artifacts/ledger/verify"},
    {"name": "artifact_object_store_mirror_plan", "path": "/artifacts/object-store/mirror/plan?limit=500"},
    {"name": "qc_bundle_archive_semantics", "path": "/artifacts/qc-bundles/semantic-summary?limit=6&verify_files=false"},
    {"name": "structured_import_archive_semantics", "path": "/artifacts/structured-imports/semantic-summary?limit=6&verify_files=false"},
    {"name": "data_snapshot_archive_semantics", "path": "/artifacts/data-snapshots/semantic-summary?limit=6&verify_files=false"},
    {"name": "data_refresh_plan_archive_semantics", "path": "/artifacts/data-refresh-plans/semantic-summary?limit=6&verify_files=false"},
    {"name": "data_release_archive_semantics", "path": "/artifacts/data-releases/semantic-summary?limit=6&verify_files=false"},
    {"name": "rag_evaluation_archive_semantics", "path": "/artifacts/rag-evaluations/semantic-summary?limit=6&verify_files=false"},
    {"name": "rag_regression_archive_semantics", "path": "/artifacts/rag-regressions/semantic-summary?limit=6&verify_files=false"},
    {"name": "rag_vector_index_archive_semantics", "path": "/artifacts/rag-vector-indexes/semantic-summary?limit=6&verify_files=false"},
    {"name": "optimizer_benchmark_archive_semantics", "path": "/artifacts/optimizer-benchmarks/semantic-summary?limit=6&verify_files=false"},
    {"name": "workflow_trace_archive_semantics", "path": "/artifacts/workflow-traces/semantic-summary?limit=6&verify_files=false"},
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
        print(json.dumps(write_result(report, json_path=json_path, markdown_path=md_path), indent=2, sort_keys=True))

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
    else:
        command.append("--static-only")
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
    mode = payload.get("mode") if isinstance(payload.get("mode"), dict) else {}
    generated_at = str(payload.get("generated_at") or "")
    checks_hash = payload.get("checks_hash")
    preflight_hash = payload.get("preflight_hash")
    mode_hash = payload.get("mode_hash")
    skipped_hash = payload.get("skipped_hash")
    preflight_schema = payload.get("preflight_schema")
    required_checks = payload.get("required_checks")
    root = str(payload.get("root") or "")
    output_json = str(payload.get("output_json") or "")
    check_names = [str(check.get("name")) for check in checks if isinstance(check, dict) and check.get("name")]
    actual_failed_checks = sorted(
        str(check.get("name"))
        for check in checks
        if isinstance(check, dict) and check.get("name") and int_or_default(check.get("returncode"), 0) != 0
    )
    status_mismatch_checks = sorted(
        str(check.get("name"))
        for check in checks
        if isinstance(check, dict)
        and check.get("name")
        and check.get("status") != ("pass" if int_or_default(check.get("returncode"), 0) == 0 else "fail")
    )
    missing_command_checks = sorted(
        str(check.get("name"))
        for check in checks
        if isinstance(check, dict) and check.get("name") and not _valid_command_evidence(check.get("command"))
    )
    missing_cwd_checks = sorted(
        str(check.get("name"))
        for check in checks
        if isinstance(check, dict) and check.get("name") and not str(check.get("cwd") or "").strip()
    )
    missing_duration_checks = sorted(
        str(check.get("name"))
        for check in checks
        if isinstance(check, dict) and check.get("name") and not _valid_duration_evidence(check.get("duration_seconds"))
    )
    missing_details_checks = sorted(
        str(check.get("name"))
        for check in checks
        if isinstance(check, dict) and check.get("name") and "details" not in check
    )
    missing_output_checks = sorted(
        str(check.get("name"))
        for check in checks
        if isinstance(check, dict)
        and check.get("name")
        and (not isinstance(check.get("stdout"), str) or not isinstance(check.get("stderr"), str))
    )
    declared_failed_checks = sorted(stringify_list(failed))
    skipped_list = skipped if isinstance(skipped, list) else stringify_list(skipped)
    missing_checks = sorted(set(REQUIRED_PREFLIGHT_CHECKS) - set(check_names) - {str(item) for item in skipped_list})

    if preflight_schema != PREFLIGHT_SCHEMA:
        failures.append(f"preflight_schema is {preflight_schema!r}, expected {PREFLIGHT_SCHEMA!r}")
    if required_checks != REQUIRED_PREFLIGHT_CHECKS:
        failures.append("preflight required_checks does not match the production audit required check set.")
    if int_or_default(payload.get("required_check_count"), -1) != len(REQUIRED_PREFLIGHT_CHECKS):
        failures.append("preflight required_check_count does not match required_checks.")
    if Path(root).resolve() != ROOT:
        failures.append("preflight root does not match the repository root.")
    if Path(output_json).resolve() != evidence_path:
        failures.append("preflight output_json does not match the supplied evidence path.")
    if int_or_default(payload.get("check_count"), -1) != len(checks):
        failures.append("preflight check_count does not match checks[].")
    if int_or_default(payload.get("failed_count"), -1) != len(stringify_list(failed)):
        failures.append("preflight failed_count does not match failed[].")
    if declared_failed_checks != actual_failed_checks:
        failures.append("preflight failed[] does not match checks with nonzero returncode.")
    if status_mismatch_checks:
        failures.append(f"preflight check status does not match returncode: {', '.join(status_mismatch_checks)}")
    if missing_command_checks:
        failures.append(f"preflight checks are missing command evidence: {', '.join(missing_command_checks)}")
    if missing_cwd_checks:
        failures.append(f"preflight checks are missing cwd evidence: {', '.join(missing_cwd_checks)}")
    if missing_duration_checks:
        failures.append(f"preflight checks are missing duration evidence: {', '.join(missing_duration_checks)}")
    if missing_details_checks:
        failures.append(f"preflight checks are missing details evidence: {', '.join(missing_details_checks)}")
    if missing_output_checks:
        failures.append(f"preflight checks are missing stdout/stderr evidence: {', '.join(missing_output_checks)}")
    if int_or_default(payload.get("skipped_count"), -1) != len(stringify_list(skipped)):
        failures.append("preflight skipped_count does not match skipped[].")
    if int_or_default(payload.get("passed_count"), -1) != sum(1 for check in checks if isinstance(check, dict) and check.get("returncode") == 0):
        failures.append("preflight passed_count does not match passing checks.")
    expected_skipped = sorted(check for flag, check in PREFLIGHT_SKIP_MODE_TO_CHECK.items() if mode.get(flag) is True)
    if sorted(str(item) for item in skipped_list) != expected_skipped:
        failures.append("preflight skipped[] does not match mode skip flags.")
    expected_mode_hash = hash_payload(mode)
    if mode_hash != expected_mode_hash:
        failures.append("preflight mode_hash is missing or does not match mode.")
    expected_skipped_hash = hash_payload(skipped_list)
    if skipped_hash != expected_skipped_hash:
        failures.append("preflight skipped_hash is missing or does not match skipped[].")
    if status != "pass":
        failures.append(f"preflight status is {status!r}, expected 'pass'")
    if failed:
        failures.append(f"preflight failed checks are present: {', '.join(stringify_list(failed))}")
    if missing_checks:
        failures.append(f"preflight evidence is missing required checks: {', '.join(missing_checks)}")
    expected_checks_hash = hash_payload(checks)
    if checks_hash != expected_checks_hash:
        failures.append("preflight checks_hash is missing or does not match checks[].")
    expected_preflight_hash = hash_payload({key: value for key, value in payload.items() if key != "preflight_hash"})
    if preflight_hash != expected_preflight_hash:
        failures.append("preflight_hash is missing or does not match the evidence payload.")
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
        "preflight_schema": preflight_schema,
        "generated_at": generated_at,
        "age_hours": round(age_hours, 3) if age_hours is not None else None,
        "root": root,
        "output_json": output_json,
        "checks": len(checks),
        "check_count": payload.get("check_count"),
        "passed_count": payload.get("passed_count"),
        "failed_count": payload.get("failed_count"),
        "skipped_count": payload.get("skipped_count"),
        "checks_hash": checks_hash,
        "preflight_hash": preflight_hash,
        "mode": mode,
        "mode_hash": mode_hash,
        "skipped_hash": skipped_hash,
        "required_checks": REQUIRED_PREFLIGHT_CHECKS,
        "missing_required_checks": missing_checks,
        "failed": failed if isinstance(failed, list) else stringify_list(failed),
        "actual_failed_checks": actual_failed_checks,
        "status_mismatch_checks": status_mismatch_checks,
        "missing_command_checks": missing_command_checks,
        "missing_cwd_checks": missing_cwd_checks,
        "missing_duration_checks": missing_duration_checks,
        "missing_details_checks": missing_details_checks,
        "missing_output_checks": missing_output_checks,
        "skipped": skipped_list,
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
    plan_release_lock = plan_details.get("release_lock") if isinstance(plan_details.get("release_lock"), dict) else {}
    plan_summary = plan.get("summary") if isinstance(plan.get("summary"), dict) else {}
    if plan_details.get("status") != "pass":
        failures.append("data_refresh_cli_plan status is not pass.")
    if plan.get("dry_run") is not True and plan.get("refresh_status") != "planned":
        failures.append("data_refresh_cli_plan did not run as a dry run.")
    if int_value(plan_summary.get("planned")) <= 0:
        failures.append("data_refresh_cli_plan did not plan any operations.")
    if not (plan.get("manifest_hash") or plan_release_lock.get("current_hash")):
        failures.append("data_refresh_cli_plan did not include a manifest or release-lock hash.")

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


def int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _valid_command_evidence(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(str(item).strip() for item in value)


def _valid_duration_evidence(value: Any) -> bool:
    try:
        return float(value) >= 0
    except (TypeError, ValueError):
        return False


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
    if name == "deployment_readiness":
        if not payload.get("deployment_ready"):
            failures.append("deployment readiness has blocking failures")
        gates = payload.get("gates") if isinstance(payload.get("gates"), list) else []
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        attention_gates = payload.get("attention_gates") if isinstance(payload.get("attention_gates"), list) else []
        required_actions = payload.get("required_actions") if isinstance(payload.get("required_actions"), list) else []
        gate_status_counts = {
            "pass": sum(1 for gate in gates if isinstance(gate, dict) and gate.get("status") == "pass"),
            "warning": sum(1 for gate in gates if isinstance(gate, dict) and gate.get("status") == "warning"),
            "fail": sum(1 for gate in gates if isinstance(gate, dict) and gate.get("status") == "fail"),
        }
        expected_attention_gates = [gate.get("name") for gate in gates if isinstance(gate, dict) and gate.get("status") != "pass"]
        if not gates:
            failures.append("deployment readiness does not expose gates")
        if not summary:
            failures.append("deployment readiness does not expose summary counts")
        elif any(safe_int(summary.get(key), -1) != value for key, value in gate_status_counts.items()):
            failures.append("deployment readiness summary counts do not match gates")
        if attention_gates != expected_attention_gates:
            failures.append("deployment readiness attention_gates do not match non-pass gates")
        if payload.get("attention_gates_hash") != compact_hash_payload(expected_attention_gates):
            failures.append("deployment readiness attention_gates_hash is missing or does not match attention_gates")
        if payload.get("required_actions_hash") != compact_hash_payload(required_actions):
            failures.append("deployment readiness required_actions_hash is missing or does not match required_actions")
        gates_by_name = {str(gate.get("name")): gate for gate in gates if isinstance(gate, dict) and gate.get("name")}
        action_gates = []
        for action in required_actions:
            if not isinstance(action, dict):
                failures.append("deployment readiness required_actions contains a non-object item")
                continue
            gate_name = str(action.get("gate") or "")
            action_gates.append(gate_name)
            gate = gates_by_name.get(gate_name)
            if not gate:
                failures.append(f"deployment readiness required action references unknown gate {gate_name!r}")
                continue
            if action.get("status") != gate.get("status"):
                failures.append(f"deployment readiness required action status does not match gate {gate_name}")
            if action.get("detail_hash") != compact_hash_payload(gate.get("details") or {}):
                failures.append(f"deployment readiness required action detail_hash does not match gate {gate_name}")
        if sorted(action_gates) != sorted(str(name) for name in expected_attention_gates):
            failures.append("deployment readiness required_actions do not cover all non-pass gates")
    if name == "production_audit_status":
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        gap_summary = payload.get("production_gap_summary") if isinstance(payload.get("production_gap_summary"), dict) else {}
        evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
        if not payload.get("audit_hash") or len(str(payload.get("audit_hash"))) != 64:
            failures.append("production audit status audit_hash is missing or invalid")
        if summary.get("status") not in {"pass", "warning", "fail"}:
            failures.append("production audit status summary status is missing or invalid")
        failures.extend(production_gap_summary_failures(gap_summary, evidence.get("production_gap_summary")))
    if name == "production_audit_bundle_verify":
        semantic_summary = payload.get("semantic_summary") if isinstance(payload.get("semantic_summary"), dict) else {}
        semantic_checks = payload.get("semantic_checks") if isinstance(payload.get("semantic_checks"), dict) else {}
        artifact_verification = payload.get("artifact_verification") if isinstance(payload.get("artifact_verification"), dict) else {}
        if payload.get("status") != "pass":
            failures.append("production audit bundle verification status is not pass")
        if artifact_verification.get("status") != "pass":
            failures.append("production audit bundle artifact verification did not pass")
        if not payload.get("audit_hash") or len(str(payload.get("audit_hash"))) != 64:
            failures.append("production audit bundle audit_hash is missing or invalid")
        if semantic_summary.get("summary_schema") != "agentic-rag-production-audit-semantic-summary-v1":
            failures.append("production audit bundle semantic_summary schema is missing or invalid")
        if safe_int(semantic_summary.get("check_count")) < 1:
            failures.append("production audit bundle semantic_summary check_count is missing")
        if semantic_summary.get("status") != "pass":
            failures.append("production audit bundle semantic_summary status is not pass")
        if safe_int(semantic_summary.get("fail_count"), -1) != 0:
            failures.append("production audit bundle semantic_summary fail_count is not zero")
        if safe_int(semantic_summary.get("warning_count"), -1) != 0:
            failures.append("production audit bundle semantic_summary warning_count is not zero")
        if safe_int(semantic_summary.get("pass_count")) != safe_int(semantic_summary.get("check_count")):
            failures.append("production audit bundle semantic_summary pass_count does not match check_count")
        if not semantic_summary.get("summary_hash") or len(str(semantic_summary.get("summary_hash"))) != 64:
            failures.append("production audit bundle semantic_summary summary_hash is missing or invalid")
        required_audit_checks = [
            "audit_hash",
            "evidence_hashes_manifest",
            "evidence_hashes_combined",
            "check_detail_hashes",
            "required_action_coverage",
            "required_action_detail_hashes",
            "markdown_recomputed",
            "summary_recomputed",
        ]
        missing = [check for check in required_audit_checks if semantic_checks.get(check) != "pass"]
        if missing:
            failures.append(f"production audit bundle semantic checks did not pass: {', '.join(missing)}")
    if name == "data_provenance" and payload.get("status") == "fail":
        failures.append("data provenance status is fail")
    if name == "rag_diagnostics" and payload.get("status") == "fail":
        failures.append("RAG diagnostics status is fail")
    if name == "rag_embedding_status":
        if payload.get("production_ready") is not True:
            failures.append("RAG embedding backend is not production_ready")
        if payload.get("active_backend") not in {"sentence_transformers", "openai"}:
            failures.append("RAG embedding active_backend is not a production backend")
        if payload.get("fallback_active"):
            failures.append("RAG embedding fallback_active is true")
        if not payload.get("model_fingerprint_hash"):
            failures.append("RAG embedding model_fingerprint_hash is missing")
        if payload.get("active_backend") == "openai":
            budget = (payload.get("openai") or {}).get("budget") or {}
            if not budget.get("price_configured"):
                failures.append("OpenAI embedding price is not configured")
            if not budget.get("budget_configured"):
                failures.append("OpenAI embedding budget is not configured")
            if not budget.get("within_budget"):
                failures.append("OpenAI embedding budget is not within budget")
    if name == "rna_folding_status":
        if payload.get("production_ready") is not True:
            failures.append("RNA folding backend is not production_ready")
        if payload.get("active_backend") != "rnafold":
            failures.append("RNA folding active_backend is not rnafold")
        if payload.get("fallback_active"):
            failures.append("RNA folding fallback_active is true")
        if payload.get("validated_backend") is not True:
            failures.append("RNA folding validated_backend is not true")
        if not payload.get("executable_path"):
            failures.append("RNA folding executable_path is missing")
    if name == "optimizer_diagnostics" and payload.get("status") == "fail":
        failures.append("optimizer diagnostics status is fail")
    if name == "storage_status":
        postgres = payload.get("postgres") or {}
        if payload.get("target_backend") != "postgres":
            failures.append("storage target_backend is not postgres")
        if payload.get("active_runtime_adapter") != "postgres":
            failures.append("storage active_runtime_adapter is not postgres")
        if not payload.get("database_url_configured"):
            failures.append("storage DATABASE_URL is not configured")
        if not postgres.get("schema_hash"):
            failures.append("storage postgres schema_hash is missing")
        if postgres.get("driver_available") is False:
            failures.append("storage postgres driver is unavailable")
        if "vector" not in (postgres.get("required_extensions") or []):
            failures.append("storage postgres vector extension requirement is missing")
    if name == "security_status":
        if not payload.get("auth_enabled"):
            failures.append("API authentication is not enabled")
        if not payload.get("rbac_enabled"):
            failures.append("API RBAC role bindings are not enabled")
        if safe_int(payload.get("configured_keys")) < 1:
            failures.append("security status reports no configured API keys")
        if safe_int(payload.get("configured_role_bindings")) < 1:
            failures.append("security status reports no configured API key role bindings")
        if safe_int(payload.get("rate_limit_per_minute")) < 1:
            failures.append("security status reports non-positive rate_limit_per_minute")
        signing = payload.get("signing") or {}
        hmac_signing = (signing.get("hmac") or {}).get("signing_enabled") or payload.get("artifact_signing_enabled")
        ed25519_signing = (signing.get("ed25519") or {}).get("signing_enabled") or payload.get("artifact_asymmetric_signing_enabled")
        if not (hmac_signing or ed25519_signing):
            failures.append("artifact signing is not enabled")
        if hmac_signing and not ((signing.get("hmac") or {}).get("key_id") or payload.get("artifact_signing_key_id")):
            failures.append("HMAC artifact signing key_id is missing")
        if ed25519_signing and not ((signing.get("ed25519") or {}).get("key_id") or payload.get("artifact_ed25519_key_id")):
            failures.append("Ed25519 artifact signing key_id is missing")
    if name == "artifact_object_store_mirror_plan":
        status = payload.get("status")
        object_store = payload.get("object_store") or {}
        candidate_count = safe_int(payload.get("candidate_count"))
        candidate_bytes = safe_int(payload.get("candidate_bytes"))
        if status == "misconfigured" or object_store.get("status") == "misconfigured":
            failures.append("artifact object-store mirror plan is misconfigured")
        if status == "ready" and candidate_count > 0:
            failures.append("artifact object-store mirror plan has pending candidates")
        if candidate_count < 0:
            failures.append("artifact object-store mirror candidate_count is invalid")
        if candidate_bytes < 0:
            failures.append("artifact object-store mirror candidate_bytes is invalid")
    if name in {
        "data_release_bundle_verify",
        "data_snapshot_bundle_verify",
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
            "source_provenance_schema",
            "source_provenance_consistency",
            "source_provenance_count",
            "retrieval_trace_hash",
            "evidence_sufficiency_hash",
            "facet_gap_analysis_hash",
            "query_term_coverage_hash",
            "top_sources_consistency",
            "evaluation_hash",
            "top_sources_hash",
            "source_provenance_hash",
            "score_breakdown_hash",
            "chunks_hash",
        ]
        missing = [check for check in required_rag_explainability if checks.get(check) != "pass"]
        if missing:
            failures.append(f"RAG evaluation bundle does not verify explainability checks: {', '.join(missing)}.")
        for hash_check in [
            "evaluation_hash",
            "retrieval_trace_hash",
            "evidence_sufficiency_hash",
            "facet_gap_analysis_hash",
            "query_term_coverage_hash",
            "top_sources_hash",
            "source_provenance_hash",
            "score_breakdown_hash",
            "chunks_hash",
        ]:
            if not payload.get(hash_check):
                failures.append(f"RAG evaluation bundle does not expose {hash_check}.")
        if int(payload.get("source_provenance_count") or 0) < 1:
            failures.append("RAG evaluation bundle does not expose source_provenance_count.")
    if name == "data_release_bundle_verify":
        checks = payload.get("semantic_checks") or {}
        for hash_check in ["records_hash", "records_csv_hash", "record_source_summary_hash", "release_handoff_hash"]:
            if checks.get(hash_check) != "pass" or not payload.get(hash_check):
                failures.append(f"Data release bundle does not verify {hash_check}.")
        for check in ["record_source_summary_schema", "record_source_summary_consistency", "record_source_summary_counts"]:
            if checks.get(check) != "pass":
                failures.append(f"Data release bundle does not verify {check}.")
        if checks.get("rag_structured_manifest_hash") != "pass" or not payload.get("rag_structured_manifest_hash"):
            failures.append("Data release bundle does not verify rag_structured_manifest_hash.")
        if checks.get("rag_index_hash") not in {"pass", "warning"}:
            failures.append("Data release bundle does not expose rag_index_hash status.")
        for caveat_check in ["trna_caveat_count", "trna_blocking_production_use"]:
            if checks.get(caveat_check) != "pass" or caveat_check not in payload:
                failures.append(f"Data release bundle does not verify {caveat_check}.")
        if checks.get("external_snapshot_reference_coverage") != "pass":
            failures.append("Data release bundle does not verify external_snapshot_reference_coverage.")
    if name == "data_snapshot_bundle_verify":
        checks = payload.get("semantic_checks") or {}
        for hash_check in ["snapshot_manifest_hash", "structured_manifest_hash"]:
            if checks.get(hash_check) != "pass" or not payload.get(hash_check):
                failures.append(f"Data snapshot bundle does not verify {hash_check}.")
        if checks.get("snapshot_file_listing") != "pass":
            failures.append("Data snapshot bundle does not verify snapshot_file_listing.")
        if checks.get("rag_index_hash") != "pass" or not payload.get("rag_index_hash"):
            failures.append("Data snapshot bundle does not expose rag_index_hash.")
        if checks.get("external_snapshot_files") != "pass" or not payload.get("external_snapshot_file_count"):
            failures.append("Data snapshot bundle does not include external source snapshot files.")
    if name == "rag_regression_bundle_verify":
        checks = payload.get("semantic_checks") or {}
        if checks.get("results_hash") != "pass" or not payload.get("results_hash"):
            failures.append("RAG regression bundle does not verify results_hash.")
        for check in [
            "quality_summary_hash",
            "quality_summary_schema",
            "quality_summary_case_count",
            "quality_summary_status",
            "case_metric_columns",
            "case_metrics_hash",
            "source_provenance_summary_schema",
            "source_provenance_summary_consistency",
            "source_provenance_summary_hash",
            "source_provenance_case_count",
        ]:
            if checks.get(check) != "pass":
                failures.append(f"RAG regression bundle does not verify {check}.")
        if not payload.get("quality_summary_hash"):
            failures.append("RAG regression bundle is missing quality_summary_hash.")
        if not payload.get("case_metrics_hash"):
            failures.append("RAG regression bundle is missing case_metrics_hash.")
        if not payload.get("source_provenance_summary_hash"):
            failures.append("RAG regression bundle is missing source_provenance_summary_hash.")
    if name == "optimizer_benchmark_bundle_verify":
        checks = payload.get("semantic_checks") or {}
        required_optimizer_checks = [
            "search_strategy_schema",
            "optimizer_seed_strategy",
            "candidate_diagnostics",
            "recommendation_audit",
            "pareto_quality_schema",
            "pareto_quality_hash",
            "recommendation_summary_schema",
            "recommendation_summary_consistency",
            "recommendation_summary_case_count",
            "recommendation_summary_hash",
            "case_metric_columns",
            "benchmark_hash",
            "diagnostics_hash",
            "case_metrics_hash",
            "candidate_diagnostics_hash",
            "recommended_folding_evidence_schema",
            "recommended_folding_evidence_hashes",
            "recommended_folding_evidence_payload_hash",
            "recommended_folding_evidence_hash",
        ]
        missing = [check for check in required_optimizer_checks if checks.get(check) != "pass"]
        if missing:
            failures.append(f"Optimizer benchmark bundle does not verify semantic checks: {', '.join(missing)}.")
        if checks.get("results_hash") != "pass" or not payload.get("results_hash"):
            failures.append("Optimizer benchmark bundle does not verify results_hash.")
        for hash_check in [
            "benchmark_hash",
            "diagnostics_hash",
            "case_metrics_hash",
            "candidate_diagnostics_hash",
            "recommendation_summary_hash",
            "recommended_folding_evidence_hash",
        ]:
            if not payload.get(hash_check):
                failures.append(f"Optimizer benchmark bundle does not expose {hash_check}.")
    if name == "qc_report_bundle_verify":
        checks = payload.get("semantic_checks") or {}
        if checks.get("request_payload") != "pass":
            failures.append("QC report bundle does not verify request_payload.")
        for hash_check in [
            "request_hash",
            "qc_report_hash",
            "report_formats_summary_hash",
            "candidate_ranking_hash",
            "recommendation_audit_hash",
            "recommendation_readiness_hash",
            "recommended_folding_evidence_hash",
        ]:
            if checks.get(hash_check) != "pass" or not payload.get(hash_check):
                failures.append(f"QC report bundle does not verify {hash_check}.")
        required_qc_explainability = [
            "optimizer_hash_report",
            "report_formats_summary_schema",
            "report_formats_summary_consistency",
            "candidate_csv_explainability_columns",
            "candidate_ranking_report_count",
            "candidate_ranking_report_ids",
            "candidate_ranking_report_scores",
            "candidate_diagnostics_candidate_count",
            "recommended_constraint_risk_csv",
            "recommended_candidate_score_csv",
            "objective_inventory",
            "evidence_retrieval_quality_schema",
            "evidence_retrieval_quality_sources",
            "evidence_retrieval_quality_rank_hashes",
            "recommendation_audit",
            "recommended_folding_evidence_schema",
            "recommended_folding_evidence_report",
            "recommended_folding_evidence_payload_hash",
            "recommendation_readiness_schema",
            "recommendation_readiness_report",
            "recommendation_readiness_payload_hash",
            "recommendation_readiness_candidate",
            "recommendation_readiness_status",
        ]
        missing = [check for check in required_qc_explainability if checks.get(check) != "pass"]
        if missing:
            failures.append(f"QC report bundle does not verify explainability checks: {', '.join(missing)}.")
        failed_targets = [key for key, value in checks.items() if key.startswith("request_target_") and value != "pass"]
        if failed_targets:
            failures.append(f"QC report bundle request target checks failed: {', '.join(failed_targets)}")
    if name == "governance_attestation_verify":
        artifact_verification = payload.get("artifact_verification") or {}
        signature = payload.get("signature") or {}
        if payload.get("status") != "pass":
            failures.append("governance attestation verification did not pass")
        if not payload.get("attestation_hash"):
            failures.append("governance attestation_hash is missing")
        if artifact_verification.get("status") != "pass":
            failures.append("governance attestation artifact verification did not pass")
        if safe_int(artifact_verification.get("checked_files")) < 1:
            failures.append("governance attestation artifact verification checked no files")
        if signature.get("status") != "verified":
            failures.append("governance attestation signature is not verified")
    if name == "artifact_ledger_verify":
        if payload.get("status") != "pass":
            failures.append("artifact ledger verification did not pass")
        if safe_int(payload.get("entry_count")) < 1:
            failures.append("artifact ledger has no entries")
        if not payload.get("latest_hash") or payload.get("latest_hash") == "GENESIS":
            failures.append("artifact ledger latest_hash is missing or GENESIS")
        if safe_int(payload.get("missing_from_ledger_count")) != 0:
            failures.append("artifact ledger has archived artifacts missing from ledger")
    if name.endswith("_archive_semantics") and payload.get("status") == "fail":
        failures.append(f"{name} archive semantic summary is fail")
    if name == "qc_bundle_archive_semantics":
        latest = (payload.get("latest_artifacts") or [{}])[0]
        checked_count = int(payload.get("checked_count") or 0)
        if checked_count and latest.get("request_payload_status") != "pass":
            failures.append("latest QC archive request_payload_status is not pass")
        for hash_field in [
            "request_hash",
            "qc_report_hash",
            "report_formats_summary_hash",
            "candidate_ranking_hash",
            "recommendation_audit_hash",
            "recommendation_readiness_hash",
            "recommended_folding_evidence_hash",
            "optimizer_manifest_hash",
        ]:
            if checked_count and not latest.get(hash_field):
                failures.append(f"latest QC archive is missing {hash_field}")
        if checked_count and latest.get("recommendation_readiness_status") not in {"pass", "warning"}:
            failures.append("latest QC archive is missing pass/warning recommendation_readiness_status")
        if checked_count and not latest.get("recommendation_readiness_candidate_id"):
            failures.append("latest QC archive is missing recommendation_readiness_candidate_id")
        if checked_count and latest.get("recommended_folding_status") not in {"pass", "warning"}:
            failures.append("latest QC archive is missing pass/warning recommended_folding_status")
        if checked_count and not latest.get("recommended_folding_candidate_id"):
            failures.append("latest QC archive is missing recommended_folding_candidate_id")
        if checked_count and latest.get("retrieval_quality_status") not in {"pass", "warning"}:
            failures.append("latest QC archive is missing pass/warning retrieval_quality_status")
        if checked_count and int(latest.get("retrieval_quality_source_count") or 0) < 1:
            failures.append("latest QC archive is missing retrieval_quality_source_count")
        if checked_count and int(latest.get("retrieval_quality_rank_evidence_count") or 0) < 1:
            failures.append("latest QC archive is missing retrieval_quality_rank_evidence_count")
        if checked_count and len(str(latest.get("retrieval_quality_rank_evidence_hash") or "")) != 64:
            failures.append("latest QC archive is missing retrieval_quality_rank_evidence_hash")
        if checked_count and int(latest.get("objective_count") or 0) < 1:
            failures.append("latest QC archive is missing objective_count")
        if checked_count and latest.get("data_quality_status") not in {"pass", "warning"}:
            failures.append("latest QC archive is missing pass/warning data_quality_status")
        if checked_count and latest.get("optimizer_stress_status") not in {"pass", "warning"}:
            failures.append("latest QC archive is missing pass/warning optimizer_stress_status")
    if name == "data_release_archive_semantics":
        latest = (payload.get("latest_artifacts") or [{}])[0]
        checked_count = int(payload.get("checked_count") or 0)
        if checked_count and not latest.get("records_hash"):
            failures.append("latest data release archive is missing records_hash")
        if checked_count and not latest.get("records_csv_hash"):
            failures.append("latest data release archive is missing records_csv_hash")
        if checked_count and not latest.get("record_source_summary_hash"):
            failures.append("latest data release archive is missing record_source_summary_hash")
        if checked_count and int(latest.get("dataset_count") or 0) < 1:
            failures.append("latest data release archive is missing dataset_count")
        if checked_count and int(latest.get("source_file_count") or 0) < 1:
            failures.append("latest data release archive is missing source_file_count")
        if checked_count and not latest.get("release_handoff_hash"):
            failures.append("latest data release archive is missing release_handoff_hash")
        if checked_count and not latest.get("rag_structured_manifest_hash"):
            failures.append("latest data release archive is missing rag_structured_manifest_hash")
        if checked_count and not latest.get("rag_index_hash"):
            failures.append("latest data release archive is missing rag_index_hash")
        if checked_count and latest.get("trna_caveat_count") is None:
            failures.append("latest data release archive is missing trna_caveat_count")
        if checked_count and latest.get("trna_blocking_production_use") is None:
            failures.append("latest data release archive is missing trna_blocking_production_use")
        if checked_count and latest.get("external_snapshot_missing_count") not in {0, None}:
            failures.append("latest data release archive has missing external source snapshots")
        if checked_count and int(latest.get("external_snapshot_referenced_count") or 0) < 1:
            failures.append("latest data release archive is missing external snapshot reference evidence")
        if checked_count and int(latest.get("external_snapshot_contained_count") or 0) < int(latest.get("external_snapshot_referenced_count") or 0):
            failures.append("latest data release archive does not contain all referenced external snapshots")
    if name == "data_snapshot_archive_semantics":
        latest = (payload.get("latest_artifacts") or [{}])[0]
        checked_count = int(payload.get("checked_count") or 0)
        for hash_field in ["snapshot_manifest_hash", "structured_manifest_hash", "rag_index_hash"]:
            if checked_count and not latest.get(hash_field):
                failures.append(f"latest data snapshot archive is missing {hash_field}")
        if checked_count and int(latest.get("external_snapshot_file_count") or 0) < 1:
            failures.append("latest data snapshot archive is missing external_snapshot_file_count")
        if checked_count and int(latest.get("snapshot_file_count") or 0) < 1:
            failures.append("latest data snapshot archive is missing snapshot_file_count")
    if name == "data_refresh_plan_archive_semantics":
        latest = (payload.get("latest_artifacts") or [{}])[0]
        checked_count = int(payload.get("checked_count") or 0)
        if checked_count and int(latest.get("operation_count") or 0) < 1:
            failures.append("latest data refresh plan archive is missing operation_count")
        if checked_count and not latest.get("request_hash"):
            failures.append("latest data refresh plan archive is missing request_hash")
        if checked_count and not latest.get("operations_hash"):
            failures.append("latest data refresh plan archive is missing operations_hash")
        if checked_count and not latest.get("data_catalog_hash"):
            failures.append("latest data refresh plan archive is missing data_catalog_hash")
        if checked_count and not latest.get("external_sources_hash"):
            failures.append("latest data refresh plan archive is missing external_sources_hash")
        if checked_count and not latest.get("structured_quality_hash"):
            failures.append("latest data refresh plan archive is missing structured_quality_hash")
        if checked_count and not latest.get("data_provenance_hash"):
            failures.append("latest data refresh plan archive is missing data_provenance_hash")
        if checked_count and not latest.get("rag_status_hash"):
            failures.append("latest data refresh plan archive is missing rag_status_hash")
        if checked_count and not latest.get("validation_status"):
            failures.append("latest data refresh plan archive is missing validation_status")
        if checked_count and not latest.get("dataset_id"):
            failures.append("latest data refresh plan archive is missing dataset_id")
        if checked_count and not latest.get("structured_manifest_hash"):
            failures.append("latest data refresh plan archive is missing structured_manifest_hash")
    if name == "rag_evaluation_archive_semantics":
        latest = (payload.get("latest_artifacts") or [{}])[0]
        checked_count = int(payload.get("checked_count") or 0)
        if checked_count and not latest.get("source_provenance_hash"):
            failures.append("latest RAG evaluation archive is missing source_provenance_hash")
        if checked_count and int(latest.get("source_provenance_count") or 0) < 1:
            failures.append("latest RAG evaluation archive is missing source_provenance_count")
    if name == "structured_import_archive_semantics":
        latest = (payload.get("latest_artifacts") or [{}])[0]
        checked_count = int(payload.get("checked_count") or 0)
        if checked_count and not latest.get("structured_manifest_hash"):
            failures.append("latest structured import archive is missing structured_manifest_hash")
        if checked_count and int(latest.get("checked_files") or 0) < 1:
            failures.append("latest structured import archive is missing checked_files")
        if checked_count and int(latest.get("file_count") or 0) < 1:
            failures.append("latest structured import archive is missing file_count")
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
        if checked_count and not latest.get("recommended_backend"):
            failures.append("latest RAG vector index archive is missing recommended_backend")
        if checked_count and not latest.get("migration_target_backend"):
            failures.append("latest RAG vector index archive is missing migration_target_backend")
        if checked_count and latest.get("parity_status") not in {"pass", "warning"}:
            failures.append("latest RAG vector index archive is missing pass/warning parity_status")
        if checked_count and not latest.get("vector_row_hash"):
            failures.append("latest RAG vector index archive is missing vector_row_hash")
        if checked_count and not latest.get("structured_manifest_hash"):
            failures.append("latest RAG vector index archive is missing structured_manifest_hash")
        semantic_checks = latest.get("semantic_checks") if isinstance(latest.get("semantic_checks"), dict) else {}
        for check_name in (
            "migration_target_backend_consistency",
            "recommended_backend_consistency",
            "migration_parity_source_count",
        ):
            if checked_count and semantic_checks.get(check_name) != "pass":
                failures.append(f"latest RAG vector index archive semantic check {check_name} is not pass")
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
        for hash_field in [
            "results_hash",
            "benchmark_hash",
            "diagnostics_hash",
            "case_metrics_hash",
            "candidate_diagnostics_hash",
            "recommendation_summary_hash",
            "case_provenance_hash",
            "recommended_folding_evidence_hash",
        ]:
            if checked_count and not latest.get(hash_field):
                failures.append(f"latest optimizer benchmark archive is missing {hash_field}")
        if checked_count and (
            latest.get("recommendation_summary_status") not in {"pass", "warning"}
        ):
            failures.append("latest optimizer benchmark archive is missing pass/warning recommendation_summary_status")
        if checked_count and (
            latest.get("recommended_on_pareto_front_count") is None
            or int(latest.get("recommended_on_pareto_front_count") or 0) < 1
        ):
            failures.append("latest optimizer benchmark archive is missing recommended_on_pareto_front_count")
        if checked_count and latest.get("recommendation_max_regret") is None:
            failures.append("latest optimizer benchmark archive is missing recommendation_max_regret")
        if checked_count and (
            not latest.get("case_fingerprint_count")
            or int(latest.get("case_fingerprint_count") or 0) < int(latest.get("case_count") or 0)
        ):
            failures.append("latest optimizer benchmark archive is missing case_fingerprint_count coverage")
        if checked_count and (
            not latest.get("recommended_folding_evidence_count")
            or int(latest.get("recommended_folding_evidence_count") or 0) < int(latest.get("case_count") or 0)
        ):
            failures.append("latest optimizer benchmark archive is missing recommended_folding_evidence_count coverage")
        if checked_count and (
            latest.get("recommended_folding_candidate_match_count") is None
            or int(latest.get("recommended_folding_candidate_match_count") or 0) < int(latest.get("case_count") or 0)
        ):
            failures.append("latest optimizer benchmark archive is missing recommended_folding_candidate_match_count coverage")
    if name == "rag_regression_archive_semantics":
        latest = (payload.get("latest_artifacts") or [{}])[0]
        checked_count = int(payload.get("checked_count") or 0)
        if checked_count and int(latest.get("case_count") or 0) < 1:
            failures.append("latest RAG regression archive is missing case_count")
        for hash_field in [
            "cases_hash",
            "results_hash",
            "quality_summary_hash",
            "case_metrics_hash",
            "source_provenance_summary_hash",
            "structured_manifest_hash",
        ]:
            if checked_count and not latest.get(hash_field):
                failures.append(f"latest RAG regression archive is missing {hash_field}")
        if checked_count and latest.get("quality_status") not in {"pass", "warning", "fail"}:
            failures.append("latest RAG regression archive is missing quality_status")
        if checked_count and latest.get("top_source_count") is None:
            failures.append("latest RAG regression archive is missing top_source_count")
        if checked_count and latest.get("missing_term_case_count") is None:
            failures.append("latest RAG regression archive is missing missing_term_case_count")
        if checked_count and int(latest.get("source_provenance_case_count") or 0) < int(latest.get("case_count") or 0):
            failures.append("latest RAG regression archive is missing source_provenance_case_count coverage")
    if name == "workflow_trace_archive_semantics":
        latest = (payload.get("latest_artifacts") or [{}])[0]
        checked_count = int(payload.get("checked_count") or 0)
        if checked_count and int(latest.get("trace_step_count") or 0) < 1:
            failures.append("latest workflow trace archive is missing trace_step_count")
        if checked_count and not latest.get("trace_hash"):
            failures.append("latest workflow trace archive is missing trace_hash")
        if checked_count and not latest.get("task_type"):
            failures.append("latest workflow trace archive is missing task_type")
        if checked_count and not latest.get("structured_manifest_hash"):
            failures.append("latest workflow trace archive is missing structured_manifest_hash")
    return failures


def production_gap_summary_failures(summary: Any, embedded_summary: Any | None = None) -> list[str]:
    failures: list[str] = []
    if not isinstance(summary, dict):
        return ["production audit status production_gap_summary is missing or invalid"]
    if embedded_summary is not None and summary != embedded_summary:
        failures.append("production audit status production_gap_summary does not match embedded evidence")
    if summary.get("gap_schema") != "agentic-rag-production-gap-summary-v1":
        failures.append("production audit status production_gap_summary schema is missing or invalid")
    gaps = summary.get("gaps") if isinstance(summary.get("gaps"), list) else []
    if safe_int(summary.get("gap_count"), -1) != len(gaps):
        failures.append("production audit status gap_count does not match gaps")
    if safe_int(summary.get("blocking_count"), -1) != sum(1 for gap in gaps if isinstance(gap, dict) and gap.get("priority") == "blocking"):
        failures.append("production audit status blocking_count does not match gaps")
    if safe_int(summary.get("promotion_count"), -1) != sum(1 for gap in gaps if isinstance(gap, dict) and gap.get("priority") == "promotion"):
        failures.append("production audit status promotion_count does not match gaps")
    expected_scope_counts = count_by(gaps, "resolution_scope")
    expected_mode_counts = count_by(gaps, "resolution_mode")
    if summary.get("resolution_scope_counts") != expected_scope_counts:
        failures.append("production audit status resolution_scope_counts do not match gaps")
    if summary.get("resolution_mode_counts") != expected_mode_counts:
        failures.append("production audit status resolution_mode_counts do not match gaps")
    proof_checklist = summary.get("proof_checklist") if isinstance(summary.get("proof_checklist"), list) else []
    if safe_int(summary.get("proof_checklist_count"), -1) != len(proof_checklist):
        failures.append("production audit status proof_checklist_count does not match proof_checklist")
    if summary.get("proof_checklist_hash") != compact_hash_payload(proof_checklist):
        failures.append("production audit status proof_checklist_hash does not match proof_checklist")
    if proof_checklist != production_gap_proof_checklist(gaps):
        failures.append("production audit status proof_checklist does not match gaps")
    for gap in gaps:
        if not isinstance(gap, dict):
            failures.append("production audit status production_gap_summary contains a non-object gap")
            continue
        missing = [
            key
            for key in [
                "area",
                "status",
                "priority",
                "resolution_scope",
                "resolution_mode",
                "proof_hint",
                "proof_artifact",
                "proof_command",
                "evidence_key",
                "check_detail_hash",
                "action",
            ]
            if not gap.get(key)
        ]
        if missing:
            failures.append(f"production audit status gap {gap.get('area', 'unknown')!r} is missing fields: {', '.join(missing)}")
        if gap.get("gap_hash") != compact_hash_payload({key: value for key, value in gap.items() if key != "gap_hash"}):
            failures.append(f"production audit status gap_hash does not match gap {gap.get('area', 'unknown')!r}")
    expected_summary_hash = compact_hash_payload({key: value for key, value in summary.items() if key != "gap_summary_hash"})
    if summary.get("gap_summary_hash") != expected_summary_hash:
        failures.append("production audit status gap_summary_hash does not match production_gap_summary")
    return failures


def count_by(items: list[Any], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def production_gap_proof_checklist(gaps: list[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for gap in gaps:
        if not isinstance(gap, dict):
            continue
        items.append(
            {
                "area": gap.get("area"),
                "priority": gap.get("priority"),
                "resolution_scope": gap.get("resolution_scope"),
                "resolution_mode": gap.get("resolution_mode"),
                "proof_artifact": gap.get("proof_artifact"),
                "proof_command": gap.get("proof_command"),
                "gap_hash": gap.get("gap_hash"),
            }
        )
    return sorted(items, key=lambda item: (str(item.get("priority") or ""), str(item.get("area") or "")))


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
    if name == "rag_embedding_status" and payload.get("production_ready") is not True:
        warnings.append("RAG embedding backend is not production_ready")
    if name == "rna_folding_status" and payload.get("production_ready") is not True:
        warnings.append("RNA folding backend is not production_ready")
    if name in {
        "production_audit_bundle_verify",
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
        if not payload.get("rbac_enabled"):
            warnings.append("API RBAC role bindings are not enabled")
        if ("rate_limit_enabled" in payload and not payload.get("rate_limit_enabled")) or safe_int(payload.get("rate_limit_per_minute")) < 1:
            warnings.append("rate limiting is not enabled")
        if not (payload.get("artifact_signing_enabled") or payload.get("artifact_asymmetric_signing_enabled")):
            warnings.append("artifact signing is not enabled")
    if name == "storage_status":
        if payload.get("target_backend") != "postgres" or payload.get("active_runtime_adapter") != "postgres":
            warnings.append("runtime storage backend is not postgres")
        if not payload.get("database_url_configured"):
            warnings.append("DATABASE_URL is not configured")
    if name == "artifact_object_store_mirror_plan":
        status = payload.get("status")
        object_store = payload.get("object_store") or {}
        if status == "disabled" or object_store.get("enabled") is False:
            warnings.append("artifact object-store mirror is disabled")
        if status == "ready" and safe_int(payload.get("candidate_count")) > 0:
            warnings.append("artifact object-store mirror has pending candidates")
    return warnings


def api_payload(details: Any) -> Any:
    if isinstance(details, dict) and "data" in details and isinstance(details["data"], dict):
        return details["data"]
    return details


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


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


def write_result(report: dict[str, Any], *, json_path: Path, markdown_path: Path) -> dict[str, Any]:
    preflight = next((check for check in report.get("checks", []) if isinstance(check, dict) and check.get("name") == "preflight_evidence"), None)
    preflight_details = preflight.get("details") if isinstance(preflight, dict) and isinstance(preflight.get("details"), dict) else {}
    return {
        "result_schema": "agentic-rag-cli-production-audit-write-result-v1",
        "summary": report.get("summary"),
        "audit_hash": report.get("audit_hash"),
        "json_path": str(json_path),
        "json_sha256": file_sha256(json_path),
        "markdown_path": str(markdown_path),
        "markdown_sha256": file_sha256(markdown_path),
        "preflight_evidence": (report.get("mode") or {}).get("preflight_evidence") if isinstance(report.get("mode"), dict) else None,
        "preflight_status": preflight.get("status") if isinstance(preflight, dict) else None,
        "preflight_failure_count": len(preflight.get("failures") or []) if isinstance(preflight, dict) else 0,
        "preflight_warning_count": len(preflight.get("warnings") or []) if isinstance(preflight, dict) else 0,
        "preflight_hash": preflight_details.get("preflight_hash"),
        "preflight_checks_hash": preflight_details.get("checks_hash"),
    }


def hash_payload(payload: Any) -> str:
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str | None:
    try:
        return sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def compact_hash_payload(payload: Any) -> str:
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


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
    api_checks = [check for check in report["checks"] if check.get("kind") == "api"]
    if api_checks:
        api_failures = [check for check in api_checks if check.get("status") == "fail"]
        api_warnings = [check for check in api_checks if check.get("warnings") or check.get("status") == "warning"]
        lines.extend(
            [
                "",
                "## API Evidence",
                "",
                f"- API checks: `{len(api_checks)}`",
                f"- API failures: `{len(api_failures)}`",
                f"- API warnings: `{len(api_warnings)}`",
                "",
                "| API Check | Status | HTTP | Detail |",
                "| --- | --- | --- | --- |",
            ]
        )
        for check in api_checks:
            detail = "; ".join(check.get("failures") or check.get("warnings") or []) or "pass"
            lines.append(
                "| {name} | {status} | {http_status} | {detail} |".format(
                    name=escape_cell(str(check.get("name") or "")),
                    status=escape_cell(str(check.get("status") or "")),
                    http_status=check.get("http_status", "n/a"),
                    detail=escape_cell(detail),
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
                f"- Schema: `{details.get('preflight_schema', 'n/a')}`",
                f"- Evidence path: `{details.get('path', preflight.get('path', 'n/a'))}`",
                f"- Evidence root: `{details.get('root', 'n/a')}`",
                f"- Output JSON: `{details.get('output_json', 'n/a')}`",
                f"- Generated at: `{details.get('generated_at', 'n/a')}`",
                f"- Age hours: `{details.get('age_hours', 'n/a')}`",
                f"- Mode hash: `{details.get('mode_hash', 'n/a')}`",
                f"- Skipped hash: `{details.get('skipped_hash', 'n/a')}`",
                f"- Checks hash: `{details.get('checks_hash', 'n/a')}`",
                f"- Preflight hash: `{details.get('preflight_hash', 'n/a')}`",
                f"- Required checks: `{len(details.get('required_checks') or [])}`",
                f"- Missing required checks: `{len(details.get('missing_required_checks') or [])}`",
                f"- Failed checks: `{len(details.get('failed') or [])}`",
                f"- Actual failed checks: `{len(details.get('actual_failed_checks') or [])}`",
                f"- Status mismatch checks: `{len(details.get('status_mismatch_checks') or [])}`",
                f"- Skipped checks: `{len(details.get('skipped') or [])}`",
                f"- Missing command evidence: `{len(details.get('missing_command_checks') or [])}`",
                f"- Missing cwd evidence: `{len(details.get('missing_cwd_checks') or [])}`",
                f"- Missing duration evidence: `{len(details.get('missing_duration_checks') or [])}`",
                f"- Missing details evidence: `{len(details.get('missing_details_checks') or [])}`",
                f"- Missing output evidence: `{len(details.get('missing_output_checks') or [])}`",
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
