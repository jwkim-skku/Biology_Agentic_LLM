from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import app


REQUIRED_PATHS = {
    "/api/v1/health/ready": {"get"},
    "/api/v1/deployment/readiness": {"get"},
    "/api/v1/deployment/audit": {"get"},
    "/api/v1/deployment/audit/cache": {"get"},
    "/api/v1/deployment/audit/export.zip": {"get"},
    "/api/v1/deployment/audit/verify": {"get"},
    "/api/v1/security/status": {"get"},
    "/api/v1/storage/status": {"get"},
    "/api/v1/storage/postgres/schema.sql": {"get"},
    "/api/v1/storage/postgres/schema.sql/write": {"post"},
    "/api/v1/storage/migration/sqlite/summary": {"get"},
    "/api/v1/storage/migration/sqlite/export.zip": {"get"},
    "/api/v1/storage/migration/sqlite/parity": {"get"},
    "/api/v1/storage/migration/sqlite/import": {"post"},
    "/api/v1/governance/attestation": {"get"},
    "/api/v1/governance/attestation/export.zip": {"get"},
    "/api/v1/governance/attestation/verify": {"get"},
    "/api/v1/optimizer/benchmark": {"get"},
    "/api/v1/optimizer/benchmark/export.zip": {"get"},
    "/api/v1/optimizer/benchmark/export/verify": {"get"},
    "/api/v1/optimizer/diagnostics": {"get"},
    "/api/v1/optimizer/stress": {"get"},
    "/api/v1/optimizer/rna-folding/status": {"get"},
    "/api/v1/optimizer/rna-folding/evaluate": {"post"},
    "/api/v1/optimizer/benchmark/cases": {"get"},
    "/api/v1/audit/events": {"get"},
    "/api/v1/audit/summary": {"get"},
    "/api/v1/artifacts": {"get"},
    "/api/v1/artifacts/summary": {"get"},
    "/api/v1/artifacts/qc-bundles/semantic-summary": {"get"},
    "/api/v1/artifacts/structured-imports/semantic-summary": {"get"},
    "/api/v1/artifacts/data-refresh-plans/semantic-summary": {"get"},
    "/api/v1/artifacts/data-releases/semantic-summary": {"get"},
    "/api/v1/artifacts/rag-evaluations/semantic-summary": {"get"},
    "/api/v1/artifacts/rag-regressions/semantic-summary": {"get"},
    "/api/v1/artifacts/optimizer-benchmarks/semantic-summary": {"get"},
    "/api/v1/artifacts/retention/plan": {"get"},
    "/api/v1/artifacts/retention/apply": {"post"},
    "/api/v1/artifacts/object-store/status": {"get"},
    "/api/v1/artifacts/object-store/mirror/plan": {"get"},
    "/api/v1/artifacts/object-store/mirror": {"post"},
    "/api/v1/artifacts/ledger": {"get"},
    "/api/v1/artifacts/ledger/verify": {"get"},
    "/api/v1/artifacts/ledger/backfill": {"post"},
    "/api/v1/artifacts/{artifact_id}/download": {"get"},
    "/api/v1/artifacts/{artifact_id}/verify": {"get"},
    "/api/v1/score": {"post"},
    "/api/v1/validate-cds": {"post"},
    "/api/v1/optimize": {"post"},
    "/api/v1/design-from-gene": {"post"},
    "/api/v1/report": {"post"},
    "/api/v1/report/export/{export_format}": {"post"},
    "/api/v1/report/export-bundle.zip": {"post"},
    "/api/v1/report/export-bundle/verify": {"post"},
    "/api/v1/report-from-gene/export-bundle.zip": {"post"},
    "/api/v1/report-from-gene/export-bundle/verify": {"post"},
    "/api/v1/rag/search": {"post"},
    "/api/v1/rag/evaluate": {"post"},
    "/api/v1/rag/evaluate/export.zip": {"post"},
    "/api/v1/rag/evaluate/export/verify": {"post"},
    "/api/v1/rag/vector-index/export.zip": {"get"},
    "/api/v1/rag/vector-index/export/verify": {"get"},
    "/api/v1/rag/diagnostics": {"get"},
    "/api/v1/rag/embedding/status": {"get"},
    "/api/v1/rag/vector-store/status": {"get"},
    "/api/v1/rag/vector-store/import/plan": {"get"},
    "/api/v1/rag/vector-store/import": {"post"},
    "/api/v1/rag/vector-store/parity": {"get"},
    "/api/v1/rag/regression": {"get"},
    "/api/v1/rag/regression/export.zip": {"get"},
    "/api/v1/rag/regression/export/verify": {"get"},
    "/api/v1/rag/regression/cases": {"get"},
    "/api/v1/agent-memory": {"get"},
    "/api/v1/agent-memory/summary": {"get"},
    "/api/v1/agent-memory/{run_id}": {"get"},
    "/api/v1/workflow/status": {"get"},
    "/api/v1/workflow/plan": {"post"},
    "/api/v1/workflow/design-from-gene": {"post"},
    "/api/v1/runs/{run_id}/export.zip": {"get"},
    "/api/v1/runs/{run_id}/export/verify": {"get"},
    "/api/v1/runs/{run_id}/workflow/export.zip": {"get"},
    "/api/v1/runs/{run_id}/workflow/export/verify": {"get"},
    "/api/v1/jobs/batch-design-from-genes": {"post"},
    "/api/v1/jobs/{job_id}/export.zip": {"get"},
    "/api/v1/jobs/{job_id}/export/verify": {"get"},
    "/api/v1/data/provenance": {"get"},
    "/api/v1/data/catalog": {"get"},
    "/api/v1/data/coverage": {"get"},
    "/api/v1/data/quality": {"get"},
    "/api/v1/data/refresh/validate": {"post"},
    "/api/v1/data/refresh-log": {"get"},
    "/api/v1/data/refresh/plan/export.zip": {"post"},
    "/api/v1/data/refresh/plan/export/verify": {"post"},
    "/api/v1/data/lockfile": {"get"},
    "/api/v1/data/release-lock": {"get"},
    "/api/v1/data/release-lock/write": {"post"},
    "/api/v1/data/release/export.zip": {"get"},
    "/api/v1/data/release/export/verify": {"get"},
    "/api/v1/data/external-sources": {"get"},
    "/api/v1/data/external-sources/backfill": {"post"},
    "/api/v1/data/snapshot.zip": {"get"},
    "/api/v1/data/snapshot/verify": {"get"},
    "/api/v1/structured/status": {"get"},
    "/api/v1/structured/manifest": {"get"},
    "/api/v1/structured/validate": {"get"},
    "/api/v1/structured/import/preview": {"post"},
    "/api/v1/structured/import": {"post"},
    "/api/v1/external/gtex/import-gene-expression": {"post"},
    "/api/v1/external/allen/import-whb-taxonomy": {"post"},
}

REQUIRED_SCHEMAS = {
    "ApiResponse",
    "ScoreRequest",
    "OptimizeRequest",
    "DesignFromGeneRequest",
    "BatchDesignFromGenesRequest",
    "DataRefreshRequest",
}


def main() -> int:
    spec = app.openapi()
    failures: list[str] = []
    paths = spec.get("paths", {})
    schemas = spec.get("components", {}).get("schemas", {})

    for path, methods in sorted(REQUIRED_PATHS.items()):
        if path not in paths:
            failures.append(f"missing path: {path}")
            continue
        actual_methods = set(paths[path])
        missing_methods = methods - actual_methods
        if missing_methods:
            failures.append(f"{path} missing methods: {', '.join(sorted(missing_methods))}")

    for schema in sorted(REQUIRED_SCHEMAS):
        if schema not in schemas:
            failures.append(f"missing schema: {schema}")

    for path, methods in paths.items():
        if not path.startswith("/api/v1/"):
            continue
        for method, operation in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            if "responses" not in operation:
                failures.append(f"{method.upper()} {path} missing responses")
            if "operationId" not in operation:
                failures.append(f"{method.upper()} {path} missing operationId")

    result = {
        "required_paths": len(REQUIRED_PATHS),
        "required_schemas": len(REQUIRED_SCHEMAS),
        "path_count": len(paths),
        "schema_count": len(schemas),
        "failures": failures,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
