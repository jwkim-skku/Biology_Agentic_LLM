# Deployment

The included production path is Docker Compose with persistent backend data mounted into `backend/app/data`.

Production profile template:

```powershell
Copy-Item .env.production.example .env.production
```

Replace every placeholder secret in `.env.production` before use. The template enables the settings expected by the production readiness gate: Postgres runtime storage, API keys with explicit RBAC roles, request rate limiting, artifact HMAC signing, CORS narrowed to the deployed dashboard origin, and nonzero artifact retention.

Validate the template shape in CI/local preflight, then validate the filled file before deployment:

```powershell
python scripts/validate_production_env.py --template
python scripts/validate_production_env.py --path .env.production
```

Validate the Compose deployment graph before starting containers:

```powershell
python scripts/compose_preflight.py
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.production.yml config
```

`docker-compose.production.yml` is an override for production-like launches. It requires Postgres storage, API keys with explicit role mapping, artifact signing, nonzero retention settings, a configured frontend API URL, and a healthy Postgres service before the backend starts.
`scripts/compose_preflight.py` uses a synthetic production env with Postgres, signing, rate-limit, and object-store mirror values, so `docker compose config` checks the same required variables that production startup demands.

Create a deployment audit artifact before promotion:

```powershell
python scripts/production_audit.py --path .env.production --require-api
python scripts/production_audit.py --path .env.production --require-api --preflight-evidence backend/app/data/runtime/preflight_latest.json
```

For CI or a workstation without a live backend, use the template/static profile:

```powershell
python scripts/production_audit.py --template --skip-api
```

The audit writes paired JSON and Markdown reports to `backend/app/data/runtime/production_audits`. The full profile combines production env validation, Docker Compose deployment-shape checks, optional recent preflight evidence validation, and live API checks for health readiness, deployment readiness, security, storage, data provenance, data-release bundle verification, external source coverage, RAG diagnostics, RAG evaluation/regression bundle verification, optimizer diagnostics, optimizer benchmark bundle verification, QC report bundle request-provenance verification, governance attestation verification, artifact-ledger verification, and archived QC/structured-import/data-release/RAG/vector-index/optimizer semantic summaries. The live bundle checks require data-release record hashes, RAG evaluation retrieval-trace/evidence-sufficiency/facet-gap/query-term coverage checks, the RAG regression result hash, archived RAG vector-index chunk/dimension/model/retrieval/manifest evidence, optimizer benchmark result hash plus seed-strategy/candidate-diagnostics/recommendation-audit checks, archived optimizer benchmark/stress/case evidence, and QC request/report/candidate-ranking hashes plus optimizer reproducibility and candidate-ranking explainability checks before promotion. The CLI JSON includes an `audit_hash`, and the Markdown report repeats the hash plus the preflight evidence age, required-check count, missing-check count, failed-check count, and skipped-check count for promotion review. When `--preflight-evidence` is supplied, stale, failing, or incomplete preflight JSON is treated as a production audit failure.

Useful runtime variables:

- `APP_DATA_DIR`: persistent backend data root.
- `CORS_ORIGINS`: comma-separated frontend origins.
- `STORAGE_BACKEND`: `sqlite` for local runtime storage or `postgres` for the optional Postgres run/job/audit adapter.
- `DATABASE_URL`: optional Postgres connection URL. It is redacted in status payloads.
- `RAG_VECTOR_BACKEND`: `local_json`, `pgvector`, or `qdrant`. Production promotion expects `pgvector` or `qdrant`.
- `RAG_PGVECTOR_TABLE`: pgvector table used when `RAG_VECTOR_BACKEND=pgvector`.
- `QDRANT_URL` and `QDRANT_COLLECTION`: Qdrant endpoint and collection used when `RAG_VECTOR_BACKEND=qdrant`.
- `RAG_EMBEDDING_BACKEND`: `hash_bow`, `sentence_transformers`, or `openai`. Production promotion expects `sentence_transformers` with a pinned local model or `openai` with an API key and regression evidence.
- `OPENAI_API_KEY` and `OPENAI_EMBEDDING_BASE_URL`: optional managed embedding backend credentials used only when `RAG_EMBEDDING_BACKEND=openai`. Repeated OpenAI embedding calls are cached under `APP_DATA_DIR/runtime/openai_embedding_cache.json`.
- `API_KEYS`: comma-separated API keys. Empty means local-development mode.
- `API_KEY_ROLES`: optional API key role mapping. Use `key=admin;other=viewer,operator` or JSON such as `{"key":["admin"]}`. Without this mapping, configured keys default to `admin`.
- `NEXT_PUBLIC_API_KEY`: browser demo key when API auth is enabled. It must map to a configured `viewer` or `operator` role, never `admin`.
- `RATE_LIMIT_PER_MINUTE`: per-client in-process request limit. Set `0` to disable.
- `ARTIFACT_SIGNING_KEY`: optional HMAC signing secret for run/job/data export manifests.
- `ARTIFACT_SIGNING_KEY_ID`: optional public identifier for the signing key used in artifact manifests.
- `ARTIFACT_ED25519_PRIVATE_KEY`: optional Ed25519 private key for asymmetric artifact and governance-attestation signatures. Supports PEM or base64 raw private key bytes.
- `ARTIFACT_ED25519_PUBLIC_KEY`: optional Ed25519 public key for external verification. Supports PEM or base64 raw public key bytes.
- `ARTIFACT_ED25519_KEY_ID`: public identifier for the Ed25519 keypair when Ed25519 signing or verification is configured.
- `ARTIFACT_RETENTION_DAYS`: archived export retention window. `0` disables automatic eligibility.
- `ARTIFACT_RETENTION_KEEP_MIN`: minimum newest archived exports to preserve when retention is applied.
- `ARTIFACT_OBJECT_STORE_ENABLED`: set `true` to enable S3-compatible mirroring for immutable archived ZIP bundles.
- `ARTIFACT_OBJECT_STORE_ENDPOINT`: S3-compatible HTTPS endpoint, such as AWS S3 or MinIO behind TLS.
- `ARTIFACT_OBJECT_STORE_BUCKET`: destination bucket for archived bundle mirrors.
- `ARTIFACT_OBJECT_STORE_PREFIX`: destination object key prefix.
- `ARTIFACT_OBJECT_STORE_REGION`: SigV4 region used for object-store requests.
- `ARTIFACT_OBJECT_STORE_ACCESS_KEY_ID` and `ARTIFACT_OBJECT_STORE_SECRET_ACCESS_KEY`: object-store credentials used by the mirror endpoint.
- `NEXT_PUBLIC_API_BASE_URL`: frontend API base URL.

Operational endpoints:

- `GET /api/v1/health/live`
- `GET /api/v1/health/ready`
- `GET /api/v1/deployment/readiness`
- `GET /api/v1/deployment/audit`
- `GET /api/v1/deployment/audit/cache`
- `GET /api/v1/deployment/audit/export.zip`
- `GET /api/v1/deployment/audit/verify`
- `GET /api/v1/security/status`
- `GET /api/v1/optimizer/benchmark`
- `GET /api/v1/optimizer/benchmark/export.zip`
- `GET /api/v1/optimizer/benchmark/export/verify`
- `GET /api/v1/optimizer/diagnostics`
- `GET /api/v1/optimizer/stress`
- `GET /api/v1/optimizer/benchmark/cases`
- `GET /api/v1/agent-memory/summary`
- `GET /api/v1/agent-memory`
- `GET /api/v1/agent-memory/{run_id}`
- `GET /api/v1/workflow/status`
- `POST /api/v1/workflow/plan`
- `GET /api/v1/runs/{run_id}/workflow/export.zip`
- `GET /api/v1/runs/{run_id}/workflow/export/verify`
- `GET /api/v1/data/quality`
- `GET /api/v1/rag/regression`
- `GET /api/v1/rag/regression/export.zip`
- `GET /api/v1/rag/regression/export/verify`
- `GET /api/v1/rag/regression/cases`
- `GET /api/v1/rag/diagnostics`
- `GET /api/v1/rag/vector-store/status`
- `GET /api/v1/rag/vector-store/import/plan?target_backend=pgvector`
- `POST /api/v1/rag/vector-store/import?target_backend=pgvector&dry_run=true`
- `GET /api/v1/rag/vector-store/parity?target_backend=local_json`
- `POST /api/v1/rag/evaluate/export.zip`
- `POST /api/v1/rag/evaluate/export/verify`
- `GET /api/v1/rag/vector-index/export.zip`
- `GET /api/v1/rag/vector-index/export/verify`
- `GET /api/v1/storage/status`
- `GET /api/v1/storage/postgres/schema.sql`
- `POST /api/v1/storage/postgres/schema.sql/write`
- `GET /api/v1/storage/migration/sqlite/summary`
- `GET /api/v1/storage/migration/sqlite/export.zip`
- `GET /api/v1/storage/migration/sqlite/parity`
- `POST /api/v1/storage/migration/sqlite/import?dry_run=true`
- `GET /api/v1/governance/attestation`
- `GET /api/v1/governance/attestation/export.zip`
- `GET /api/v1/governance/attestation/verify`
- `GET /api/v1/metrics`
- `GET /api/v1/metrics/prometheus`
- `GET /api/v1/audit/summary`
- `GET /api/v1/audit/events`
- `GET /api/v1/artifacts/summary`
- `GET /api/v1/artifacts/qc-bundles/semantic-summary`
- `GET /api/v1/artifacts/structured-imports/semantic-summary`
- `GET /api/v1/artifacts/data-refresh-plans/semantic-summary`
- `GET /api/v1/artifacts/data-releases/semantic-summary`
- `GET /api/v1/artifacts/rag-evaluations/semantic-summary`
- `GET /api/v1/artifacts/rag-regressions/semantic-summary`
- `GET /api/v1/artifacts/rag-vector-indexes/semantic-summary`
- `GET /api/v1/artifacts/optimizer-benchmarks/semantic-summary`
- `GET /api/v1/artifacts`
- `GET /api/v1/artifacts/retention/plan`
- `POST /api/v1/artifacts/retention/apply?dry_run=true`
- `GET /api/v1/artifacts/object-store/status`
- `GET /api/v1/artifacts/object-store/mirror/plan`
- `POST /api/v1/artifacts/object-store/mirror?dry_run=true`
- `GET /api/v1/artifacts/ledger`
- `GET /api/v1/artifacts/ledger/verify`
- `POST /api/v1/artifacts/ledger/backfill?dry_run=true`
- `GET /api/v1/data/release-lock`
- `POST /api/v1/data/release-lock/write`
- `POST /api/v1/data/refresh/plan/export.zip`
- `POST /api/v1/data/refresh/plan/export/verify`
- `GET /api/v1/data/release/export.zip`
- `GET /api/v1/data/release/export/verify`
- `GET /api/v1/data/external-sources`
- `POST /api/v1/data/external-sources/backfill?dry_run=true`
- `POST /api/v1/report/export-bundle.zip`
- `POST /api/v1/report/export-bundle/verify`

The semantic-summary endpoints include a `freshness_status`, `latest_created_at`, `latest_age_hours`, and `freshness_policy.warning_hours` so production reviewers can distinguish current archive evidence from stale or untimestamped artifacts.

Smoke test:

```powershell
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
python scripts/smoke_test.py
```

The smoke test checks health readiness, deployment readiness, settings, security status, RAG status, structured data status, metrics, CDS report generation, release-lock pinning, data release bundle export/verification, data refresh plan bundle export/verification, run export archival and verification, data snapshot archival and verification, a dry-run background data-refresh job with job export archival/verification, artifact archive download/re-verification, indexed semantic archive summaries, artifact ledger verification, and audit-log visibility.

RAG evaluation responses include `query_fingerprint`, `ranking_policy`, `retrieval_trace`, `facet_gap_analysis`, `query_term_coverage`, and per-result `rationale` fields so operators can reproduce and explain why evidence chunks were selected for a design context and which requested facets are absent from retrieved results or the indexed corpus. `POST /api/v1/rag/evaluate/export.zip` turns the same evaluation into a manifested ZIP with `evaluation.json`, `retrieval_trace.json`, `score_breakdown.csv`, `chunks.jsonl`, `rag_status.json`, and `structured_manifest.json`; `POST /api/v1/rag/evaluate/export/verify` rebuilds and semantically checks the bundle before reuse in audit records.

RAG regression exports capture the fixed retrieval regression suite as deployment evidence. `GET /api/v1/rag/regression/export.zip` writes `regression.json`, `cases.json`, `case_metrics.csv`, `weak_cases.json`, `diagnostics.json`, `ranking_policy.json`, `rag_status.json`, and `structured_manifest.json`; `regression.json` and the bundle manifest include a stable `results_hash`, and `GET /api/v1/rag/regression/export/verify` checks case hash consistency, result hash consistency, case metric row counts, diagnostics schema, weak-case counts, ranking policy, and structured manifest consistency before the archive is reused for search-policy promotion.

Workflow trace exports make the PDF's agentic role topology auditable independently from full run bundles. `GET /api/v1/workflow/status` reports the local deterministic role-graph runtime and LangGraph-compatible state contract. `GET /api/v1/runs/{run_id}/workflow/export.zip` writes task, plan, trace, role-contract, run summary, RAG status, and structured manifest files; `GET /api/v1/runs/{run_id}/workflow/export/verify` checks trace row shape, required files, workflow id consistency, trace/plan hashes, and structured-manifest consistency.

QC report exports are promotion evidence, not just rendered summaries. `POST /api/v1/report/export-bundle.zip` and the gene-based equivalent include `data_quality.json`, `optimizer_stress.json`, and `recommendation_audit.json` beside the report formats, request/report/candidate-ranking/recommendation-audit hashes, candidate ranking CSV, optimizer reproducibility manifest, RAG status, and structured manifest. Bundle verification cross-checks the report's data-quality and optimizer-stress summaries plus the request/report/candidate-ranking/recommendation-audit hashes against those evidence files before the archive can be treated as semantically clean.

`GET /api/v1/rag/embedding/status` reports the configured RAG embedding backend (`hash_bow` or `sentence_transformers`), active adapter, fallback state, model id, dimensions, and production readiness. Local development defaults to deterministic `hash-bow-v1`; production should install and pin a locally available biomedical `sentence-transformers` model before rebuilding the RAG index and rerunning regression.

`GET /api/v1/rag/vector-store/status` reports the configured RAG vector backend (`local_json`, `pgvector`, or `qdrant`), the active adapter, fallback state, pgvector table name, and Qdrant collection settings. `GET /api/v1/rag/vector-index/export.zip` exports the complete local RAG vector index for migration planning. The manifested ZIP includes `vector_chunks.jsonl`, `payload_schema.json`, `embedding_status.json`, `pgvector_schema.sql`, `qdrant_collection.json`, `vector_store_import_plan.json`, `vector_store_parity.json`, `rag_status.json`, `rag_diagnostics.json`, and `structured_manifest.json`; `GET /api/v1/rag/vector-index/export/verify` checks row counts, embedding dimensions, embedding status schema, embedding-model consistency, unique chunk IDs, required payload fields, vector-readiness schema, migration-plan schema, parity-report schema, vector row hashes, and structured-manifest hash consistency before the archive is stored in the artifact ledger.

RAG vector-store cutover follows the same dry-run-first pattern as runtime storage migration. Use `GET /api/v1/rag/vector-store/import/plan?target_backend=pgvector` to inspect the source row count, row hash, target requirements, and migration steps; use `POST /api/v1/rag/vector-store/import?target_backend=pgvector&dry_run=true` for an audited dry-run. When the target service is configured, call the same import endpoint with `dry_run=false`, then verify `GET /api/v1/rag/vector-store/parity?target_backend=pgvector` before setting `RAG_VECTOR_BACKEND=pgvector`. Qdrant uses the same endpoints with `target_backend=qdrant`.

Optimizer benchmark exports follow the same audit pattern. `GET /api/v1/optimizer/benchmark/export.zip` writes `benchmark.json`, `diagnostics.json`, `cases.json`, `case_metrics.csv`, `candidate_diagnostics.json`, optimizer/score config snapshots, and `structured_manifest.json`; `benchmark.json` and the bundle manifest include a stable `results_hash`, and `case_metrics.csv` includes recommendation trade-off/regret plus deterministic secondary-structure/MFE-proxy metrics. `GET /api/v1/optimizer/benchmark/export/verify` checks case hashes, result hashes, case counts, required CSV metric columns, diagnostics schemas, candidate diagnostics coverage, recommendation-audit trade-off metadata, and structured manifest consistency.

RNA folding backend readiness is exposed separately from deterministic scoring proxies. `GET /api/v1/optimizer/rna-folding/status` reports the requested backend, active backend, fallback state, executable path, timeout, and production readiness. `POST /api/v1/optimizer/rna-folding/evaluate` evaluates a CDS window with ViennaRNA `RNAfold --noPS` when `RNA_FOLDING_BACKEND=rnafold` is configured and the executable is available; otherwise it returns the deterministic proxy evidence and a warning. Optimizer diagnostics, benchmark ZIPs, deployment readiness, and production audit bundles include the same folding-backend evidence. Production deployments should install ViennaRNA and set `RNA_FOLDING_BACKEND=rnafold`, `RNAFOLD_EXECUTABLE`, `RNAFOLD_TIMEOUT_SECONDS`, and `RNAFOLD_WINDOW_NT`.

Deployment readiness:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/deployment/readiness
```

This returns pass/warning/fail gates for structured data, data provenance, structured promotion quality, data release bundle verification, RAG regression, RAG embedding backend, optimizer benchmark, RNA folding backend, workflow runtime, QC snapshot export, storage, security, artifact signing, governance attestation, artifact archive integrity, artifact object-store mirroring, archived QC bundle semantic verification, archived data-refresh plan semantic verification, archived data-release semantic verification, archived RAG vector-index semantic verification, and archived structured import audit semantic verification. The archive semantic gates include `freshness_status`, `latest_created_at`, `latest_age_hours`, and `freshness_warning_hours` in their details. The `data_provenance`, `structured_quality`, `data_release_bundle`, `data_refresh_plan_archive_semantics`, `data_release_archive_semantics`, `rag_vector_index_archive_semantics`, `rag_embedding_backend`, and `rna_folding_backend` gates keep tRNA caveats, seed priors, live/source snapshot coverage, release pinning, refresh-plan operation evidence, release-bundle semantic checks, release archive record hashes, vector-index migration evidence, archive evidence freshness, hash-BOW retrieval fallback, and deterministic structure-proxy fallback visible until replaced with release-pinned quantitative matrices, a pinned biomedical embedding model or configured OpenAI embedding backend, and a validated folding executable. `deployment_ready=true` means no blocking failures were found; `production_ready=true` additionally requires zero warnings.

The strict production gates expect:

- `STORAGE_BACKEND=postgres` with `DATABASE_URL` configured and the active runtime adapter on Postgres.
- `RAG_VECTOR_BACKEND=pgvector` with `RAG_PGVECTOR_TABLE`, or `RAG_VECTOR_BACKEND=qdrant` with `QDRANT_URL` and `QDRANT_COLLECTION`.
- `API_KEYS`, explicit `API_KEY_ROLES`, and `RATE_LIMIT_PER_MINUTE > 0`.
- `ARTIFACT_SIGNING_KEY` plus key id, or Ed25519 signing/verification with `ARTIFACT_ED25519_KEY_ID`.
- `ARTIFACT_OBJECT_STORE_ENABLED=true` with HTTPS endpoint, bucket, prefix, region, and credentials for immutable archive mirroring.
- zero structured-data validation warnings and a clean artifact archive ledger.

Governance attestation:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/governance/attestation
Invoke-WebRequest http://127.0.0.1:8000/api/v1/governance/attestation/export.zip -OutFile governance_attestation.zip
Invoke-RestMethod http://127.0.0.1:8000/api/v1/governance/attestation/verify
```

The attestation ZIP captures OpenAPI shape, structured data provenance, release lock status, RAG status, storage parity, signing status, and artifact ledger verification. Exported attestation bundles are archived into the artifact archive and hash-chain ledger. HMAC signing is available through `ARTIFACT_SIGNING_KEY`; Ed25519 asymmetric signing is available through `ARTIFACT_ED25519_PRIVATE_KEY` plus an optional public verifier key.

Production audit bundle:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/deployment/audit
Invoke-RestMethod http://127.0.0.1:8000/api/v1/deployment/audit/cache
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/deployment/audit?refresh=true"
Invoke-WebRequest http://127.0.0.1:8000/api/v1/deployment/audit/export.zip -OutFile production_audit.zip
Invoke-RestMethod http://127.0.0.1:8000/api/v1/deployment/audit/verify
```

The production audit endpoint uses a short in-process TTL cache for dashboard and verification calls; use `refresh=true` when producing fresh deployment-promotion evidence. The production audit ZIP is a deployment-promotion artifact. It includes JSON and Markdown summaries plus evidence files for deployment readiness, security, storage, data provenance, structured data quality, external source coverage, RAG diagnostics, optimizer diagnostics, workflow runtime, agent memory, governance attestation verification, artifact ledger verification, artifact archive summary, archived QC bundle semantic verification, archived data-refresh plan semantic verification, archived data-release semantic verification, archived structured import audit semantic verification, archived RAG evaluation/regression/vector-index semantic verification, audit timing, and audit-log summary. The CLI audit also fails archived data-refresh plan evidence that claims checked artifacts but lacks operation count, validation status, dataset id, or structured manifest hash, archived RAG vector-index evidence that lacks chunk count, embedding dimensions/model, retrieval model, or structured manifest hash, and archived optimizer benchmark evidence that lacks benchmark, diagnostics, stress, case-count, or case-hash evidence. The Markdown report includes the slowest audit evidence timings for quick operator triage. Exported bundles are archived into the immutable artifact archive and ledger.

Artifact retention:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/artifacts/retention/plan?retention_days=30&keep_min=100"
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/api/v1/artifacts/retention/apply?dry_run=true&retention_days=30&keep_min=100"
```

Retention apply defaults to `dry_run=true`. When called with `dry_run=false`, eligible ZIP files and archive index rows are removed, and a `retention_delete` tombstone is appended to the artifact ledger so hash-chain verification remains auditable.

Artifact ledger backfill:

```powershell
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/api/v1/artifacts/ledger/backfill?dry_run=true"
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/api/v1/artifacts/ledger/backfill?dry_run=false"
```

Backfill appends `backfill_store` ledger events for archived artifacts that predate ledger support. It preserves the append-only hash chain and lets `/api/v1/artifacts/ledger/verify` return `pass` once all historical archive rows are represented.

Postgres migration contract:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/api/v1/storage/postgres/schema.sql -OutFile postgres_schema.sql
Invoke-WebRequest http://127.0.0.1:8000/api/v1/storage/migration/sqlite/export.zip -OutFile sqlite_to_postgres_migration_bundle.zip
Invoke-RestMethod http://127.0.0.1:8000/api/v1/storage/migration/sqlite/parity
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/api/v1/storage/migration/sqlite/import?dry_run=true"
docker compose --profile postgres up postgres
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.production.yml up --build
```

SQLite remains the default for local reproducibility. When `STORAGE_BACKEND=postgres` and `DATABASE_URL` are set, run/job/audit stores use the Postgres adapter and apply the published schema automatically. The published Postgres DDL also includes the `vector` extension and `rag_chunks` table for `RAG_VECTOR_BACKEND=pgvector`. The migration bundle exports SQLite rows as JSONL plus manifest hashes; the import endpoint defaults to dry-run and only writes to Postgres when called with `dry_run=false` in a configured environment. The parity endpoint compares record counts and row-level canonical SHA-256 hashes when a target Postgres database is reachable.

Preflight verification:

```powershell
python scripts/preflight.py
python scripts/preflight.py --output-json backend/app/data/runtime/preflight_latest.json
```

This runs backend compile checks, production env template validation, Docker Compose deployment-shape checks, the static production audit profile, API contract checks, structured import CLI preview verification, GTEx/Allen data-refresh CLI plan verification, response/value golden tests, the manual backend regression suite, the frontend production build, the HTTP smoke test, a separate signed-artifact smoke profile with `ARTIFACT_SIGNING_KEY` enabled, and a browser UI smoke test that checks operational panels and metrics hydration. Use `--skip-frontend`, `--skip-smoke`, `--skip-signing-smoke`, or `--skip-ui-smoke` for narrower deployment diagnostics. `--output-json` writes a timestamped evidence payload with the command, working directory, duration, skipped checks, pass/fail result, compact stdout/stderr, and parsed JSON details for every check; CI uploads this file as the `backend-preflight-evidence` artifact, then validates it through `production_audit.py --preflight-evidence` and uploads the resulting `backend-production-audit` artifact. The production audit also checks that preflight structured-import and data-refresh details include source hashes, planned operations, dry-run status, validation schema, and normalized gene/region requests.

API contract tests can also be run directly:

```powershell
cd backend
python scripts/api_contract_test.py
python scripts/golden_response_test.py
python scripts/golden_value_test.py
```
