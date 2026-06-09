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
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
READY_URL = "http://127.0.0.1:8000/api/v1/health/ready"
DEFAULT_FRONTEND_URL = "http://127.0.0.1:3000"
DEV_FRONTEND_URL = "http://127.0.0.1:3001"
SIGNING_SMOKE_PORT = 8002
PREFLIGHT_SCHEMA = "agentic-rag-production-preflight-evidence-v1"
REQUIRED_CHECKS = [
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
    parser = argparse.ArgumentParser(description="Run production preflight checks for the Agentic RAG platform.")
    parser.add_argument("--skip-frontend", action="store_true", help="Skip the Next.js production build.")
    parser.add_argument("--skip-smoke", action="store_true", help="Skip the HTTP smoke test.")
    parser.add_argument("--skip-signing-smoke", action="store_true", help="Skip the signed artifact HTTP smoke test.")
    parser.add_argument("--skip-ui-smoke", action="store_true", help="Skip the browser UI smoke test.")
    parser.add_argument(
        "--output-json",
        type=Path,
        help="Optional path for a machine-readable preflight evidence JSON file. Relative paths resolve from the repository root.",
    )
    args = parser.parse_args()

    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    checks: list[dict[str, object]] = []
    server: subprocess.Popen[str] | None = None
    signing_server: subprocess.Popen[str] | None = None
    frontend_server: subprocess.Popen[str] | None = None
    try:
        checks.append(run_check("backend_compile", [sys.executable, "-m", "compileall", "app", "scripts"], cwd=BACKEND))
        checks.append(run_check("production_env_template", [sys.executable, "scripts/validate_production_env.py", "--template"], cwd=ROOT))
        checks.append(run_check("compose_preflight", [sys.executable, "scripts/compose_preflight.py"], cwd=ROOT))
        checks.append(
            run_check(
                "production_audit_template",
                [sys.executable, "scripts/production_audit.py", "--template", "--skip-api", "--no-write"],
                cwd=ROOT,
            )
        )
        checks.append(run_check("api_contract", [sys.executable, "scripts/api_contract_test.py"], cwd=BACKEND))
        checks.append(
            run_check(
                "structured_import_cli_preview",
                [sys.executable, "scripts/structured_import.py", "preview", "app/data/structured/custom_tissue_codon_priors.json"],
                cwd=BACKEND,
            )
        )
        checks.append(
            run_check(
                "data_refresh_cli_plan",
                [
                    sys.executable,
                    "scripts/data_refresh.py",
                    "plan",
                    "--genes",
                    "SNCA",
                    "--regions",
                    "substantia nigra",
                    "--no-allen",
                    "--no-rebuild-index",
                ],
                cwd=BACKEND,
            )
        )
        checks.append(
            run_check(
                "data_refresh_cli_validate",
                [
                    sys.executable,
                    "scripts/data_refresh.py",
                    "validate",
                    "--genes",
                    "SNCA",
                    "--regions",
                    "substantia nigra",
                    "--no-allen",
                    "--no-rebuild-index",
                ],
                cwd=BACKEND,
            )
        )
        checks.append(run_check("golden_response", [sys.executable, "scripts/golden_response_test.py"], cwd=BACKEND))
        checks.append(run_check("golden_value", [sys.executable, "scripts/golden_value_test.py"], cwd=BACKEND))
        checks.append(
            run_check(
                "manual_backend_tests",
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "tests/test_optimizer.py",
                    "-q",
                    "-k",
                    "portfolio_readiness_matrix or production_audit_write_result or compose_preflight",
                ],
                cwd=BACKEND,
            )
        )
        if not args.skip_frontend:
            checks.append(run_check("frontend_build", [npm_command(), "run", "build"], cwd=FRONTEND))
        if not args.skip_smoke or not args.skip_ui_smoke:
            if not backend_ready():
                server = start_backend()
                wait_for_backend()
        if not args.skip_smoke:
            checks.append(run_check("http_smoke", [sys.executable, "scripts/smoke_test.py"], cwd=BACKEND))
        if not args.skip_signing_smoke:
            signing_server = start_backend(
                port=SIGNING_SMOKE_PORT,
                extra_env={
                    "ARTIFACT_SIGNING_KEY": "preflight-hmac-signing-secret-with-32-bytes",
                    "ARTIFACT_SIGNING_KEY_ID": "preflight-hmac-key",
                },
            )
            signing_ready_url = f"http://127.0.0.1:{SIGNING_SMOKE_PORT}/api/v1/health/ready"
            wait_for_backend(url=signing_ready_url)
            signing_env = os.environ.copy()
            signing_env["SMOKE_BASE_URL"] = f"http://127.0.0.1:{SIGNING_SMOKE_PORT}/api/v1"
            signing_env["SMOKE_REQUIRE_SIGNING"] = "1"
            checks.append(run_check("signed_http_smoke", [sys.executable, "scripts/smoke_test.py"], cwd=BACKEND, env=signing_env))
        if not args.skip_ui_smoke:
            frontend_url = os.environ.get("FRONTEND_URL", DEFAULT_FRONTEND_URL)
            if not frontend_ready(frontend_url):
                frontend_url = DEV_FRONTEND_URL
                frontend_server = start_frontend(frontend_url)
                wait_for_frontend(frontend_url)
            ui_env = os.environ.copy()
            ui_env["FRONTEND_URL"] = frontend_url
            ui_env.setdefault("NEXT_PUBLIC_API_BASE_URL", "http://127.0.0.1:8000/api/v1")
            checks.append(run_check("frontend_smoke", [npm_command(), "run", "smoke:ui"], cwd=FRONTEND, env=ui_env))
    finally:
        if signing_server is not None:
            signing_server.terminate()
            try:
                signing_server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                signing_server.kill()
        if frontend_server is not None:
            frontend_server.terminate()
            try:
                frontend_server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                frontend_server.kill()
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()

    report = build_report(args, checks, started_at=started_at, duration_seconds=round(time.perf_counter() - started, 3))
    if args.output_json:
        output_path = args.output_json if args.output_json.is_absolute() else ROOT / args.output_json
        output_path.parent.mkdir(parents=True, exist_ok=True)
        report["output_json"] = str(output_path)
        stamp_report_hashes(report)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["failed"] else 0


def run_check(name: str, command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> dict[str, object]:
    started = time.perf_counter()
    print(f"[preflight] {name}: {' '.join(command)}", flush=True)
    completed = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True)
    stdout = compact_text(completed.stdout)
    stderr = compact_text(completed.stderr)
    details = parse_json(completed.stdout)
    return {
        "name": name,
        "returncode": completed.returncode,
        "status": "pass" if completed.returncode == 0 else "fail",
        "duration_seconds": round(time.perf_counter() - started, 3),
        "command": command,
        "cwd": str(cwd),
        "stdout": stdout,
        "stderr": stderr,
        "details": details if details is not None else stdout,
    }


def build_report(args: argparse.Namespace, checks: list[dict[str, object]], *, started_at: datetime, duration_seconds: float) -> dict[str, Any]:
    failed = [check for check in checks if check["returncode"] != 0]
    skipped = [
        name
        for name, enabled in {
            "frontend_build": args.skip_frontend,
            "http_smoke": args.skip_smoke,
            "signed_http_smoke": args.skip_signing_smoke,
            "frontend_smoke": args.skip_ui_smoke,
        }.items()
        if enabled
    ]
    report = {
        "preflight_schema": PREFLIGHT_SCHEMA,
        "generated_at": started_at.isoformat(),
        "duration_seconds": duration_seconds,
        "root": str(ROOT),
        "status": "fail" if failed else "pass",
        "required_checks": REQUIRED_CHECKS,
        "required_check_count": len(REQUIRED_CHECKS),
        "check_count": len(checks),
        "passed_count": sum(1 for check in checks if check["returncode"] == 0),
        "failed_count": len(failed),
        "skipped_count": len(skipped),
        "mode": {
            "skip_frontend": args.skip_frontend,
            "skip_smoke": args.skip_smoke,
            "skip_signing_smoke": args.skip_signing_smoke,
            "skip_ui_smoke": args.skip_ui_smoke,
        },
        "checks": checks,
        "failed": [str(check["name"]) for check in failed],
        "skipped": skipped,
    }
    report["mode_hash"] = hash_payload(report["mode"])
    report["skipped_hash"] = hash_payload(report["skipped"])
    stamp_report_hashes(report)
    return report


def backend_ready(url: str = READY_URL) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def frontend_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def start_backend(port: int = 8000, extra_env: dict[str, str] | None = None) -> subprocess.Popen[str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=BACKEND,
        env=env,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def start_frontend(frontend_url: str) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env.setdefault("NEXT_PUBLIC_API_BASE_URL", "http://127.0.0.1:8000/api/v1")
    port = frontend_url.rsplit(":", 1)[-1]
    return subprocess.Popen(
        [npm_command(), "run", "dev", "--", "--hostname", "127.0.0.1", "--port", port],
        cwd=FRONTEND,
        env=env,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for_backend(url: str = READY_URL) -> None:
    for _ in range(40):
        if backend_ready(url):
            return
        time.sleep(0.5)
    raise RuntimeError(f"Backend did not become ready at {url}.")


def wait_for_frontend(url: str) -> None:
    for _ in range(60):
        if frontend_ready(url):
            return
        time.sleep(0.5)
    raise RuntimeError(f"Frontend did not become ready at {url}.")


def npm_command() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def parse_json(text: str) -> Any | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def compact_text(text: str, limit: int = 4000) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[:limit] + "...<truncated>"


def hash_payload(payload: Any) -> str:
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def stamp_report_hashes(report: dict[str, Any]) -> None:
    report["checks_hash"] = hash_payload(report["checks"])
    report["preflight_hash"] = hash_payload({key: value for key, value in report.items() if key != "preflight_hash"})


if __name__ == "__main__":
    raise SystemExit(main())
