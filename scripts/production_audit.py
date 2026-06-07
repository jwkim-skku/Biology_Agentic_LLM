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
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "backend" / "app" / "data" / "runtime" / "production_audits"
DEFAULT_API_BASE = "http://127.0.0.1:8000/api/v1"

API_CHECKS = [
    ("health_ready", "/health/ready"),
    ("deployment_readiness", "/deployment/readiness"),
    ("security_status", "/security/status"),
    ("storage_status", "/storage/status"),
    ("data_provenance", "/data/provenance"),
    ("external_sources", "/data/external-sources"),
    ("rag_diagnostics", "/rag/diagnostics"),
    ("optimizer_diagnostics", "/optimizer/diagnostics"),
    ("governance_attestation_verify", "/governance/attestation/verify"),
    ("artifact_ledger_verify", "/artifacts/ledger/verify"),
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
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Directory for JSON/Markdown reports.")
    parser.add_argument("--no-write", action="store_true", help="Print the audit JSON without writing files.")
    args = parser.parse_args()

    started = datetime.now(timezone.utc)
    checks: list[dict[str, Any]] = []
    checks.append(run_local_check("production_env", env_command(args), cwd=ROOT))
    checks.append(run_local_check("compose_preflight", compose_command(args), cwd=ROOT))
    if not args.skip_api:
        checks.extend(run_api_checks(args.api_base.rstrip("/"), args.api_key, require_api=args.require_api, timeout=args.api_timeout))

    summary = summarize(checks)
    report = {
        "generated_at": started.isoformat(),
        "duration_seconds": round(time.perf_counter() - STARTED_MONOTONIC, 3),
        "mode": {
            "env": "template" if args.template else "strict",
            "api_base": args.api_base.rstrip("/"),
            "skip_api": args.skip_api,
            "require_api": args.require_api,
            "require_docker": args.require_docker,
        },
        "summary": summary,
        "checks": checks,
    }

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


def run_api_checks(api_base: str, api_key: str, *, require_api: bool, timeout: float) -> list[dict[str, Any]]:
    checks = []
    for name, path in API_CHECKS:
        checks.append(fetch_api_check(name, f"{api_base}{path}", api_key, require_api=require_api, timeout=timeout))
    return checks


def fetch_api_check(name: str, url: str, api_key: str, *, require_api: bool, timeout: float) -> dict[str, Any]:
    started = time.perf_counter()
    request = urllib.request.Request(url)
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
    if name == "governance_attestation_verify" and payload.get("status") not in {None, "pass"}:
        failures.append("governance attestation verification did not pass")
    if name == "artifact_ledger_verify" and payload.get("status") not in {None, "pass"}:
        failures.append("artifact ledger verification did not pass")
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
        f"- Generated at: `{report['generated_at']}`",
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
