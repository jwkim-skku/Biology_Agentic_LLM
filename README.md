# Agentic RAG Codon Optimization Platform

This repository implements a production-oriented prototype of the PDF proposal for brain-region and cell-type-aware gene therapy CDS design.

## Current Capability

- FastAPI backend for scoring, gene-to-CDS resolution, optimization, evidence retrieval, and QC report export.
- Next.js dashboard for target entry, candidate comparison, evidence review, QC summary, data provenance, source-snapshot coverage, and operational readiness.
- MANE-aware Ensembl transcript selection.
- Local document ingestion for PDF, Markdown, text, and JSON.
- Hybrid local RAG retrieval over seed evidence, ingested documents, and structured GTEx/Allen/CUSTOM priors, including alias expansion, facet-aware reranking, source-priority scoring, and retrieval coverage evaluation.
- RAG diagnostics for corpus/source distribution, reproducible chunking policy, token and embedding health, regression macro metrics, weak-case surfacing, and operational recommendations.
- Manifested RAG evaluation ZIP bundles with retrieval trace, score CSV, retrieved chunks JSONL, RAG status, structured manifest, artifact verification, and semantic cross-checks.
- Data catalog and refresh API for reproducible GTEx/Allen reference panel ingestion.
- Data provenance audit for source hashes, release metadata, RAG/document coverage, and refresh history.
- Production caveat tracking for low-confidence or placeholder tRNA/codon-availability priors so seed matrices cannot be mistaken for release-pinned quantitative data.
- Baseline audit recording for local seed/ingested data so reproducibility checks can start before a live external refresh.
- Data lockfile support for pinning and drift-checking a reproducible data/RAG/document state.
- Data release lockfile support for pinning structured dataset releases, file hashes, and release drift independently from runtime refresh state.
- Data release bundle verification in deployment readiness and production audit evidence, including required file, row-count, source-byte, and promotion-status checks.
- Data snapshot ZIP export and archived semantic verification for structured data, document extracts, RAG index, refresh log, manifests, external source snapshots, and artifact-level SHA-256 audit manifests.
- External source snapshot coverage and backfill controls for reproducible bundled/manual structured records.
- Seeded NSGA-II synonymous optimizer with repair and manufacturing-policy scoring for GC/CpG/motif/polyA/splice/restriction-site/codon-pair/5-prime-GC/hairpin/secondary-structure-proxy/complexity constraints.
- Optimizer diagnostics for objective inventory, benchmark quality bands, weak-case surfacing, and tuning recommendations.
- Manifested optimizer benchmark ZIP bundles with benchmark metrics, case provenance fingerprints, recommended-candidate folding evidence, diagnostics, case catalog, candidate diagnostics, config snapshots, artifact verification, archive storage, readiness/audit evidence, and semantic cross-checks.
- ORF validation and QC gate checks for CDS inputs and recommended candidates.
- QC report bundles preserve recommended-candidate RNA folding/proxy evidence with stable hashes for archive and audit review.
- Candidate diagnostics for feasible-set counts, Pareto-front representatives, score ranges, codon-level diversity, and recommendation-audit trade-off regret against best-by-metric alternatives.
- Optimizer reproducibility manifests with canonical config hashes, score-config hashes, objective inventory, seed, repair policy, target hash, and source CDS hash.
- CUSTOM-style tissue-aware codon multipliers from structured priors.
- Markdown, HTML, JSON, and PDF QC report export.
- Manifested QC report ZIP bundles with JSON, Markdown, HTML, PDF, candidate ranking CSV, provenance files, artifact verification, and semantic cross-checks for report and optimizer reproducibility metadata.
- SQLite run persistence with request/design/report/trace storage.
- Persistent agent memory index for session, semantic-rule, and artifact summaries derived from saved design runs.
- SQLite background job store for long-running single-gene design, batch gene design, and data refresh work.
- SQLite operational audit log for report/export/verify/job/data-refresh actions with actor hashes and request IDs.
- ZIP audit bundle export for persisted runs and background jobs, including file-level SHA-256 artifact manifests.
- Artifact verification endpoints for run, job, and data snapshot export bundles.
- Governance attestation export for OpenAPI, data, RAG, storage, and artifact-ledger state with hash verification.
- Production audit export for deployment readiness, security, storage, data provenance, RAG/optimizer diagnostics, QC/data snapshot archive semantics, governance verification, artifact-ledger state, and hash-pinned required action evidence.
- Optional Postgres runtime adapter for run/job/audit stores, with redacted `DATABASE_URL` status and migration DDL.
- SQLite-to-Postgres migration bundle export, dry-run import planning, and row-level source/target parity hashing.
- Immutable local artifact archive for exported run/job/data snapshot/QC report bundles with SHA-256 indexing, archive re-verification endpoints, and QC bundle semantic re-checks.
- Append-only artifact archive ledger with hash-chain verification for stored export history.
- Dry-run-first artifact retention cleanup with ledger tombstones for auditable archive pruning.
- JSON and Prometheus-style metrics for request, run, job, RAG, and structured-data observability.
- Local agentic workflow orchestration for planner, retriever, optimizer, and QC writer roles.
- Docker Compose configuration for backend/frontend deployment.
- API shape and value-level golden fixtures for deterministic scoring, validation, optimization, and QC regression checks.

## Local Development

Backend:

```powershell
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Optional environment:

```powershell
$env:APP_DATA_DIR = 'C:\path\to\persistent\data'
$env:CORS_ORIGINS = 'http://127.0.0.1:3000,http://127.0.0.1:3001'
$env:API_KEYS = 'replace-with-a-long-random-key'
$env:API_KEY_ROLES = 'replace-with-a-long-random-key=admin'
$env:RATE_LIMIT_PER_MINUTE = '120'
$env:ARTIFACT_SIGNING_KEY = 'replace-with-a-long-random-signing-secret'
$env:ARTIFACT_SIGNING_KEY_ID = 'local-hmac-sha256'
$env:ARTIFACT_RETENTION_DAYS = '0'
$env:ARTIFACT_RETENTION_KEEP_MIN = '100'
```

`API_KEYS` is optional and disabled by default for local development. When set, protected API routes accept either `X-API-Key: <key>` or `Authorization: Bearer <key>`. `API_KEY_ROLES` can map keys to `viewer`, `operator`, or `admin` roles using `key=admin;other=viewer,operator` or a JSON object. If roles are omitted, configured keys default to `admin` for backwards-compatible single-key deployments. For browser-based demos, set the matching `NEXT_PUBLIC_API_KEY` in the frontend environment.
`ARTIFACT_SIGNING_KEY` is optional. When set, exported run/job/data snapshot manifests are signed with HMAC-SHA256 and verify endpoints validate the signature.

For a production-style configuration, copy `.env.production.example`, replace every placeholder secret, set `STORAGE_BACKEND=postgres`, and confirm `/api/v1/deployment/readiness` has no `fail` gates before deployment.

Validate the production template or a filled production env file:

```powershell
python scripts/validate_production_env.py --template
python scripts/validate_production_env.py --path .env.production
python scripts/production_audit.py --template --skip-api
```

Frontend:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:3001`.

## Docker

Docker is not installed in the current workspace environment, but deployment files are included:

```powershell
docker compose up --build
```

For a production-like Compose plan with Postgres, auth, retention, and artifact signing required:

```powershell
Copy-Item .env.production.example .env.production
python scripts/validate_production_env.py --path .env.production
python scripts/compose_preflight.py
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.production.yml up --build
```

Then open:

- Frontend: `http://127.0.0.1:3000`
- Backend docs: `http://127.0.0.1:8000/docs`

Runtime settings are available at `http://127.0.0.1:8000/api/v1/settings`.
Security status is available at `http://127.0.0.1:8000/api/v1/security/status`.
Operational audit summary is available at `http://127.0.0.1:8000/api/v1/audit/summary`.
Archived artifacts are available at `http://127.0.0.1:8000/api/v1/artifacts`.
Readiness is available at `http://127.0.0.1:8000/api/v1/health/ready`.
Reference data catalog is available at `http://127.0.0.1:8000/api/v1/data/catalog`, and the target-level live/seed coverage matrix is available at `http://127.0.0.1:8000/api/v1/data/coverage`.
Deployment notes are in `docs/DEPLOYMENT.md`.

## Verification

Run the full local preflight from the repository root:

```powershell
python scripts/preflight.py
python scripts/preflight.py --output-json backend/app/data/runtime/preflight_latest.json
```

The preflight compiles the backend, validates the production env template, checks Docker Compose deployment shape, checks the OpenAPI contract, verifies the structured import CLI preview path, verifies the GTEx/Allen data-refresh CLI planning and validation paths, runs response/value golden tests, runs the manual backend regression suite, builds the frontend, starts the backend and frontend if needed, runs the HTTP smoke test, runs a separate signed-artifact smoke profile with `ARTIFACT_SIGNING_KEY` enabled, and verifies the browser UI smoke path. The readiness smoke also verifies that tRNA/codon-availability seed caveats are exposed through the data provenance gate, and the artifact smoke checks indexed semantic summaries including workflow trace archives. Use `--skip-frontend`, `--skip-smoke`, `--skip-signing-smoke`, or `--skip-ui-smoke` for narrower diagnostics. `--output-json` writes a reproducible evidence file with timestamps, executed commands, durations, skipped checks, pass/fail status, compact stdout/stderr, parsed JSON details where commands emit JSON, `checks_hash`, and `preflight_hash`.

For a deployment audit artifact, run:

```powershell
python scripts/production_audit.py --path .env.production --require-api
python scripts/production_audit.py --path .env.production --require-api --preflight-evidence backend/app/data/runtime/preflight_latest.json
```

This writes JSON and Markdown reports under `backend/app/data/runtime/production_audits`, combining production env validation, Compose checks, optional preflight evidence validation, and live operational API status when the backend is reachable. When `--preflight-evidence` is provided, the audit requires a passing, recent preflight payload with the expected backend, contract, data-refresh, golden, and regression checks.

Individual contract and golden checks are also available from the repository root:

```powershell
python scripts/api_contract_test.py
python scripts/golden_response_test.py
python scripts/golden_value_test.py
```

Backend smoke and manual regression checks can be run from `backend`:

```powershell
cd backend
python -c "from tests import test_optimizer as t; [getattr(t, name)() for name in dir(t) if name.startswith('test_')]; print('manual tests passed')"
python scripts/smoke_test.py
cd ..\frontend
npm.cmd run smoke:ui
```

Install `pytest` if you want standard test discovery.
